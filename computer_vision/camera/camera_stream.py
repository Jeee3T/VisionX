"""Webcam capture with explicit, non-crashing handling of every failure mode."""

import logging
import time

import cv2

logger = logging.getLogger(__name__)


class CameraUnavailableError(RuntimeError):
    """Raised when the requested camera cannot be opened at all."""


class CameraStream:
    """Thin wrapper over cv2.VideoCapture with reconnect support.

    Failure modes handled:
      * camera index missing / already in use  -> CameraUnavailableError on open()
      * OS denies permission                   -> CameraUnavailableError on open()
      * camera disconnected mid-session        -> read() returns None, reconnect() retries
    """

    def __init__(self, index: int = 0, width: int = 640, height: int = 480):
        self.index = index
        self.width = width
        self.height = height
        self._capture: cv2.VideoCapture | None = None
        self._consecutive_failures = 0
        self._last_reconnect = 0.0

    # --- lifecycle -----------------------------------------------------------
    def open(self) -> None:
        capture = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not capture.isOpened():
            capture.release()
            capture = cv2.VideoCapture(self.index)  # fall back to the default backend

        if not capture.isOpened():
            capture.release()
            raise CameraUnavailableError(
                f"No camera found at index {self.index}. Connect a webcam, close any app "
                f"already using it, and allow camera access for this application."
            )

        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        ok, _ = capture.read()
        if not ok:
            capture.release()
            raise CameraUnavailableError(
                "The camera was opened but returned no frames. It may be in use by another "
                "application or blocked by the operating system's privacy settings."
            )

        self._capture = capture
        self._consecutive_failures = 0
        logger.info("Camera %s opened at %sx%s", self.index, self.width, self.height)

    def read(self):
        """Return a BGR frame, or None when a frame could not be grabbed."""
        if self._capture is None:
            return None
        ok, frame = self._capture.read()
        if not ok or frame is None:
            self._consecutive_failures += 1
            return None
        self._consecutive_failures = 0
        return frame

    @property
    def is_lost(self) -> bool:
        """True once enough consecutive reads failed to call the camera gone."""
        return self._consecutive_failures >= 15

    def reconnect(self) -> bool:
        """Attempt to reopen a disconnected camera, at most once every 2 seconds."""
        now = time.time()
        if now - self._last_reconnect < 2.0:
            return False
        self._last_reconnect = now
        self.release()
        try:
            self.open()
            return True
        except CameraUnavailableError as exc:
            logger.warning("Camera reconnect failed: %s", exc)
            return False

    def release(self) -> None:
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:  # noqa: BLE001
                pass
            self._capture = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_exc):
        self.release()


def list_available_cameras(max_index: int = 4) -> list[int]:
    """Probe the first few camera indexes so the UI can offer a real choice."""
    available: list[int] = []
    for index in range(max_index):
        capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if capture.isOpened():
            available.append(index)
        capture.release()
    return available
