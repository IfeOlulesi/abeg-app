"""In-process pub/sub event bus for Abeg.

Every published event fans out to all live global (operator) subscribers and,
if the event carries a session_id, to that session's subscribers too.

Subscribers are backed by bounded asyncio.Queue objects. If a consumer is slow
and its queue is full, the event is dropped for that consumer rather than
blocking the publisher (robust to slow consumers).
"""
import asyncio
import time
from contextlib import contextmanager
from typing import AsyncIterator, Iterator

_QUEUE_MAXSIZE = 256


def make_event(type: str, data: dict, session_id: str | None = None) -> dict:
    """Build a canonical event dict stamped with a wall-clock timestamp."""
    return {
        "type": type,
        "ts": time.time(),
        "session_id": session_id,
        "data": data,
    }


class EventBus:
    def __init__(self) -> None:
        # Global/operator subscribers.
        self._global: set[asyncio.Queue] = set()
        # Per-session subscribers.
        self._sessions: dict[str, set[asyncio.Queue]] = {}
        # Cache of the most recent inventory_update payload (products list).
        self.latest_inventory: list[dict] | None = None
        # When muted, publish is a no-op. Used to run the eval suite through the
        # real agent loop without leaking its events into the operator stream or
        # overwriting the live X-ray.
        self._muted = False

    # ---- muting -----------------------------------------------------------
    @contextmanager
    def muted(self) -> Iterator[None]:
        """Suppress all publishing for the duration of the block."""
        previous = self._muted
        self._muted = True
        try:
            yield
        finally:
            self._muted = previous

    # ---- publishing -------------------------------------------------------
    def publish(self, event: dict) -> None:
        """Fan out an event to all live subscribers (drop on full queue)."""
        if self._muted:
            return
        if event.get("type") == "inventory_update":
            self.latest_inventory = event.get("data", {}).get("products")

        for q in list(self._global):
            self._offer(q, event)

        sid = event.get("session_id")
        if sid is not None:
            for q in list(self._sessions.get(sid, ())):
                self._offer(q, event)

    @staticmethod
    def _offer(q: asyncio.Queue, event: dict) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Slow consumer: drop rather than block the whole bus.
            pass

    def set_latest_inventory(self, products: list[dict]) -> None:
        """Convenience setter for the cached inventory snapshot."""
        self.latest_inventory = products

    # ---- subscribing ------------------------------------------------------
    async def subscribe(self) -> AsyncIterator[dict]:
        """Subscribe to the global/operator stream."""
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._global.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._global.discard(q)

    async def subscribe_session(self, session_id: str) -> AsyncIterator[dict]:
        """Subscribe to a single session's stream."""
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._sessions.setdefault(session_id, set()).add(q)
        try:
            while True:
                yield await q.get()
        finally:
            subs = self._sessions.get(session_id)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    self._sessions.pop(session_id, None)


# Module-level singleton bus.
bus = EventBus()
