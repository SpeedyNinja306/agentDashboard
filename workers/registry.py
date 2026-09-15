"""The registry of worker types the orchestrator can dispatch to.

This is an *explicit* mapping, populated at import time from the known worker packages. It is not
dynamic discovery: adding a worker means adding a line here, on purpose. Routing (`orchestrator.
router`) and the server's worker endpoints read the set of known workers from here so there is one
place that answers "which workers exist", and a worker that is not registered simply cannot be
addressed rather than half-existing.

Each worker package exposes the same surface — `WORKER_NAME` and `run(goal, *, backend,
prompt_version)` — which is what lets a single registry entry stand in for any of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from workers import coding_agent, research_specialist
from workers.contracts import WorkerResult

#: `run(goal, *, backend, prompt_version) -> WorkerResult`, never raises, never returns free text.
WorkerRunner = Callable[..., WorkerResult]


@dataclass(frozen=True)
class WorkerEntry:
    name: str
    run: WorkerRunner


#: Insertion order is the tie-break order the router uses, so it is meaningful. The default worker
#: (an unrouted goal's destination) is named separately rather than "whatever is first".
_ENTRIES: tuple[WorkerEntry, ...] = (
    WorkerEntry(research_specialist.WORKER_NAME, research_specialist.run),
    WorkerEntry(coding_agent.WORKER_NAME, coding_agent.run),
)

_BY_NAME: dict[str, WorkerEntry] = {entry.name: entry for entry in _ENTRIES}

#: An unrouted goal goes here. research-specialist because the system began as a research tool and
#: a bare question is the more common unrouted input; an explicit selection always overrides this.
DEFAULT_WORKER = research_specialist.WORKER_NAME


def worker_names() -> tuple[str, ...]:
    """Every registered worker name, in registration order."""
    return tuple(_BY_NAME)


def is_known(name: str) -> bool:
    return name in _BY_NAME


def get_worker(name: str) -> WorkerEntry | None:
    return _BY_NAME.get(name)


def run_worker(name: str, goal: str) -> WorkerResult:
    """Dispatch `goal` to the named worker. Raises `KeyError` for an unknown worker.

    The caller (the graph's worker node) has already resolved the name via the router, so an
    unknown name here is a programming error, not a user-facing one — hence a raise rather than an
    error envelope.
    """
    entry = _BY_NAME[name]
    return entry.run(goal)
