# Design — websocket-event-stream

## Shape

```
orchestrator/server.py   FastAPI app: WS /events, POST/GET /tasks, GET /health, lifespan wiring
orchestrator/hub.py      EventHub: subscriber registry, ordered fan-out, replay ring
orchestrator/tasks.py    TaskQueue: in-memory FIFO + one sequential consumer
orchestrator/events.py   unchanged event shape; gains a pluggable sink
orchestrator/graph.py    run_goal() gains an optional run_id
orchestrator/run.py      untouched; still the one-shot CLI
```

The event path end to end:

```
graph/worker code  --events.emit()-->  installed sink
                                         |
                    (worker thread) call_soon_threadsafe
                                         v
                                   EventHub._publish_text   (loop thread)
                                         |
                            one bounded queue per subscriber
                                         v
                              per-client writer task -> WebSocket
```

## The sink seam

`events.emit` used to call `print`. It now builds the identical dict and hands it to a
process-wide sink, defaulting to `stdout_sink` — literally the old `print` call. The server swaps
in `hub.publish_from_thread` for the life of the process.

This is what "replace stdout with WebSockets" costs: one indirection. It is worth more than the
alternative of ripping stdout out, because the stdout path is the oracle for this ticket's main
acceptance criterion. Being able to run the same goal through the CLI and through the server and
diff the two streams is how "the events are unchanged" gets verified rather than asserted. It
also leaves `orchestrator.run` working and the eval harness's stdout capture correct.

A sink that raises is caught and reported on stderr. Observing a run must not be able to fail it.

## Why the WebSocket carries events and only events

Every frame on `/events` is one ticket 3 event. No acks, no envelopes, no keepalives, no error
frames. Goal submission is `POST /tasks` instead of a WebSocket message, even though the socket
is already open and the receive loop already exists to detect disconnects.

The reason is that the requirement is "broadcast every event from ticket 3's schema verbatim",
and a client cannot rely on that if some frames are not events. Mixing control traffic into the
stream would force every consumer to discriminate before parsing, and the schema — fixed by
ticket 3 — has no `type` discriminator to do it with. Keeping the stream pure also means a
stdout capture stays a byte-level oracle, and lets the submitter and the watcher be different
processes, which is what the dashboard will actually want.

## Why the run happens on a thread

`run_goal` is blocking: LangGraph's `invoke`, and under a hosted backend an HTTP call that can
take tens of seconds. Running it on the event loop would freeze the whole server for the duration
— no new connections, no `/health`, no accepting goals — which directly contradicts "handle a
client connecting mid-run". So the consumer does `await asyncio.to_thread(run_goal, ...)`.

That is what forces the thread hop in the hub. `contextvars` survive it: `asyncio.to_thread`
copies the current context, so `events.start_run()` inside the thread sets IDs in that copy and
cannot leak into the next task.

## Ordering

Three FIFO stages in series, so emission order is what every client sees:

1. `loop.call_soon_threadsafe` — the loop's callback queue is FIFO.
2. `EventHub._publish_text` — fully synchronous, no awaits, so it cannot interleave with a
   subscribe or unsubscribe and every subscriber is offered events in the same order.
3. One `asyncio.Queue` per subscriber, drained by exactly one writer task.

Serialization happens once per event, on the producing thread, so every client gets identical
bytes and the JSON is produced by the same `json.dumps` call ticket 3 used for stdout.

## Back-pressure: drop the client, never the run

Each subscriber has a 1024-event buffer. If it fills, the subscriber latches `overflowed` and the
writer closes that connection with 1011 on its next wake-up.

The orchestrator is never blocked by a slow reader — a dashboard that stops reading must not be
able to stall agent execution. Given the choice between silently dropping events and telling the
client, closing the connection is the honest option: the schema has nowhere to put a "you missed
some" marker, so a client that cannot be sent a complete stream is sent a closed one instead. At
six events per run, overflow means a genuinely stuck reader, not a slow one.

## Connecting mid-run

Two things make this safe. The subscriber is registered *before* the handshake is awaited, so no
event can slip through the gap between accepting a client and being able to send to it. And
registration plus replay-seeding happen in one synchronous step, so an event published while a
client is connecting lands in either the seeded history or the live queue — never both, never
neither.

`?replay=N` seeds the new subscriber from a 256-event ring. A late joiner can therefore see how
the current run reached its current state. It is off by default so that the default stream is
exactly the live events, in order, with nothing prepended.

## Task queue semantics

Stated plainly because it is the part most likely to be assumed wrong:

- The queue belongs to the process, not to a connection. Nothing in `tasks.py` knows whether a
  client exists.
- A disconnect — graceful or an aborted TCP connection — cancels nothing, drains nothing, and
  blocks nothing. A run whose submitter has vanished runs to completion; its events are broadcast
  to whoever is connected at the time and are kept in the replay ring for whoever connects next.
- Goals submitted with nobody connected are queued and run normally.
- One run at a time, in submission order. Sequential is a correctness requirement, not a
  simplification: `events.py` tracks the current run in contextvars and the schema has no run
  identifier, so two concurrent runs would produce one indistinguishable interleaved stream.
- `task_id` is reused as the orchestrator's `agent_id` via `run_goal(run_id=...)`. Correlating a
  task to its events therefore needs no new event field.
- The pending queue is bounded at 256 (`429` past that) and finished records are trimmed at 512.

## Shutdown

Lifespan teardown restores the previous sink, cancels the consumer, and clears subscribers.

A run already on a worker thread cannot be cancelled — Python threads are not interruptible — so
it finishes on its own. Two consequences, both verified: the server's own shutdown does not wait
on it and completes in well under a second, but the process does, because `asyncio.to_thread`
uses non-daemon pool threads that are joined at exit. And any events that thread emits after
teardown reach the restored `stdout_sink` and are printed, rather than being dropped or raising.
Its task record stays `running`, since claiming it finished would be false.

## Deviation from tech.mdc: none, but two libraries added

tech.mdc already specifies WebSockets as the event transport, so the transport is not a
deviation. FastAPI, uvicorn, and `websockets` are new libraries, and tech.mdc requires new
libraries be recorded there in the same PR; that edit is part of this ticket.

`uvicorn` is pinned plain rather than `uvicorn[standard]`, with `websockets` named directly. The
extra bundles unrelated dependencies and hides the WebSocket implementation behind a marker, and
this is the one dependency the ticket actually turns on.
