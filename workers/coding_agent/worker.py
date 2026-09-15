"""The coding-agent worker.

`run()` is the only public entry point. It always returns a `WorkerResult` and never raises and
never returns free text — the same guarantee research-specialist makes, on the same envelope, via
the same `workers.runner` path. See `specs/second-worker-generalization/design.md` for why the
`{status, result, error}` contract was kept unchanged for a worker that returns code.
"""

from __future__ import annotations

from workers.contracts import WorkerResult
from workers.model import ModelBackend
from workers.runner import WorkerSpec, run_worker

WORKER_NAME = "coding-agent"
PROMPT_VERSION = "v1"

#: Twice research-specialist's limit. A coding goal legitimately carries the code it is about,
#: so the length that signals "this is too vague to be one task" sits higher here.
MAX_GOAL_CHARS = 16_000

# Words that name no code, file, or behaviour on their own. A goal made only of these is
# degenerate rather than merely underspecified, and is rejected without spending a model call.
# The generic coding verbs are here deliberately: "fix the bug" is every bit as empty as "do the
# thing". A goal that names anything concrete — a file, a symbol, a symptom, a language — keeps
# at least one non-filler word and goes to the model, which the prompt instructs to return
# status "error" when it still cannot act.
_FILLER_WORDS = frozenset(
    {
        "a", "add", "an", "and", "any", "anything", "app", "asap", "bug", "build", "change",
        "clean", "code", "do", "fix", "for", "get", "go", "help", "i", "implement", "improve",
        "issue", "it", "make", "me", "my", "need", "now", "of", "on", "please", "pls", "program",
        "refactor", "script", "some", "something", "stuff", "task", "thanks", "that", "the",
        "thing", "things", "this", "ticket", "to", "up", "update", "want", "whatever", "with",
        "work", "write",
    }
)

SPEC = WorkerSpec(
    name=WORKER_NAME,
    prompt_version=PROMPT_VERSION,
    max_goal_chars=MAX_GOAL_CHARS,
    filler_words=_FILLER_WORDS,
    empty_goal_error="goal is empty; nothing to build",
    no_words_error="goal contains no words; nothing to build",
    filler_goal_error=(
        "goal '{goal}' names no code, file, or behaviour to change; "
        "state what to build and where"
    ),
    oversize_goal_error=(
        "goal is {length} characters, over the {limit} limit; "
        "narrow it to the code that needs to change"
    ),
)


def run(
    goal: str,
    *,
    backend: ModelBackend | None = None,
    prompt_version: str | None = None,
) -> WorkerResult:
    """Write or change code for `goal` and return the result envelope."""
    return run_worker(SPEC, goal, backend=backend, prompt_version=prompt_version)
