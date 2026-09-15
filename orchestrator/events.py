"""Structured lifecycle event emission for the orchestrator/worker loop.

Events are written as compact JSON lines to stdout so they interleave naturally with the final
result envelope. Each event conforms to:

    {event_type, agent_id, agent_name, timestamp, payload}

The event_type values mirror the lifecycle states from tech.mdc:
    dispatch_start / dispatch_end   – orchestrator routing a goal
    worker_spawned                  – worker invocation begins
    tool_call_start / tool_call_end – model-backend call inside a worker
    completed                       – worker finished with status "ok"
    error                           – worker or orchestrator path returned status "error"

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
from typing import Any

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


def start_run() -> str:
    """Assign fresh IDs for an orchestrator run; return the orchestrator agent_id."""
    oid = str(uuid.uuid4())
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
# Emission
# ---------------------------------------------------------------------------

def emit(
    event_type: str,
    agent_id: str,
    agent_name: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Write one event as a compact JSON line to stdout, then flush."""
    event: dict[str, Any] = {
        "event_type": event_type,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload or {},
    }
    print(json.dumps(event), file=sys.stdout, flush=True)
