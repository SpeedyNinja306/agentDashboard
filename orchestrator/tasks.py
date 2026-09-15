"""In-memory task queues for the persistent orchestrator process.

Ticket 2's orchestrator was one goal per process. The server keeps the process alive, so goals
arrive over time and need somewhere to wait. This is that somewhere.

Three decisions shape everything else here.

**The queue belongs to the process, not to a connection.** Submitting a goal and watching the
event stream are separate concerns. Nothing in this module knows whether a client is connected,
so a client disconnecting cannot cancel a running task, drop a queued one, or stop new work from
being accepted. A run started by a client that has since vanished runs to completion and its
events are broadcast to whoever is connected at the time.

**One lane per worker; one task at a time within a lane; lanes run concurrently.** The websocket
ticket ran a single global consumer because there was a single worker, and it wanted the event
stream to be one run's events after another's rather than interleaved. With more than one worker
that constraint becomes a bottleneck — a slow research run would block an unrelated coding run —
and it is no longer necessary: every event already carries `agent_name`, and the orchestrator's
`agent_id` is the run's `task_id`, so a consumer can attribute any event to its run and worker
even when two runs interleave. Each worker therefore gets its own FIFO lane and its own consumer,
so two *different* workers run at the same time while any *one* worker still processes its goals in
order. Per-run event isolation across those concurrent runs rides on `contextvars`: `run_goal`
executes on a worker thread via `asyncio.to_thread`, which copies the context per call, so one
run's orchestrator/worker ids cannot leak into another's.

`run_goal` is blocking — LangGraph's `invoke` plus, for hosted backends, a network call — so it
runs on a worker thread. That is what keeps the event loop free to accept WebSocket connections
while runs are in flight, and what lets lanes overlap.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from workers.contracts import WorkerResult

logger = logging.getLogger(__name__)

TaskStatus = Literal["queued", "running", "done", "error"]

#: Goals accepted per lane but not yet started. A full lane is back-pressure, reported to the
#: submitter for that worker only.
DEFAULT_MAX_PENDING = 256

#: Finished tasks retained for inspection. Oldest completed records are evicted past this.
DEFAULT_HISTORY = 512

#: `run_goal`-shaped callable: (goal, *, run_id, worker) -> WorkerResult, blocking.
GoalRunner = Callable[..., WorkerResult]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueueFull(RuntimeError):
    """Too many goals are already waiting for this worker; the submitter should retry later."""


class UnknownWorker(ValueError):
    """A goal was submitted for a worker that has no lane."""


@dataclass
class TaskRecord:
    """The server's view of one submitted goal.

    `task_id` is also the orchestrator's agent_id for the run, so a client can match this record
    to its events without the event schema carrying a task field. `worker` records which lane ran
    it, so a client can tell two concurrent runs apart by more than their ids.
    """

    task_id: str
    goal: str
    worker: str
    status: TaskStatus = "queued"
    submitted_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "worker": self.worker,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
        }


class TaskQueue:
    """Per-worker FIFO lanes, each with one sequential consumer, running concurrently."""

    def __init__(
        self,
        runner: GoalRunner,
        *,
        workers: Iterable[str],
        max_pending: int = DEFAULT_MAX_PENDING,
        history: int = DEFAULT_HISTORY,
    ) -> None:
        self._runner = runner
        self._history = history
        self._lanes: dict[str, asyncio.Queue[str]] = {
            name: asyncio.Queue(maxsize=max_pending) for name in workers
        }
        if not self._lanes:
            raise ValueError("TaskQueue needs at least one worker lane")
        self._records: OrderedDict[str, TaskRecord] = OrderedDict()
        self._consumers: list[asyncio.Task[None]] = []
        self._running: dict[str, str | None] = {name: None for name in self._lanes}

    # -- lifecycle ------------------------------------------------------------------------

    def start(self) -> None:
        if self._consumers and not all(c.done() for c in self._consumers):
            return
        self._consumers = [
            asyncio.create_task(self._consume(name), name=f"task-lane-{name}")
            for name in self._lanes
        ]

    async def stop(self) -> None:
        """Stop accepting work and wind down every lane's consumer.

        A run already on its worker thread cannot be cancelled — threads are not interruptible —
        so this waits for the awaiting coroutines to unwind and lets those threads finish.
        """
        consumers = self._consumers
        self._consumers = []
        for consumer in consumers:
            consumer.cancel()
        for consumer in consumers:
            try:
                await consumer
            except asyncio.CancelledError:
                pass

    # -- producing ------------------------------------------------------------------------

    def submit(self, goal: str, worker: str) -> TaskRecord:
        """Queue `goal` on `worker`'s lane and return its record.

        Raises `UnknownWorker` if there is no such lane and `QueueFull` if the lane is saturated.
        """
        lane = self._lanes.get(worker)
        if lane is None:
            raise UnknownWorker(f"no lane for worker {worker!r}; known: {sorted(self._lanes)}")

        record = TaskRecord(task_id=str(uuid.uuid4()), goal=goal, worker=worker)
        try:
            lane.put_nowait(record.task_id)
        except asyncio.QueueFull as exc:
            raise QueueFull(
                f"{lane.maxsize} goals are already queued for {worker}; retry once some drain"
            ) from exc

        self._records[record.task_id] = record
        self._evict_old_records()
        return record

    def _evict_old_records(self) -> None:
        """Trim finished records once history is over budget; never touch live ones."""
        excess = len(self._records) - self._history
        if excess <= 0:
            return
        for task_id in list(self._records)[:excess]:
            if self._records[task_id].status in ("done", "error"):
                del self._records[task_id]

    # -- consuming ------------------------------------------------------------------------

    async def _consume(self, worker: str) -> None:
        lane = self._lanes[worker]
        while True:
            task_id = await lane.get()
            record = self._records.get(task_id)
            if record is None:
                lane.task_done()
                continue
            try:
                await self._run_one(record)
            finally:
                lane.task_done()

    async def _run_one(self, record: TaskRecord) -> None:
        record.status = "running"
        record.started_at = _now()
        self._running[record.worker] = record.task_id
        try:
            result = await asyncio.to_thread(
                self._runner, record.goal, run_id=record.task_id, worker=record.worker
            )
        except asyncio.CancelledError:
            # Shutdown while this run was in flight. The record stays "running": claiming it
            # finished would be a lie, and the thread may well still be going.
            raise
        except Exception as exc:
            # run_goal is contracted never to raise; if that contract breaks, the lane keeps
            # going and the breakage is recorded on the task rather than killing the consumer.
            logger.exception("task %s: runner raised", record.task_id)
            record.status = "error"
            record.result = {
                "status": "error",
                "result": None,
                "error": f"task runner raised {type(exc).__name__}: {exc}",
            }
        else:
            record.result = result.model_dump()
            record.status = "done" if result.status == "ok" else "error"
        finally:
            if record.status != "running":
                record.finished_at = _now()
            self._running[record.worker] = None

    # -- introspection --------------------------------------------------------------------

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def list(self) -> list[TaskRecord]:
        return list(self._records.values())

    @property
    def pending_count(self) -> int:
        return sum(lane.qsize() for lane in self._lanes.values())

    def pending_by_worker(self) -> dict[str, int]:
        return {name: lane.qsize() for name, lane in self._lanes.items()}

    @property
    def running_ids(self) -> dict[str, str | None]:
        """The task id currently running in each lane, or None when that lane is idle."""
        return dict(self._running)
