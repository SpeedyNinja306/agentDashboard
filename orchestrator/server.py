"""Persistent orchestrator process: `python -m orchestrator.server`.

Replaces the one-shot CLI as the way the orchestrator runs. The process stays up, accepts goals
over HTTP, runs them one at a time, and broadcasts each lifecycle event to every connected
WebSocket client as it happens.

    POST /tasks          {"goal": "..."} -> 202 with the task record
    GET  /tasks          every task the process still remembers
    GET  /tasks/{id}     one task, including its result envelope once finished
    GET  /health         client count, queue depth, currently running task
    WS   /events         the live event stream; `?replay=N` to catch up first

**The WebSocket carries lifecycle events and nothing else.** Every frame is exactly one of
ticket 3's events, serialized exactly as that ticket wrote it to stdout. No envelopes, no acks,
no keepalive frames — a client can parse every frame the same way, and a stdout capture from the
CLI remains a valid oracle for what a client should receive. Submitting goals is therefore an
HTTP concern, not a WebSocket one; that is also what lets a submitter and a watcher be different
processes.

Connecting mid-run is ordinary: a new subscriber starts receiving at the next event the
orchestrator emits, and `?replay=N` replays the last N events first so a late joiner can see how
the run got to where it is. Disconnecting is equally ordinary — see `orchestrator.tasks` for why
it cannot disturb the queue.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import events
from orchestrator.graph import run_goal
from orchestrator.hub import EventHub, Subscriber
from orchestrator.tasks import QueueFull, TaskQueue, TaskRecord

#: Workers that accept direct task submissions from the dashboard.
KNOWN_WORKERS: frozenset[str] = frozenset({"research-specialist"})

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

#: Closes a client that fell so far behind it missed events. See `hub.Subscriber`.
WS_INTERNAL_ERROR = 1011


class SubmitGoal(BaseModel):
    """Body of `POST /tasks`."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, description="the goal to dispatch")


def create_app() -> FastAPI:
    hub = EventHub()
    queue = TaskQueue(run_goal)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub.bind(asyncio.get_running_loop())
        previous_sink = events.set_sink(hub.publish_from_thread)
        queue.start()
        logger.info("orchestrator server ready")
        try:
            yield
        finally:
            events.set_sink(previous_sink)
            await queue.stop()
            hub.close_all()
            logger.info("orchestrator server stopped")

    app = FastAPI(
        title="AgentDashboard orchestrator",
        description="Persistent orchestrator with a WebSocket lifecycle event stream.",
        lifespan=lifespan,
    )

    # The dashboard is served from a different origin in dev (Vite on :5173), and goals get
    # submitted from a browser console during manual testing. The server binds to loopback by
    # default, so the trust boundary is the bind address rather than the origin list.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.hub = hub
    app.state.queue = queue

    _register_routes(app, hub, queue)
    return app


def _register_routes(app: FastAPI, hub: EventHub, queue: TaskQueue) -> None:
    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "clients": hub.client_count,
            "events_published": hub.published_count,
            "replay_available": hub.replay_available,
            "queued": queue.pending_count,
            "running": queue.running_id,
        }

    @app.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
    async def submit_task(body: SubmitGoal) -> dict[str, Any]:
        goal = body.goal.strip()
        if not goal:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="goal is empty; nothing to research",
            )
        try:
            record = queue.submit(goal)
        except QueueFull as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
            ) from exc
        return record.to_dict()

    @app.post("/workers/{worker_name}/tasks", status_code=status.HTTP_202_ACCEPTED)
    async def submit_worker_task(worker_name: str, body: SubmitGoal) -> dict[str, Any]:
        """Submit a goal to a specific named worker.

        The worker name must be one of the registered workers. Today there is only one
        (research-specialist), but the endpoint is named so the dashboard can address
        workers by identity once more are added.
        """
        if worker_name not in KNOWN_WORKERS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown worker '{worker_name}'; known workers: {sorted(KNOWN_WORKERS)}",
            )
        goal = body.goal.strip()
        if not goal:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="goal is empty; nothing to research",
            )
        try:
            record = queue.submit(goal)
        except QueueFull as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
            ) from exc
        return record.to_dict()

    @app.get("/tasks")
    async def list_tasks() -> dict[str, Any]:
        return {"tasks": [record.to_dict() for record in queue.list()]}

    @app.get("/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        record: TaskRecord | None = queue.get(task_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"no task {task_id}"
            )
        return record.to_dict()

    @app.websocket("/events")
    async def stream_events(
        websocket: WebSocket,
        replay: int = Query(0, ge=0, description="replay the last N events before going live"),
    ) -> None:
        # Subscribing before the handshake completes closes the window in which an event could
        # be emitted after the client is committed but before it is registered.
        subscriber = hub.subscribe(replay=replay)
        try:
            await websocket.accept()
            await _serve_client(websocket, subscriber)
        finally:
            hub.unsubscribe(subscriber)


async def _serve_client(websocket: WebSocket, subscriber: Subscriber) -> None:
    """Run the outbound pump and the disconnect watcher until either finishes.

    Both are needed. The pump alone would only notice a vanished client on the next send, which
    between runs can be a long time; the watcher notices immediately. Neither is allowed to
    escape as an exception: a client dying mid-broadcast is routine, not a server fault.
    """
    pump = asyncio.create_task(_pump_events(websocket, subscriber), name="ws-pump")
    watch = asyncio.create_task(_watch_for_disconnect(websocket), name="ws-watch")

    done, pending = await asyncio.wait({pump, watch}, return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    for task in done:
        # Retrieve any exception so asyncio does not report it as never-retrieved.
        exception = task.exception()
        if exception is not None:
            logger.debug("websocket %s ended: %r", task.get_name(), exception)


async def _pump_events(websocket: WebSocket, subscriber: Subscriber) -> None:
    while True:
        text = await subscriber.queue.get()
        if subscriber.overflowed:
            logger.warning("closing a client that fell behind and missed events")
            with contextlib.suppress(Exception):
                await websocket.close(
                    code=WS_INTERNAL_ERROR, reason="event backlog overflowed"
                )
            return
        await websocket.send_text(text)


async def _watch_for_disconnect(websocket: WebSocket) -> None:
    """Drain inbound frames so a disconnect is noticed promptly.

    Inbound frames are ignored by design: `/events` is an outbound stream of ticket 3 events and
    goals are submitted over HTTP, so there is no client-to-server protocol to speak here.
    """
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        logger.debug("ignoring inbound frame on /events: %s", message["type"])


app = create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.server",
        description="Run the persistent orchestrator with a WebSocket event stream.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind address (default: %(default)s)")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="bind port (default: %(default)s)"
    )
    parser.add_argument(
        "--log-level", default="info", help="uvicorn log level (default: %(default)s)"
    )
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
