# Requirements — second-worker-generalization

Tickets 1–4 built the orchestrator loop, the result contract, the event stream, and the eval
harness around a single worker (research-specialist). A one-worker system cannot show whether any
of that *generalizes* or was just fitted to the one case. This ticket adds a second, deliberately
different worker (coding-agent) and uses it to test the seams the earlier tickets left.

The second worker was chosen to stress the contract, not to flatter it: research returns prose,
coding returns code — newlines, quotes, fences — so if `{status, result, error}` were secretly
research-shaped, code is where it would show.

## In scope

1. A `coding-agent` worker that returns the **exact same** `{status, result, error}` envelope as
   research-specialist, through the same never-raises / never-free-text guarantee.
2. `prompts/coding-agent/v1.md` and `eval/coding-agent/cases.yaml` (10–15 cases), scored through
   the ticket-4 harness with no harness changes required.
3. Routing in the orchestrator: a goal maps to a worker by explicit selection or a simple keyword
   router. No dynamic worker discovery.
4. The dashboard renders both workers as independent nodes with independent live status.
5. Concurrent execution of the two workers with no state or event cross-contamination.

## Out of scope

Dynamic worker discovery, an LLM supervisor (`create_supervisor`), subagents, authentication,
persistence, and a third worker. A hosted-model eval baseline (no credentials on this machine).

## The contract-fit test (the point of the ticket)

The instruction was: coding-agent must fit the ticket-2 contract as-is, and if it does not, the
contract is what gets fixed — not the worker with a workaround. So the first question was whether
a worker that returns code fits `{status, result, error}` where `result` is a single string.

**Finding: it fits, unchanged.** Code is text; a fenced code block lives inside the `result`
string with its newlines and quotes JSON-escaped like any other string content. The temptation was
to add structure — `files[]`, a `language` field, a separate `tests` key — and that temptation is
exactly what `extra="forbid"` in `WorkerResult` and the unknown-key rejection in `parse_payload`
are there to resist. Adding coding-only fields would have made the envelope worker-specific, so the
orchestrator could no longer branch on `status` alone, which is the one thing the contract exists
to guarantee. The differences a coding worker needs (where the code goes, how it is delimited, what
the caller must install) are *presentation inside `result`*, and belong in the prompt, which is
where they now live. No contract change was made, and that is the result being reported, not a gap.

The one real seam the contract lacks is unchanged from ticket 3 and still out of scope here:
worker lifecycle events carry the worker's `agent_id`, not the run's `task_id`, so correlating a
worker event back to its task from the event alone is not possible. It did not need to be for this
ticket — attribution is by `agent_name` — but see Known gaps.

## Acceptance

- coding-agent returns a valid envelope for every path (success, refusal, degenerate input,
  backend failure) and never raises, identical in shape to research-specialist.
- `eval/coding-agent/cases.yaml` loads and runs through `python -m eval.harness --worker
  coding-agent`, with the same two-tier (checks + judge) scoring; `python -m eval.selftest` proves
  every deterministic check discriminates good output from bad for both workers.
- A goal routes to the right worker: explicit selection is honoured; an unrouted coding goal
  reaches coding-agent; the CLI and both HTTP endpoints agree on the destination.
- The dashboard shows two nodes, each with its own live status; a task sent to one advances only
  that node.
- Two tasks, one per worker, run concurrently with disjoint events and independent state; a
  serialized queue would fail the concurrency check rather than pass it.
- `python -m orchestrator.run`, `python -m eval.selftest`, and the ticket-3 event schema are
  unchanged for existing callers.

## Known gaps

- No hosted-model eval baseline. The harness runs coding-agent end to end, but the `offline`
  backend returns a canned payload and ignores the prompt, so the only real (non-degenerate)
  passes offline are the pre-filter cases. A meaningful pass rate needs `AGENTDASH_MODEL` set to a
  hosted model with credentials, which this machine does not have — the same gap tickets 3 and 4
  recorded.
- The keyword router is deliberately simple and can misroute a goal that mixes vocabularies. This
  is acceptable because explicit selection overrides it and a misroute still returns a valid
  envelope from a real worker. An LLM supervisor is the eventual replacement.
- Worker lifecycle events still cannot be demultiplexed to a task by the event schema alone
  (`agent_id` is the worker's, not the run's). Concurrent runs are now isolated and correctly
  attributed *by worker*, but per-run correlation across interleaved events remains a follow-up.
