"""Temporal smoothing over the recognizer's per-frame output.

The recognizer - geometric or personalized - classifies each frame independently.
A hand held perfectly still still produces the occasional wrong frame, and two
poses in the library differ by exactly one bit:

    INDEX_UP         (0, 1, 0, 0, 0)   -> ANNOTATION_MODE  -> the pen
    INDEX_MIDDLE_UP  (0, 1, 1, 0, 0)   -> VIRTUAL_POINTER  -> the pointer

A middle finger that dips slightly below the extension threshold for two frames
turns the pointer into the pen, and the pen used to mean a blind `Ctrl+P`, which
outside a slideshow is the **Print dialog**. That is how holding up two fingers
opened Print.

This stabilizer is the frame-level half of that fix (the controller's slideshow
guard is the other half). It takes a plurality vote over a short sliding window,
so a pose has to be genuinely present - not merely present in the single frame the
camera happened to catch mid-transition - before it reaches the command mapper.

It does not replace, bypass or second-guess the model. It consumes the model's
predictions and reports which one the model has actually been making. When no pose
commands a clear plurality it reports UNKNOWN, which is the neutral state the
debouncer already knows how to handle - the same contract the intent gate uses.
"""

from __future__ import annotations

from collections import deque

from computer_vision.gesture_recognition.gesture_recognizer import GestureResult
from computer_vision.gesture_recognition.poses import NO_HAND, UNKNOWN

DEFAULT_WINDOW = 5


class GestureStabilizer:
    """Plurality vote over the last `window` frames.

    `min_votes` defaults to a simple majority of the window, so with the default
    window of 5 a pose needs 3 of the last 5 frames. One or two stray frames can
    never change what the command mapper sees.
    """

    def __init__(self, window: int = DEFAULT_WINDOW, min_votes: int | None = None,
                 enabled: bool = True):
        self.window = max(1, int(window))
        self.min_votes = max(1, min_votes if min_votes is not None else (self.window // 2) + 1)
        # A window of 1 is "no smoothing"; min_votes can never exceed the window
        # or nothing would ever be reported.
        self.min_votes = min(self.min_votes, self.window)
        self.enabled = bool(enabled)
        self._frames: deque[GestureResult] = deque(maxlen=self.window)

    def reset(self) -> None:
        self._frames.clear()

    @property
    def filled(self) -> bool:
        return len(self._frames) >= self.window

    def update(self, result: GestureResult) -> GestureResult:
        """Return the stabilised view of the stream ending at `result`.

        The returned result carries the *voted* gesture and a confidence averaged
        over the frames that voted for it, but the *live* pointer position, so the
        on-screen pointer keeps tracking the fingertip at full frame rate while the
        classification is being smoothed.
        """
        if not self.enabled or self.window == 1:
            return result

        self._frames.append(result)

        counts: dict[str, int] = {}
        for frame in self._frames:
            counts[frame.gesture] = counts.get(frame.gesture, 0) + 1

        # Plurality, with ties broken towards the most recent frame's gesture so a
        # 2-2 split does not oscillate between two poses.
        winner = max(counts, key=lambda name: (counts[name], name == result.gesture))
        votes = counts[winner]

        # The full vote is required even while the window is filling. Relaxing it
        # for the first few frames would let the very first frame of a session
        # through unsmoothed, which is exactly the stray frame this class exists
        # to absorb. The cost is a couple of frames of latency at start-up.
        if votes < self.min_votes:
            return self._as(result, UNKNOWN, result.confidence)

        if winner == result.gesture:
            confidence = result.confidence
        else:
            supporting = [f.confidence for f in self._frames if f.gesture == winner]
            confidence = sum(supporting) / len(supporting) if supporting else 0.0

        return self._as(result, winner, confidence)

    def _as(self, result: GestureResult, gesture: str, confidence: float) -> GestureResult:
        """A copy of `result` relabelled, keeping the live pointer and metadata."""
        if gesture == result.gesture and confidence == result.confidence:
            return result

        if gesture == NO_HAND:
            return GestureResult(
                gesture=NO_HAND, confidence=0.0, hand_detected=False,
                source=result.source, model_version=result.model_version,
            )

        # `hand_detected` follows the *voted* pose when there is one: a single
        # dropped frame in the middle of a hold must not be reported as the hand
        # leaving, or the command mapper stops seeing the pose. UNKNOWN is not a
        # vote for anything, so there it defers to the live frame - otherwise the
        # warm-up frames of a session claim a hand while the frame is empty.
        hand_detected = result.hand_detected if gesture == UNKNOWN else True

        return GestureResult(
            gesture=gesture,
            confidence=round(float(confidence), 4),
            fingers=result.fingers,
            hand_detected=hand_detected,
            # The last fingertip we actually saw, so a dropped frame does not blank
            # the pointer for the frame it was dropped on.
            pointer=result.pointer if result.pointer is not None else self._last_pointer(),
            handedness=result.handedness,
            timestamp=result.timestamp,
            source=result.source,
            probabilities=result.probabilities,
            model_version=result.model_version,
            margin=result.margin,
        )

    def _last_pointer(self) -> tuple[float, float] | None:
        for frame in reversed(self._frames):
            if frame.pointer is not None:
                return frame.pointer
        return None

    def describe(self) -> dict:
        return {"enabled": self.enabled, "window": self.window, "minVotes": self.min_votes}
