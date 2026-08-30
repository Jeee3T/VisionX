"""PyAutoGUI keyboard/mouse backend - the only place that touches OS input.

Tuned for Windows, which is the platform VisionX runs on:

  * per-monitor DPI awareness, so `screen_size()` reports physical pixels and the
    pointer lands where the presenter is actually pointing on a scaled display;
  * a small non-zero inter-key pause, because PowerPoint's slideshow window drops
    keystrokes delivered back-to-back with no gap at all;
  * explicit mouse button control, which is what actually draws with the pen -
    moving the cursor with no button held down draws nothing.
"""

import logging
import threading

from presentation_controller.base import PresentationControlError
from presentation_controller.windows import IS_WINDOWS, enable_dpi_awareness

logger = logging.getLogger(__name__)

# PowerPoint's slideshow window silently drops keystrokes that arrive with no gap
# between them, which is what made "previous slide" occasionally do nothing. 12 ms
# is below the threshold a presenter can perceive and above the one PowerPoint
# misses.
WINDOWS_KEY_PAUSE = 0.012


class KeyboardBackend:
    """Wraps PyAutoGUI so an unavailable display never crashes the engine."""

    def __init__(self):
        self._pyautogui = None
        self._unavailable_reason: str | None = None
        self._mouse_down = False
        # The camera thread draws while a Flask request thread can switch modes,
        # so the button flag is touched from two threads. Check-then-set without
        # this lock loses a release and strands the physical button down.
        self._mouse_lock = threading.RLock()

    def _gui(self):
        if self._pyautogui is not None:
            return self._pyautogui
        if self._unavailable_reason:
            raise PresentationControlError(self._unavailable_reason)
        try:
            # DPI awareness must be set before PyAutoGUI first asks for the screen
            # size, because that is when it caches it.
            enable_dpi_awareness()

            import pyautogui

            pyautogui.FAILSAFE = False   # a corner-of-screen pointer must not abort a talk
            pyautogui.PAUSE = WINDOWS_KEY_PAUSE if IS_WINDOWS else 0.0
            self._pyautogui = pyautogui
            return pyautogui
        except Exception as exc:  # noqa: BLE001
            self._unavailable_reason = (
                f"Keyboard/mouse control is unavailable on this machine ({exc}). "
                "Slide commands cannot be sent to PowerPoint."
            )
            logger.warning(self._unavailable_reason)
            raise PresentationControlError(self._unavailable_reason)

    @property
    def available(self) -> bool:
        try:
            self._gui()
            return True
        except PresentationControlError:
            return False

    def press(self, key: str) -> None:
        self._gui().press(key)

    def hotkey(self, *keys: str) -> None:
        self._gui().hotkey(*keys)

    def write(self, text: str) -> None:
        """Type literal characters. PowerPoint's 'jump to slide' is digits + Enter."""
        self._gui().write(str(text))

    def move_to(self, x: int, y: int) -> None:
        self._gui().moveTo(x, y)

    # --- mouse buttons -------------------------------------------------------
    # Drawing with the PowerPoint pen is a *drag*: button down, move, button up.
    # The backend tracks whether the button is held so that a released button is
    # never released twice and a dropped session always lets go of it.
    def mouse_down(self, button: str = "left") -> None:
        gui = self._gui()
        with self._mouse_lock:
            if self._mouse_down:
                return
            # Flag first, then press. If the press raises, a later `mouse_up`
            # still tries to release - the safe direction: a spurious release is
            # harmless, a missed one leaves the desktop stuck in a drag.
            self._mouse_down = True
            gui.mouseDown(button=button)

    def mouse_up(self, button: str = "left") -> None:
        with self._mouse_lock:
            if not self._mouse_down:
                return
            # Clear the flag first: if the release itself fails we must not be
            # stuck believing the button is still held, or it can never be
            # released.
            self._mouse_down = False
            self._gui().mouseUp(button=button)

    @property
    def mouse_is_down(self) -> bool:
        return self._mouse_down

    def screen_size(self) -> tuple[int, int]:
        width, height = self._gui().size()
        return int(width), int(height)
