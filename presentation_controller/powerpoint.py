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
    def describe(self) -> dict:
        return {
            "controller": self.name,
            "available": self.keyboard.available,
            "pointerActive": self._pointer_active,
            "annotationActive": self._annotation_active,
        }

    def health_check(self) -> None:
        if not self.keyboard.available:
            raise PresentationControlError(
                "PyAutoGUI cannot reach the desktop, so slide commands will not be delivered."
            )
