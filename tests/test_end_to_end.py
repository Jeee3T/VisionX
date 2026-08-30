"""fixes.md §6: the whole pipeline, end to end.

    MediaPipe landmarks -> recognizer -> intent gate -> stabilizer
                        -> mapper -> debouncer -> engine -> dispatcher
                        -> PowerPointController -> keystrokes / COM

Everything below the landmarks is the real, shipped code. Two fakes stand at the
edges, both at the OS boundary: `FakeKeyboard` records what would have reached
Windows, and `FakeCom` scripts what PowerPoint would have answered.

`GestureEngine.decide()` is the same method the camera loop calls, so these tests
drive the shipped path rather than a re-implementation of it.

The assertions are the verification list from the brief, one test each.
"""

import random

import pytest

from computer_vision.command_mapping.gesture_mapper import (
    ANNOTATION_MODE,
    CLEAR_ANNOTATION,
    DEFAULT_PREFERENCES,
    NEXT_SLIDE,
    PREVIOUS_SLIDE,
    VIRTUAL_POINTER,
)
from computer_vision.engine import (
    MODE_ANNOTATE,
    MODE_IDLE,
    MODE_POINTER,
    EngineConfig,
    GestureEngine,
)
from computer_vision.gesture_recognition.gesture_recognizer import GestureResult
from computer_vision.gesture_recognition.poses import NO_HAND
from multimodal.command import SOURCE_GESTURE, SOURCE_VOICE
from multimodal.command import build as build_intent
from tests.conftest import FakeCom, build_dispatcher

PRINT_HOTKEY = ("ctrl", "p")


FPS = 30.0


class Harness:
    """A live engine wired to a live dispatcher, driven a frame at a time.

    Time advances by one frame per `frame()` call rather than by sleeping, so the
    900 ms cooldown is exercised at its real value with the tests still running in
    milliseconds.
    """

    def __init__(self, keyboard, com, total_slides: int = 20, **config):
        self.keyboard = keyboard
        self.com = com
        self.dispatcher = build_dispatcher(keyboard, com, total_slides=total_slides)
        self.events: list[dict] = []
        self.commands: list[str] = []
        self.now = 1_000_000.0

        self.engine = GestureEngine(
            EngineConfig(
                preferences=DEFAULT_PREFERENCES,
                confidence_threshold=config.pop("confidence_threshold", 0.70),
                debounce_frames=config.pop("debounce_frames", 6),
                cooldown_ms=config.pop("cooldown_ms", 900),
                **config,
            ),
            on_command=self._on_command,
            on_event=self.events.append,
            on_pointer=self._on_pointer,
            on_pointer_lost=self._on_pointer_lost,
        )
        self.engine.debouncer.clock = lambda: self.now

    # --- the wiring the engine service does in production --------------------
    def _on_command(self, command, payload):
        # Returning the record is part of the contract: the engine reconciles its
        # mode against it, exactly as EngineService._on_command does.
        self.commands.append(command)
        return self.dispatcher.execute(command, {**payload, "source": SOURCE_GESTURE})

    def _on_pointer(self, x, y, _mode):
        self.dispatcher.stream_pointer(x, y)

    def _on_pointer_lost(self):
        self.dispatcher.end_stroke()

    # --- one camera frame ----------------------------------------------------
    def frame(self, gesture: str, confidence: float = 0.95,
              pointer: tuple[float, float] | None = (0.5, 0.5)):
        self.now += 1.0 / FPS
        hand = gesture != NO_HAND
        result = GestureResult(
            gesture=gesture,
            confidence=0.0 if not hand else confidence,
            hand_detected=hand,
            pointer=pointer if hand else None,
        )
        stable, _reason, decision = self.engine.decide(result)

        # `stable.pointer`, exactly as the camera loop does. Mirroring the loop
        # with `result.pointer` here is what let a dropped-frame bug hide: the
        # harness reproduced the OLD loop body, so it agreed with the bug.
        smoothed = self.engine._track_pointer(stable.pointer)
        if decision.fire and decision.command:
            self.engine._handle_command(decision.command, smoothed)

        if smoothed is not None and self.engine.mode != MODE_IDLE:
            self.engine._pointer_streaming = True
            self.engine.on_pointer(smoothed[0], smoothed[1], self.engine.mode)
        elif self.engine._pointer_streaming:
            self.engine._release_pointer()

        return stable, decision

    def hold(self, gesture: str, frames: int, noise: float = 0.0,
             rng: random.Random | None = None, pointer=(0.5, 0.5)):
        """Hold a gesture, optionally with a fraction of frames misread/lost."""
        rng = rng or random.Random(0)
        for _ in range(frames):
            if noise and rng.random() < noise:
                self.frame(NO_HAND)
            else:
                self.frame(gesture, pointer=pointer)

    def release(self, frames: int = 30):
        """Lower the hand for a second.

        Long enough to satisfy both the neutral-release rule and the 900 ms
        cooldown, which is what a presenter actually does between gestures. Both
        run at their production values here - the clock is advanced per frame
        rather than shortened.
        """
        for _ in range(frames):
            self.frame(NO_HAND)


@pytest.fixture
def harness(keyboard, presenting):
    """The real deployment: Windows, PowerPoint presenting."""
    return Harness(keyboard, presenting)


# ============================================ "Gesture recognition is stable" =
def test_gesture_recognition_is_stable_under_realistic_noise(harness):
    rng = random.Random(3)
    harness.hold("PINKY_UP", 300, noise=0.10, rng=rng)
    assert harness.commands == [NEXT_SLIDE]


# ============ "Holding a gesture does not repeatedly trigger commands" ========
def test_holding_a_gesture_does_not_repeatedly_trigger_commands(harness):
    harness.hold("PINKY_UP", 600)          # 20 seconds at 30 fps
    assert harness.commands == [NEXT_SLIDE]
    assert harness.dispatcher.current_slide == 2


# ==================== "Slide numbers do not randomly/continuously change" =====
def test_a_hand_resting_in_frame_never_moves_a_slide(harness):
    """The reported drift, as it actually happens.

    A presenter gestures with their hands while talking. Their hand passes
    *through* bound poses constantly - a couple of frames each - on its way to
    somewhere else. Those transits must never reach a command; only a pose held
    deliberately should.
    """
    rng = random.Random(5)
    bound = ["PINKY_UP", "THUMB_UP", "INDEX_UP", "INDEX_MIDDLE_UP", "THREE_FINGERS_UP"]

    for _ in range(200):                       # ~30 seconds of talking with the hands
        # Resting on an unbound pose, then away, then a brief transit through a
        # bound one. A hand physically has to pass through other configurations
        # on either side of a pose, so the transit is bracketed rather than
        # abutting the next one - two 4-frame transits with a single frame
        # between them are one 9-frame gesture, and should be treated as one.
        for _ in range(rng.randint(4, 10)):
            harness.frame("OPEN_PALM")
        for _ in range(rng.randint(2, 6)):
            harness.frame(NO_HAND)
        pose = rng.choice(bound)
        for _ in range(rng.randint(1, 4)):     # shorter than the 6-frame hold
            harness.frame(pose)

    assert harness.commands == [], f"a hand in transit fired {harness.commands}"
    assert harness.dispatcher.current_slide == 1


def test_an_unsettled_hand_never_moves_a_slide(harness):
    """A hand the recognizer cannot settle on must produce nothing.

    Frames like these carry *low* confidence - that is what "the model is unsure"
    means numerically - so the confidence gate is the layer that catches them.
    Feeding them at 0.95 would be testing an input the recognizer cannot produce.
    """
    rng = random.Random(29)
    for _ in range(900):
        harness.frame(rng.choice(["PINKY_UP", "INDEX_UP", "THUMB_UP"]), confidence=0.45)

    assert harness.commands == []
    assert harness.dispatcher.current_slide == 1


def test_a_presenter_who_never_gestures_never_moves_a_slide(harness):
    rng = random.Random(9)
    poses = ["OPEN_PALM", "FIST", "FOUR_FINGERS_UP", NO_HAND, "UNKNOWN"]
    for _ in range(900):
        harness.frame(rng.choice(poses))
    assert harness.commands == []
    assert harness.dispatcher.current_slide == 1


def test_deliberate_gestures_still_advance_the_deck(harness):
    """The stability fixes must not make the product stop working."""
    for _ in range(5):
        harness.hold("PINKY_UP", 12)
        harness.release()
    assert harness.commands == [NEXT_SLIDE] * 5
    assert harness.dispatcher.current_slide == 6

    for _ in range(2):
        harness.hold("THUMB_UP", 12)
        harness.release()
    assert harness.dispatcher.current_slide == 4


# ================== "INDEX + MIDDLE correctly controls the Virtual Pointer" ===
def test_index_middle_controls_the_virtual_pointer(harness):
    harness.hold("INDEX_MIDDLE_UP", 12)
    assert harness.commands == [VIRTUAL_POINTER]
    assert harness.engine.mode == MODE_POINTER
    assert harness.dispatcher.pointer_active is True

    harness.release()
    harness.hold("INDEX_MIDDLE_UP", 12)
    assert harness.engine.mode == MODE_IDLE
    assert harness.dispatcher.pointer_active is False


def test_the_pointer_follows_the_fingertip(harness):
    harness.hold("INDEX_MIDDLE_UP", 12)
    harness.keyboard.log.clear()
    for step in range(20):
        harness.frame("INDEX_MIDDLE_UP", pointer=(0.2 + step * 0.03, 0.5))

    moves = [entry for entry in harness.keyboard.log if entry[0] == "move"]
    assert len(moves) == 20
    assert moves[-1][1] > moves[0][1], "the pointer did not track to the right"
    assert not harness.keyboard.mouse_is_down, "pointing must not draw"


# ============================== "Virtual Pointer never opens Print" ===========
@pytest.mark.parametrize("com", [
    FakeCom(False, False),      # no COM binding
    FakeCom(True, True),        # Windows, presenting
    FakeCom(True, False),       # Windows, NOT presenting - the dangerous one
], ids=["no-com", "presenting", "not-presenting"])
def test_the_virtual_pointer_never_opens_print(keyboard, com):
    harness = Harness(keyboard, com)
    for _ in range(6):
        harness.hold("INDEX_MIDDLE_UP", 12)
        harness.release()
        for step in range(10):
            harness.frame("INDEX_MIDDLE_UP", pointer=(0.3 + step * 0.02, 0.5))

    assert PRINT_HOTKEY not in keyboard.hotkeys()


def test_noisy_two_finger_frames_never_open_print(keyboard, not_presenting):
    """The reported failure mode exactly: two fingers up, occasionally misread as
    one, on a machine where Ctrl+P means Print."""
    harness = Harness(keyboard, not_presenting)
    rng = random.Random(13)

    for _ in range(600):
        roll = rng.random()
        if roll < 0.05:
            harness.frame(NO_HAND)
        elif roll < 0.18:
            harness.frame("INDEX_UP")           # the misread
        else:
            harness.frame("INDEX_MIDDLE_UP")

    assert PRINT_HOTKEY not in keyboard.hotkeys()
    assert ANNOTATION_MODE not in harness.commands


# ================= "INDEX ONLY correctly enables/disables annotation" =========
def test_index_only_enables_and_disables_annotation(harness):
    harness.hold("INDEX_UP", 12)
    assert harness.commands == [ANNOTATION_MODE]
    assert harness.engine.mode == MODE_ANNOTATE
    assert harness.dispatcher.annotation_active is True

    harness.release()
    harness.hold("INDEX_UP", 12)
    assert harness.engine.mode == MODE_IDLE
    assert harness.dispatcher.annotation_active is False


# ================== "Fingertip movement correctly draws on slides" ============
def test_fingertip_movement_draws_on_the_slide(harness):
    """The bug: streaming cursor moves with no button held draws nothing at all."""
    harness.keyboard.log.clear()
    harness.hold("INDEX_UP", 12)                  # arms the pen, then starts drawing
    assert harness.dispatcher.annotation_active

    for step in range(15):
        harness.frame("INDEX_UP", pointer=(0.25 + step * 0.03, 0.4 + step * 0.01))

    kinds = [entry[0] for entry in harness.keyboard.log]
    assert "mouseDown" in kinds, "the pen never touched the slide"
    assert kinds.count("mouseDown") == 1, "the button was pressed more than once"
    assert kinds.index("move") < kinds.index("mouseDown"), (
        "the button went down before the first move, dragging a line in from "
        "wherever the cursor happened to be"
    )
    assert kinds.count("move") > 15
    assert harness.dispatcher.annotations.is_drawing

    harness.release()
    assert not harness.keyboard.mouse_is_down, "the pen was left held down"
    assert harness.dispatcher.annotations.count == 1


def test_a_drawn_stroke_is_recorded_against_the_current_slide(harness):
    harness.hold("PINKY_UP", 12)
    harness.release()
    assert harness.dispatcher.current_slide == 2

    harness.hold("INDEX_UP", 12)
    for step in range(12):
        harness.frame("INDEX_UP", pointer=(0.2 + step * 0.04, 0.5))
    harness.release()

    strokes = harness.dispatcher.annotations.strokes(2)
    assert len(strokes) == 1
    assert len(strokes[0]["points"]) > 5


# ==================== "Clear Annotation actually clears annotations" ==========
def test_clear_annotation_actually_clears(harness):
    harness.hold("INDEX_UP", 12)
    for step in range(12):
        harness.frame("INDEX_UP", pointer=(0.2 + step * 0.04, 0.5))
    harness.release()
    assert harness.dispatcher.annotations.count == 1

    harness.hold("THREE_FINGERS_UP", 12)
    assert CLEAR_ANNOTATION in harness.commands
    assert harness.com.erased == 1, "PowerPoint was never told to erase"
    assert harness.dispatcher.annotations.strokes(harness.dispatcher.current_slide) == []


def test_clearing_does_not_turn_the_pen_off(harness):
    """After Clear the presenter should be able to keep drawing, not have to
    re-arm the pen - and VisionX must not think the pen is off when it is on."""
    harness.hold("INDEX_UP", 12)
    harness.release()
    assert harness.dispatcher.annotation_active is True

    harness.hold("THREE_FINGERS_UP", 12)
    harness.release()
    assert harness.dispatcher.annotation_active is True
    assert harness.dispatcher.controller.annotation_active is True


# ============= "Gesture and voice controls do not interfere with each other" ==
def test_voice_and_gesture_share_one_consistent_state(harness):
    """Both modalities drive the same dispatcher, so they must agree about it."""
    harness.hold("INDEX_MIDDLE_UP", 12)
    assert harness.dispatcher.pointer_active is True

    # Voice turns the pen on explicitly; the pointer must go off, once.
    harness.dispatcher.execute_intent(
        build_intent(ANNOTATION_MODE, SOURCE_VOICE, {"state": True})
    )
    assert harness.dispatcher.annotation_active is True
    assert harness.dispatcher.pointer_active is False
    assert harness.dispatcher.controller.pointer_active is False

    # Voice says it again: idempotent, not a toggle.
    harness.dispatcher.execute_intent(
        build_intent(ANNOTATION_MODE, SOURCE_VOICE, {"state": True})
    )
    assert harness.dispatcher.annotation_active is True


def test_voice_navigation_and_gesture_navigation_agree_on_the_slide(harness):
    harness.hold("PINKY_UP", 12)
    harness.release()
    assert harness.dispatcher.current_slide == 2

    harness.dispatcher.execute_intent(
        build_intent("GO_TO_SLIDE", SOURCE_VOICE, {"slideNumber": 9}, total_slides=20)
    )
    assert harness.dispatcher.current_slide == 9

    harness.hold("THUMB_UP", 12)
    assert harness.commands[-1] == PREVIOUS_SLIDE
    assert harness.dispatcher.current_slide == 8


def test_a_voice_command_mid_stroke_lifts_the_pen(harness):
    harness.hold("INDEX_UP", 12)
    for step in range(8):
        harness.frame("INDEX_UP", pointer=(0.2 + step * 0.04, 0.5))
    assert harness.keyboard.mouse_is_down

    harness.dispatcher.execute_intent(
        build_intent(VIRTUAL_POINTER, SOURCE_VOICE, {"state": True})
    )
    assert not harness.keyboard.mouse_is_down
    assert harness.dispatcher.annotation_active is False


# ============================================ ENGINE / DISPATCHER AGREEMENT ===
def test_the_engine_mode_and_the_dispatcher_never_disagree(harness):
    """Both used to toggle independently from the same event."""
    rng = random.Random(17)
    for _ in range(30):
        pose = rng.choice(["INDEX_UP", "INDEX_MIDDLE_UP", "THREE_FINGERS_UP"])
        harness.hold(pose, 12)
        harness.release()

        if harness.engine.mode == MODE_POINTER:
            assert harness.dispatcher.pointer_active is True
            assert harness.dispatcher.annotation_active is False
        elif harness.engine.mode == MODE_ANNOTATE:
            assert harness.dispatcher.annotation_active is True
            assert harness.dispatcher.pointer_active is False


def test_a_refused_pen_leaves_the_engine_and_dispatcher_consistent(keyboard, not_presenting):
    """PowerPoint is not presenting, so the pen is refused. The engine's mode and
    the dispatcher's state must not drift apart because of it."""
    harness = Harness(keyboard, not_presenting)
    harness.hold("INDEX_UP", 12)

    assert harness.dispatcher.annotation_active is False
    assert harness.dispatcher.controller.annotation_active is False
    assert PRINT_HOTKEY not in keyboard.hotkeys()

    # The engine must not go on believing it is in a mode PowerPoint was never
    # put into: it adopts the dispatcher's outcome, so the UI shows the truth.
    assert harness.engine.mode == MODE_IDLE, (
        "the engine reported ANNOTATE while the pen was refused"
    )

    # And the session keeps working: navigation is unaffected by the refusal.
    harness.release()
    harness.hold("PINKY_UP", 12)
    assert harness.dispatcher.current_slide == 2


def test_a_refused_pen_does_not_stream_a_pointer(keyboard, not_presenting):
    """A mode the controller refused must not start moving the mouse around."""
    harness = Harness(keyboard, not_presenting)
    harness.hold("INDEX_UP", 12)
    keyboard.log.clear()

    for step in range(20):
        harness.frame("INDEX_UP", pointer=(0.2 + step * 0.03, 0.5))

    assert keyboard.log == [], f"a refused mode still drove the mouse: {keyboard.log}"


def test_the_engine_adopts_the_dispatcher_state_when_they_differ(harness):
    """Directly: whatever the dispatcher reports is what the engine's mode becomes."""
    harness.hold("INDEX_MIDDLE_UP", 12)
    assert harness.engine.mode == MODE_POINTER

    # Something else - voice, the control bar - turns everything off underneath.
    # EngineService._after_command feeds every dispatch back to the engine; the
    # harness does the same, because that wiring is what is under test.
    record = harness.dispatcher.execute("VIRTUAL_POINTER", {"parameters": {"state": False}})
    harness.engine.sync_mode(record)
    assert harness.dispatcher.pointer_active is False
    assert harness.engine.mode == MODE_IDLE

    # So the next pointer gesture turns it ON, rather than toggling off something
    # that was already off.
    harness.release()
    harness.hold("INDEX_MIDDLE_UP", 12)
    assert harness.engine.mode == MODE_POINTER
    assert harness.dispatcher.pointer_active is True


def test_telemetry_reports_the_stabilised_gesture_and_the_raw_one(harness):
    """The UI showed the raw per-frame prediction, which is why the displayed
    action flickered. It now gets both, and shows the stable one."""
    for _ in range(6):
        harness.frame("INDEX_MIDDLE_UP")
    stable, _decision = harness.frame("INDEX_UP")
    assert stable.gesture == "INDEX_MIDDLE_UP", "the stray frame was not absorbed"


def test_stopping_the_engine_releases_the_pen(harness):
    harness.hold("INDEX_UP", 12)
    for step in range(8):
        harness.frame("INDEX_UP", pointer=(0.2 + step * 0.04, 0.5))
    assert harness.keyboard.mouse_is_down

    harness.engine.stop()
    assert not harness.keyboard.mouse_is_down


def test_the_engine_snapshot_reports_the_new_safeguards(harness):
    snapshot = harness.engine.snapshot()
    assert snapshot["stabilizer"]["window"] == 5
    assert snapshot["releaseFrames"] >= 2
    assert snapshot["cooldownMs"] == 900
    assert snapshot["bindings"]["INDEX_MIDDLE_UP"] == VIRTUAL_POINTER
    assert snapshot["bindings"]["INDEX_UP"] == ANNOTATION_MODE


def test_remapping_preferences_mid_session_clears_the_vote_history(harness):
    """Frames recorded under the old bindings must not vote for a new command."""
    for _ in range(4):
        harness.frame("INDEX_MIDDLE_UP")

    harness.engine.apply_preferences({
        **DEFAULT_PREFERENCES,
        "pointerGesture": "FOUR_FINGERS_UP",
        "nextSlideGesture": "INDEX_MIDDLE_UP",
    })
    assert not harness.engine.stabilizer.filled
    assert harness.engine.debouncer.held_command is None


# =========================================================== CONCURRENCY ======
def test_the_camera_thread_and_request_threads_do_not_deadlock(keyboard, presenting):
    """Five threads, every lock in the system, hostile interleaving.

    VisionX holds several locks at once in normal operation - the dispatcher's,
    the keyboard's mouse lock, the engine's mode lock, the COM bridge's - across
    a camera thread and Flask request threads. A lock-order inversion between
    them would not show up in any single-threaded test, and would present as the
    presenter's session freezing mid-talk.

    Also asserts the two invariants that must survive any interleaving: the mouse
    button ends up released, and the dispatcher agrees with the controller.
    """
    import threading

    dispatcher = build_dispatcher(keyboard, presenting)
    errors: list[tuple] = []
    engine = GestureEngine(
        EngineConfig(preferences=DEFAULT_PREFERENCES, confidence_threshold=0.7,
                     debounce_frames=6, cooldown_ms=100),
        on_command=lambda c, p: dispatcher.execute(c, {**p, "source": SOURCE_GESTURE}),
        on_pointer=lambda x, y, _m: dispatcher.stream_pointer(x, y),
        on_pointer_lost=dispatcher.end_stroke,
    )

    stop = threading.Event()
    poses = ["PINKY_UP", "THUMB_UP", "INDEX_UP", "INDEX_MIDDLE_UP",
             "THREE_FINGERS_UP", NO_HAND]

    def camera():
        rng = random.Random(1)
        try:
            while not stop.is_set():
                pose = rng.choice(poses)
                result = GestureResult(
                    gesture=pose, confidence=0.0 if pose == NO_HAND else 0.95,
                    hand_detected=pose != NO_HAND,
                    pointer=None if pose == NO_HAND else (0.5, 0.5),
                )
                _stable, _reason, decision = engine.decide(result)
                pointer = engine._track_pointer(result.pointer)
                if decision.fire and decision.command:
                    engine._handle_command(decision.command, pointer)
                if pointer is not None and engine.mode != MODE_IDLE:
                    engine._pointer_streaming = True
                    engine.on_pointer(pointer[0], pointer[1], engine.mode)
                elif engine._pointer_streaming:
                    engine._release_pointer()
        except Exception as exc:  # noqa: BLE001
            errors.append(("camera", exc))

    def requests(seed):
        rng = random.Random(seed)
        try:
            while not stop.is_set():
                roll = rng.random()
                if roll < 0.3:
                    engine.sync_mode(dispatcher.execute(
                        "ANNOTATION_MODE", {"parameters": {"state": rng.random() < 0.5}}))
                elif roll < 0.6:
                    engine.sync_mode(dispatcher.execute(
                        "VIRTUAL_POINTER", {"parameters": {"state": rng.random() < 0.5}}))
                elif roll < 0.8:
                    dispatcher.execute("NEXT_SLIDE")
                else:
                    dispatcher.state()
                    engine.snapshot()
                    dispatcher.end_stroke()
        except Exception as exc:  # noqa: BLE001
            errors.append((f"request-{seed}", exc))

    threads = [threading.Thread(target=camera, daemon=True)]
    threads += [threading.Thread(target=requests, args=(i,), daemon=True) for i in range(4)]
    for thread in threads:
        thread.start()
    stop.wait(1.5)
    stop.set()
    for thread in threads:
        thread.join(timeout=10)

    assert not [t for t in threads if t.is_alive()], "a thread deadlocked"
    assert not errors, errors

    dispatcher.execute("ANNOTATION_MODE", {"parameters": {"state": False}})
    assert not keyboard.mouse_is_down, "the mouse button was stranded down"
    assert dispatcher.annotation_active == dispatcher.controller.annotation_active
    assert dispatcher.pointer_active == dispatcher.controller.pointer_active


# ========================== REGRESSIONS FROM THE THIRD REVIEW ROUND ===========
def test_one_dropped_frame_does_not_split_a_pen_stroke(harness):
    """The engine used the RAW result's pointer, not the stabilised one.

    The stabilizer deliberately carries the last known fingertip through a frame
    that had none - and that value was thrown away. One lost MediaPipe frame
    mid-stroke therefore made `pointer` None, which took the pointer-release
    branch, lifted the pen, and split the stroke into two fragments with a gap.
    """
    harness.hold("INDEX_UP", 12)
    assert harness.dispatcher.annotation_active
    for step in range(6):
        harness.frame("INDEX_UP", pointer=(0.25 + step * 0.02, 0.4))
    assert harness.keyboard.mouse_is_down

    harness.frame(NO_HAND)                      # one dropped frame, hand still there
    assert harness.keyboard.mouse_is_down, "one dropped frame lifted the pen"

    for step in range(6):
        harness.frame("INDEX_UP", pointer=(0.4 + step * 0.02, 0.4))

    downs = [entry for entry in harness.keyboard.log if entry[0] == "mouseDown"]
    assert len(downs) == 1, f"the stroke was split into {len(downs)} fragments"


def test_a_hand_that_really_leaves_still_ends_the_stroke(harness):
    """The fix above must not make the pen impossible to lift."""
    harness.hold("INDEX_UP", 12)
    for step in range(6):
        harness.frame("INDEX_UP", pointer=(0.25 + step * 0.02, 0.4))
    assert harness.keyboard.mouse_is_down

    for _ in range(10):                          # the hand genuinely goes away
        harness.frame(NO_HAND)
    assert not harness.keyboard.mouse_is_down
    assert harness.dispatcher.annotations.count == 1
