"""Abstract presentation control surface.

`PowerPointController` is the only concrete implementation shipped today. A
future `GoogleSlidesController` slots in behind this same interface without any
change to the dispatcher, engine or API - that is the whole point of the base
class. VisionX does not claim Slides support until such a class actually exists.
"""

from abc import ABC, abstractmethod


class PresentationControlError(RuntimeError):
    """Raised when the OS-level control layer is unavailable."""


class PresentationController(ABC):
    name = "abstract"

    @abstractmethod
    def next_slide(self) -> None: ...

    @abstractmethod
    def previous_slide(self) -> None: ...

    @abstractmethod
    def set_pointer(self, active: bool) -> None: ...

    @abstractmethod
    def move_pointer(self, x: float, y: float) -> None:
        """Move the pointer to a normalised (0..1) screen position."""

    @abstractmethod
    def set_annotation(self, active: bool) -> None: ...

    @abstractmethod
    def clear_annotation(self) -> None: ...

    def describe(self) -> dict:
        return {"controller": self.name}
