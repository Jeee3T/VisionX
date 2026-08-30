"""PowerPoint slideshow control, Windows-first.

Two control surfaces, tried in this order:

  1. **COM** (`presentation_controller.windows.PowerPointComBridge`) - asks the
     running PowerPoint to do the thing. Sets the pen, erases ink and moves
     between slides with no keystroke at all.
  2. **Keystrokes** (PyAutoGUI) - the fallback, and what VisionX has always used.

Why the COM path exists at all: PowerPoint's slideshow shortcuts are not merely
useless outside a slideshow, they are *dangerous*.

    Ctrl+P   in a slideshow -> pen
             on a normal PowerPoint window -> **Print dialog**

Blind `Ctrl+P` is exactly why the Print dialog kept appearing. So the pen is never
armed by a keystroke that has not been earned:

    slideshow CONFIRMED  ->  set the pen (COM first, Ctrl+P only if COM is absent)
    slideshow DENIED     ->  refuse, with a message naming the reason
    slideshow UNKNOWN    ->  send Ctrl+P, the historical behaviour, because we are
                             not on Windows / cannot ask and there is no evidence
                             of danger

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

import logging

from presentation_controller.base import PresentationControlError, PresentationController
from presentation_controller.keyboard import KeyboardBackend
from presentation_controller.pointer import PointerController
from presentation_controller.windows import (
    PP_POINTER_ARROW,
    PP_POINTER_PEN,
    SLIDESHOW_CONFIRMED,
    SLIDESHOW_DENIED,
    PowerPointComBridge,
)

logger = logging.getLogger(__name__)

NO_SLIDESHOW_MESSAGE = (
    "PowerPoint is not running a slideshow, so the pen cannot be switched on. "
    "Start the slideshow (F5) and try again. VisionX did not send Ctrl+P, because "
    "outside a slideshow that opens the Print dialog instead of the pen."
)

NO_SLIDESHOW_ERASE_MESSAGE = (
    "PowerPoint is not running a slideshow, so there is no ink to erase. "
    "Start the slideshow (F5) and try again."
)


class PowerPointController(PresentationController):
    """Drives a running PowerPoint slideshow. Sole owner of pointer/pen state.

    The dispatcher used to keep its own copy of `pointer_active` /
    `annotation_active` alongside this class's, and the two drifted apart: a
    refused command left the dispatcher believing the pen was on while PowerPoint
    had never been told. There is now exactly one copy of that state and it lives
    here, next to the thing it describes.
    """

    name = "powerpoint"

    def __init__(self, keyboard: KeyboardBackend | None = None,
                 com: PowerPointComBridge | None = None):
        self.keyboard = keyboard or KeyboardBackend()
        self.com = com if com is not None else PowerPointComBridge()
        self.pointer = PointerController(self.keyboard)
        self._pointer_active = False
        self._annotation_active = False

    # --- state ---------------------------------------------------------------
    @property
    def pointer_active(self) -> bool:
        return self._pointer_active

    @property
    def annotation_active(self) -> bool:
        return self._annotation_active

    # --- navigation ----------------------------------------------------------
    def next_slide(self) -> None:
        if self.com.next_slide():
            return
        self.keyboard.press("right")

    def previous_slide(self) -> None:
        if self.com.previous_slide():
            return
        self.keyboard.press("left")

    def goto_slide(self, number: int) -> None:
        """PowerPoint jumps to a slide when its number is typed then Enter."""
        target = int(number)
        if target < 1:
            raise PresentationControlError("Slide numbers start at 1.")
        if self.com.goto_slide(target):
            return
        self.keyboard.write(str(target))
        self.keyboard.press("enter")

    def first_slide(self) -> None:
        if self.com.goto_slide(1):
            return
        self.keyboard.press("home")

    def last_slide(self) -> None:
        count = self.com.slide_count()
        if count and self.com.goto_slide(count):
            return
        self.keyboard.press("end")

    # --- slideshow ------------------------------------------------------------
    def start_presentation(self) -> None:
        self.keyboard.press("f5")
        self.com.invalidate()   # a slideshow is (probably) starting - re-probe

    def end_presentation(self) -> None:
        # Let go of the pen before leaving: a slideshow that ends with the mouse
        # button still held leaves the desktop in a drag.
        self.pen_up()
        self.keyboard.press("esc")
        self._pointer_active = False
        self._annotation_active = False
        self.com.invalidate()

    def blackout(self) -> None:
        """`B` toggles a black screen - the same key blanks and restores."""
        self.keyboard.press("b")

    def whiteout(self) -> None:
        self.keyboard.press("w")

    # --- slideshow guard ------------------------------------------------------
    def slideshow_state(self) -> str:
        """CONFIRMED / DENIED / UNKNOWN. The gate every pen command passes."""
        return self.com.probe()

    # --- pointer -------------------------------------------------------------
    def set_pointer(self, active: bool) -> None:
        """Virtual pointer on/off.

        This method must never, under any circumstance, send `Ctrl+P`. The pointer
        and the pen are different features; conflating them is what put the Print
        dialog on screen. Turning the pointer on while the pen is active turns the
        pen off through `set_annotation`, which is the *only* Ctrl+P-adjacent path
        and only ever presses `Ctrl+A` in that direction.
        """
        if active:
            if self._annotation_active:
                self.set_annotation(False)
            # COM sets the arrow directly; the laser keystroke only fires when a
            # slideshow is genuinely running or we have no way to check.
            if not self.com.set_pointer_type(PP_POINTER_ARROW) \
                    and self.slideshow_state() != SLIDESHOW_DENIED:
                self.keyboard.hotkey("ctrl", "l")
            # Set only once the change has actually landed. If the keystroke above
            # raised - no desktop to reach - the pointer is not on, and saying it
            # is would be the same lie the dispatcher used to tell.
            self._pointer_active = True
        else:
            # Turning the pointer off restores the arrow, and the arrow is not the
            # pen: PowerPoint is no longer in pen mode, so neither is VisionX.
            # Saying otherwise left the button held down in arrow mode, where a
            # drag *advances the slide* - so every attempted stroke skipped
            # slides while the UI still showed the pen as on.
            self.pen_up()
            self._pointer_active = False
            self._annotation_active = False
            self._restore_arrow()

    def move_pointer(self, x: float, y: float) -> None:
        self.pointer.move(x, y)

    # --- annotation ----------------------------------------------------------
    def set_annotation(self, active: bool) -> None:
        """Pen on/off. Refuses to arm the pen without a running slideshow."""
        if active:
            state = self.slideshow_state()
            if state == SLIDESHOW_DENIED:
                # The single most important line in this file: do not press
                # Ctrl+P into a PowerPoint that will read it as Print.
                raise PresentationControlError(NO_SLIDESHOW_MESSAGE)

            if self._pointer_active:
                self._pointer_active = False

            if self.com.set_pointer_type(PP_POINTER_PEN):
                self._annotation_active = True
                return
            if state == SLIDESHOW_CONFIRMED:
                # A slideshow is running but COM could not set the pointer type;
                # the keystroke is safe here because the slideshow is confirmed.
                self.com.activate()
            self.keyboard.hotkey("ctrl", "p")
            self._annotation_active = True
        else:
            # Release the button before leaving pen mode, or PowerPoint keeps
            # drawing a line to wherever the cursor goes next.
            self.pen_up()
            self._annotation_active = False
            self._restore_arrow()

    def _restore_arrow(self) -> None:
        """Back to the ordinary arrow pointer, by whichever route works."""
        if self.com.set_pointer_type(PP_POINTER_ARROW):
            return
        if self.slideshow_state() == SLIDESHOW_DENIED:
            # Nothing to restore - and Ctrl+A outside a slideshow is Select All.
            return
        self.keyboard.hotkey("ctrl", "a")

    # --- pen strokes ---------------------------------------------------------
    # PowerPoint's pen draws on a *drag*, not on a move. Streaming cursor
    # positions with no button held down - which is what VisionX used to do - moves
    # the pen around the slide without leaving a single mark. These two methods are
    # the difference between "annotation mode is on" and "annotation works".
    def pen_down(self) -> None:
        if not self._annotation_active:
            return
        try:
            self.keyboard.mouse_down()
        except PresentationControlError:
            raise
        except Exception as exc:  # noqa: BLE001 - a stroke must not end the session
            logger.debug("Pen down failed: %s", exc)

    def pen_up(self) -> None:
        try:
            self.keyboard.mouse_up()
        except PresentationControlError:
            # The desktop is gone; there is no button to release.
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("Pen up failed: %s", exc)

    @property
    def pen_is_down(self) -> bool:
        return bool(getattr(self.keyboard, "mouse_is_down", False))

    def clear_annotation(self) -> None:
        """Erase the ink on the current slide.

        `View.EraseDrawing()` is the real thing and works whatever pointer mode the
        show is in. The `E` key only erases while the pen is already selected, so
        the keystroke path selects the pen first when it has to fall back - which
        is safe, because it only runs when a slideshow is confirmed or unknowable.
        """
        self.pen_up()

        if self.com.erase_ink():
            # PowerPoint keeps whatever pointer mode it was in; ours is unchanged.
            return

        state = self.slideshow_state()
        if state == SLIDESHOW_DENIED:
            raise PresentationControlError(NO_SLIDESHOW_ERASE_MESSAGE)

        self.keyboard.press("e")
        # `E` erases the ink but leaves PowerPoint in pen mode. Reporting the pen
        # as off here is what previously desynced the dispatcher from PowerPoint,
        # so the state is left exactly as it is - erasing ink is not a mode change.

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
            "penDown": self.pen_is_down,
            "slideshow": self.slideshow_state(),
            "windows": self.com.describe(),
        }

    def health_check(self) -> None:
        if not self.keyboard.available:
            raise PresentationControlError(
                "PyAutoGUI cannot reach the desktop, so slide commands will not be delivered."
            )
