# Tasks — websocket-event-stream

- [x] Branch `feature/websocket-event-stream` off `main`
- [x] Capture ticket 3's stdout event stream as the comparison oracle before touching anything
- [x] Add `fastapi`, `uvicorn`, `websockets` to `pyproject.toml`; record them in tech.mdc
- [x] Pluggable sink in `events.py`, defaulting to the existing stdout behaviour
- [x] Optional `run_id` through `start_run` / `run_goal` so a task_id can be the orchestrator's
      agent_id without adding a field to the event
- [x] `EventHub`: subscriber registry, thread-safe publish, per-client buffers, replay ring
- [x] `TaskQueue`: bounded in-memory FIFO, one sequential consumer, runs on a worker thread
- [x] FastAPI app: `WS /events`, `POST/GET /tasks`, `GET /health`, lifespan wiring
- [x] Verify: WebSocket stream matches the stdout oracle byte for byte, modulo agent_id/timestamp
- [x] Verify: mid-run connect, mid-run abrupt disconnect, reconnect, queueing while disconnected
- [x] Verify: hub overflow, slow-client isolation, 1000-event cross-thread ordering
- [x] Verify: shutdown with a run in flight
- [x] Verify: `orchestrator.run` CLI and `eval.selftest` unchanged

## Follow-ups (not this ticket)

- [ ] Dashboard consumes `/events`; React Flow nodes driven by the lifecycle events
- [ ] Give events a run-scoped identifier, then allow concurrent runs
- [ ] Automated tests in-repo. Everything above was verified with throwaway clients outside the
      repo; the project still has no test suite beyond `eval.selftest`
- [ ] Decide whether task records and the replay ring should survive a restart
- [ ] Authentication and a real CORS policy before this binds to anything but loopback
- [ ] Revisit the eval harness's stdout capture once the CLI is no longer the primary entrypoint
