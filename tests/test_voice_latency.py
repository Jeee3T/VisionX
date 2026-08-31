"""Voice latency: where the seconds were, and that they are gone.

§7 asks for the *source* of the delay to be fixed rather than the UI changed, so
these tests measure the pipeline's structure rather than a wall clock - a timing
assertion on CI hardware would be a coin flip, and would tell you nothing about
why a command was slow.

Three costs were found, in the order the audio meets them:

  1. a fixed 3-second recording window, so "OK" waited up to 3 s to be uploaded
     (tests/frontend: useContinuousVoice's endpointer replaces it)
  2. the Whisper and intent models loading lazily, so the FIRST command of a talk
     paid several seconds and every later one did not
  3. work done per segment that need not be done per segment

The wake machine and the trained intent model are unchanged, and the tests below
assert that too: reusing them was a requirement (§8), not an accident.
"""

import time

import pytest

from voice_assistant.wake import (
    ACTION_ARMED,
    ACTION_EXECUTE,
    ACTION_IDLE,
    STATE_LISTENING,
    WakeWordSession,
)


# ==================================== 2. THE MODELS LOAD ONCE, AT BOOT =======
def test_the_speech_recognizer_is_a_process_wide_singleton():
    """Requirement §7: no repeated model initialisation.

    A recognizer built per request would reload Whisper's weights on every
    command - the single most expensive thing in the pipeline, paid over and over.
    """
    from voice_assistant.speech.factory import get_speech_recognizer, reset_speech_recognizer

    reset_speech_recognizer()
    try:
        first = get_speech_recognizer()
        second = get_speech_recognizer()
        assert first is second
    finally:
        reset_speech_recognizer()


def test_the_intent_model_is_a_process_wide_singleton():
    from voice_assistant.intent.interpreter import get_interpreter

    first = get_interpreter()
    if first is None:
        pytest.skip("the voice intent model has not been trained")
    assert get_interpreter() is first


def test_prewarm_never_blocks_startup(monkeypatch):
    """A missing or broken speech backend must not stop the API coming up."""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in (str(root), str(root / "backend")):
        if path not in sys.path:
            sys.path.insert(0, path)

    import app as flask_app
    from config.settings import settings

    monkeypatch.setattr(settings, "VOICE_PREWARM", True)
    monkeypatch.setattr(
        "voice_assistant.speech.factory.get_speech_recognizer",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no backend here")),
    )

    started = time.perf_counter()
    flask_app._prewarm_voice()
    # Returns immediately: the work is on a daemon thread, and a raising backend
    # is swallowed rather than propagated into create_app().
    assert time.perf_counter() - started < 0.5


def test_prewarm_can_be_turned_off(monkeypatch):
    """A machine that will never use voice should not load Whisper at boot."""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in (str(root), str(root / "backend")):
        if path not in sys.path:
            sys.path.insert(0, path)

    import app as flask_app
    from config.settings import settings

    called: list[str] = []
    monkeypatch.setattr(settings, "VOICE_PREWARM", False)
    monkeypatch.setattr(
        "voice_assistant.speech.factory.get_speech_recognizer",
        lambda *args, **kwargs: called.append("loaded"),
    )
    flask_app._prewarm_voice()
    time.sleep(0.05)
    assert called == []


def test_the_whisper_backend_exposes_a_warm_up():
    """Prewarming the weights is not enough - the first inference is slower.

    `warm_up` runs a second of silence through the model so the runtime builds
    its graph at boot rather than on the presenter's first command. It existed
    and was never called; this pins the contract the boot path depends on.
    """
    from voice_assistant.speech.whisper_recognizer import FasterWhisperRecognizer

    assert callable(getattr(FasterWhisperRecognizer, "warm_up", None))


# ==================================== 3. WORK PER SEGMENT ====================
def test_ordinary_speech_never_reaches_the_intent_model():
    """The cheapest possible path for the common case.

    Almost every segment of a talk is ordinary speech. It is matched against the
    wake vocabulary - a compiled regex - and dropped. Nothing is classified,
    nothing is dispatched and nothing is written to MongoDB.
    """
    session = WakeWordSession()
    talk = [
        "so if we look at the next slide you can see the results",
        "our vision going forward is fairly simple",
        "i want to go back to the point about annotation",
        "okay so that is the architecture",
    ]
    for line in talk:
        outcome = session.observe(line)
        assert outcome.action == ACTION_IDLE, f"ordinary speech armed VisionX: {line!r}"
        assert outcome.commands == []
    assert session.state == STATE_LISTENING


def test_a_command_completes_in_the_segment_that_carries_it():
    """§6: "Vision <command> OK" spoken in one breath runs on that segment.

    No second round trip, no waiting for a following segment to confirm the
    command ended - the terminator is the confirmation.
    """
    session = WakeWordSession()
    outcome = session.observe("vision go to next slide ok")

    assert outcome.action == ACTION_EXECUTE
    assert outcome.command == "go to next slide"
    assert outcome.commands == ["go to next slide"]
    # And it is immediately ready for the next one, with no reset call needed.
    assert session.state == STATE_LISTENING


def test_a_command_split_across_segments_still_completes():
    """The endpointer cuts on silence, so a slow speaker spans segments."""
    session = WakeWordSession()
    assert session.observe("vision").action == ACTION_ARMED
    assert session.observe("previous slide").commands == []
    outcome = session.observe("ok")

    assert outcome.action == ACTION_EXECUTE
    assert outcome.command == "previous slide"


def test_two_commands_in_one_segment_both_run():
    """A longer segment must not silently drop the first command in it.

    The endpointer's ceiling means a fast presenter can fit two commands into one
    recording. Running only the last would lose a slide change with no error.
    """
    session = WakeWordSession()
    outcome = session.observe("vision next slide ok vision go to slide five ok")
    assert outcome.commands == ["next slide", "go to slide five"]


def test_the_microphone_returns_to_listening_without_being_touched():
    """§5/§9: no interaction between commands, ever."""
    session = WakeWordSession()
    for _ in range(5):
        assert session.observe("vision next slide ok").action == ACTION_EXECUTE
        assert session.state == STATE_LISTENING
        assert session.observe("and as i was saying about the next slide").action == ACTION_IDLE


# ==================================== 8. THE TRAINED MODEL IS REUSED =========
def test_the_shipped_intent_model_is_the_one_described_in_the_requirements():
    """§8: reused, not retrained.

    The requirement names the artifact precisely - 1,008 utterances, 15 intents,
    TF-IDF into logistic regression, ~90% test accuracy. Asserting those numbers
    is how "reuse the existing model" becomes checkable: a retrain to chase
    latency would move them, and this would say so.
    """
    from voice_assistant.intent.classifier import model_status

    status = model_status()
    if not status["available"]:
        pytest.skip("the voice intent model has not been trained")

    assert status["utterances"]["total"] == 1008
    assert len(status["intents"]) == 15
    assert status["metrics"]["test"]["accuracy"] == pytest.approx(0.90, abs=0.03)


def test_the_pipeline_is_still_tfidf_into_logistic_regression():
    """The same §8 check, on the artifact rather than its metrics."""
    from voice_assistant.intent.classifier import IntentModel, IntentModelError

    try:
        model = IntentModel.load()
    except IntentModelError as exc:
        pytest.skip(f"voice intent model unavailable: {exc}")

    steps = {name: type(step).__name__ for name, step in model.pipeline.steps}
    assert steps["classifier"] == "LogisticRegression"
    # TF-IDF over word and character n-grams, unioned - the character half is
    # what keeps a Whisper mis-transcription ("nex slide") classifiable.
    features = model.pipeline.named_steps["features"]
    assert {type(t).__name__ for _, t in features.transformer_list} == {"TfidfVectorizer"}


def test_a_completed_command_is_classified_by_the_trained_model(interpreter):
    """The wake machine decides *when*; the model still decides *what*."""
    session = WakeWordSession()
    outcome = session.observe("vision go to next slide ok")
    assert outcome.should_execute

    decision = interpreter.interpret(outcome.command, total_slides=20)
    assert decision.command == "NEXT_SLIDE"
    assert decision.should_execute


def test_a_spoken_slide_number_survives_the_wake_machine(interpreter):
    session = WakeWordSession()
    outcome = session.observe("vision go to slide seven ok")
    decision = interpreter.interpret(outcome.command, total_slides=20)

    assert decision.command == "GO_TO_SLIDE"
    assert decision.parameters.get("slideNumber") == 7


def test_speech_that_reads_as_a_command_without_the_wake_word_does_nothing(interpreter):
    """§9, end to end: the confidence gate is not what protects the deck.

    "The next slide contains our results" classifies as NEXT_SLIDE with high
    confidence, because it genuinely is that phrase. What stops it is that the
    intent model never sees it.
    """
    sentence = "the next slide contains our results"
    session = WakeWordSession()
    assert session.observe(sentence).commands == []

    # Proof that the gate below would NOT have caught it.
    decision = interpreter.interpret(sentence, total_slides=20)
    assert decision.command == "NEXT_SLIDE"
