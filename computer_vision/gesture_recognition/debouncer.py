"""Temporal filter standing between recognition and command dispatch.

Four independent conditions must all hold before a command may fire (see
SAFETY RULES): confidence gate, temporal persistence, a *sustained* neutral state
between two repeats of the same command, and a cooldown. False positives - a slide
jumping three steps from one flick of the hand - are the worst failure mode of a
gesture-driven presenter tool, so the filter is deliberately strict.

## The repeat bug this filter used to have

The neutral latch used to be re-armed by a *single* neutral frame. That sounds
harmless and is not, because a held gesture does not produce a clean run of
identical frames: MediaPipe occasionally loses the hand for one frame, the
personalized model occasionally emits a runner-up class, and the intent gate
deliberately neutralises ambiguous frames. Any one of those unlocked the repeat,
the streak rebuilt in a fifth of a second, and the command fired again - so
holding Next Slide walked through the deck and the slide number appeared to change
on its own.

Neutrality is now a state that has to be held, exactly like the gesture itself:
`release_frames` consecutive neutral observations before the same command may
repeat. One dropped frame in the middle of a hold no longer counts as "the hand
went away and came back".
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

# The floor on how long neutrality must be held before the same command may fire
# again. Three frames, not one: one frame is what caused the repeat bug, and two
# leaves no margin at all once the stabilizer's own lag is accounted for.
MIN_RELEASE_FRAMES = 3


@dataclass
class Decision:
    fire: bool
    status: str
    command: str | None = None
    progress: float = 0.0   # 0..1 hold progress, drives the UI ring
    # 0..1 progress towards re-arming a repeat, so the UI can show *why* a held
    # gesture is not firing again instead of looking broken.
    release_progress: float = 1.0


class GestureDebouncer:
    def __init__(self, required_frames: int = 6, cooldown_ms: int = 900,
                 release_frames: int | None = None, clock=time.time):
        self.required_frames = max(1, required_frames)
        self.cooldown_ms = max(0, cooldown_ms)
        # Injectable so the cooldown - the one rule here that depends on wall
        # time - can be driven deterministically instead of with sleeps.
        self.clock = clock
        # Defaults to the *full* hold requirement, deliberately symmetric: it
        # takes as long to release a gesture as to make one. Half of it is not
        # enough, because the stabilizer needs a few frames to swing over to
        # NO_HAND, so N raw dropped frames already produce close to N stabilized
        # neutral frames - a 100 ms MediaPipe dropout mid-hold would still have
        # unlocked a repeat. At 30 fps the default is ~200 ms, far below the time
        # a presenter takes to lower a hand and raise it again.
        self.release_frames = max(
            MIN_RELEASE_FRAMES,
            release_frames if release_frames is not None else self.required_frames,
        )
        self._candidate: str | None = None
        self._streak = 0
        self._last_command: str | None = None
        self._last_fired_at = 0.0
        self._neutral_streak = 0
        self._neutral_since_last_fire = True

    def reset(self) -> None:
        self._candidate = None
        self._streak = 0
        self._last_command = None
        self._last_fired_at = 0.0
        self._neutral_streak = 0
        self._neutral_since_last_fire = True

    # --- introspection -------------------------------------------------------
    @property
    def held_command(self) -> str | None:
        """The command currently being held towards a fire, if any."""
        return self._candidate

    @property
    def release_progress(self) -> float:
        if self._neutral_since_last_fire:
            return 1.0
        return min(1.0, self._neutral_streak / self.release_frames)

    def observe_neutral(self, status: str = STATUS_IDLE) -> Decision:
        """Called for every frame that carries no command intent.

        Neutrality accumulates rather than latching instantly. Only once it has
        been held for `release_frames` frames does it unlock a repeat of the last
        command - the fix for a held gesture firing over and over.
        """
        self._candidate = None
        self._streak = 0
        self._neutral_streak += 1
        if self._neutral_streak >= self.release_frames:
            self._neutral_since_last_fire = True
        return Decision(
            fire=False, status=status, progress=0.0,
            release_progress=self.release_progress,
        )

    def submit(self, gesture: str, command: str | None, confidence: float, threshold: float) -> Decision:
        now = self.clock()

        if gesture in (NO_HAND, UNKNOWN):
            return self.observe_neutral(STATUS_IDLE if gesture == NO_HAND else STATUS_LOW_CONFIDENCE)

        if confidence < threshold:
            # Low confidence never counts toward a streak, but it is not a neutral
            # state either - a blurred frame mid-gesture must not unlock a repeat.
            self._candidate = None
            self._streak = 0
            return Decision(fire=False, status=STATUS_LOW_CONFIDENCE, progress=0.0,
                            release_progress=self.release_progress)

        if command is None:
            # A well-formed pose that is not bound to any command IS the neutral state.
            return self.observe_neutral(STATUS_UNMAPPED)

        # A recognised, mapped, confident pose is the opposite of neutral: any
        # partial neutral run is abandoned. Without this, alternating
        # neutral/gesture frames would accumulate a release that was never held.
        self._neutral_streak = 0

        if command != self._candidate:
            self._candidate = command
            self._streak = 1
        else:
            self._streak += 1

        progress = min(1.0, self._streak / self.required_frames)

        if self._streak < self.required_frames:
            return Decision(fire=False, status=STATUS_HOLDING, command=command,
                            progress=progress, release_progress=self.release_progress)

        if command == self._last_command and not self._neutral_since_last_fire:
            return Decision(fire=False, status=STATUS_WAIT_NEUTRAL, command=command,
                            progress=1.0, release_progress=self.release_progress)

        if (now - self._last_fired_at) * 1000.0 < self.cooldown_ms:
            return Decision(fire=False, status=STATUS_COOLDOWN, command=command,
                            progress=1.0, release_progress=self.release_progress)

        self._last_command = command
        self._last_fired_at = now
        self._neutral_since_last_fire = False
        self._neutral_streak = 0
        self._streak = 0
        self._candidate = None
        return Decision(fire=True, status=STATUS_EXECUTED, command=command,
                        progress=1.0, release_progress=0.0)
