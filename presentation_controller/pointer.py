"""Maps a normalised fingertip position onto real screen coordinates."""

import logging

from presentation_controller.base import PresentationControlError
from presentation_controller.keyboard import KeyboardBackend

logger = logging.getLogger(__name__)

# The hand never reaches the extreme edges of the camera frame comfortably, so the
# usable region is inset and then stretched back over the full screen.
MARGIN_X = 0.15
MARGIN_Y = 0.15


class PointerController:
    def __init__(self, keyboard: KeyboardBackend):
        self.keyboard = keyboard
        self._screen: tuple[int, int] | None = None

    def _screen_size(self) -> tuple[int, int]:
        if self._screen is None:
            self._screen = self.keyboard.screen_size()
        return self._screen

    @staticmethod
    def _stretch(value: float, margin: float) -> float:
        span = 1.0 - 2 * margin
        return min(1.0, max(0.0, (value - margin) / span))

    def move(self, x: float, y: float) -> tuple[int, int] | None:
        try:
            width, height = self._screen_size()
            screen_x = int(self._stretch(x, MARGIN_X) * width)
            screen_y = int(self._stretch(y, MARGIN_Y) * height)
            self.keyboard.move_to(screen_x, screen_y)
            return screen_x, screen_y
        except PresentationControlError:
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("Pointer move failed: %s", exc)
            return None
