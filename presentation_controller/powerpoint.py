"""PowerPoint slideshow control through real keyboard shortcuts."""

import logging

from presentation_controller.base import PresentationControlError, PresentationController
from presentation_controller.keyboard import KeyboardBackend
from presentation_controller.pointer import PointerController

logger = logging.getLogger(__name__)


class PowerPointController(PresentationController):
    """Drives a running PowerPoint slideshow via PyAutoGUI.

    Shortcuts used (Microsoft PowerPoint slideshow mode):
      right / left   next & previous slide
      ctrl+l         laser pointer on
      ctrl+p         pen (annotation) on
      ctrl+a         back to the arrow pointer
      e              erase all ink on the current slide
      <n> enter      jump to slide n
      home / end     first & last slide
      f5 / esc       start & end the slideshow
      b / w          black & white screen
    """

    name = "powerpoint"

    def __init__(self, keyboard: KeyboardBackend | None = None):
        self.keyboard = keyboard or KeyboardBackend()
        self.pointer = PointerController(self.keyboard)
        self._pointer_active = False
        self._annotation_active = False

    # --- navigation ----------------------------------------------------------
    def next_slide(self) -> None:
        self.keyboard.press("right")

    def previous_slide(self) -> None:
        self.keyboard.press("left")

    def goto_slide(self, number: int) -> None:
        """PowerPoint jumps to a slide when its number is typed then Enter."""
        target = int(number)
        if target < 1:
            raise PresentationControlError("Slide numbers start at 1.")
        self.keyboard.write(str(target))
        self.keyboard.press("enter")

    def first_slide(self) -> None:
        self.keyboard.press("home")

    def last_slide(self) -> None:
        self.keyboard.press("end")

    # --- slideshow ------------------------------------------------------------
    def start_presentation(self) -> None:
        self.keyboard.press("f5")

    def end_presentation(self) -> None:
        self.keyboard.press("esc")

    def blackout(self) -> None:
        """`B` toggles a black screen - the same key blanks and restores."""
        self.keyboard.press("b")

    def whiteout(self) -> None:
        self.keyboard.press("w")

    # --- pointer -------------------------------------------------------------
    def set_pointer(self, active: bool) -> None:
        if active:
            if self._annotation_active:
                self.set_annotation(False)
            self.keyboard.hotkey("ctrl", "l")
        else:
            self.keyboard.hotkey("ctrl", "a")
        self._pointer_active = active

    def move_pointer(self, x: float, y: float) -> None:
        self.pointer.move(x, y)

    # --- annotation ----------------------------------------------------------
    def set_annotation(self, active: bool) -> None:
        if active:
            if self._pointer_active:
                self._pointer_active = False
            self.keyboard.hotkey("ctrl", "p")
        else:
            self.keyboard.hotkey("ctrl", "a")
        self._annotation_active = active

    def clear_annotation(self) -> None:
        self.keyboard.press("e")
        self._annotation_active = False

    # --- introspection -------------------------------------------------------
    def capabilities(self) -> set[str]:
        from computer_vision.command_mapping.gesture_mapper import ALL_COMMANDS

        return set(ALL_COMMANDS)

    def describe(self) -> dict:
        return {
            "controller": self.name,
            "capabilities": sorted(self.capabilities()),
            "available": self.keyboard.available,
            "pointerActive": self._pointer_active,
            "annotationActive": self._annotation_active,
        }

    def health_check(self) -> None:
        if not self.keyboard.available:
            raise PresentationControlError(
                "PyAutoGUI cannot reach the desktop, so slide commands will not be delivered."
            )
