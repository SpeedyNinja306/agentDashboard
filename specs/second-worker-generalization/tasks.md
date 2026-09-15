# Tasks — second-worker-generalization

- [x] Branch `feature/second-worker-generalization` off `main`
- [x] Confirm the ticket-2 contract fits a code-returning worker; record the finding (it fits
      unchanged — code is a string inside `result`; no `files[]`/`language`/`tests` additions)
- [x] Extract the shared worker path into `workers/runner.py` (`WorkerSpec` + `run_worker`)
- [x] Re-express research-specialist over the shared path; verify `eval.selftest` still green
- [x] Add `workers/coding_agent/` (`WorkerSpec` + `run()`) with its own filler set and limits
- [x] Write `prompts/coding-agent/v1.md`
- [x] Write `eval/coding-agent/cases.yaml` (14 cases: 7 deterministic, 7 judged; all four tags)
- [x] Extend `eval/selftest.py`: both golden sets validated; discrimination fixtures for every
      check-bearing coding-agent case
- [x] `workers/registry.py`: explicit worker map, default worker, `is_known`/`run_worker`
- [x] `orchestrator/router.py`: `select_worker` (explicit first) + keyword `route`
- [x] `orchestrator/graph.py`: dispatch routes via conditional edge to one node per worker
- [x] `orchestrator/tasks.py`: per-worker FIFO lanes, concurrent consumers, `worker` on the record
- [x] `orchestrator/server.py`: registry-driven known workers, endpoints resolve + submit to a lane,
      per-worker `/health`
- [x] `orchestrator/run.py`: optional `--worker`; unknown worker returns an error envelope
- [x] Dashboard: second node, hook keyed by `agent_name`, endpoint made configurable (default 8000)
- [x] `orchestrator/selftest.py`: routing + concurrent-isolation checks (barrier-forced overlap)
- [x] Verify: `eval.selftest`, `orchestrator.selftest` green; dashboard `tsc -b` + `oxlint` clean
- [x] Verify: coding-agent runs through `eval.harness` (offline: plumbing only, baseline unmeasured)
- [x] Verify: end-to-end HTTP+WS — concurrent submit to both workers, disjoint events, correct
      per-task dispatch, both nodes update independently in the browser

## Eval baseline

Offline harness run (`AGENTDASH_MODEL=offline python -m eval.harness --worker coding-agent`):
**3/14**, which is the three degenerate pre-filter cases and nothing else — the offline backend
returns a canned payload and ignores the prompt, so this measures wiring, not the prompt. The
harness prints that warning itself. A real baseline needs a hosted model:

```
AGENTDASH_MODEL=anthropic:claude-sonnet-4-5 python -m eval.harness --worker coding-agent --repeat 3
```

Per structure.mdc, a prompt-touching ticket owes an eval score in its PR before merge. `v1` is a new
prompt file, so that score is owed and is **not yet obtainable on this machine** (no credentials) —
the same blocker tickets 3 and 4 recorded. Run the command above where a key exists and record the
number before merging.

## Follow-ups (not this ticket)

- [ ] Obtain the hosted-model coding-agent baseline and iterate `v1` if it is below threshold
- [ ] Replace the keyword router with `create_supervisor` once model tiering/credentials exist, or
      amend tech.mdc to bless deterministic routing
- [ ] Give worker lifecycle events a run-scoped id so interleaved concurrent runs can be
      demultiplexed to a task from the event alone (not just by `agent_name`)
- [ ] In-repo automated tests beyond the `selftest` scripts (still no pytest suite)
- [ ] Archive or delete this spec folder after merge, per structure.mdc
