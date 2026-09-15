# Requirements — websocket-event-stream

Ticket 3 gave the orchestrator structured lifecycle events and wrote them to stdout, which only
works for something reading that one process's pipe. This ticket moves the same events onto the
WebSocket transport tech.mdc calls for, and keeps the orchestrator process alive so there is
something for the dashboard to connect *to*.

## In scope

1. A persistent orchestrator process replacing the one-shot CLI invocation as the primary way to
   run goals. It stays up, accepts new goals over time, and keeps serving while a run is in
   flight.
2. An in-memory task queue. Goals are accepted whenever, run one at a time in submission order.
3. A WebSocket endpoint broadcasting every lifecycle event to every connected client.
4. Ticket 3's event schema, unchanged: `{event_type, agent_id, agent_name, timestamp, payload}`,
   with the same `event_type` values and the same payloads. This ticket changes the transport and
   nothing else about an event.
5. Clients may connect and disconnect at any point, including partway through a run, without
   disturbing the server, the run, or other clients.

## Out of scope

The dashboard frontend, authentication, persistence across restarts, concurrent runs, subagents,
multi-worker routing, and replacing the eval harness's stdout capture. Prompts are untouched, so
per structure.mdc no eval score is owed.

## Acceptance

- The event sequence a client receives over the WebSocket is identical to what ticket 3 printed
  to stdout for the same goal — same events, same order, same JSON.
- Two clients connected at once receive identical frames in identical order.
- A client connecting mid-run does not crash or stall the server; it receives the rest of the run
  live, and can ask for recent history to see how the run got there.
- A client disconnecting mid-run — including an abrupt drop with no close frame — does not cancel
  the run, drop queued goals, or stop new goals being accepted.
- A client reconnecting afterwards receives subsequent events normally.
- Goals submitted while no client is connected still run.
- The existing `python -m orchestrator.run` CLI and `python -m eval.selftest` still behave as
  they did before this ticket.

## Known gaps

- Runs are sequential. Two concurrent runs would interleave their events on one stream, and the
  event schema has no run field to demultiplex them — `agent_id` identifies an agent, not an
  invocation. Concurrency needs that resolved first and is deliberately deferred.
- Nothing is persisted. A restart loses the queue, the task records, and the replay buffer.
- No authentication, and CORS is wide open. The server binds to loopback, which is the only thing
  keeping it private.
- Verified against the `offline` backend and an artificially slowed variant of it. No hosted model
  has run through this path, because this machine has no credentials.
