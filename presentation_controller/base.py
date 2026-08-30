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

    # --- optional capabilities ------------------------------------------------
    # Concrete rather than abstract on purpose: a controller that cannot do these
    # still satisfies the interface, and the dispatcher reports "not supported"
    # instead of crashing. Adding them as @abstractmethod would break every
    # existing implementation, including a future GoogleSlidesController.
    def _unsupported(self, capability: str) -> None:
        raise PresentationControlError(
            f"The {self.name} controller does not support {capability}."
        )

    def goto_slide(self, number: int) -> None:
        self._unsupported("jumping to a slide number")

    def first_slide(self) -> None:
        self._unsupported("jumping to the first slide")

    def last_slide(self) -> None:
        self._unsupported("jumping to the last slide")

    def start_presentation(self) -> None:
        self._unsupported("starting the slideshow")

    def end_presentation(self) -> None:
        self._unsupported("ending the slideshow")

    def blackout(self) -> None:
        self._unsupported("blanking the screen")

    def whiteout(self) -> None:
        self._unsupported("whiting out the screen")

    # --- pen strokes ----------------------------------------------------------
    # Drawing is a drag, not a move: a controller that can annotate needs to hold
    # a button down between `pen_down` and `pen_up`. No-ops by default so a
    # controller that draws some other way is still a valid implementation.
    def pen_down(self) -> None:
        """Begin a stroke at the current pointer position."""

    def pen_up(self) -> None:
        """End the stroke in progress, if any."""

    @property
    def pen_is_down(self) -> bool:
        return False

    def slideshow_state(self) -> str:
        """CONFIRMED / DENIED / UNKNOWN - is a slideshow actually running?"""
        from presentation_controller.windows import SLIDESHOW_UNKNOWN

        return SLIDESHOW_UNKNOWN

    def capabilities(self) -> set[str]:
        """Command names this controller can actually deliver."""
        from computer_vision.command_mapping.gesture_mapper import COMMANDS

        return set(COMMANDS)

    def describe(self) -> dict:
        return {"controller": self.name, "capabilities": sorted(self.capabilities())}
