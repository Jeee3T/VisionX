"""PyAutoGUI keyboard/mouse backend - the only place that touches OS input."""

import logging

from presentation_controller.base import PresentationControlError

logger = logging.getLogger(__name__)


class KeyboardBackend:
    """Wraps PyAutoGUI so an unavailable display never crashes the engine."""

    def __init__(self):
        self._pyautogui = None
        self._unavailable_reason: str | None = None

    def _gui(self):
        if self._pyautogui is not None:
            return self._pyautogui
        if self._unavailable_reason:
            raise PresentationControlError(self._unavailable_reason)
        try:
            import pyautogui

            pyautogui.FAILSAFE = False   # a corner-of-screen pointer must not abort a talk
            pyautogui.PAUSE = 0.0
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

    def screen_size(self) -> tuple[int, int]:
        width, height = self._gui().size()
        return int(width), int(height)
