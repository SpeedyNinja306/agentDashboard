Dispatch, planning, and result synthesis for the supervisor agent.

## Running

Persistent process with the WebSocket event stream (the normal way):

    python -m orchestrator.server --host 127.0.0.1 --port 8000

    POST /tasks          {"goal": "..."} -> 202 with the task record
    GET  /tasks          every task the process still remembers
    GET  /tasks/{id}     one task, with its result envelope once finished
    GET  /health         client count, queue depth, currently running task
    WS   /events         live lifecycle events; `?replay=N` to catch up first

One-shot, no server, events on stdout:

    python -m orchestrator.run "goal text"

## The event stream

Every frame on `/events` is exactly one lifecycle event, in the schema fixed by the
lifecycle-event-emission ticket:

    {event_type, agent_id, agent_name, timestamp, payload}

Nothing else is sent on that socket — no acks, no envelopes, no keepalives — so goals are
submitted over HTTP rather than through the socket. Frames are byte-identical to the JSON lines
`orchestrator.run` prints to stdout for the same goal.

Goals run one at a time in submission order, on a worker thread. The queue belongs to the
process, not to any connection: disconnecting does not cancel a running goal or drop queued ones,
and goals submitted with nobody connected still run. See
`specs/websocket-event-stream/design.md`.
