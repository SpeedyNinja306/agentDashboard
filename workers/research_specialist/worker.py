"""The research-specialist worker.

`run()` is the only public entry point. It always returns a `WorkerResult` and never raises and
never returns free text, so the orchestrator can branch on `status` without defensive handling.
"""

from __future__ import annotations

from pathlib import Path

from workers.contracts import WorkerResult, failure, parse_payload
from workers.model import ModelBackend, ModelCallFailed, ModelUnavailable, resolve_backend

WORKER_NAME = "research-specialist"
PROMPT_VERSION = "v1"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPT_PATH = _REPO_ROOT / "prompts" / WORKER_NAME / f"{PROMPT_VERSION}.md"

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


def run(goal: str, *, backend: ModelBackend | None = None) -> WorkerResult:
    """Research `goal` and return the result envelope."""
    try:
        rejection = _reject_goal(goal)
        if rejection is not None:
            return rejection

        try:
            system_prompt = _load_prompt()
        except OSError as exc:
            return failure(f"could not read prompt {_PROMPT_PATH.name} for {WORKER_NAME}: {exc}")

        try:
            active_backend = backend if backend is not None else resolve_backend()
        except ModelUnavailable as exc:
            return failure(f"no model backend available: {exc}")

        try:
            raw = active_backend.complete(system=system_prompt, user=goal.strip())
        except (ModelCallFailed, ModelUnavailable) as exc:
            return failure(str(exc))
        except Exception as exc:
            return failure(f"model backend raised {type(exc).__name__}: {exc}")

        if not isinstance(raw, str):
            return failure(
                f"model backend '{getattr(active_backend, 'name', '?')}' returned "
                f"{type(raw).__name__}, expected str"
            )

        try:
            return parse_payload(_strip_code_fence(raw))
        except ValueError as exc:
            return failure(str(exc))

    except Exception as exc:
        # Last line of defence: the contract is that this function never raises.
        return failure(f"unhandled {type(exc).__name__} in {WORKER_NAME}: {exc}")


def _reject_goal(goal: object) -> WorkerResult | None:
    """Return an error envelope for goals not worth a model call, else `None`."""
    if not isinstance(goal, str):
        return failure(f"goal must be a string, got {type(goal).__name__}")

    stripped = goal.strip()
    if not stripped:
        return failure("goal is empty; nothing to research")

    if len(stripped) > MAX_GOAL_CHARS:
        return failure(
            f"goal is {len(stripped)} characters, over the {MAX_GOAL_CHARS} limit; "
            "split it into smaller goals"
        )

    words = [word for word in _words(stripped) if word]
    if not words:
        return failure("goal contains no words; nothing to research")

    if all(word in _FILLER_WORDS for word in words):
        return failure(
            f"goal '{stripped}' names no researchable subject; "
            "state what to investigate and about what"
        )

    return None


def _words(text: str) -> list[str]:
    return ["".join(ch for ch in token if ch.isalnum()).lower() for token in text.split()]


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _strip_code_fence(raw: str) -> str:
    """Tolerate a ```json fence around the payload; some models add one despite instructions."""
    text = raw.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) < 2:
        return text

    body = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
    return "\n".join(body).strip()
