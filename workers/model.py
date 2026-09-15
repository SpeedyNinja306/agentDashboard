"""Pluggable model backends for workers.

Selected by the `AGENTDASH_MODEL` environment variable:

    offline                      deterministic, no network, no credentials (default)
    anthropic[:<model-name>]     hosted Claude via langchain-anthropic
    openai[:<model-name>]        hosted GPT via langchain-openai

tech.mdc calls for a hosted Claude/GPT-class model for reasoning workers. `offline` exists so the
orchestrator loop and the result contract stay runnable and testable on a machine with no
credentials; it does not do research and says so in its output.
"""

from __future__ import annotations

import json
import os
import textwrap
from typing import Protocol, runtime_checkable

DEFAULT_BACKEND = "offline"
ENV_VAR = "AGENTDASH_MODEL"

_DEFAULT_MODEL_NAMES = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4.1",
}


class ModelUnavailable(RuntimeError):
    """The requested backend cannot be constructed (missing package, missing credentials)."""


class ModelCallFailed(RuntimeError):
    """The backend was constructed but the call itself failed (network, rate limit, refusal)."""


@runtime_checkable
class ModelBackend(Protocol):
    name: str

    def complete(self, *, system: str, user: str) -> str:
        """Return the model's raw text response. Callers parse and validate it."""


class OfflineBackend:
    """Deterministic stand-in. Emits a contract-shaped payload without calling anything.

    The payload is deliberately labelled rather than dressed up as findings — a stub that looks
    like real research is worse than no stub.
    """

    name = "offline"

    def complete(self, *, system: str, user: str) -> str:
        goal = user.strip()
        summary = textwrap.shorten(goal, width=280, placeholder="...")
        result = (
            "[offline backend — not real research] No language model is configured, so this run "
            "exercised the orchestrator loop and the result contract only. "
            f"The goal received and accepted for research was: {summary} "
            "Set AGENTDASH_MODEL=anthropic:<model> or openai:<model> with the matching API key "
            "to get actual findings."
        )
        return json.dumps({"status": "ok", "result": result, "error": None})


class ChatModelBackend:
    """Adapter over a LangChain chat model."""

    def __init__(self, name: str, chat_model: object) -> None:
        self.name = name
        self._chat_model = chat_model

    def complete(self, *, system: str, user: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            response = self._chat_model.invoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
        except Exception as exc:
            raise ModelCallFailed(f"{self.name} call failed: {type(exc).__name__}: {exc}") from exc

        return _extract_text(response)


def _extract_text(response: object) -> str:
    """Pull plain text out of an AIMessage whose content may be a string or content blocks."""
    content = getattr(response, "content", response)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        chunks = []
        for block in content:
            if isinstance(block, str):
                chunks.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                chunks.append(block.get("text", ""))
        return "".join(chunks)

    return str(content)


def resolve_backend(spec: str | None = None) -> ModelBackend:
    """Build the backend named by `spec`, falling back to `AGENTDASH_MODEL`, then `offline`.

    Raises `ModelUnavailable` for an unknown provider, a missing provider package, or a missing
    API key — all of which the worker turns into a `status: "error"` envelope.
    """
    raw = (spec or os.getenv(ENV_VAR) or DEFAULT_BACKEND).strip()
    provider, _, model_name = raw.partition(":")
    provider = provider.lower()
    model_name = model_name.strip() or _DEFAULT_MODEL_NAMES.get(provider, "")

    if provider == "offline":
        return OfflineBackend()

    if provider == "anthropic":
        _require_env("ANTHROPIC_API_KEY", raw)
        chat_model = _build(
            "langchain_anthropic", "ChatAnthropic", raw, model=model_name, temperature=0
        )
        return ChatModelBackend(f"anthropic:{model_name}", chat_model)

    if provider == "openai":
        _require_env("OPENAI_API_KEY", raw)
        chat_model = _build(
            "langchain_openai", "ChatOpenAI", raw, model=model_name, temperature=0
        )
        return ChatModelBackend(f"openai:{model_name}", chat_model)

    known = "offline, anthropic, openai"
    raise ModelUnavailable(f"unknown model backend '{raw}'; {ENV_VAR} must name one of: {known}")


def _require_env(var: str, spec: str) -> None:
    if not os.getenv(var):
        raise ModelUnavailable(f"{spec} requires the {var} environment variable to be set")


def _build(module_name: str, class_name: str, spec: str, **kwargs: object) -> object:
    try:
        module = __import__(module_name, fromlist=[class_name])
    except ImportError as exc:
        package = module_name.replace("_", "-")
        raise ModelUnavailable(f"{spec} requires the {package} package: pip install {package}") from exc

    try:
        return getattr(module, class_name)(**kwargs)
    except Exception as exc:
        raise ModelUnavailable(
            f"could not construct {class_name} for '{spec}': {type(exc).__name__}: {exc}"
        ) from exc
