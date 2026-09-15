# Design — orchestrator-single-worker-loop

## Shape

```
orchestrator/run.py     CLI: argv -> goal string -> printed envelope + exit code
orchestrator/graph.py   LangGraph StateGraph: START -> dispatch -> research-specialist -> finalize -> END
workers/contracts.py    WorkerResult {status, result, error} + parser for untrusted payloads
workers/model.py        pluggable backends: offline (default), anthropic, openai
workers/research_specialist/worker.py   run(goal) -> WorkerResult, never raises
prompts/research-specialist/v1.md       system prompt
```

`dispatch` exists as its own node even though it only ever returns one worker name. It is the
seam where routing lands in the multi-worker ticket, and having it now means that ticket edits one
node instead of restructuring the graph.

`finalize` is the synthesis seam (structure.mdc gives the orchestrator "result synthesis"). With
one worker there is nothing to synthesize, so it only asserts that an envelope exists.

## Deviation from tech.mdc: `StateGraph`, not `create_supervisor`

tech.mdc specifies LangGraph `create_supervisor` unless a ticket's design.md says otherwise. This
ticket says otherwise, for three reasons:

1. `create_supervisor` is a *routing* construct: an LLM picks which worker gets the task. This
   ticket has exactly one worker and lists multi-worker routing as out of scope, so the supervisor
   would spend a model call choosing from a set of one.
2. It requires a bound chat model to construct. That would make the loop unrunnable without
   credentials, which on this machine means unrunnable at all — and the base loop is precisely
   what needs to be testable independent of model access.
3. It pulls in `langgraph-supervisor`, a dependency with no purpose until routing exists.

This is still LangGraph and still the orchestrator-worker pattern; only the prebuilt supervisor is
deferred. The multi-worker ticket should revisit it and either adopt `create_supervisor` at the
`dispatch` node or amend tech.mdc.

## Deviation from tech.mdc: no WebSocket events

tech.mdc specifies structured lifecycle events over WebSockets. This ticket emits none — the
event bus and the dashboard are out of scope here. The node boundaries (`dispatch`, the worker
node, `finalize`) are where `spawned` / `completed` / `error` events will be emitted.

## The never-free-text guarantee

Three layers, because the requirement is unconditional:

1. `run()` catches goal-validation failures, prompt-read failures, backend-construction failures,
   backend call failures, and parse failures individually, each mapped to a specific `error` string.
2. A bare `except Exception` wraps the whole body as a backstop.
3. `run_goal()` and `main()` each wrap their callee, so a fault in the graph machinery or in
   argparse still exits through an envelope.

Validation lives in `WorkerResult` rather than at call sites, so `status: "ok"` with an empty
`result` is unconstructible. `parse_payload` rejects unknown keys: silently dropping a field a
model invented hides prompt drift, and product.mdc asks for debuggability over polish.

Goal rejection is deterministic and runs before any model call, so bad input costs nothing and
behaves identically across backends. It only catches *degenerate* goals — empty, punctuation-only,
or made entirely of filler words. Genuine semantic ambiguity needs judgment, so the prompt
instructs the model to return `status: "error"` for it; that path is unverified until a hosted
model is wired up.

## Model backends

`AGENTDASH_MODEL` selects the backend: `offline` (default), `anthropic[:model]`, `openai[:model]`.

`offline` is a deterministic stub that returns a contract-shaped payload. It states in its own
`result` that it is not real research — a stub that reads like findings would eventually be
mistaken for them. It exists so the loop and contract stay testable without credentials, not as a
model tier; tech.mdc's hosted-model requirement stands for real runs.

Provider packages are imported lazily and are optional extras. A missing package or a missing API
key raises `ModelUnavailable`, which becomes a normal error envelope rather than an import crash.

## Naming

Python packages use `research_specialist`; prompts and worker identity use `research-specialist`,
as structure.mdc writes it. Module paths cannot contain hyphens, so the underscore form is
confined to the import path and `WORKER_NAME` carries the hyphenated identity everywhere else —
graph node name, prompt directory, and log output.
