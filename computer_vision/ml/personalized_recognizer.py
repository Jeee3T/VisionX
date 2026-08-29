"""The personalized recognizer: same seam, learned decision.

It implements exactly the interface the engine already calls -

    recognize(hand: HandLandmarks | None, aspect: float) -> GestureResult

- so nothing downstream (debouncer, mapper, dispatcher, controller) changes. The
difference is what fills the result:

    geometric   confidence = weakest finger's margin x MediaPipe's score
                             (a designed proxy, not a probability)
    personalized confidence = the model's softmax probability for the predicted
                             class, plus the full distribution and the top-2 margin

If anything at all goes wrong at inference time the recognizer degrades to the
geometric recognizer for that frame rather than raising into the camera loop.
"""

from __future__ import annotations

import logging

import numpy as np

from computer_vision.gesture_recognition.gesture_recognizer import (
    GestureRecognizer,
    GestureResult,
    SOURCE_PERSONALIZED,
)
from computer_vision.gesture_recognition.poses import NO_HAND, POSE_BY_NAME
from computer_vision.hand_detection.hand_detector import HandLandmarks, INDEX_TIP
from computer_vision.ml.canonicalization import canonical_features
from computer_vision.ml.mlp import GestureModelArtifact

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 10


class PersonalizedRecognizer:
    """Wraps a trained per-user model behind the geometric recognizer's interface."""

    def __init__(
        self,
        artifact: GestureModelArtifact,
        fallback: GestureRecognizer | None = None,
        blend_detection_score: bool = True,
    ):
        self.artifact = artifact
        self.fallback = fallback or GestureRecognizer()
        self.blend_detection_score = blend_detection_score
        self.degraded = False
        self._failures = 0
        self._inferences = 0
        self._total_seconds = 0.0

    # --- public API ----------------------------------------------------------
    @property
    def model_version(self) -> str:
        return self.artifact.model_version

    @property
    def source(self) -> str:
        return "geometric" if self.degraded else SOURCE_PERSONALIZED

    def recognize(self, hand: HandLandmarks | None, aspect: float = 1.333) -> GestureResult:
        if hand is None:
            return GestureResult(gesture=NO_HAND, confidence=0.0, hand_detected=False,
                                 source=self.source, model_version=self.model_version)
        if self.degraded:
            return self.fallback.recognize(hand, aspect)

        try:
            return self._recognize(hand, aspect)
        except Exception as exc:  # noqa: BLE001 - inference must never reach the camera loop
            self._failures += 1
            logger.warning("Personalized inference failed (%s/%s): %s",
                           self._failures, MAX_CONSECUTIVE_FAILURES, exc)
            if self._failures >= MAX_CONSECUTIVE_FAILURES:
                self.degraded = True
                logger.error(
                    "Personalized recognizer disabled after %s consecutive failures; "
                    "the geometric recognizer is now handling this session.",
                    self._failures,
                )
            return self.fallback.recognize(hand, aspect)

    def _recognize(self, hand: HandLandmarks, aspect: float) -> GestureResult:
        import time

        started = time.perf_counter()
        features = canonical_features(hand.points, aspect)
        probabilities = self.artifact.predict_proba(features)[0]
        self._failures = 0
        self._inferences += 1
        self._total_seconds += time.perf_counter() - started

        order = np.argsort(probabilities)[::-1]
        top, runner_up = int(order[0]), int(order[1]) if len(order) > 1 else int(order[0])
        gesture = self.artifact.classes[top]
        probability = float(probabilities[top])
        margin = probability - float(probabilities[runner_up]) if runner_up != top else probability

        # MediaPipe's own detection score still matters: a confident classification
        # of a badly detected hand is a confident classification of noise.
        confidence = probability
        if self.blend_detection_score:
            confidence *= float(np.clip(hand.detection_score, 0.0, 1.0))

        pose = POSE_BY_NAME.get(gesture)
        tip = hand.points[INDEX_TIP]
        return GestureResult(
            gesture=gesture,
            confidence=round(float(np.clip(confidence, 0.0, 1.0)), 4),
            fingers=list(pose.fingers) if pose else [],
            hand_detected=True,
            pointer=(float(tip[0]), float(tip[1])),
            handedness=hand.handedness,
            source=SOURCE_PERSONALIZED,
            probabilities={
                name: round(float(value), 4)
                for name, value in zip(self.artifact.classes, probabilities)
                if value >= 0.005
            },
            model_version=self.artifact.model_version,
            margin=round(float(margin), 4),
        )

    def stats(self) -> dict:
        mean_ms = (self._total_seconds / self._inferences * 1000.0) if self._inferences else 0.0
        return {
            "source": self.source,
            "modelVersion": self.model_version,
            "runtime": self.artifact.runtime,
            "inferences": self._inferences,
            "meanInferenceMs": round(mean_ms, 4),
            "degraded": self.degraded,
            "classes": len(self.artifact.classes),
        }
