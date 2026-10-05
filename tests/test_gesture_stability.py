"""Gesture stability: the bugs from fixes.md §1 and §2, and their fixes.

Reported symptoms, all traced to two places:

  * holding a gesture walked through several slides
  * the displayed action and slide number changed continuously / randomly
  * INDEX+MIDDLE (the pointer) sometimes acted as INDEX (the pen)

Root cause A - the debouncer's neutral latch was re-armed by a *single* neutral
frame, so one dropped frame in the middle of a hold unlocked a repeat.

Root cause B - nothing smoothed the recognizer's per-frame output, so a single
misclassified frame reached the command mapper, and the raw per-frame prediction
was what the UI displayed.

Every test below fails against the code as it was.
"""

import random

import pytest

from computer_vision.command_mapping.gesture_mapper import (
    ANNOTATION_MODE,
    DEFAULT_PREFERENCES,
    NEXT_SLIDE,
    VIRTUAL_POINTER,
    GestureMapper,
)
from computer_vision.gesture_recognition.debouncer import (
    STATUS_COOLDOWN,
    STATUS_HOLDING,
    STATUS_LOW_CONFIDENCE,
    STATUS_WAIT_NEUTRAL,
    GestureDebouncer,
)
from computer_vision.gesture_recognition.gesture_recognizer import (
    GestureRecognizer,
    GestureResult,
)
from computer_vision.gesture_recognition.poses import NO_HAND, UNKNOWN
from computer_vision.gesture_recognition.stabilizer import GestureStabilizer
from computer_vision.hand_detection.hand_detector import HandLandmarks
from computer_vision.ml.synthetic import generate_recording


def _result(gesture: str, confidence: float = 0.95, hand: bool = True) -> GestureResult:
    return GestureResult(
        gesture=gesture,
        confidence=confidence,
        hand_detected=hand,
        pointer=(0.5, 0.5) if hand else None,
    )


def _hand(label: str, rng: random.Random, score: float = 0.95):
    frame = generate_recording(label, frames=1, rng=rng, aspect=4 / 3)[0]
    return HandLandmarks(points=frame, handedness="Right", detection_score=score)


# ============================================================ THE REPEAT BUG ==
def test_a_single_dropped_frame_cannot_repeat_a_held_gesture():
    """The reported bug, reduced to its smallest reproduction.

    A presenter holds Next Slide. MediaPipe loses the hand for exactly one frame -
    which happens constantly - and then finds it again. That must not count as
    "the hand went away and came back", because if it does the command fires again
    and the deck advances on its own.
    """
    debouncer = GestureDebouncer(required_frames=3, cooldown_ms=0)

    for _ in range(3):
        decision = debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7)
    assert decision.fire, "the first deliberate gesture must fire"

    fires = 0
    for frame in range(120):        # ~4 seconds of holding the gesture
        gesture = NO_HAND if frame % 17 == 0 else "PINKY_UP"   # one dropped frame in 17
        command = None if gesture == NO_HAND else NEXT_SLIDE
        confidence = 0.0 if gesture == NO_HAND else 0.95
        if debouncer.submit(gesture, command, confidence, 0.7).fire:
            fires += 1

    assert fires == 0, f"a held gesture fired {fires} extra times from dropped frames"


def test_an_ambiguous_frame_cannot_repeat_a_held_gesture():
    """Same bug through the other door: the intent gate neutralises ambiguous
    frames to UNKNOWN, and UNKNOWN used to unlock a repeat just as NO_HAND did."""
    debouncer = GestureDebouncer(required_frames=3, cooldown_ms=0)

    for _ in range(3):
        debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7)

    fires = 0
    for frame in range(120):
        gesture = UNKNOWN if frame % 11 == 0 else "PINKY_UP"
        command = None if gesture == UNKNOWN else NEXT_SLIDE
        if debouncer.submit(gesture, command, 0.95, 0.7).fire:
            fires += 1

    assert fires == 0


def test_an_unmapped_pose_flickering_in_cannot_repeat_a_held_gesture():
    """And the third: a well-formed pose bound to nothing is also neutral."""
    debouncer = GestureDebouncer(required_frames=3, cooldown_ms=0)
    for _ in range(3):
        debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7)

    fires = 0
    for frame in range(120):
        if frame % 9 == 0:
            fires += bool(debouncer.submit("OPEN_PALM", None, 0.95, 0.7).fire)
        else:
            fires += bool(debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7).fire)
    assert fires == 0


def test_holding_a_gesture_for_ten_seconds_fires_exactly_once():
    """The headline requirement: hold Next Slide, get one slide."""
    debouncer = GestureDebouncer(required_frames=6, cooldown_ms=900)
    fires = sum(
        bool(debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7).fire)
        for _ in range(300)          # 10 s at 30 fps
    )
    assert fires == 1


def test_a_deliberate_release_still_lets_the_command_repeat():
    """The fix must not make the feature unusable: lowering the hand and raising
    it again is how a presenter advances two slides, and it has to work."""
    debouncer = GestureDebouncer(required_frames=3, cooldown_ms=0)
    fires = 0
    for _ in range(4):
        for _ in range(6):                                   # hold
            fires += bool(debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7).fire)
        for _ in range(6):                                   # lower the hand
            debouncer.submit(NO_HAND, None, 0.0, 0.7)
    assert fires == 4


def test_release_progress_reports_why_a_held_gesture_is_not_firing():
    """The UI needs to distinguish 'ignored' from 'waiting for you to let go'."""
    debouncer = GestureDebouncer(required_frames=2, cooldown_ms=0, release_frames=4)
    for _ in range(2):
        debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7)

    # Firing resets the streak, so the hold has to be re-satisfied before the
    # debouncer can say *why* it is not firing again.
    assert debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7).status == STATUS_HOLDING
    decision = debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7)
    assert decision.status == STATUS_WAIT_NEUTRAL
    assert decision.release_progress == 0.0

    progress = [debouncer.observe_neutral().release_progress for _ in range(4)]
    assert progress == [0.25, 0.5, 0.75, 1.0]


def test_alternating_neutral_and_gesture_frames_never_accumulate_a_release():
    """Neutrality has to be *held*. A hand flickering in and out at 15 Hz is not a
    presenter putting their hand down four times a second."""
    debouncer = GestureDebouncer(required_frames=2, cooldown_ms=0, release_frames=3)
    for _ in range(2):
        debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7)

    fires = 0
    for frame in range(200):
        if frame % 2 == 0:
            fires += bool(debouncer.submit(NO_HAND, None, 0.0, 0.7).fire)
        else:
            fires += bool(debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7).fire)
    assert fires == 0


def test_the_cooldown_still_bounds_the_rate_of_distinct_commands():
    """Two *different* commands are not gated by the neutral rule, so the cooldown
    is the only thing standing between them and a burst."""
    debouncer = GestureDebouncer(required_frames=1, cooldown_ms=10_000)
    assert debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7).fire
    second = debouncer.submit("THUMB_UP", "PREVIOUS_SLIDE", 0.95, 0.7)
    assert not second.fire and second.status == STATUS_COOLDOWN


def test_low_confidence_frames_are_not_neutral():
    """A blurred frame mid-gesture must not unlock a repeat - it is 'unclear',
    not 'the hand went away'."""
    debouncer = GestureDebouncer(required_frames=2, cooldown_ms=0, release_frames=2)
    for _ in range(2):
        debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7)

    for _ in range(20):
        blurred = debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.10, 0.7)
        assert blurred.status == STATUS_LOW_CONFIDENCE

    for _ in range(2):
        decision = debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7)
    assert not decision.fire and decision.status == STATUS_WAIT_NEUTRAL


def test_reset_returns_the_debouncer_to_a_fireable_state():
    debouncer = GestureDebouncer(required_frames=2, cooldown_ms=0)
    for _ in range(2):
        debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7)
    debouncer.reset()
    assert debouncer.held_command is None
    for _ in range(2):
        decision = debouncer.submit("PINKY_UP", NEXT_SLIDE, 0.95, 0.7)
    assert decision.fire


def test_the_neutral_hold_rule_is_what_stops_the_deck_walking():
    """The bug and the fix, measured on identical input.

    `release_frames=1` is the old behaviour exactly: one neutral frame re-arms
    the repeat. Same frames, same clock, same everything else - only the rule
    that was added differs. This is the regression that matters, so it is
    asserted as a number rather than a vibe.
    """
    def fires(release_frames: int) -> int:
        now = [1_000_000.0]
        debouncer = GestureDebouncer(
            required_frames=6, cooldown_ms=900, clock=lambda: now[0],
        )
        # Assigned rather than passed: MIN_RELEASE_FRAMES deliberately refuses to
        # construct the buggy configuration, and reproducing the bug means going
        # around that floor.
        debouncer.release_frames = release_frames
        count = 0
        for frame in range(900):        # 30 s of holding one gesture at 30 fps
            now[0] += 1 / 30.0
            # One dropped frame in twelve: an ordinary webcam, not a bad one.
            dropped = frame % 12 == 0
            decision = debouncer.submit(
                NO_HAND if dropped else "PINKY_UP",
                None if dropped else NEXT_SLIDE,
                0.0 if dropped else 0.95,
                0.7,
            )
            count += bool(decision.fire)
        return count

    before = fires(release_frames=1)
    after = fires(release_frames=3)

    assert before > 20, (
        "the old single-frame unlock should walk the deck; if it does not, this "
        "test is no longer reproducing the reported bug"
    )
    assert after == 1, f"the held gesture still fired {after} times"


# ============================================================== STABILISER ====
def test_one_stray_frame_never_reaches_the_command_mapper():
    """fixes.md §2: INDEX+MIDDLE must not act as INDEX.

    They differ by one bit - a middle finger that dips below the extension
    threshold for a frame - and INDEX used to mean a blind Ctrl+P.
    """
    stabilizer = GestureStabilizer(window=5)
    mapper = GestureMapper(DEFAULT_PREFERENCES)

    commands = []
    for frame in range(100):
        gesture = "INDEX_UP" if frame % 13 == 0 else "INDEX_MIDDLE_UP"
        stable = stabilizer.update(_result(gesture))
        commands.append(mapper.map(stable.gesture))

    assert ANNOTATION_MODE not in commands, "a stray frame reached the pen command"
    assert commands[-1] == VIRTUAL_POINTER


def test_two_stray_frames_in_five_still_never_reach_the_mapper():
    """The plurality threshold is a simple majority, so 2 of 5 is not enough."""
    stabilizer = GestureStabilizer(window=5)
    mapper = GestureMapper(DEFAULT_PREFERENCES)
    seen = set()
    for frame in range(100):
        gesture = "INDEX_UP" if frame % 5 in (0, 1) else "INDEX_MIDDLE_UP"
        seen.add(mapper.map(stabilizer.update(_result(gesture)).gesture))
    assert ANNOTATION_MODE not in seen


def test_a_genuine_pose_change_is_followed_within_the_window():
    """Smoothing must not mean the pose is ignored: a real switch has to land."""
    stabilizer = GestureStabilizer(window=5)
    for _ in range(10):
        stabilizer.update(_result("INDEX_MIDDLE_UP"))

    outputs = [stabilizer.update(_result("INDEX_UP")).gesture for _ in range(5)]
    assert outputs[-1] == "INDEX_UP"
    assert "INDEX_MIDDLE_UP" not in outputs[3:], "the old pose lingered too long"


def test_an_evenly_split_window_reports_unknown_rather_than_guessing():
    """With no plurality there is no answer, and UNKNOWN is the neutral state the
    debouncer already handles - the same contract the intent gate uses."""
    stabilizer = GestureStabilizer(window=4, min_votes=3)
    stabilizer.update(_result("INDEX_UP"))
    stabilizer.update(_result("INDEX_MIDDLE_UP"))
    stabilizer.update(_result("INDEX_UP"))
    assert stabilizer.update(_result("INDEX_MIDDLE_UP")).gesture == UNKNOWN


def test_the_stabilizer_keeps_the_live_pointer_position():
    """Smoothing a *label* must not add lag to the pointer the presenter is
    aiming with."""
    stabilizer = GestureStabilizer(window=5)
    for _ in range(5):
        stabilizer.update(_result("INDEX_MIDDLE_UP"))

    live = GestureResult(gesture="INDEX_UP", confidence=0.9, hand_detected=True,
                         pointer=(0.123, 0.456))
    out = stabilizer.update(live)
    assert out.gesture == "INDEX_MIDDLE_UP"     # smoothed classification
    assert out.pointer == (0.123, 0.456)        # live position


def test_a_voted_no_hand_reports_no_hand():
    stabilizer = GestureStabilizer(window=3)
    for _ in range(3):
        result = stabilizer.update(_result(NO_HAND, 0.0, hand=False))
    assert result.gesture == NO_HAND and result.hand_detected is False


def test_a_single_lost_frame_does_not_report_no_hand():
    """One dropped frame is not the hand leaving - that is the whole point."""
    stabilizer = GestureStabilizer(window=5)
    for _ in range(5):
        stabilizer.update(_result("PINKY_UP"))
    out = stabilizer.update(_result(NO_HAND, 0.0, hand=False))
    assert out.gesture == "PINKY_UP" and out.hand_detected is True


def test_a_window_of_one_is_a_passthrough():
    stabilizer = GestureStabilizer(window=1)
    result = _result("INDEX_UP")
    assert stabilizer.update(result) is result


def test_a_disabled_stabilizer_is_a_passthrough():
    stabilizer = GestureStabilizer(window=5, enabled=False)
    result = _result("INDEX_UP")
    assert stabilizer.update(result) is result


def test_reset_clears_the_vote_history():
    stabilizer = GestureStabilizer(window=5)
    for _ in range(5):
        stabilizer.update(_result("INDEX_MIDDLE_UP"))
    stabilizer.reset()
    assert not stabilizer.filled

    # No pose is reported until it has won a full vote: the first frame after a
    # reset is UNKNOWN, not whatever that single frame happened to be.
    assert stabilizer.update(_result("INDEX_UP")).gesture == UNKNOWN
    outputs = [stabilizer.update(_result("INDEX_UP")).gesture for _ in range(3)]
    assert outputs[-1] == "INDEX_UP"


def test_the_very_first_frame_of_a_session_cannot_fire_a_command():
    """The warm-up must not be a hole in the smoothing.

    Relaxing the vote while the window fills would let frame 0 through
    unsmoothed - and frame 0 of a session, with the hand still moving into place,
    is one of the least reliable frames there is.
    """
    stabilizer = GestureStabilizer(window=5)
    mapper = GestureMapper(DEFAULT_PREFERENCES)
    first = stabilizer.update(_result("INDEX_UP"))
    assert first.gesture == UNKNOWN
    assert mapper.map(first.gesture) is None


def test_min_votes_can_never_exceed_the_window():
    """Otherwise nothing would ever be reported and the engine would go silent."""
    stabilizer = GestureStabilizer(window=3, min_votes=99)
    assert stabilizer.min_votes == 3


# ================================================= THE TWO LAYERS TOGETHER ====
def test_pointer_gesture_held_never_produces_the_pen_command():
    """The end-to-end statement of fixes.md §2, at the frame level.

    Two fingers held up for ten seconds, with realistic per-frame noise, must
    produce the pointer and only the pointer.
    """
    stabilizer = GestureStabilizer(window=5)
    debouncer = GestureDebouncer(required_frames=6, cooldown_ms=900)
    mapper = GestureMapper(DEFAULT_PREFERENCES)
    rng = random.Random(7)

    fired = []
    for _ in range(300):
        # 10% of frames misread as one finger, 5% lost entirely: worse than a
        # real camera, and still not enough to reach the pen.
        roll = rng.random()
        if roll < 0.05:
            gesture, confidence = NO_HAND, 0.0
        elif roll < 0.15:
            gesture, confidence = "INDEX_UP", 0.9
        else:
            gesture, confidence = "INDEX_MIDDLE_UP", 0.95

        stable = stabilizer.update(_result(gesture, confidence, gesture != NO_HAND))
        command = mapper.map(stable.gesture) if stable.hand_detected else None
        decision = debouncer.submit(stable.gesture, command, stable.confidence, 0.7)
        if decision.fire:
            fired.append(decision.command)

    assert ANNOTATION_MODE not in fired, f"the pen fired from noise: {fired}"
    assert fired == [VIRTUAL_POINTER], f"expected one pointer toggle, got {fired}"


def test_slide_number_is_stable_while_a_hand_is_simply_present(dispatcher):
    """fixes.md §1: 'slide numbers do not randomly/continuously change'.

    A presenter holding one gesture, with noise, for twenty seconds moves the
    deck by exactly one slide.
    """
    stabilizer = GestureStabilizer(window=5)
    debouncer = GestureDebouncer(required_frames=6, cooldown_ms=900)
    mapper = GestureMapper(DEFAULT_PREFERENCES)
    rng = random.Random(11)

    for _ in range(600):
        roll = rng.random()
        if roll < 0.08:
            gesture, confidence = NO_HAND, 0.0
        elif roll < 0.14:
            gesture, confidence = UNKNOWN, 0.5
        else:
            gesture, confidence = "PINKY_UP", 0.95

        stable = stabilizer.update(_result(gesture, confidence, gesture != NO_HAND))
        command = mapper.map(stable.gesture) if stable.hand_detected else None
        decision = debouncer.submit(stable.gesture, command, stable.confidence, 0.7)
        if decision.fire and decision.command:
            dispatcher.execute(decision.command)

    assert dispatcher.current_slide == 2, (
        f"the deck moved to slide {dispatcher.current_slide} from one held gesture"
    )


def test_real_landmarks_through_the_stabilised_pipeline_stay_on_one_command(dispatcher):
    """The same guarantee with real geometry rather than hand-written labels."""
    recognizer = GestureRecognizer()
    stabilizer = GestureStabilizer(window=5)
    debouncer = GestureDebouncer(required_frames=4, cooldown_ms=900)
    mapper = GestureMapper(DEFAULT_PREFERENCES)
    rng = random.Random(23)

    fired = []
    for _ in range(200):
        result = recognizer.recognize(_hand("INDEX_MIDDLE_UP", rng), 4 / 3)
        stable = stabilizer.update(result)
        command = mapper.map(stable.gesture) if stable.hand_detected else None
        decision = debouncer.submit(stable.gesture, command, stable.confidence, 0.30)
        if decision.fire and decision.command:
            fired.append(decision.command)

    assert ANNOTATION_MODE not in fired
    assert len(fired) <= 1, f"a held two-finger pose fired {len(fired)} commands"


@pytest.mark.parametrize("pose", ["PINKY_UP", "THUMB_UP", "INDEX_UP", "INDEX_MIDDLE_UP",
                                  "THREE_FINGERS_UP"])
def test_every_bound_pose_held_fires_at_most_once(pose):
    """No pose in the library repeats when held - not just the ones reported."""
    stabilizer = GestureStabilizer(window=5)
    debouncer = GestureDebouncer(required_frames=6, cooldown_ms=900)
    mapper = GestureMapper(DEFAULT_PREFERENCES)

    fired = 0
    for frame in range(300):
        gesture = NO_HAND if frame % 19 == 0 else pose
        stable = stabilizer.update(_result(gesture, 0.95, gesture != NO_HAND))
        command = mapper.map(stable.gesture) if stable.hand_detected else None
        if debouncer.submit(stable.gesture, command, stable.confidence, 0.7).fire:
            fired += 1
    assert fired == 1, f"holding {pose} fired {fired} times"


# ============================ REGRESSIONS FOUND IN REVIEW OF THIS CHANGE SET ==
def test_a_realistic_mediapipe_dropout_cannot_repeat_a_held_gesture():
    """Found in review: the first release rule was half the hold requirement,
    which the stabilizer's own lag ate almost entirely.

    The stabilizer needs a few frames to swing over to NO_HAND, so N raw dropped
    frames already produce close to N stabilized neutral frames. With a 3-frame
    release, a 100 ms dropout (3 frames at 30 fps) mid-hold still unlocked the
    repeat and advanced a second slide.
    """
    from computer_vision.engine import EngineConfig, GestureEngine

    for dropout in range(1, 6):          # 33 ms to 166 ms of lost hand
        now = [1_000_000.0]
        engine = GestureEngine(EngineConfig(
            preferences=DEFAULT_PREFERENCES, confidence_threshold=0.7,
            debounce_frames=6, cooldown_ms=900,
        ))
        engine.debouncer.clock = lambda: now[0]

        fired = []
        for frame in range(300):
            now[0] += 1 / 30.0
            lost = 60 <= frame < 60 + dropout
            _stable, _reason, decision = engine.decide(_result(
                NO_HAND if lost else "PINKY_UP", 0.0 if lost else 0.95, not lost,
            ))
            if decision.fire:
                fired.append(round(now[0] - 1_000_000.0, 2))

        assert len(fired) == 1, (
            f"a {dropout}-frame dropout ({dropout * 33} ms) fired {len(fired)} "
            f"commands at {fired}"
        )


def test_a_genuine_release_still_works_at_the_wider_margin():
    """The wider release must not make the deck feel unresponsive: half a second
    with the hand down is a normal pause between two slides."""
    from computer_vision.engine import EngineConfig, GestureEngine

    now = [1_000_000.0]
    engine = GestureEngine(EngineConfig(
        preferences=DEFAULT_PREFERENCES, confidence_threshold=0.7,
        debounce_frames=6, cooldown_ms=900,
    ))
    engine.debouncer.clock = lambda: now[0]

    fired = 0
    for _ in range(5):
        for _ in range(12):                  # hold, ~0.4 s
            now[0] += 1 / 30.0
            _s, _r, decision = engine.decide(_result("PINKY_UP"))
            fired += bool(decision.fire)
        for _ in range(15):                  # lower the hand, 0.5 s
            now[0] += 1 / 30.0
            engine.decide(_result(NO_HAND, 0.0, False))

    assert fired == 5, f"five deliberate gestures produced {fired} commands"


def test_the_stabilizer_does_not_claim_a_hand_during_warm_up():
    """Found in review: relabelling a NO_HAND frame to UNKNOWN during warm-up set
    hand_detected=True, so the UI reported a hand over an empty frame."""
    stabilizer = GestureStabilizer(window=5)
    first = stabilizer.update(_result(NO_HAND, 0.0, hand=False))
    assert first.gesture == UNKNOWN
    assert first.hand_detected is False

    # ...while a dropped frame amid a real hand still reports the hand.
    stabilizer.reset()
    for _ in range(5):
        stabilizer.update(_result("PINKY_UP"))
    out = stabilizer.update(_result(NO_HAND, 0.0, hand=False))
    assert out.gesture == "PINKY_UP" and out.hand_detected is True
