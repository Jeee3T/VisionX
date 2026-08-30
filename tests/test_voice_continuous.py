"""fixes.md §4 at the service seam: continuous listening drives the real pipeline.

`voice_assistant/wake` is tested exhaustively in test_wake_word.py. What is tested
here is the *wiring*: that a segment of continuously-listened speech reaches the
existing trained intent model and the existing dispatcher, and that ordinary
speech does not.

The database is stubbed out - these tests are about the pipeline, and the rest of
the suite is deliberately free of MongoDB - but the wake machine, the trained
TF-IDF + logistic-regression model and the real CommandDispatcher are all the
shipped code.
"""

import time

import pytest

from multimodal.command import SOURCE_VOICE
from tests.conftest import FakeCom, FakeKeyboard, build_dispatcher

voice_service = pytest.importorskip("services.voice_service")


@pytest.fixture
def voice(monkeypatch, interpreter):
    """The service with its database edges stubbed and a live session bound."""
    from voice_assistant.intent.intents import VoiceThresholds

    keyboard = FakeKeyboard()
    dispatcher = build_dispatcher(keyboard, FakeCom(connected=True, slideshow=True))

    monkeypatch.setattr(voice_service, "_require_enabled", lambda user_id: None)
    monkeypatch.setattr(voice_service, "_require_interpreter", lambda: interpreter)
    monkeypatch.setattr(voice_service, "_log_command", lambda *a, **k: None)
    monkeypatch.setattr(
        voice_service.personalization_service, "thresholds",
        lambda user_id: VoiceThresholds(),
    )
    monkeypatch.setattr(voice_service.bus, "publish", lambda event: None)

    engine_service = voice_service.engine_service
    monkeypatch.setattr(engine_service, "dispatcher", dispatcher, raising=False)
    monkeypatch.setattr(engine_service, "session", {"userId": "u1"}, raising=False)
    monkeypatch.setattr(engine_service, "engine", None, raising=False)
    monkeypatch.setattr(engine_service, "owns_session", lambda user_id: True)
    monkeypatch.setattr(engine_service, "_after_command", lambda *a, **k: None)

    voice_service.clear_wake_sessions()
    yield voice_service, dispatcher, keyboard
    voice_service.clear_wake_sessions()


# ============================================== ORDINARY SPEECH IS INERT ======
@pytest.mark.parametrize("speech", [
    "so as you can see the revenue grew last quarter",
    "let us move on to the next slide in a moment",
    "next slide please",
    "ok so that is the roadmap",
    "any questions",
])
def test_ordinary_speech_never_reaches_the_dispatcher(voice, speech):
    service, dispatcher, keyboard = voice
    result = service.observe_segment("u1", speech)

    assert result["executed"] is False
    assert result["wake"]["action"] == "IDLE"
    assert dispatcher.current_slide == 1
    assert keyboard.log == []


def test_a_whole_talk_moves_nothing(voice):
    service, dispatcher, _keyboard = voice
    for line in [
        "good morning everyone and thank you for coming",
        "on the next slide we look at the numbers",
        "let me go back one slide",
        "ok so the last slide showed the roadmap",
        "go to slide seven and you will see the detail",
    ]:
        assert service.observe_segment("u1", line)["executed"] is False
    assert dispatcher.current_slide == 1


# ============================================ "Vision <command> OK" RUNS ======
def test_the_brief_example_executes_immediately(voice):
    service, dispatcher, keyboard = voice
    result = service.observe_segment("u1", "Vision go to next slide OK")

    assert result["executed"] is True
    assert result["command"] == "NEXT_SLIDE"
    assert result["wake"]["command"] == "go to next slide"
    assert dispatcher.current_slide == 2


def test_a_command_split_across_segments_executes_on_the_terminator(voice):
    service, dispatcher, _keyboard = voice

    armed = service.observe_segment("u1", "vision")
    assert armed["executed"] is False and armed["wake"]["action"] == "ARMED"

    capturing = service.observe_segment("u1", "go to slide twelve")
    assert capturing["executed"] is False and capturing["wake"]["action"] == "CAPTURING"

    done = service.observe_segment("u1", "ok")
    assert done["executed"] is True
    assert done["command"] == "GO_TO_SLIDE"
    assert dispatcher.current_slide == 12


def test_the_command_reaches_powerpoint_as_real_input(voice):
    """The point of the whole exercise: a spoken command becomes desktop input."""
    service, _dispatcher, keyboard = voice
    service.observe_segment("u1", "vision black screen ok")
    assert "b" in keyboard.keys()


def test_listening_resumes_after_every_command(voice):
    """'Voice returns to wake-word listening after each command.'"""
    service, dispatcher, _keyboard = voice

    for _ in range(4):
        result = service.observe_segment("u1", "vision next slide ok")
        assert result["executed"] is True
        assert service.wake_session("u1").state == "LISTENING"

    assert dispatcher.current_slide == 5


def test_no_ui_interaction_is_needed_between_commands(voice):
    """Commands separated by ordinary speech, with nothing in between but talking."""
    service, dispatcher, _keyboard = voice

    service.observe_segment("u1", "vision next slide ok")
    service.observe_segment("u1", "so this chart shows the growth we saw")
    service.observe_segment("u1", "and as you can see it is quite steep")
    service.observe_segment("u1", "vision next slide ok")
    service.observe_segment("u1", "which brings me to the conclusion")

    assert dispatcher.current_slide == 3


def test_the_wake_word_alone_arms_but_dispatches_nothing(voice):
    service, dispatcher, keyboard = voice
    result = service.observe_segment("u1", "vision")

    assert result["executed"] is False
    assert service.wake_session("u1").is_capturing
    assert dispatcher.current_slide == 1
    assert keyboard.log == []


def test_wake_word_then_terminator_with_nothing_between_is_not_a_command(voice):
    service, dispatcher, _keyboard = voice
    result = service.observe_segment("u1", "vision ok")

    assert result["executed"] is False
    assert "no command" in result["message"].lower()
    assert dispatcher.current_slide == 1
    assert service.wake_session("u1").state == "LISTENING"


def test_chatter_after_the_wake_word_still_faces_the_confidence_gate(voice):
    """The wake word decides *when* to classify; the trained model still decides
    whether it is a command at all."""
    service, dispatcher, _keyboard = voice
    result = service.observe_segment("u1", "vision the weather is nice today ok")

    assert result["executed"] is False
    assert dispatcher.current_slide == 1


# ================================================= STATE MANAGEMENT ===========
def test_each_user_listens_independently(voice):
    service, _dispatcher, _keyboard = voice
    service.observe_segment("u1", "vision go to")
    assert service.wake_session("u1").is_capturing
    assert not service.wake_session("u2").is_capturing


def test_resetting_abandons_a_half_captured_command(voice):
    service, dispatcher, _keyboard = voice
    service.observe_segment("u1", "vision go to slide")
    assert service.wake_session("u1").is_capturing

    snapshot = service.reset_wake_session("u1")
    assert snapshot["state"] == "LISTENING"
    assert service.observe_segment("u1", "seven ok")["executed"] is False
    assert dispatcher.current_slide == 1


def test_execute_false_interprets_without_dispatching(voice):
    """The dry-run mode the settings screen uses to preview what a phrase does."""
    service, dispatcher, keyboard = voice
    result = service.observe_segment("u1", "vision next slide ok", execute=False)

    assert result["executed"] is False
    assert result["command"] == "NEXT_SLIDE"     # it was still understood
    assert dispatcher.current_slide == 1
    assert keyboard.log == []


def test_status_advertises_continuous_listening(voice, monkeypatch):
    service, _dispatcher, _keyboard = voice
    monkeypatch.setattr(
        service.personalization_service, "ensure",
        lambda user_id: {"voiceEnabled": True, "voiceTranscriptRetention": False},
    )
    monkeypatch.setattr(service.personalization_service, "voice_enabled", lambda user_id: True)

    status = service.status("u1")
    assert status["continuous"]["supported"] is True
    assert status["continuous"]["wakeWord"] == "vision"
    assert status["continuous"]["terminator"] == "ok"
    assert status["continuous"]["state"] == "LISTENING"


# ============================== VOICE AND GESTURE DO NOT INTERFERE ============
def test_a_voice_command_and_a_gesture_share_the_same_slide_counter(voice):
    service, dispatcher, _keyboard = voice
    from multimodal.command import build as build_intent

    service.observe_segment("u1", "vision next slide ok")
    assert dispatcher.current_slide == 2

    dispatcher.execute("NEXT_SLIDE", {"source": "gesture"})
    assert dispatcher.current_slide == 3

    service.observe_segment("u1", "vision go to slide ten ok")
    assert dispatcher.current_slide == 10

    dispatcher.execute_intent(build_intent("PREVIOUS_SLIDE", SOURCE_VOICE, {}))
    assert dispatcher.current_slide == 9


def test_voice_pen_control_is_explicit_not_a_toggle(voice):
    """'turn on the pen' twice must leave the pen on - unchanged behaviour."""
    service, dispatcher, _keyboard = voice

    service.observe_segment("u1", "vision turn on the pen ok")
    assert dispatcher.annotation_active is True

    service.observe_segment("u1", "vision turn on the pen ok")
    assert dispatcher.annotation_active is True

    service.observe_segment("u1", "vision turn off the pen ok")
    assert dispatcher.annotation_active is False


# ============================ REGRESSIONS FOUND IN REVIEW OF THIS CHANGE SET ==
def test_two_commands_in_one_segment_both_reach_the_dispatcher(voice):
    """Found in review: only the last command in a segment was executed.

    A 3-second recording routinely spans a sentence boundary, so "…slide two OK.
    Vision, next slide, OK" arrives as one segment. Running only the last silently
    dropped the first.
    """
    service, dispatcher, _keyboard = voice
    result = service.observe_segment("u1", "vision go to slide 2 ok vision next slide ok")

    assert result["wake"]["commands"] == ["go to slide 2", "next slide"]
    assert result["executed"] is True
    assert dispatcher.current_slide == 3, "both commands should have run, in order"


def test_a_command_finished_in_the_same_segment_as_the_next_wake_word(voice):
    """Found in review: a wake word later in the segment wiped a command that had
    already completed earlier in it."""
    service, dispatcher, _keyboard = voice

    service.observe_segment("u1", "vision go to slide 2")
    result = service.observe_segment("u1", "ok vision")

    assert result["executed"] is True
    assert dispatcher.current_slide == 2
    assert service.wake_session("u1").is_capturing, "the trailing wake word re-armed"

    service.observe_segment("u1", "next slide ok")
    assert dispatcher.current_slide == 3


@pytest.mark.parametrize("speech", [
    "our vision going forward is to move on to the next slide okay",
    "we need to provision more servers and then move to the next slide okay",
    "the vision is simple lets go back one slide okay so",
])
def test_a_talk_about_vision_does_not_reach_the_dispatcher(voice, speech):
    """Found in review: these all moved the deck. The wake word really was
    spoken and what followed really was a command phrase, so the confidence gate
    classified it at >0.85 - only refusing to arm can stop it."""
    service, dispatcher, keyboard = voice
    result = service.observe_segment("u1", speech)

    assert result["executed"] is False, f"{speech!r} executed a command"
    assert dispatcher.current_slide == 1
    assert keyboard.log == []


def test_ending_a_session_drops_a_half_spoken_command(voice, monkeypatch):
    """Found in review: a session that ended mid-capture left the machine armed,
    so the first words of the next talk became a command."""
    from services.engine_service import engine_service

    service, _dispatcher, _keyboard = voice
    service.observe_segment("u1", "vision go to")
    assert service.wake_session("u1").is_capturing

    monkeypatch.setattr(engine_service, "session", {"userId": "u1"}, raising=False)
    engine_service._teardown()

    assert service.wake_session("u1").state == "LISTENING"
    assert service.wake_session("u1").buffered == ""


def test_concurrent_flushes_do_not_duplicate_annotations(monkeypatch):
    """Found in review: `_flush_annotations` ran on the camera thread (every 3 s
    while drawing) and on Flask threads (pen off, session stop) with no lock.

    Both read `_saved_strokes`, both sliced the same pending strokes, and both
    inserted them - every annotation duplicated in MongoDB across the
    `insert_many` round trip, and `annotationsMade` double-counted.
    """
    import threading

    from services.engine_service import EngineService

    service = EngineService()
    inserted: list[dict] = []
    insert_lock = threading.Lock()

    class FakeCollection:
        def insert_many(self, docs):
            time.sleep(0.01)          # the round trip that opened the window
            with insert_lock:
                inserted.extend(docs)

        def update_one(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr("services.engine_service.annotations_collection",
                        lambda: FakeCollection())
    monkeypatch.setattr("services.engine_service.presentation_history",
                        lambda: FakeCollection())
    monkeypatch.setattr("services.engine_service.bus.publish", lambda event: None)

    keyboard = FakeKeyboard()
    service.dispatcher = build_dispatcher(keyboard, FakeCom(connected=True, slideshow=True))
    oid = "0" * 24
    service.session = {"sessionId": oid, "userId": oid, "presentationId": oid}

    for index in range(20):
        service.dispatcher.annotations.begin(1)
        service.dispatcher.annotations.add_point(0.1 * index, 0.2)
        service.dispatcher.annotations.add_point(0.1 * index + 0.05, 0.3)
        service.dispatcher.annotations.end()
    assert service.dispatcher.annotations.count == 20

    barrier = threading.Barrier(4)

    def flush():
        barrier.wait()
        service._flush_annotations()

    threads = [threading.Thread(target=flush) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(inserted) == 20, (
        f"{len(inserted)} annotations persisted for 20 strokes - duplicated"
    )
    assert service._annotations_made == 20
