"""In-session annotation buffer.

Strokes are accumulated per slide while annotation mode is active; the session
service persists them to the Annotations collection.

Thread-safe, because two threads genuinely share one of these: the camera loop
adds points at frame rate while a Flask request thread ends and reads strokes to
persist them. Unguarded, `add_point` could see `_active` set on entry and None by
the time it dereferenced it, raising AttributeError inside the camera loop - the
exception was swallowed upstream, so drawing simply stopped with the pen still
held down. The lock is a leaf: nothing in here calls back out, so it cannot take
part in a deadlock.
"""

import threading
import time
from dataclasses import dataclass, field

MIN_POINT_DELTA = 0.006   # ignore sub-pixel jitter between samples


@dataclass
class Stroke:
    slide: int
    points: list[dict] = field(default_factory=list)
    colour: str = "#ef4444"
    width: int = 4
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "slide": self.slide,
            # A copy, not the live list: callers serialise these off-thread while
            # the camera loop may still be drawing.
            "points": list(self.points),
            "colour": self.colour,
            "width": self.width,
            "createdAt": self.created_at,
        }


class AnnotationController:
    def __init__(self):
        self._lock = threading.RLock()
        self._strokes: list[Stroke] = []
        self._active: Stroke | None = None

    # --- drawing -------------------------------------------------------------
    def begin(self, slide: int, colour: str = "#ef4444", width: int = 4) -> None:
        with self._lock:            # RLock: end() takes it too
            self.end()
            self._active = Stroke(slide=slide, colour=colour, width=width)

    def add_point(self, x: float, y: float) -> bool:
        with self._lock:
            active = self._active
            if active is None:
                return False
            if active.points:
                last = active.points[-1]
                if abs(last["x"] - x) < MIN_POINT_DELTA and abs(last["y"] - y) < MIN_POINT_DELTA:
                    return False
            active.points.append({"x": round(x, 4), "y": round(y, 4)})
            return True

    def end(self) -> Stroke | None:
        with self._lock:
            stroke = self._active
            self._active = None
            if stroke and len(stroke.points) > 1:
                self._strokes.append(stroke)
                return stroke
            return None

    # --- buffer --------------------------------------------------------------
    def clear(self, slide: int | None = None) -> int:
        with self._lock:
            self._active = None
            if slide is None:
                removed = len(self._strokes)
                self._strokes = []
                return removed
            before = len(self._strokes)
            self._strokes = [s for s in self._strokes if s.slide != slide]
            return before - len(self._strokes)

    def strokes(self, slide: int | None = None) -> list[dict]:
        with self._lock:
            source = self._strokes if slide is None \
                else [s for s in self._strokes if s.slide == slide]
            # Serialised inside the lock: the dicts handed out are copies, so a
            # caller can iterate them while the camera thread keeps drawing.
            return [s.as_dict() for s in source]

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._strokes)

    @property
    def is_drawing(self) -> bool:
        return self._active is not None
