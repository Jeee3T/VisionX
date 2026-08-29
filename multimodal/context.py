"""Shared live context - the hook multimodal commands resolve against.

Feature D (voice + gesture together, e.g. "highlight this" while pointing) needs
one place where the current pointer position and mode live, readable by whichever
modality is being interpreted. The gesture engine publishes into it every frame;
the voice layer reads from it when an intent carries a deictic reference.

Only the plumbing ships today. No intent currently consumes the pointer, and the
voice dataset contains no deictic utterances, because inventing commands the
dispatcher cannot execute would be worse than leaving the seam empty.
"""

from __future__ import annotations

import threading
import time


class MultimodalContext:
    """Thread-safe snapshot of what the other modality is currently doing."""

    STALE_AFTER = 1.5   # seconds; a pointer older than this is not "here" any more

    def __init__(self):
        self._lock = threading.Lock()
        self._pointer: tuple[float, float] | None = None
        self._pointer_at = 0.0
        self._mode = "IDLE"
        self._slide = 1
        self._hand_present = False

    def update_pointer(self, x: float, y: float, mode: str) -> None:
        with self._lock:
            self._pointer = (float(x), float(y))
            self._pointer_at = time.time()
            self._mode = mode
            self._hand_present = True

    def update_slide(self, slide: int) -> None:
        with self._lock:
            self._slide = int(slide)

    def update_hand(self, present: bool) -> None:
        with self._lock:
            self._hand_present = bool(present)
            if not present:
                self._pointer = None

    def pointer(self) -> tuple[float, float] | None:
        """The fingertip position, or None when it is missing or stale."""
        with self._lock:
            if self._pointer is None or time.time() - self._pointer_at > self.STALE_AFTER:
                return None
            return self._pointer

    def snapshot(self) -> dict:
        pointer = self.pointer()
        with self._lock:
            return {
                "pointer": {"x": pointer[0], "y": pointer[1]} if pointer else None,
                "mode": self._mode,
                "currentSlide": self._slide,
                "handPresent": self._hand_present,
            }

    def reset(self) -> None:
        with self._lock:
            self._pointer = None
            self._pointer_at = 0.0
            self._mode = "IDLE"
            self._hand_present = False


context = MultimodalContext()
