"""Temporal filter standing between recognition and command dispatch.

Three independent conditions must all hold before a command may fire (see
SAFETY RULES): confidence gate, temporal persistence, and a neutral state
between two repeats of the same command. False positives - a slide jumping
three steps from one flick of the hand - are the worst failure mode of a
gesture-driven presenter tool, so the filter is deliberately strict.
"""

import time
from dataclasses import dataclass

from computer_vision.gesture_recognition.poses import NO_HAND, UNKNOWN

# Telemetry statuses surfaced to the UI.
STATUS_IDLE = "IDLE"                    # no hand in frame
STATUS_LOW_CONFIDENCE = "LOW_CONFIDENCE"
STATUS_UNMAPPED = "UNMAPPED"            # recognised pose, but not bound to a command
STATUS_HOLDING = "HOLDING"              # persistence not satisfied yet
STATUS_WAIT_NEUTRAL = "WAIT_NEUTRAL"    # needs a neutral pose before repeating
STATUS_COOLDOWN = "COOLDOWN"
STATUS_EXECUTED = "EXECUTED"


@dataclass
class Decision:
    fire: bool
    status: str
    command: str | None = None
    progress: float = 0.0   # 0..1 hold progress, drives the UI ring


class GestureDebouncer:
    def __init__(self, required_frames: int = 6, cooldown_ms: int = 900):
        self.required_frames = max(1, required_frames)
        self.cooldown_ms = max(0, cooldown_ms)
        self._candidate: str | None = None
        self._streak = 0
        self._last_command: str | None = None
        self._last_fired_at = 0.0
        self._neutral_since_last_fire = True

    def reset(self) -> None:
        self._candidate = None
        self._streak = 0
        self._last_command = None
        self._last_fired_at = 0.0
        self._neutral_since_last_fire = True

    def observe_neutral(self, status: str = STATUS_IDLE) -> Decision:
        """Called for every frame that carries no command intent."""
        self._candidate = None
        self._streak = 0
        self._neutral_since_last_fire = True
        return Decision(fire=False, status=status, progress=0.0)

    def submit(self, gesture: str, command: str | None, confidence: float, threshold: float) -> Decision:
        now = time.time()

        if gesture in (NO_HAND, UNKNOWN):
            return self.observe_neutral(STATUS_IDLE if gesture == NO_HAND else STATUS_LOW_CONFIDENCE)

        if confidence < threshold:
            # Low confidence never counts toward a streak, but it is not a neutral
            # state either - a blurred frame mid-gesture must not unlock a repeat.
            self._candidate = None
            self._streak = 0
            return Decision(fire=False, status=STATUS_LOW_CONFIDENCE, progress=0.0)

        if command is None:
            # A well-formed pose that is not bound to any command IS the neutral state.
            return self.observe_neutral(STATUS_UNMAPPED)

        if command != self._candidate:
            self._candidate = command
            self._streak = 1
        else:
            self._streak += 1

        progress = min(1.0, self._streak / self.required_frames)

        if self._streak < self.required_frames:
            return Decision(fire=False, status=STATUS_HOLDING, command=command, progress=progress)

        if command == self._last_command and not self._neutral_since_last_fire:
            return Decision(fire=False, status=STATUS_WAIT_NEUTRAL, command=command, progress=1.0)

        if (now - self._last_fired_at) * 1000.0 < self.cooldown_ms:
            return Decision(fire=False, status=STATUS_COOLDOWN, command=command, progress=1.0)

        self._last_command = command
        self._last_fired_at = now
        self._neutral_since_last_fire = False
        self._streak = 0
        self._candidate = None
        return Decision(fire=True, status=STATUS_EXECUTED, command=command, progress=1.0)
