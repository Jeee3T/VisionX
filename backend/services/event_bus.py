"""Fan-out of engine events to Server-Sent Events subscribers.

Interim live-update mechanism (documented in the README): the browser opens one
SSE connection per screen and the engine pushes events into it. No frame-rate
REST polling anywhere. Swapping SSE for WebSockets later only touches this file
and the stream route.

## Two channels, because they have opposite requirements

    commands / state / telemetry   discrete, must never be lost   -> queue
    pointer positions              continuous, only the newest    -> latest-wins
                                   one matters

Pointer movement used to travel inside the 12 Hz `telemetry` event, sharing one
bounded queue with everything else. That gave the virtual pointer an 83 ms
quantisation floor before it left the server, and a browser that fell behind
received a *backlog* of stale positions - which is exactly what "the pointer
moves in large delayed jumps" looks like.

The pointer channel is therefore a single-slot mailbox per subscriber: publishing
overwrites whatever had not been read yet. A slow client skips positions instead
of lagging, so it is always drawing the presenter's hand as it is *now*. The
camera thread never blocks, never allocates a queue entry and never waits on a
subscriber - `publish_pointer` is a lock, a dict store and a `set()`.
"""

import queue
import threading

MAX_QUEUE = 100


class Subscriber:
    """One SSE connection's mailbox: a durable queue plus a pointer slot."""

    def __init__(self):
        self.queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE)
        self._pointer: dict | None = None
        self._lock = threading.Lock()
        # Lets the stream generator block until something actually arrives on
        # *either* channel, rather than polling both on a timer.
        self._wake = threading.Event()

    # --- producer side -------------------------------------------------------
    def push(self, event: dict) -> None:
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            # A slow client must never stall the camera loop - drop its oldest event.
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(event)
            except (queue.Empty, queue.Full):  # pragma: no cover - lost either way
                pass
        self._wake.set()

    def push_pointer(self, event: dict) -> None:
        """Latest-wins. An unread position is replaced, never queued behind."""
        with self._lock:
            self._pointer = event
        self._wake.set()

    # --- consumer side -------------------------------------------------------
    def drain(self, timeout: float = 1.0) -> list[dict]:
        """Block until something arrives, then take everything that is waiting.

        Returns an empty list on timeout, which is the stream route's cue to
        consider sending a keep-alive.
        """
        self._wake.wait(timeout)
        self._wake.clear()

        events: list[dict] = []
        while True:
            try:
                events.append(self.queue.get_nowait())
            except queue.Empty:
                break

        with self._lock:
            pointer, self._pointer = self._pointer, None
        if pointer is not None:
            # Last: a pointer position describes where the hand is *after*
            # whatever commands arrived in the same batch.
            events.append(pointer)
        return events


class EventBus:
    def __init__(self):
        self._subscribers: set[Subscriber] = set()
        self._lock = threading.Lock()
        self._last_event: dict | None = None

    def subscribe(self) -> Subscriber:
        """Register a connection and seed it with the most recent telemetry.

        The seed happens INSIDE the lock, and that matters. Registering first and
        seeding afterwards leaves a window in which a concurrent `publish` can
        deliver newer telemetry to this subscriber, which the stale seed then
        arrives behind - so a window opening mid-session would render the older
        snapshot last and show state that had already been superseded.

        Holding the lock is safe because seeding cannot block: the queue is
        brand new and empty, so `put_nowait` always succeeds.
        """
        subscriber = Subscriber()
        with self._lock:
            self._subscribers.add(subscriber)
            if self._last_event:
                subscriber.push(self._last_event)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def publish(self, event: dict) -> None:
        with self._lock:
            if event.get("type") == "telemetry":
                self._last_event = event
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.push(event)

    def publish_pointer(self, event: dict) -> None:
        """A fingertip position. Coalescing: only the newest is ever delivered."""
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.push_pointer(event)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


bus = EventBus()
