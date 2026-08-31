"""Camera frames in, presentation window out - the whole new path.

`tests/test_web_presentation.py` drives the dispatcher directly. This drives the
*engine*, frame by frame, through the same recognition -> stabilizer -> intent
gate -> debouncer chain the camera loop runs, and asserts what the presentation
window would actually have received.

That is the difference that matters for the two requirements a unit test cannot
reach: a gesture held in front of the camera must move the deck once (§2), while
the fingertip driving the pointer in that same hand must reach the window on
every frame (§3). They are opposite requirements over the same input, and only a
test that runs both through one engine can show they do not interfere.
"""

import random

import pytest

from computer_vision.command_mapping.gesture_mapper import (
    ANNOTATION_MODE,
    DEFAULT_PREFERENCES,
    NEXT_SLIDE,
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
from presentation_controller.annotation import AnnotationController
from presentation_controller.dispatcher import CommandDispatcher
from presentation_controller.web import WebPresentationController

FPS = 30.0

# The poses DEFAULT_PREFERENCES binds to the commands used below. Read from the
# bindings rather than hard-coded, so a change to the defaults breaks this
# loudly instead of silently testing an unbound pose.
POSE_FOR = {command: pose for pose, command in
            [(DEFAULT_PREFERENCES[field], command)
             for field, command in (
                 ("nextSlideGesture", NEXT_SLIDE),
                 ("pointerGesture", VIRTUAL_POINTER),
                 ("annotationGesture", ANNOTATION_MODE),
             )]}


class WebHarness:
    """A live engine wired to a live web presentation, driven a frame at a time.

    Time advances one frame per `frame()` call instead of sleeping, so the 900 ms
    cooldown runs at its real value while the test finishes in milliseconds.
    """

    def __init__(self, total_slides: int = 20, **config):
        self.events: list[dict] = []
        self.pointer: list[dict] = []
        self.commands: list[str] = []
        self.now = 1_000_000.0

        self.controller = WebPresentationController(
            publish=self.events.append, publish_pointer=self.pointer.append,
        )
        self.dispatcher = CommandDispatcher(self.controller, AnnotationController())
        self.dispatcher.bind_presentation(current_slide=1, total_slides=total_slides)

        self.engine = GestureEngine(
            EngineConfig(
                preferences=DEFAULT_PREFERENCES,
                confidence_threshold=config.pop("confidence_threshold", 0.70),
                debounce_frames=config.pop("debounce_frames", 6),
                cooldown_ms=config.pop("cooldown_ms", 900),
                **config,
            ),
            on_command=self._on_command,
            on_pointer=self._on_pointer,
            on_pointer_lost=self._on_pointer_lost,
        )
        self.engine.debouncer.clock = lambda: self.now

    # --- exactly the wiring EngineService installs ---------------------------
    def _on_command(self, command, payload):
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
        rng = rng or random.Random(0)
        for _ in range(frames):
            if noise and rng.random() < noise:
                self.frame(NO_HAND)
            else:
                self.frame(gesture, pointer=pointer)

    def release(self, frames: int = 30):
        for _ in range(frames):
            self.frame(NO_HAND)

    def speak(self, command: str, **parameters):
        """A voice command, through the same dispatcher the gestures use."""
        intent = build_intent(command, SOURCE_VOICE, parameters or None,
                              total_slides=self.dispatcher.total_slides)
        record = self.dispatcher.execute_intent(intent)
        # EngineService._after_command does this for every modality, and it is
        # what stops the engine's mode inverting the next gesture.
        self.engine.sync_mode(record)
        return record

    def ink_actions(self) -> list[str]:
        return [e["action"] for e in self.events if e.get("type") == "ink"]


@pytest.fixture
def harness() -> WebHarness:
    return WebHarness()


# ================================== 2. GESTURE STABILITY, AT THE SURFACE =====
def test_holding_next_slide_advances_the_deck_exactly_once(harness):
    """§2, stated as the presenter experiences it.

    A gesture held in front of the camera for four seconds is one instruction. It
    used to walk through the deck, because a held gesture does not produce a clean
    run of identical frames and any single neutral frame re-armed the repeat.
    """
    harness.hold(POSE_FOR[NEXT_SLIDE], frames=120)      # ~4 seconds
    assert harness.commands == [NEXT_SLIDE]
    assert harness.dispatcher.current_slide == 2


def test_holding_a_gesture_through_dropped_frames_still_fires_once(harness):
    """The same hold, with MediaPipe losing the hand one frame in eight."""
    harness.hold(POSE_FOR[NEXT_SLIDE], frames=150, noise=0.12, rng=random.Random(7))
    assert harness.commands.count(NEXT_SLIDE) == 1


def test_lowering_the_hand_and_raising_it_again_is_a_second_command(harness):
    """The system must distinguish holding from a genuinely new gesture."""
    harness.hold(POSE_FOR[NEXT_SLIDE], frames=40)
    harness.release()
    harness.hold(POSE_FOR[NEXT_SLIDE], frames=40)

    assert harness.commands == [NEXT_SLIDE, NEXT_SLIDE]
    assert harness.dispatcher.current_slide == 3


def test_the_deck_never_advances_on_its_own(harness):
    """Two minutes of a presenter standing still in front of the camera."""
    rng = random.Random(11)
    for _ in range(int(FPS * 120)):
        harness.frame(NO_HAND if rng.random() < 0.5 else "OPEN_PALM")
    assert harness.commands == []
    assert harness.dispatcher.current_slide == 1


# ============================== 3. THE POINTER IS NOT DEBOUNCED ==============
def test_the_pointer_reaches_the_window_on_every_frame(harness):
    """§3, and the reason it is a separate requirement from §2.

    The very same hold that fires one command must stream one pointer position
    per frame. A debounce that reached the pointer is what made it lag; a pointer
    that reached the debouncer would make the deck race.
    """
    harness.hold(POSE_FOR[VIRTUAL_POINTER], frames=30)
    assert harness.engine.mode == MODE_POINTER
    assert harness.commands == [VIRTUAL_POINTER], "the pointer gesture fired more than once"

    before = len(harness.pointer)
    for step in range(90):                      # three seconds of hand movement
        harness.frame(POSE_FOR[VIRTUAL_POINTER], pointer=(0.30 + step * 0.005, 0.5))

    assert len(harness.pointer) - before == 90, "the pointer stream was throttled"


def test_the_pointer_follows_the_hand_rather_than_jumping(harness):
    """Smoothing must lag by frames, not by a visible distance.

    The engine smooths the fingertip so the dot does not jitter. If that
    smoothing is too heavy the dot trails the hand across the slide, which is
    exactly what "moves in large delayed jumps" describes.
    """
    harness.hold(POSE_FOR[VIRTUAL_POINTER], frames=30, pointer=(0.30, 0.5))

    for step in range(45):                      # a steady 1.5 s sweep
        harness.frame(POSE_FOR[VIRTUAL_POINTER], pointer=(0.30 + step * 0.01, 0.5))

    # After a sweep at constant speed the published position must have converged
    # to within a couple of frames of travel of the real fingertip.
    published = harness.pointer[-1]["x"]
    actual = 0.30 + 44 * 0.01
    assert abs(published - actual) < 0.02, (
        f"the pointer is {abs(published - actual):.3f} behind the fingertip"
    )


def test_the_pointer_stops_when_the_hand_leaves(harness):
    """And stops within the stabilizer window, not immediately.

    The stabilizer needs a plurality of NO_HAND frames before it agrees the hand
    is gone, so a couple more positions are published after the last real one.
    That lag is deliberate and is the same mechanism that stops one dropped frame
    splitting a stroke - but it must be bounded by the window, not open-ended,
    or the dot would linger on the slide after the presenter lowered their hand.
    """
    window = harness.engine.stabilizer.window
    harness.hold(POSE_FOR[VIRTUAL_POINTER], frames=30)
    before = len(harness.pointer)

    harness.release(frames=20)
    trailing = len(harness.pointer) - before
    assert 0 < trailing <= window, (
        f"{trailing} positions were published after the hand left "
        f"(the stabilizer window is {window} frames)"
    )

    # And then nothing at all: the stream has genuinely stopped, not slowed.
    settled = len(harness.pointer)
    harness.release(frames=60)
    assert len(harness.pointer) == settled


# ==================================== 4. ANNOTATION, THROUGH THE ENGINE ======
def test_drawing_with_a_fingertip_produces_one_stroke(harness):
    """§4: the pen goes on with a gesture, the fingertip draws, the ink is kept."""
    harness.hold(POSE_FOR[ANNOTATION_MODE], frames=30, pointer=(0.30, 0.40))
    assert harness.engine.mode == MODE_ANNOTATE

    for step in range(60):
        harness.frame(POSE_FOR[ANNOTATION_MODE], pointer=(0.30 + step * 0.005, 0.40))

    harness.release()      # the hand leaves - the stroke closes exactly once
    assert harness.ink_actions().count("BEGIN") == 1
    assert harness.ink_actions().count("END") == 1

    strokes = harness.dispatcher.annotations.strokes()
    assert len(strokes) == 1 and len(strokes[0]["points"]) > 10


def test_a_dropped_frame_mid_stroke_does_not_split_the_ink(harness):
    """One lost MediaPipe frame must not end the stroke and start a new one.

    The stabilizer carries the last known fingertip through a dropped frame for
    exactly this reason: without it the pen lifted, and a single line arrived in
    the window as two fragments with a gap between them.
    """
    harness.hold(POSE_FOR[ANNOTATION_MODE], frames=30, pointer=(0.30, 0.40))

    rng = random.Random(5)
    for step in range(120):
        if rng.random() < 0.08:
            harness.frame(NO_HAND)
        else:
            harness.frame(POSE_FOR[ANNOTATION_MODE], pointer=(0.30 + step * 0.003, 0.40))

    assert harness.ink_actions().count("BEGIN") == 1, "the stroke was split by a dropped frame"


# ============================= 10. GESTURE AND VOICE ON ONE PRESENTATION =====
def test_a_voice_command_moves_the_same_deck_a_gesture_just_moved(harness):
    harness.hold(POSE_FOR[NEXT_SLIDE], frames=40)
    harness.release()
    assert harness.dispatcher.current_slide == 2

    harness.speak("PREVIOUS_SLIDE")
    assert harness.dispatcher.current_slide == 1

    harness.hold(POSE_FOR[NEXT_SLIDE], frames=40)
    assert harness.dispatcher.current_slide == 2


def test_voice_turning_the_pointer_off_does_not_invert_the_next_gesture(harness):
    """The mode has one owner, whichever modality changed it.

    The engine used to keep its own copy: after voice turned the pointer off, the
    presenter's next pointer gesture computed "toggle off" from a stale POINTER
    and appeared to do nothing at all.
    """
    harness.hold(POSE_FOR[VIRTUAL_POINTER], frames=30)
    assert harness.engine.mode == MODE_POINTER

    harness.speak("VIRTUAL_POINTER", state=False)
    assert harness.engine.mode == MODE_IDLE, "the engine kept a stale mode"

    harness.release()
    harness.hold(POSE_FOR[VIRTUAL_POINTER], frames=30)
    assert harness.engine.mode == MODE_POINTER
    assert harness.dispatcher.pointer_active


def test_voice_can_erase_ink_a_gesture_drew(harness):
    harness.hold(POSE_FOR[ANNOTATION_MODE], frames=30, pointer=(0.30, 0.40))
    for step in range(40):
        harness.frame(POSE_FOR[ANNOTATION_MODE], pointer=(0.30 + step * 0.005, 0.40))
    harness.release()
    assert harness.dispatcher.annotations.count == 1

    record = harness.speak("CLEAR_ANNOTATION")
    assert record["delivered"]
    assert harness.dispatcher.annotations.count == 0
    assert "CLEAR" in harness.ink_actions()
