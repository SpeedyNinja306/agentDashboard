"""The result envelope every worker returns.

A worker's public entry point returns a `WorkerResult` and never raises and never emits free
text. Callers can rely on `status` alone to branch.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

Status = Literal["ok", "error"]

#: Keys a worker payload may contain. Anything else is drift and is rejected loudly.
ALLOWED_KEYS = frozenset({"status", "result", "error"})


class WorkerResult(BaseModel):
    """`{status, result, error}` with the success/failure invariants enforced."""

    model_config = ConfigDict(extra="forbid")

    status: Status
    result: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _enforce_invariants(self) -> WorkerResult:
        if self.status == "ok":
            if not (self.result or "").strip():
                raise ValueError("status 'ok' requires a non-empty result")
            if (self.error or "").strip():
                raise ValueError("status 'ok' requires error to be null")
        else:
            if not (self.error or "").strip():
                raise ValueError("status 'error' requires a non-empty error")
            if (self.result or "").strip():
                raise ValueError("status 'error' requires result to be null")
        return self

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.model_dump(), indent=indent, ensure_ascii=False)


def ok(result: str) -> WorkerResult:
    return WorkerResult(status="ok", result=result.strip(), error=None)


def failure(error: str) -> WorkerResult:
    return WorkerResult(status="error", result=None, error=error.strip())


def _blank_to_none(value: Any) -> Any:
    """Treat `""` as absent so a model emitting `"error": ""` alongside `ok` still validates."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


def parse_payload(payload: Any) -> WorkerResult:
    """Coerce an untrusted payload (typically model output) into a `WorkerResult`.

    Raises `ValueError` with a debuggable message rather than letting pydantic internals or a
    `TypeError` escape; the worker converts that into an error envelope.
    """
    if isinstance(payload, WorkerResult):
        return payload

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"worker output was not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"worker output was {type(payload).__name__}, expected a JSON object")

    unexpected = sorted(set(payload) - ALLOWED_KEYS)
    if unexpected:
        raise ValueError(f"worker output had unexpected key(s): {', '.join(unexpected)}")

    normalized = {key: _blank_to_none(payload.get(key)) for key in ALLOWED_KEYS}

    try:
        return WorkerResult(**normalized)
    except ValidationError as exc:
        raise ValueError(f"worker output violated the result contract: {_terse(exc)}") from exc


def _terse(exc: ValidationError) -> str:
    """Flatten a pydantic error into one line; multi-line tracebacks are noise in a CLI."""
    parts = []
    for err in exc.errors():
        location = ".".join(str(item) for item in err["loc"]) or "<root>"
        parts.append(f"{location}: {err['msg']}")
    return "; ".join(parts)
