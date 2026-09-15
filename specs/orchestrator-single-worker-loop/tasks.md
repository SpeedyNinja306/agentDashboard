# Tasks — orchestrator-single-worker-loop

- [x] Branch `feature/orchestrator-single-worker-loop` off `main`
- [x] Add `langgraph`, `langchain-core`, `pydantic` to `pyproject.toml`; provider SDKs as extras
- [x] Write `prompts/research-specialist/v1.md`
- [x] `WorkerResult` contract with enforced invariants + parser for untrusted payloads
- [x] Pluggable model backends (`offline`, `anthropic`, `openai`)
- [x] `research-specialist` worker; never raises, never returns free text
- [x] LangGraph `StateGraph` loop: dispatch -> worker -> finalize
- [x] CLI `python -m orchestrator.run "goal text"`; JSON on stdout always
- [x] Manual verification: 3 substantive goals, 1 degenerate goal, plus failure-injection runs

## Follow-ups (not this ticket)

- [ ] Golden-set eval under `eval/research-specialist/`; score `v1.md` against a hosted model
- [ ] Verify the model-judged ambiguity path, which the offline backend cannot exercise
- [ ] Structured lifecycle events + WebSocket transport at the node boundaries
- [ ] Multi-worker routing at `dispatch`; revisit `create_supervisor` or amend tech.mdc
- [ ] Automated regression tests for the contract (manual runs only, so far)
