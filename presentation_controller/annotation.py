"""In-session annotation buffer.

Strokes are accumulated per slide while annotation mode is active; the session
service persists them to the Annotations collection.
"""

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
            "points": self.points,
            "colour": self.colour,
            "width": self.width,
            "createdAt": self.created_at,
        }


class AnnotationController:
    def __init__(self):
        self._strokes: list[Stroke] = []
        self._active: Stroke | None = None

    # --- drawing -------------------------------------------------------------
    def begin(self, slide: int, colour: str = "#ef4444", width: int = 4) -> None:
        self.end()
        self._active = Stroke(slide=slide, colour=colour, width=width)

    def add_point(self, x: float, y: float) -> bool:
        if self._active is None:
            return False
        if self._active.points:
            last = self._active.points[-1]
            if abs(last["x"] - x) < MIN_POINT_DELTA and abs(last["y"] - y) < MIN_POINT_DELTA:
                return False
        self._active.points.append({"x": round(x, 4), "y": round(y, 4)})
        return True

    def end(self) -> Stroke | None:
        stroke = self._active
        self._active = None
        if stroke and len(stroke.points) > 1:
            self._strokes.append(stroke)
            return stroke
        return None

    # --- buffer --------------------------------------------------------------
    def clear(self, slide: int | None = None) -> int:
        self._active = None
        if slide is None:
            removed = len(self._strokes)
            self._strokes = []
            return removed
        before = len(self._strokes)
        self._strokes = [s for s in self._strokes if s.slide != slide]
        return before - len(self._strokes)

    def strokes(self, slide: int | None = None) -> list[dict]:
        source = self._strokes if slide is None else [s for s in self._strokes if s.slide == slide]
        return [s.as_dict() for s in source]

    @property
    def count(self) -> int:
        return len(self._strokes)

    @property
    def is_drawing(self) -> bool:
        return self._active is not None
