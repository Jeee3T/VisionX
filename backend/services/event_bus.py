"""Fan-out of engine events to Server-Sent Events subscribers.

Interim live-update mechanism (documented in the README): the browser opens one
SSE connection per session screen and the engine pushes rate-limited telemetry
into it. No frame-rate REST polling anywhere. Swapping SSE for WebSockets later
only touches this file and the stream route.
"""

import queue
import threading

MAX_QUEUE = 100


class EventBus:
    def __init__(self):
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._last_event: dict | None = None

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=MAX_QUEUE)
        with self._lock:
            self._subscribers.add(q)
            if self._last_event:
                q.put_nowait(self._last_event)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            if event.get("type") == "telemetry":
                self._last_event = event
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                # A slow client must never stall the camera loop - drop its oldest event.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except queue.Empty:  # pragma: no cover
                    pass

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


bus = EventBus()
