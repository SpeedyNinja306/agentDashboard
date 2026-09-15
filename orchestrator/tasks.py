"""In-memory task queue for the persistent orchestrator process.

Ticket 2's orchestrator was one goal per process: argv in, envelope out, exit. The server keeps
the process alive, so goals arrive over time and need somewhere to wait. This is that somewhere.

Two decisions shape everything else here.

**The queue belongs to the process, not to a connection.** Submitting a goal and watching the
event stream are separate concerns. Nothing in this module knows whether a client is connected,
so a client disconnecting cannot cancel a running task, drop a queued one, or stop new work from
being accepted. A run started by a client that has since vanished runs to completion and its
events are broadcast to whoever is connected at the time.

**One task at a time, in submission order.** `orchestrator.events` tracks the current run in
contextvars, and a single worker keeps that unambiguous: the event stream is one run's events
followed by the next run's, never two runs interleaved. Concurrency belongs in a later ticket
that gives events a run-scoped identity beyond the orchestrator agent_id.

`run_goal` is blocking — LangGraph's `invoke` plus, for hosted backends, a network call — so it
runs on a worker thread. That is what keeps the event loop free to accept WebSocket connections
while a run is in flight.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from workers.contracts import WorkerResult

logger = logging.getLogger(__name__)

TaskStatus = Literal["queued", "running", "done", "error"]

#: Goals accepted but not yet started. A full queue is back-pressure, reported to the submitter.
DEFAULT_MAX_PENDING = 256

#: Finished tasks retained for inspection. Oldest completed records are evicted past this.
DEFAULT_HISTORY = 512

#: `run_goal`-shaped callable: (goal, *, run_id) -> WorkerResult, blocking.
GoalRunner = Callable[..., WorkerResult]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueueFull(RuntimeError):
    """Too many goals are already waiting; the submitter should retry later."""


@dataclass
class TaskRecord:
    """The server's view of one submitted goal.

    `task_id` is also the orchestrator's agent_id for the run, so a client can match this record
    to its events without the event schema carrying a task field.
    """

    task_id: str
    goal: str
    status: TaskStatus = "queued"
    submitted_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
        }


class TaskQueue:
    """FIFO goal queue with one sequential consumer."""

    def __init__(
        self,
        runner: GoalRunner,
        *,
        max_pending: int = DEFAULT_MAX_PENDING,
        history: int = DEFAULT_HISTORY,
    ) -> None:
        self._runner = runner
        self._history = history
        self._pending: asyncio.Queue[str] = asyncio.Queue(maxsize=max_pending)
        self._records: OrderedDict[str, TaskRecord] = OrderedDict()
        self._consumer: asyncio.Task[None] | None = None
        self._running_id: str | None = None

    # -- lifecycle ------------------------------------------------------------------------

    def start(self) -> None:
        if self._consumer is None or self._consumer.done():
            self._consumer = asyncio.create_task(self._consume(), name="task-queue-consumer")

    async def stop(self) -> None:
        """Stop accepting work and wind down the consumer.

        A run already on its worker thread cannot be cancelled — threads are not interruptible —
        so this waits for the awaiting coroutine to unwind and lets the thread finish on its own.
        """
        consumer = self._consumer
        self._consumer = None
        if consumer is None:
            return
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

    # -- producing ------------------------------------------------------------------------

    def submit(self, goal: str) -> TaskRecord:
        """Queue `goal` and return its record. Raises `QueueFull` if there is no room."""
        record = TaskRecord(task_id=str(uuid.uuid4()), goal=goal)
        try:
            self._pending.put_nowait(record.task_id)
        except asyncio.QueueFull as exc:
            raise QueueFull(
                f"{self._pending.maxsize} goals are already queued; retry once some drain"
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

    async def _consume(self) -> None:
        while True:
            task_id = await self._pending.get()
            record = self._records.get(task_id)
            if record is None:
                self._pending.task_done()
                continue
            try:
                await self._run_one(record)
            finally:
                self._pending.task_done()

    async def _run_one(self, record: TaskRecord) -> None:
        record.status = "running"
        record.started_at = _now()
        self._running_id = record.task_id
        try:
            result = await asyncio.to_thread(self._runner, record.goal, run_id=record.task_id)
        except asyncio.CancelledError:
            # Shutdown while this run was in flight. The record stays "running": claiming it
            # finished would be a lie, and the thread may well still be going.
            raise
        except Exception as exc:
            # run_goal is contracted never to raise; if that contract breaks, the queue keeps
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
            self._running_id = None

    # -- introspection --------------------------------------------------------------------

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def list(self) -> list[TaskRecord]:
        return list(self._records.values())

    @property
    def pending_count(self) -> int:
        return self._pending.qsize()

    @property
    def running_id(self) -> str | None:
        return self._running_id
