# Design — second-worker-generalization

## Shape

```
workers/runner.py                    run_worker(spec, goal, ...) -> WorkerResult; the shared path
workers/research_specialist/worker.py  now a WorkerSpec + run() over the shared path
workers/coding_agent/worker.py         a WorkerSpec + run() over the same path
workers/registry.py                  explicit name -> run() map; the one list of known workers
orchestrator/router.py               select_worker(goal, requested) = explicit else keyword route
orchestrator/graph.py                dispatch -> conditional edge -> one node per worker -> finalize
orchestrator/tasks.py                one FIFO lane per worker, lanes run concurrently
orchestrator/server.py               endpoints resolve a worker, submit to its lane
prompts/coding-agent/v1.md           system prompt
eval/coding-agent/cases.yaml         14-case golden set
orchestrator/selftest.py             routing + concurrent-isolation self-test (no credentials)
```

## Sharing the worker runtime instead of copying it

The single-worker `research_specialist/worker.py` held the whole execution path: reject degenerate
goals, load the pinned prompt, resolve the backend, emit the tool-call events, coerce the reply
into a `WorkerResult`. Adding a second worker by copying that file would create two places where
"never raises, never returns free text" has to independently stay true — and a contract guarantee
that holds in one copy but not the other is worse than none.

So the path moved to `workers/runner.py` as `run_worker(spec, goal, ...)`, and a worker is now a
`WorkerSpec` (name, prompt version, goal-length limit, filler-word set, and the handful of error
strings a rejection produces) plus a three-line `run()`. The rule this enforces: **the only things
a worker may differ in are the fields on `WorkerSpec`.** Anything a worker wants beyond those is a
signal the difference belongs in its prompt, not its runtime. research-specialist is now expressed
in that form too, so there is exactly one execution path and both workers ride it.

The per-worker error strings (`"nothing to research"` vs `"nothing to build"`) are on the spec
rather than generic because they are user-facing on rejection and are asserted verbatim by each
worker's golden set. coding-agent's filler-word set also includes the generic coding verbs — "fix",
"build", "code", "bug" — so "please fix the bug asap" is caught by the pre-filter as degenerate,
the coding analogue of "please do the thing".

## Deviation from tech.mdc: deterministic routing, not `create_supervisor`

tech.mdc specifies LangGraph `create_supervisor`. The single-worker design deferred it and said the
multi-worker ticket should "either adopt `create_supervisor` at the dispatch node or amend
tech.mdc." This ticket routes deterministically instead, for two reasons:

1. The ticket scopes routing as "explicit mapping or a simple router call — do not build dynamic
   worker discovery." An LLM supervisor is dynamic discovery's cousin: it spends a model call
   choosing a worker. That is more than asked for, and heavier than two workers warrant.
2. `create_supervisor` needs a bound chat model to construct, which makes the loop unrunnable
   without credentials — the exact constraint that kept ticket 2 on a plain `StateGraph` and keeps
   the offline path testable. On this machine that means untestable at all.

This stays LangGraph and stays the orchestrator-worker pattern; only the *routing brain* is a
keyword function rather than a model. `dispatch` is still the seam: swapping `router.select_worker`
for a supervisor call later is a one-function change, and the node-per-worker graph it feeds does
not change. When model tiering and credentials exist, revisit this and either adopt
`create_supervisor` at `dispatch` or amend tech.mdc. Recorded here per tech.mdc's "unless a
ticket's design.md says otherwise."

## Routing: explicit first, keyword fallback

`router.select_worker(goal, requested)` honours an explicit `requested` worker when it names a known
one, else falls to `router.route(goal)`, a substring keyword match over the known workers that
defaults to research-specialist. Two callers, one policy:

- The dashboard clicking a node and `POST /workers/{name}/tasks` pass `requested`, so the click is
  honoured verbatim — the reason to have addressable workers at all.
- `POST /tasks` and the bare CLI pass no worker, so the goal text routes itself.

The router is intentionally fallible: a mixed-vocabulary goal can match the wrong worker. That is
tolerable because the explicit path exists (a misroute is one click to fix) and a wrong choice
still yields a valid envelope from a real worker, never a crash. The server resolves the worker
once, uses it to pick the queue lane, and passes it into `run_goal`, so the lane and the graph's
`dispatch` never disagree.

## Graph: one node per worker behind a conditional edge

`dispatch` sets `state["worker"]`; a conditional edge maps that to a node, one per registered
worker, each built by `_make_worker_node(name)`; every worker node flows to `finalize`. Binding the
name per node (rather than one shared node reading the name from state) makes each worker a distinct
vertex, so a run touches exactly one worker and the graph itself carries the "which worker ran"
fact. `_route_to_worker` falls back to the default worker for an unknown selection so a bad state
value still terminates in a valid envelope.

## Concurrency: one lane per worker

The websocket ticket ran a single global queue consumer, justified by there being one worker and by
wanting the event stream to be one run after another rather than interleaved. With two workers that
is a false constraint: a slow research run would head-of-line-block an unrelated coding run, and the
anti-interleaving reason no longer holds because every event already carries `agent_name` and the
orchestrator `agent_id` is the run's `task_id`.

So `TaskQueue` now holds one FIFO lane per worker, each with its own consumer. Within a worker,
goals still run in submission order; across workers, lanes overlap. Per-run event isolation across
those concurrent runs rides on `contextvars`: `run_goal` executes on a worker thread via
`asyncio.to_thread`, which copies the context per call, so one run's orchestrator/worker ids live in
their own context and cannot leak into another's. This is the mechanism the concurrency self-test
verifies rather than assumes.

## Why the contract did not change

The headline instruction was to make coding-agent fit the ticket-2 envelope and, if it could not,
fix the envelope rather than work around it. It fits unchanged: `result` is a string, code is text,
and a fenced block sits inside that string with normal JSON escaping. Structured additions
(`files[]`, `language`, `tests`) were considered and rejected — they would make the envelope
worker-specific and defeat the one guarantee it exists to provide, that a caller can branch on
`status` without knowing which worker ran. The coding-specific needs (file placement, fencing,
setup notes) are presentation inside `result` and are specified by the prompt. `WorkerResult`'s
`extra="forbid"` and `parse_payload`'s unknown-key rejection are what make "put it in the prompt,
not the schema" enforceable rather than merely encouraged. Full reasoning in requirements.md.

## The prompt

`prompts/coding-agent/v1.md` mirrors the research-specialist prompt's structure — output contract,
when-to-error, what-goes-in-result, accuracy — retargeted to code:

- It is explicit that `result` is a JSON string, so newlines and quotes must be escaped, and that a
  fence belongs *inside* `result`, never wrapped around the JSON object (the failure the harness's
  fence-stripping tolerates but should not have to).
- Error conditions match the contract's spirit: refuse an unnamed defect, refuse when unseen
  context (a file's contents, a schema) determines the answer, and refuse abusive requests
  (malware, credential theft, bulk scraping) — the coding analogue of research's refusal cases.
- The accuracy clause forbids inventing an API, flag, or version, mirroring research's
  anti-hallucination clause; the golden set's `accuracy-nonexistent-api` case guards it.

## Eval

`eval/coding-agent/cases.yaml` is 14 cases in the same schema and the same two tiers: 7 deterministic
(a checkable right answer — a signature, a bugfix, SQL clauses, a fenced-and-unpadded snippet, the
three degenerate pre-filter cases) and 7 judged (open-ended design/refactor, two underspecified
goals that must error, two adversarial, and the nonexistent-API case). No harness change was needed
to run a second worker, which is itself evidence the harness generalized.

`orchestrator/selftest.py` and the extended `eval/selftest.py` are the credential-free proofs:
`eval.selftest` now asserts both golden sets are well-formed and that every deterministic check in
both sets separates good output from bad; `orchestrator.selftest` asserts routing and, with a
two-party barrier inside a stub backend, that the two workers genuinely run concurrently and stay
isolated in events and state (a serialized queue deadlocks the barrier and fails loudly).
