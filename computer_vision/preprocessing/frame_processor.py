"""Frame preprocessing: resize, mirror, colour-space conversion, light metering."""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class ProcessedFrame:
    bgr: np.ndarray           # mirrored BGR frame, used for the preview overlay
    rgb: np.ndarray           # RGB copy handed to MediaPipe
    width: int
    height: int
    brightness: float         # mean luma 0-255, used for the "low light" hint

    @property
    def aspect(self) -> float:
        return self.width / float(self.height or 1)


def preprocess(frame: np.ndarray, target_width: int = 640, mirror: bool = True) -> ProcessedFrame:
    if frame is None:
        raise ValueError("preprocess() received an empty frame")

    height, width = frame.shape[:2]
    if width != target_width and width > 0:
        scale = target_width / float(width)
        frame = cv2.resize(frame, (target_width, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)

    if mirror:
        # Presenters expect their on-screen hand to move the same way theirs does.
        frame = cv2.flip(frame, 1)

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    brightness = float(np.mean(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
    height, width = frame.shape[:2]
    return ProcessedFrame(bgr=frame, rgb=rgb, width=width, height=height, brightness=brightness)
