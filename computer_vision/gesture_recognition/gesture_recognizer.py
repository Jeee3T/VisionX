"""Geometric gesture recognition with a real, derived confidence score.

Every finger is classified extended/curled from the landmark geometry, and each
classification carries a margin. The pose confidence is the weakest finger's
certainty combined with MediaPipe's own detection score - so a hand caught
mid-transition scores low and is gated out before it can fire a command.
"""

import time
from dataclasses import dataclass, field

import numpy as np

from computer_vision.hand_detection.hand_detector import (
    HandLandmarks,
    INDEX_MCP,
    INDEX_PIP,
    INDEX_TIP,
    MIDDLE_PIP,
    MIDDLE_TIP,
    PINKY_MCP,
    PINKY_PIP,
    PINKY_TIP,
    RING_PIP,
    RING_TIP,
    THUMB_IP,
    THUMB_TIP,
    WRIST,
)
from computer_vision.gesture_recognition.poses import NO_HAND, UNKNOWN, signature_to_pose

FINGER_JOINTS = (
    (INDEX_TIP, INDEX_PIP),
    (MIDDLE_TIP, MIDDLE_PIP),
    (RING_TIP, RING_PIP),
    (PINKY_TIP, PINKY_PIP),
)

EXTEND_RATIO = 1.12   # tip must be this much farther from the wrist than the PIP joint
MARGIN_BAND = 0.22    # ratio distance over which certainty ramps from 0 to 1


@dataclass
class GestureResult:
    gesture: str
    confidence: float
    fingers: list[int] = field(default_factory=list)
    hand_detected: bool = False
    pointer: tuple[float, float] | None = None   # normalised index-fingertip position
    handedness: str = ""
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "gesture": self.gesture,
            "confidence": round(self.confidence, 3),
            "fingers": self.fingers,
            "handDetected": self.hand_detected,
            "pointer": {"x": round(self.pointer[0], 4), "y": round(self.pointer[1], 4)}
            if self.pointer
            else None,
            "handedness": self.handedness,
            "timestamp": self.timestamp,
        }


NO_HAND_RESULT = GestureResult(gesture=NO_HAND, confidence=0.0)


class GestureRecognizer:
    """Turns 21 hand landmarks into (pose name, confidence)."""

    def __init__(self, extend_ratio: float = EXTEND_RATIO, margin_band: float = MARGIN_BAND):
        self.extend_ratio = extend_ratio
        self.margin_band = margin_band

    # --- helpers -------------------------------------------------------------
    @staticmethod
    def _scaled(points: np.ndarray, aspect: float) -> np.ndarray:
        """Undo the frame's aspect distortion so distances are comparable."""
        scaled = points[:, :2].copy()
        scaled[:, 0] *= aspect
        return scaled

    def _finger_state(self, pts: np.ndarray, tip: int, pip: int) -> tuple[int, float]:
        wrist = pts[WRIST]
        d_tip = float(np.linalg.norm(pts[tip] - wrist))
        d_pip = float(np.linalg.norm(pts[pip] - wrist)) or 1e-6
        ratio = d_tip / d_pip
        extended = 1 if ratio >= self.extend_ratio else 0
        certainty = min(1.0, abs(ratio - self.extend_ratio) / self.margin_band)
        return extended, certainty

    def _thumb_state(self, pts: np.ndarray) -> tuple[int, float]:
        """The thumb bends sideways, so it is measured against the pinky knuckle."""
        anchor = pts[PINKY_MCP]
        hand_span = float(np.linalg.norm(pts[INDEX_MCP] - pts[PINKY_MCP])) or 1e-6
        d_tip = float(np.linalg.norm(pts[THUMB_TIP] - anchor))
        d_ip = float(np.linalg.norm(pts[THUMB_IP] - anchor))
        delta = (d_tip - d_ip) / hand_span
        extended = 1 if delta >= 0.12 else 0
        certainty = min(1.0, abs(delta - 0.12) / 0.20)
        return extended, certainty

    # --- public API ----------------------------------------------------------
    def recognize(self, hand: HandLandmarks | None, aspect: float = 1.333) -> GestureResult:
        if hand is None:
            return GestureResult(gesture=NO_HAND, confidence=0.0, hand_detected=False)

        pts = self._scaled(hand.points, aspect)

        states: list[int] = []
        certainties: list[float] = []

        thumb_state, thumb_certainty = self._thumb_state(pts)
        states.append(thumb_state)
        certainties.append(thumb_certainty)

        for tip, pip in FINGER_JOINTS:
            state, certainty = self._finger_state(pts, tip, pip)
            states.append(state)
            certainties.append(certainty)

        pose = signature_to_pose(tuple(states))

        # A pose is only as trustworthy as its least certain finger.
        geometric = float(min(certainties))
        confidence = geometric * float(np.clip(hand.detection_score, 0.0, 1.0))
        if pose == UNKNOWN:
            confidence *= 0.4  # a signature outside the library is never command-worthy

        tip = hand.points[INDEX_TIP]
        return GestureResult(
            gesture=pose,
            confidence=round(float(np.clip(confidence, 0.0, 1.0)), 4),
            fingers=states,
            hand_detected=True,
            pointer=(float(tip[0]), float(tip[1])),
            handedness=hand.handedness,
        )
