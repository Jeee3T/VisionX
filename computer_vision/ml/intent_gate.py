"""The intent gate: the last check before a recognised pose becomes a command.

RULE-BASED, not trained. It exists because a probabilistic recognizer exposes
something the geometric one cannot: how close the runner-up class was. A frame
the model calls INDEX_UP at 0.51 with INDEX_MIDDLE_UP at 0.47 is not a confident
INDEX_UP, it is an ambiguous hand - and firing on it is the failure mode that
matters most, because a wrong command mid-talk is worse than a missed one.

The gate never *creates* a command. It only converts an unsafe frame into the
neutral UNKNOWN state, which the existing debouncer already knows how to handle.
"""

from __future__ import annotations

from dataclasses import dataclass

from computer_vision.gesture_recognition.gesture_recognizer import GestureResult
from computer_vision.gesture_recognition.poses import UNKNOWN

REJECT_NULL_CLASS = "null_class"
REJECT_AMBIGUOUS = "ambiguous"


@dataclass
class GestureIntentGate:
    """Reject ambiguous frames. A margin of 0 disables the ambiguity check."""

    min_margin: float = 0.15
    enabled: bool = True

    def apply(self, result: GestureResult) -> tuple[GestureResult, str | None]:
        """Return (possibly neutralised result, rejection reason or None)."""
        if not self.enabled or not result.hand_detected:
            return result, None
        if result.gesture == UNKNOWN:
            return result, REJECT_NULL_CLASS
        if result.margin is None or self.min_margin <= 0:
            return result, None
        if result.margin >= self.min_margin:
            return result, None

        neutral = GestureResult(
            gesture=UNKNOWN,
            confidence=result.confidence,
            fingers=result.fingers,
            hand_detected=True,
            pointer=result.pointer,
            handedness=result.handedness,
            source=result.source,
            probabilities=result.probabilities,
            model_version=result.model_version,
            margin=result.margin,
        )
        return neutral, REJECT_AMBIGUOUS

    def describe(self) -> dict:
        return {"enabled": self.enabled, "minMargin": self.min_margin}
