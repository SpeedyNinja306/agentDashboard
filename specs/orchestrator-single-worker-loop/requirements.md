# Requirements — orchestrator-single-worker-loop

The base loop: one goal in, one worker runs, one structured result out. Everything that makes
this a *multi*-agent system is deliberately out of scope.

## In scope

1. One hardcoded worker, `research-specialist`, with its system prompt at
   `prompts/research-specialist/v1.md`.
2. The worker returns `{status, result, error}` and nothing else. Never free text, on any path.
3. An orchestrator that accepts a goal string, dispatches it to that worker, and prints the result.
4. CLI entrypoint: `python -m orchestrator.run "goal text"`.
5. Every failure — bad input, missing credentials, malformed model output, internal fault —
   surfaces as `{"status": "error", ...}`. No tracebacks reach the user.

## Out of scope

Dashboard, WebSocket event transport, subagents, multi-worker routing, planning/decomposition,
persistence, retries, and the golden-set eval. Each is a later ticket.

## Acceptance

- Three substantive goals return `status: "ok"` with a non-empty `result`.
- A degenerate or ambiguous goal returns `status: "error"` with an actionable `error`.
- No invocation exits via an uncaught exception.
- stdout parses as a single JSON object on every run, including CLI misuse.

## Known gap

The machine this was built on has no model credentials and no local Ollama, so the accepted runs
exercise the loop and the contract, not research quality. Prompt quality is unverified until the
golden-set eval ticket runs against a hosted model. Per tech.mdc no eval score is owed yet —
`v1.md` is a new prompt file, not an edit to an existing one — but this prompt should not be
considered validated.
