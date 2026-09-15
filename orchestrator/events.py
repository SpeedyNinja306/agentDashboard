"""Structured lifecycle event emission for the orchestrator/worker loop.

Each event conforms to:

    {event_type, agent_id, agent_name, timestamp, payload}

The event_type values mirror the lifecycle states from tech.mdc:
    dispatch_start / dispatch_end   – orchestrator routing a goal
    worker_spawned                  – worker invocation begins
    tool_call_start / tool_call_end – model-backend call inside a worker
    completed                       – worker finished with status "ok"
    error                           – worker or orchestrator path returned status "error"

Where an event goes is decided by the installed *sink*. The default sink writes compact JSON
lines to stdout, which is what the `orchestrator.run` CLI wants: events interleave naturally
with the final result envelope. `orchestrator.server` swaps in a sink that hands the same dict
to the WebSocket hub. The event itself is identical either way — the transport is the only
thing that varies, so a stdout capture stays a valid oracle for what clients receive.

Context is threaded through Python's contextvars so nodes and workers can emit without
receiving IDs as arguments. All context vars default to empty string so callers that
do not initialise a run still get valid (if ID-less) events rather than exceptions.

This module has no imports from the rest of the project; any layer may import it safely.
"""

from __future__ import annotations

import contextvars
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Per-run context
# ---------------------------------------------------------------------------

_orchestrator_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "orchestrator_id", default=""
)
_worker_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "worker_id", default=""
)

ORCHESTRATOR_NAME = "orchestrator"


def start_run(run_id: str | None = None) -> str:
    """Assign fresh IDs for an orchestrator run; return the orchestrator agent_id.

    `run_id` lets a caller that already has an identity for the run — the server's task queue
    uses its task_id — reuse it as the orchestrator agent_id. Correlating a task with its events
    then needs nothing added to the event itself.
    """
    oid = run_id or str(uuid.uuid4())
    _orchestrator_id.set(oid)
    _worker_id.set("")
    return oid


def spawn_worker() -> str:
    """Assign a new agent_id for the current worker invocation; return it."""
    wid = str(uuid.uuid4())
    _worker_id.set(wid)
    return wid


def orchestrator_id() -> str:
    return _orchestrator_id.get()


def worker_id() -> str:
    return _worker_id.get()


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------

#: A sink receives the finished event dict. It must not mutate it and must not raise.
EventSink = Callable[[dict[str, Any]], None]


def stdout_sink(event: dict[str, Any]) -> None:
    """Write one event as a compact JSON line to stdout, then flush."""
    print(json.dumps(event), file=sys.stdout, flush=True)


_sink: EventSink = stdout_sink


def set_sink(sink: EventSink) -> EventSink:
    """Install the process-wide event sink; return the one it replaced.

    Process-wide rather than a contextvar because emission happens deep inside synchronous
    worker code running on a thread the server does not own, and every run in a server process
    goes to the same place regardless.
    """
    global _sink
    previous = _sink
    _sink = sink
    return previous


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------

def emit(
    event_type: str,
    agent_id: str,
    agent_name: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Build one lifecycle event and hand it to the installed sink."""
    event: dict[str, Any] = {
        "event_type": event_type,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload or {},
    }
    try:
        _sink(event)
    except Exception as exc:
        # Observing a run must never be able to fail it. stderr keeps this off stdout, where
        # the CLI's result envelope lives.
        print(
            f"event sink failed on {event_type}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
