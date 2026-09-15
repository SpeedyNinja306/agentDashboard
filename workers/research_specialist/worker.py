"""The research-specialist worker.

`run()` is the only public entry point. It always returns a `WorkerResult` and never raises and
never returns free text, so the orchestrator can branch on `status` without defensive handling.
The execution path itself lives in `workers.runner`; what is here is what makes this worker this
worker rather than another one.
"""

from __future__ import annotations

from workers.contracts import WorkerResult
from workers.model import ModelBackend
from workers.runner import WorkerSpec, run_worker

WORKER_NAME = "research-specialist"
PROMPT_VERSION = "v1"

MAX_GOAL_CHARS = 8_000

# Words that carry no researchable subject on their own. A goal made only of these is degenerate
# rather than merely broad, and is rejected without spending a model call. Genuine semantic
# ambiguity is left to the model, which the prompt instructs to return status "error".
_FILLER_WORDS = frozenset(
    {
        "a", "an", "and", "any", "anything", "asap", "do", "for", "get", "go", "help", "i",
        "info", "information", "it", "me", "my", "need", "now", "of", "on", "please", "pls",
        "research", "some", "something", "stuff", "thanks", "that", "the", "thing", "things",
        "this", "to", "want", "whatever", "with", "work",
    }
)

SPEC = WorkerSpec(
    name=WORKER_NAME,
    prompt_version=PROMPT_VERSION,
    max_goal_chars=MAX_GOAL_CHARS,
    filler_words=_FILLER_WORDS,
    empty_goal_error="goal is empty; nothing to research",
    no_words_error="goal contains no words; nothing to research",
    filler_goal_error=(
        "goal '{goal}' names no researchable subject; "
        "state what to investigate and about what"
    ),
    oversize_goal_error=(
        "goal is {length} characters, over the {limit} limit; split it into smaller goals"
    ),
)


def run(
    goal: str,
    *,
    backend: ModelBackend | None = None,
    prompt_version: str | None = None,
) -> WorkerResult:
    """Research `goal` and return the result envelope."""
    return run_worker(SPEC, goal, backend=backend, prompt_version=prompt_version)
