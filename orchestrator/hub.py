"""Fan-out of lifecycle events to connected WebSocket clients.

The hub sits between `orchestrator.events` and the socket handlers in `orchestrator.server`.
It has one job and two hard constraints.

The job: take the event dict that ticket 3 would have printed to stdout and deliver it,
unchanged, to every client currently connected.

Constraint 1 — ordering. Events are produced by synchronous graph code on a worker thread and
consumed by coroutines on the event loop. `publish_from_thread` hops threads via
`call_soon_threadsafe`, whose callback queue is FIFO, and each subscriber then has its own FIFO
queue drained by one writer task. So every client sees the same events in the same order the
orchestrator emitted them — the order a stdout capture shows.

Constraint 2 — isolation. A client that stops reading, or vanishes mid-run, must not stall the
run or the other clients. Each subscriber therefore buffers independently and is dropped when it
falls too far behind, rather than being allowed to apply back-pressure to the orchestrator.

Events are serialized once per event, not once per client: all clients get identical bytes, and
those bytes are what `json.dumps` produced for stdout in ticket 3.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

#: Events kept for `?replay=`. Enough for a client joining mid-run to see the run from its start.
DEFAULT_REPLAY_SIZE = 256

#: Per-client buffer. Two orders of magnitude above a run's event count, so overflow means a
#: genuinely stuck reader rather than a momentarily slow one.
DEFAULT_CLIENT_BUFFER = 1024


class Subscriber:
    """One connected client's buffered view of the stream.

    `overflowed` is latched rather than reported per-event: once a client has missed anything,
    the stream it is reading is no longer the stream the orchestrator emitted, and the only
    honest options are to tell it or to close it. The event schema is fixed by ticket 3 and has
    nowhere to put a "you missed some" marker, so the writer closes the connection instead.
    """

    __slots__ = ("queue", "overflowed")

    def __init__(self, buffer_size: int) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=buffer_size)
        self.overflowed = False

    def offer(self, text: str) -> None:
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(text)
        except asyncio.QueueFull:
            self.overflowed = True


class EventHub:
    """Registry of connected clients plus the broadcast path into them."""

    def __init__(
        self,
        *,
        replay_size: int = DEFAULT_REPLAY_SIZE,
        client_buffer: int = DEFAULT_CLIENT_BUFFER,
    ) -> None:
        if client_buffer < replay_size:
            raise ValueError("client_buffer must be >= replay_size or a replay cannot be seeded")
        self._replay: deque[str] = deque(maxlen=replay_size)
        self._client_buffer = client_buffer
        self._subscribers: set[Subscriber] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._published = 0

    # -- wiring ---------------------------------------------------------------------------

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the loop that `publish_from_thread` should marshal onto."""
        self._loop = loop

    # -- producing ------------------------------------------------------------------------

    def publish_from_thread(self, event: dict[str, Any]) -> None:
        """Sink entry point. Safe to call from the orchestrator's worker thread.

        Serializing here, on the producing thread, keeps the cost off the event loop and pins
        the bytes to the moment of emission.
        """
        loop = self._loop
        if loop is None:
            logger.warning("event dropped: hub is not bound to a loop yet")
            return

        text = json.dumps(event)
        try:
            loop.call_soon_threadsafe(self._publish_text, text)
        except RuntimeError:
            # The loop closed while a run was still emitting; shutdown, not a fault.
            logger.debug("event dropped: loop closed during shutdown")

    def publish(self, event: dict[str, Any]) -> None:
        """Publish from the event loop thread."""
        self._publish_text(json.dumps(event))

    def _publish_text(self, text: str) -> None:
        """Runs on the loop thread only. Synchronous throughout, so it cannot interleave with a
        subscribe/unsubscribe and no client can see a torn view of the subscriber set."""
        self._published += 1
        self._replay.append(text)
        for subscriber in self._subscribers:
            subscriber.offer(text)

    # -- consuming ------------------------------------------------------------------------

    def subscribe(self, replay: int = 0) -> Subscriber:
        """Register a client, optionally seeding it with the last `replay` events.

        Registration and replay-seeding happen together without an await between them, so an
        event published while a client is connecting lands either in the seeded history or in
        the live queue — never both, and never neither.
        """
        subscriber = Subscriber(self._client_buffer)
        if replay > 0:
            for text in list(self._replay)[-replay:]:
                subscriber.offer(text)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    def close_all(self) -> None:
        self._subscribers.clear()

    # -- introspection --------------------------------------------------------------------

    @property
    def client_count(self) -> int:
        return len(self._subscribers)

    @property
    def published_count(self) -> int:
        return self._published

    @property
    def replay_available(self) -> int:
        return len(self._replay)
