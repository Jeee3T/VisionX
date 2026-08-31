"""The web presentation experience: the architectural change, verified.

The presentation moved out of PowerPoint and into VisionX's own window. These
tests pin down what that is supposed to mean, requirement by requirement, at the
seam where it is actually decidable - the dispatcher and the controller below it,
driven exactly as the camera loop and the voice pipeline drive them.

Every test here fails against the PowerPoint-only architecture.
"""

import pytest

from computer_vision.command_mapping.gesture_mapper import (
    ANNOTATION_MODE,
    BLACKOUT,
    CLEAR_ANNOTATION,
    FIRST_SLIDE,
    GO_TO_SLIDE,
    LAST_SLIDE,
    NEXT_SLIDE,
    PREVIOUS_SLIDE,
    VIRTUAL_POINTER,
    WHITEOUT,
)
from presentation_controller.annotation import AnnotationController
from presentation_controller.dispatcher import CommandDispatcher
from presentation_controller.web import WebPresentationController


class Bus:
    """Records what the presentation window would have received.

    Two lists, because the real bus has two channels with different delivery
    guarantees: discrete events are queued and must not be lost, pointer samples
    coalesce and only the newest matters.
    """

    def __init__(self):
        self.events: list[dict] = []
        self.pointer: list[dict] = []

    def publish(self, event: dict) -> None:
        self.events.append(event)

    def publish_pointer(self, event: dict) -> None:
        self.pointer.append(event)

    def actions(self, kind: str) -> list[str]:
        return [e.get("action") for e in self.events if e.get("type") == kind]


@pytest.fixture
def bus() -> Bus:
    return Bus()


@pytest.fixture
def web(bus) -> WebPresentationController:
    return WebPresentationController(publish=bus.publish, publish_pointer=bus.publish_pointer)


@pytest.fixture
def dispatcher(web) -> CommandDispatcher:
    instance = CommandDispatcher(web, AnnotationController())
    instance.bind_presentation(current_slide=1, total_slides=20)
    return instance


def gesture(dispatcher, command, **parameters):
    """Dispatch exactly as GestureEngine._handle_command does."""
    return dispatcher.execute(command, {"source": "gesture", "parameters": parameters})


def voice(dispatcher, command, **parameters):
    """Dispatch exactly as voice_service does, via a CommandIntent."""
    from multimodal.command import SOURCE_VOICE, build

    intent = build(command, SOURCE_VOICE, parameters or None, total_slides=dispatcher.total_slides)
    return dispatcher.execute_intent(intent)


# ==================================== 1. A REAL PRESENTATION SURFACE ==========
def test_every_command_is_delivered_by_the_web_controller(dispatcher):
    """Nothing can refuse.

    The PowerPoint controller had to say "no slideshow is running" for the pen,
    the eraser and every COM-only command, and a presenter experienced that as
    the feature being broken. VisionX renders the deck itself now, so there is no
    layer left that can decline.
    """
    commands = [
        (NEXT_SLIDE, {}), (PREVIOUS_SLIDE, {}), (FIRST_SLIDE, {}), (LAST_SLIDE, {}),
        (GO_TO_SLIDE, {"slideNumber": 7}), (VIRTUAL_POINTER, {}), (ANNOTATION_MODE, {}),
        (CLEAR_ANNOTATION, {}), (BLACKOUT, {}), (WHITEOUT, {}),
    ]
    for command, parameters in commands:
        record = gesture(dispatcher, command, **parameters)
        assert record["delivered"], f"{command} was not delivered: {record['message']}"


def test_the_web_controller_touches_no_keyboard_and_no_mouse(web):
    """§11, structurally rather than behaviourally.

    The Print dialog, the focus-stealing key presses and the stranded mouse
    button all came from one thing: driving another application through
    synthetic input. This asserts the new controller cannot do that at all -
    it holds no keyboard, no pointer and no COM bridge to do it with.
    """
    for attribute in ("keyboard", "pointer", "com"):
        assert not hasattr(web, attribute), (
            f"the web controller must not own a {attribute}: it is the presentation "
            "surface, not an automation layer"
        )
    assert web.describe()["automation"] == "none"


def test_the_pen_can_never_send_ctrl_p(dispatcher, bus):
    """The single worst reported failure: Ctrl+P outside a slideshow is Print.

    Turning the pen on now publishes a mode change to the presentation window.
    Nothing anywhere in this path can produce a keystroke, so the Print dialog
    cannot open however the deck is configured or wherever focus happens to be.
    """
    gesture(dispatcher, ANNOTATION_MODE)
    assert dispatcher.annotation_active
    # The only thing that left the process is a description of the new mode.
    assert bus.events[-1] == {"type": "mode", "pointerActive": False, "annotationActive": True}


def test_slide_navigation_tracks_the_deck(dispatcher):
    gesture(dispatcher, NEXT_SLIDE)
    gesture(dispatcher, NEXT_SLIDE)
    assert dispatcher.current_slide == 3

    voice(dispatcher, GO_TO_SLIDE, slideNumber=12)
    assert dispatcher.current_slide == 12

    gesture(dispatcher, FIRST_SLIDE)
    assert dispatcher.current_slide == 1
    gesture(dispatcher, LAST_SLIDE)
    assert dispatcher.current_slide == 20

    # And it still refuses to run off the end.
    gesture(dispatcher, NEXT_SLIDE)
    assert dispatcher.current_slide == 20


def test_blank_screen_toggles(dispatcher):
    gesture(dispatcher, BLACKOUT)
    assert dispatcher.blank_screen == "BLACK"
    gesture(dispatcher, BLACKOUT)
    assert dispatcher.blank_screen is None
    gesture(dispatcher, WHITEOUT)
    assert dispatcher.blank_screen == "WHITE"


# ============================================= 3. THE VIRTUAL POINTER =========
def test_the_pointer_streams_at_frame_rate(dispatcher, bus):
    """§3: pointer movement is continuous and must not be debounced.

    Discrete commands are debounced; fingertip movement is not, and conflating
    the two is what made the old pointer move in delayed jumps. Every frame the
    camera produces while the pointer is on must reach the window.
    """
    gesture(dispatcher, VIRTUAL_POINTER)
    assert dispatcher.pointer_active

    frames = [(0.30 + step * 0.01, 0.50) for step in range(60)]
    for x, y in frames:
        dispatcher.stream_pointer(x, y)

    assert len(bus.pointer) == len(frames), "the pointer stream was throttled"
    assert bus.pointer[-1]["x"] == pytest.approx(frames[-1][0], abs=1e-4)


def test_no_pointer_is_published_while_the_mode_is_off(dispatcher, bus):
    """A hand in frame with no mode engaged must not move anything."""
    for step in range(30):
        dispatcher.stream_pointer(0.3 + step * 0.01, 0.5)
    assert bus.pointer == []


def test_the_published_pointer_matches_the_persisted_stroke(dispatcher, bus):
    """The alignment invariant, and a real bug it prevents.

    A stroke is drawn live from the pointer stream and re-drawn later from
    MongoDB. If those two carried differently-scaled coordinates, replaying a
    saved annotation would put it somewhere the presenter never drew. Both come
    from `stream_pointer`, so this asserts they are the same number.
    """
    gesture(dispatcher, ANNOTATION_MODE)
    points = [(0.25, 0.40), (0.35, 0.45), (0.45, 0.55)]
    for x, y in points:
        dispatcher.stream_pointer(x, y)
    dispatcher.end_stroke()

    stroke = dispatcher.annotations.strokes()[0]
    published = [(event["x"], event["y"]) for event in bus.pointer]
    for stored, sent in zip(stroke["points"], published):
        assert (stored["x"], stored["y"]) == sent


# ==================================================== 4. ANNOTATION ===========
def test_annotation_draws_a_stroke_over_the_slide(dispatcher, bus):
    """§4: the pen goes on, the fingertip draws, the stroke is captured."""
    gesture(dispatcher, ANNOTATION_MODE)
    assert dispatcher.annotation_active

    for step in range(20):
        dispatcher.stream_pointer(0.3 + step * 0.01, 0.4 + step * 0.005)

    # The window is told where the stroke begins, and every sample after that
    # carries `drawing`. The FIRST sample deliberately does not: the dispatcher
    # moves the pointer to the start position and only then presses the pen, so
    # a stroke cannot be dragged in from wherever the hand was last seen. That is
    # why BEGIN carries the start point - it is the point that sample was at.
    begin = next(e for e in bus.events if e.get("type") == "ink" and e["action"] == "BEGIN")
    assert (begin["x"], begin["y"]) == pytest.approx((0.30, 0.40), abs=1e-4)
    assert not bus.pointer[0]["drawing"]
    assert all(event["drawing"] for event in bus.pointer[1:])

    dispatcher.end_stroke()
    assert "END" in bus.actions("ink")
    assert len(dispatcher.annotations.strokes()) == 1
    assert len(dispatcher.annotations.strokes()[0]["points"]) > 1


def test_annotation_off_ends_the_stroke_and_lifts_the_pen(dispatcher, web):
    gesture(dispatcher, ANNOTATION_MODE)
    for step in range(10):
        dispatcher.stream_pointer(0.3 + step * 0.01, 0.4)
    assert web.pen_is_down

    gesture(dispatcher, ANNOTATION_MODE)          # off
    assert not dispatcher.annotation_active
    assert not web.pen_is_down
    assert not dispatcher.annotations.is_drawing


def test_clear_annotation_always_reaches_the_window(dispatcher, bus):
    """§4: Clear must never quietly do nothing.

    The PowerPoint eraser refused whenever no slideshow was running - which,
    given the pen had the same problem, is most of the time a presenter tried it.
    """
    gesture(dispatcher, ANNOTATION_MODE)
    for step in range(10):
        dispatcher.stream_pointer(0.3 + step * 0.01, 0.4)
    dispatcher.end_stroke()
    assert dispatcher.annotations.count == 1

    record = gesture(dispatcher, CLEAR_ANNOTATION)
    assert record["delivered"]
    assert "CLEAR" in bus.actions("ink")
    assert dispatcher.annotations.count == 0


def test_clearing_ink_does_not_turn_the_pen_off(dispatcher):
    """Erasing is not a mode change: the presenter carries on drawing."""
    gesture(dispatcher, ANNOTATION_MODE)
    gesture(dispatcher, CLEAR_ANNOTATION)
    assert dispatcher.annotation_active, "Clear must not silently leave annotation mode"


def test_ink_on_one_slide_is_cleared_without_touching_another(dispatcher):
    gesture(dispatcher, ANNOTATION_MODE)
    for step in range(10):
        dispatcher.stream_pointer(0.3 + step * 0.01, 0.4)
    dispatcher.end_stroke()

    gesture(dispatcher, NEXT_SLIDE)
    for step in range(10):
        dispatcher.stream_pointer(0.5 + step * 0.01, 0.6)
    dispatcher.end_stroke()
    assert dispatcher.annotations.count == 2

    gesture(dispatcher, CLEAR_ANNOTATION)          # clears slide 2 only
    remaining = dispatcher.annotations.strokes()
    assert len(remaining) == 1 and remaining[0]["slide"] == 1


def test_the_pointer_and_the_pen_are_mutually_exclusive(dispatcher, web):
    """Exactly as in a slideshow: the arrow and the pen are one setting."""
    gesture(dispatcher, ANNOTATION_MODE)
    assert dispatcher.annotation_active and not dispatcher.pointer_active

    gesture(dispatcher, VIRTUAL_POINTER)
    assert dispatcher.pointer_active and not dispatcher.annotation_active
    assert not web.pen_is_down


# ================================= 10. GESTURE AND VOICE, ONE STATE ===========
def test_gesture_and_voice_drive_the_same_presentation(dispatcher):
    """§10: two modalities, one deck, no disagreement."""
    gesture(dispatcher, NEXT_SLIDE)
    gesture(dispatcher, NEXT_SLIDE)
    assert dispatcher.current_slide == 3

    voice(dispatcher, PREVIOUS_SLIDE)
    assert dispatcher.current_slide == 2

    voice(dispatcher, GO_TO_SLIDE, slideNumber=9)
    gesture(dispatcher, NEXT_SLIDE)
    assert dispatcher.current_slide == 10

    # Both are recorded against the same session, tagged with where they came from.
    assert [row["source"] for row in dispatcher.history[-2:]] == ["voice", "gesture"]


def test_voice_can_turn_the_pen_on_and_a_gesture_can_turn_it_off(dispatcher, web):
    """Pointer and annotation state stay synchronised across modalities (§10)."""
    voice(dispatcher, ANNOTATION_MODE, state=True)
    assert dispatcher.annotation_active and web.annotation_active

    gesture(dispatcher, ANNOTATION_MODE)           # a toggle, from the other modality
    assert not dispatcher.annotation_active and not web.annotation_active


def test_voice_repeat_counts_stop_at_the_end_of_the_deck(dispatcher):
    dispatcher.bind_presentation(current_slide=18, total_slides=20)
    voice(dispatcher, NEXT_SLIDE, count=5)
    assert dispatcher.current_slide == 20


# ======================================== THE POINTER / INK EDGE CASES ========
def test_a_hand_leaving_the_frame_closes_the_stroke_once(dispatcher, web, bus):
    """The camera loop calls end_stroke when the drawing hand disappears.

    Idempotent on purpose: `_release_pointer`, a mode change and `stop()` can all
    reach it for the same stroke, and a second END would look to the window like
    a stroke that was never begun.
    """
    gesture(dispatcher, ANNOTATION_MODE)
    for step in range(10):
        dispatcher.stream_pointer(0.3 + step * 0.01, 0.4)

    dispatcher.end_stroke()
    dispatcher.end_stroke()
    dispatcher.end_stroke()

    assert bus.actions("ink").count("END") == 1
    assert not web.pen_is_down


def test_ending_the_presentation_leaves_no_mode_engaged(dispatcher, web):
    from computer_vision.command_mapping.gesture_mapper import END_PRESENTATION

    gesture(dispatcher, ANNOTATION_MODE)
    gesture(dispatcher, END_PRESENTATION)
    assert not dispatcher.annotation_active
    assert not dispatcher.pointer_active
    assert not web.pen_is_down
