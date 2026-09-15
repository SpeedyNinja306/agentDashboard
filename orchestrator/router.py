"""Worker routing for the orchestrator's dispatch node.

Two ways a goal gets a worker, in priority order:

1. **Explicit selection.** The dashboard clicking a worker node, or `POST /workers/{name}/tasks`,
   names the worker outright. That choice is honoured verbatim — the whole point of persistent,
   addressable workers is that the caller can pick one.
2. **Keyword routing.** A goal submitted to the generic `POST /tasks` with no worker named is
   routed here by matching keywords against the goal text, defaulting to `registry.DEFAULT_WORKER`.

This is deliberately a fixed keyword map over the *known* workers. It is not dynamic worker
discovery, and it is not an LLM supervisor. tech.mdc specifies LangGraph `create_supervisor`;
`specs/second-worker-generalization/design.md` records why this ticket routes deterministically
instead (an LLM router needs a bound model and so is unrunnable without credentials, exactly the
constraint that keeps the offline path testable) and what would move it to a supervisor later.

A keyword router is imperfect by construction: a goal that mixes vocabularies can match the wrong
worker. That is acceptable precisely because the explicit path exists — a misroute is one click to
correct — and because a wrong choice still returns a valid envelope from a real worker, never a
crash.
"""

from __future__ import annotations

from workers import registry

# Matched as substrings against the lowercased goal, so keep them discriminating: a fragment like
# "api" would match "apiece" and mis-route, so multi-word or bounded phrases are preferred over
# short ambiguous stems. Only workers that need to be *pulled* off the default appear here.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "coding-agent": (
        "code", "coding", "function", "refactor", "debug", "bug in", "stack trace",
        "traceback", "exception", "compile", "implement", "unit test", "regex", "snippet",
        "class ", "def ", "sql", "query", "endpoint", "algorithm", "data structure",
        "python", "typescript", "javascript", "rust", "golang", " java ", "c++", "css",
        "html", "script to", "write a program", "fix the code", "failing test",
    ),
}


def route(goal: str) -> str:
    """Pick a worker for an unrouted goal by keyword. Falls back to `registry.DEFAULT_WORKER`."""
    text = f" {(goal or '').lower()} "
    for name in registry.worker_names():
        keywords = _KEYWORDS.get(name)
        if keywords and any(keyword in text for keyword in keywords):
            return name
    return registry.DEFAULT_WORKER


def select_worker(goal: str, *, requested: str | None = None) -> str:
    """Resolve the worker for `goal`. Explicit `requested` wins when it names a known worker.

    An unknown `requested` is treated as no request and falls to the router rather than raising:
    the server validates worker names at its edge, so anything reaching here is best served by a
    valid worker and a valid envelope over a hard failure.
    """
    if requested and registry.is_known(requested):
        return requested
    return route(goal)
