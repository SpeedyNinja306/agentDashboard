"""The execution path every worker shares.

A worker is a `WorkerSpec` plus a prompt file. `run_worker` does the rest: reject degenerate
goals before spending a model call, load the pinned prompt version, resolve the backend, emit the
tool-call events, and coerce whatever the model returned into a `WorkerResult`.

This exists because the second worker would otherwise have been a copy of the first. Duplicating
the path would mean two places where "never raises, never returns free text" has to stay true, and
a contract guarantee that holds in one copy and not the other is worse than no guarantee. The
per-worker differences are exactly the fields on `WorkerSpec`; anything a worker needs beyond
those is a signal that the difference belongs in its prompt, not in its runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from orchestrator import events as _events
from workers.contracts import WorkerResult, failure, parse_payload
from workers.model import ModelBackend, ModelCallFailed, ModelUnavailable, resolve_backend

_REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_ROOT = _REPO_ROOT / "prompts"

#: A version is `vN`. Constraining the shape keeps a caller-supplied version from escaping the
#: prompt directory.
_VERSION_RE = re.compile(r"^v\d+$")


@dataclass(frozen=True)
class WorkerSpec:
    """Everything that distinguishes one worker from another at runtime.

    The error strings are templates rather than a shared generic message because they are what a
    caller sees when a goal is rejected, and "nothing to research" and "nothing to build" send a
    human to different fixes. They are also asserted verbatim by each worker's golden set.
    """

    name: str
    prompt_version: str
    max_goal_chars: int
    filler_words: frozenset[str]
    empty_goal_error: str
    no_words_error: str
    #: Formatted with `goal`.
    filler_goal_error: str
    #: Formatted with `length` and `limit`.
    oversize_goal_error: str

    @property
    def prompt_dir(self) -> Path:
        return PROMPTS_ROOT / self.name

    def prompt_path(self, version: str | None = None) -> Path:
        return self.prompt_dir / f"{version or self.prompt_version}.md"


def run_worker(
    spec: WorkerSpec,
    goal: str,
    *,
    backend: ModelBackend | None = None,
    prompt_version: str | None = None,
) -> WorkerResult:
    """Run `goal` through `spec`'s worker and return the result envelope.

    Never raises and never returns free text, so the orchestrator can branch on `status` without
    defensive handling. `prompt_version` picks which `prompts/<worker>/vN.md` to load; it exists
    so the eval harness can score a candidate prompt before it becomes the shipped default.
    """
    try:
        rejection = _reject_goal(spec, goal)
        if rejection is not None:
            return rejection

        version = prompt_version or spec.prompt_version
        if not _VERSION_RE.match(version):
            return failure(f"prompt_version '{version}' is not of the form vN")

        prompt_path = spec.prompt_path(version)
        try:
            system_prompt = prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            return failure(f"could not read prompt {prompt_path.name} for {spec.name}: {exc}")

        try:
            active_backend = backend if backend is not None else resolve_backend()
        except ModelUnavailable as exc:
            return failure(f"no model backend available: {exc}")

        backend_name = getattr(active_backend, "name", "?")
        wid = _events.worker_id()
        _events.emit(
            "tool_call_start",
            wid,
            spec.name,
            {
                "backend": backend_name,
                "prompt_version": version,
                "goal_preview": goal.strip()[:80],
            },
        )

        try:
            raw = active_backend.complete(system=system_prompt, user=goal.strip())
        except (ModelCallFailed, ModelUnavailable) as exc:
            _events.emit(
                "tool_call_end",
                wid,
                spec.name,
                {"backend": backend_name, "status": "error", "error": str(exc)},
            )
            return failure(str(exc))
        except Exception as exc:
            _events.emit(
                "tool_call_end",
                wid,
                spec.name,
                {"backend": backend_name, "status": "error", "error": str(exc)},
            )
            return failure(f"model backend raised {type(exc).__name__}: {exc}")

        _events.emit(
            "tool_call_end", wid, spec.name, {"backend": backend_name, "status": "ok"}
        )

        if not isinstance(raw, str):
            return failure(
                f"model backend '{backend_name}' returned {type(raw).__name__}, expected str"
            )

        try:
            return parse_payload(_strip_code_fence(raw))
        except ValueError as exc:
            return failure(str(exc))

    except Exception as exc:
        # Last line of defence: the contract is that this function never raises.
        return failure(f"unhandled {type(exc).__name__} in {spec.name}: {exc}")


def _reject_goal(spec: WorkerSpec, goal: object) -> WorkerResult | None:
    """Return an error envelope for goals not worth a model call, else `None`.

    This catches *degenerate* goals only — empty, punctuation-only, or built entirely from filler.
    Genuine semantic ambiguity needs judgment and is left to the prompt.
    """
    if not isinstance(goal, str):
        return failure(f"goal must be a string, got {type(goal).__name__}")

    stripped = goal.strip()
    if not stripped:
        return failure(spec.empty_goal_error)

    if len(stripped) > spec.max_goal_chars:
        return failure(
            spec.oversize_goal_error.format(length=len(stripped), limit=spec.max_goal_chars)
        )

    words = [word for word in _words(stripped) if word]
    if not words:
        return failure(spec.no_words_error)

    if all(word in spec.filler_words for word in words):
        return failure(spec.filler_goal_error.format(goal=stripped))

    return None


def _words(text: str) -> list[str]:
    return ["".join(ch for ch in token if ch.isalnum()).lower() for token in text.split()]


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
