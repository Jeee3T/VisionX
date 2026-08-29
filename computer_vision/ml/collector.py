"""In-memory capture of enrolment frames, owned by the camera loop.

The camera loop must never wait on a disk or a database. This collector does
nothing but append to a list and count, which is microseconds per frame; the
enrolment service takes the finished recording afterwards and persists it on a
background thread.

Quality is judged per frame (`dataset.assess_frame`): a frame that is too dark,
too far away, poorly detected or a duplicate of the previous one is counted and
discarded rather than silently poisoning the training set.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

import numpy as np

from computer_vision.ml.dataset import (
    GESTURE_CLASSES,
    GestureSample,
    QualityConfig,
    assess_frame,
    hand_box_area,
    new_recording_id,
)

logger = logging.getLogger(__name__)

DEFAULT_TARGET_FRAMES = 60
MIN_TARGET_FRAMES = 20
MAX_TARGET_FRAMES = 200


class CaptureError(ValueError):
    """The requested capture is not valid."""


@dataclass
class CaptureState:
    label: str
    recording_id: str
    subject_id: str
    target_frames: int
    session_id: str | None = None
    accepted: int = 0
    rejected: int = 0
    last_reason: str = ""
    samples: list[GestureSample] = field(default_factory=list)
    _previous: np.ndarray | None = None

    @property
    def complete(self) -> bool:
        return self.accepted >= self.target_frames

    @property
    def progress(self) -> float:
        return min(1.0, self.accepted / max(1, self.target_frames))

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "recordingId": self.recording_id,
            "targetFrames": self.target_frames,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "progress": round(self.progress, 3),
            "complete": self.complete,
            "lastRejection": self.last_reason,
        }


class GestureSampleCollector:
    """Thread-safe frame collector. `offer()` is called from the camera loop."""

    def __init__(self, quality: QualityConfig | None = None, keep_landmarks: bool = True):
        self.quality = quality or QualityConfig()
        self.keep_landmarks = keep_landmarks
        self._lock = threading.Lock()
        self._state: CaptureState | None = None

    # --- control -------------------------------------------------------------
    def start(self, label: str, subject_id: str, target_frames: int = DEFAULT_TARGET_FRAMES,
              session_id: str | None = None) -> CaptureState:
        if label not in GESTURE_CLASSES:
            raise CaptureError(f"'{label}' is not one of the {len(GESTURE_CLASSES)} gesture classes.")
        frames = int(target_frames)
        if not MIN_TARGET_FRAMES <= frames <= MAX_TARGET_FRAMES:
            raise CaptureError(
                f"A recording must be between {MIN_TARGET_FRAMES} and {MAX_TARGET_FRAMES} frames."
            )

        state = CaptureState(
            label=label,
            recording_id=new_recording_id(),
            subject_id=subject_id,
            target_frames=frames,
            session_id=session_id,
        )
        with self._lock:
            self._state = state
        logger.info("Enrolment capture started: %s x%s (%s)", label, frames, state.recording_id)
        return state

    def cancel(self) -> None:
        with self._lock:
            if self._state:
                logger.info("Enrolment capture cancelled: %s", self._state.recording_id)
            self._state = None

    def take(self) -> list[GestureSample]:
        """Remove and return the captured samples, clearing the collector."""
        with self._lock:
            state = self._state
            self._state = None
        return list(state.samples) if state else []

    # --- camera loop ---------------------------------------------------------
    def offer(self, hand, aspect: float, brightness: float) -> CaptureState | None:
        """Offer one frame. Returns the live state, or None when not capturing."""
        with self._lock:
            state = self._state
            if state is None or state.complete:
                return state

            if hand is None:
                state.rejected += 1
                state.last_reason = "no hand in frame"
                return state

            accepted, reason, features = assess_frame(
                hand.points, float(hand.detection_score), float(brightness),
                previous_features=state._previous, aspect=aspect, config=self.quality,
            )
            if not accepted:
                state.rejected += 1
                state.last_reason = reason
                return state

            state._previous = features
            state.accepted += 1
            state.last_reason = ""
            state.samples.append(GestureSample(
                label=state.label,
                features=features.tolist(),
                recording_id=state.recording_id,
                subject_id=state.subject_id,
                landmarks=hand.points.tolist() if self.keep_landmarks else None,
                aspect=float(aspect),
                detection_score=float(hand.detection_score),
                brightness=float(brightness),
                hand_box_area=hand_box_area(hand.points),
                handedness=getattr(hand, "handedness", ""),
                session_id=state.session_id,
            ))
            return state

    # --- queries -------------------------------------------------------------
    @property
    def active(self) -> bool:
        with self._lock:
            return self._state is not None and not self._state.complete

    def status(self) -> dict | None:
        with self._lock:
            return self._state.as_dict() if self._state else None
