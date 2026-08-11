"""MediaPipe hand landmark detection producing plain-Python data.

MediaPipe ships a pretrained hand model - VisionX trains nothing of its own.
Two MediaPipe generations are supported so the project runs on any recent wheel:

  * MediaPipe >= 1.0  -> Tasks API (`HandLandmarker` + a bundled .task model)
  * MediaPipe 0.10.x  -> legacy `mp.solutions.hands`

The .task model lives in `computer_vision/models/hand_landmarker.task`; run
`python scripts/download_model.py` once if it is missing.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_PATH = MODEL_DIR / "hand_landmarker.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)

# Landmark indexes we reason about downstream (MediaPipe Hands topology).
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]


class HandModelMissingError(RuntimeError):
    """The MediaPipe hand model file is not available locally."""


@dataclass
class HandLandmarks:
    points: np.ndarray      # shape (21, 3), normalised to the frame (x, y in 0..1)
    handedness: str         # "Left" / "Right" as seen in the mirrored frame
    detection_score: float  # MediaPipe's own confidence in the detection

    def point(self, index: int) -> np.ndarray:
        return self.points[index]

    def pixel(self, index: int, width: int, height: int) -> tuple[int, int]:
        x, y = self.points[index][0], self.points[index][1]
        return int(x * width), int(y * height)


def ensure_model(download: bool = True) -> Path:
    """Return the local model path, downloading it once if allowed."""
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 0:
        return MODEL_PATH
    if not download:
        raise HandModelMissingError(f"Hand model not found at {MODEL_PATH}")

    import urllib.request

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading MediaPipe hand model to %s ...", MODEL_PATH)
    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    except Exception as exc:  # noqa: BLE001
        raise HandModelMissingError(
            f"Could not download the MediaPipe hand model ({exc}). Download it manually from "
            f"{MODEL_URL} and save it as {MODEL_PATH}."
        ) from exc
    return MODEL_PATH


class HandDetector:
    """Detects one hand (the presenting hand) per frame."""

    def __init__(
        self,
        max_hands: int = 1,
        detection_confidence: float = 0.6,
        tracking_confidence: float = 0.6,
        model_complexity: int = 0,
    ):
        import mediapipe as mp

        self._mp = mp
        self._legacy = None
        self._landmarker = None
        self._frame_index = 0

        if hasattr(mp, "solutions") and hasattr(mp.solutions, "hands"):
            self._legacy = mp.solutions.hands.Hands(
                static_image_mode=False,
                max_num_hands=max_hands,
                model_complexity=model_complexity,
                min_detection_confidence=detection_confidence,
                min_tracking_confidence=tracking_confidence,
            )
            logger.info("Hand detector using the legacy MediaPipe solutions API")
            return

        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(ensure_model())),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=max_hands,
            min_hand_detection_confidence=detection_confidence,
            min_hand_presence_confidence=detection_confidence,
            min_tracking_confidence=tracking_confidence,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)
        logger.info("Hand detector using the MediaPipe Tasks API (%s)", MODEL_PATH.name)

    # --- detection -----------------------------------------------------------
    def detect(self, rgb_frame: np.ndarray) -> HandLandmarks | None:
        """Return landmarks for the most confident hand, or None when no hand is visible."""
        if self._legacy is not None:
            return self._detect_legacy(rgb_frame)
        return self._detect_tasks(rgb_frame)

    def _detect_legacy(self, rgb_frame: np.ndarray) -> HandLandmarks | None:
        rgb_frame.flags.writeable = False
        result = self._legacy.process(rgb_frame)
        rgb_frame.flags.writeable = True

        if not result.multi_hand_landmarks:
            return None

        best_index, best_score, label = 0, 0.0, "Unknown"
        if result.multi_handedness:
            for index, handedness in enumerate(result.multi_handedness):
                classification = handedness.classification[0]
                if classification.score > best_score:
                    best_index, best_score, label = index, float(classification.score), classification.label

        landmarks = result.multi_hand_landmarks[best_index]
        points = np.array([[lm.x, lm.y, lm.z] for lm in landmarks.landmark], dtype=np.float32)
        return HandLandmarks(points=points, handedness=label, detection_score=best_score or 1.0)

    def _detect_tasks(self, rgb_frame: np.ndarray) -> HandLandmarks | None:
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb_frame))
        # detect_for_video requires strictly increasing timestamps.
        self._frame_index += 1
        timestamp_ms = int(time.monotonic() * 1000) + self._frame_index
        result = self._landmarker.detect_for_video(mp_image, timestamp_ms)

        if not result.hand_landmarks:
            return None

        best_index, best_score, label = 0, 0.0, "Unknown"
        for index, categories in enumerate(result.handedness or []):
            if categories and categories[0].score > best_score:
                best_index = index
                best_score = float(categories[0].score)
                label = categories[0].category_name or categories[0].display_name or "Unknown"

        landmarks = result.hand_landmarks[best_index]
        points = np.array([[lm.x, lm.y, lm.z] for lm in landmarks], dtype=np.float32)
        return HandLandmarks(points=points, handedness=label, detection_score=best_score or 1.0)

    def close(self) -> None:
        for resource in (self._legacy, self._landmarker):
            try:
                if resource is not None:
                    resource.close()
            except Exception:  # noqa: BLE001
                pass


def draw_landmarks(bgr_frame, hand: HandLandmarks, colour=(241, 102, 99)) -> None:
    """Draw the skeleton onto the preview frame (preview only - never used for logic)."""
    import cv2

    height, width = bgr_frame.shape[:2]
    pixels = [(int(p[0] * width), int(p[1] * height)) for p in hand.points]
    for start, end in HAND_CONNECTIONS:
        cv2.line(bgr_frame, pixels[start], pixels[end], colour, 2, cv2.LINE_AA)
    for x, y in pixels:
        cv2.circle(bgr_frame, (x, y), 3, (255, 255, 255), -1, cv2.LINE_AA)
