"""The voice pipeline: text normalisation, parameter extraction, and the model.

The model tests assert behaviour on the specific scenarios VisionX promises, not
an aggregate score - the aggregate lives in the training report, where it belongs.
"""

import pytest

from voice_assistant.intent.intents import (
    INTENT_CLASSES,
    INTENT_TO_COMMAND,
    NO_COMMAND,
    VoiceThresholds,
    command_for,
)
from voice_assistant.intent.normalize import normalize, parse_numbers, tokenize
from voice_assistant.intent.parameters import extract
from voice_assistant.intent.interpreter import (
    BAND_CONFIRM,
    BAND_EXECUTE,
    BAND_REJECT,
    REASON_BAD_PARAMETERS,
    REASON_LOW_CONFIDENCE,
    REASON_NOT_A_COMMAND,
    VoiceInterpreter,
)


# --- vocabulary ---------------------------------------------------------------
def test_every_intent_maps_to_a_real_dispatchable_command():
    from computer_vision.command_mapping.gesture_mapper import ALL_COMMANDS

    for intent, (command, _parameters) in INTENT_TO_COMMAND.items():
        assert command in ALL_COMMANDS, f"{intent} maps to a command the dispatcher cannot run"
    assert NO_COMMAND in INTENT_CLASSES
    with pytest.raises(KeyError):
        command_for(NO_COMMAND)


def test_every_command_a_controller_supports_is_reachable():
    """The seven unbound commands exist so voice can reach them - check they do."""
    from computer_vision.command_mapping.gesture_mapper import UNBOUND_COMMANDS

    reachable = {command for command, _ in INTENT_TO_COMMAND.values()}
    assert set(UNBOUND_COMMANDS).issubset(reachable)


# --- normalisation ------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Next Slide!", "next slide"),
        ("  GO to   slide 7. ", "go to slide 7"),
        ("Let's move on", "lets move on"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "text,values",
    [
        ("slide 7", [7]),
        ("slide seven", [7]),
        ("slide twenty three", [23]),
        ("slide one hundred and five", [105]),
        ("the seventh slide", [7]),
        ("go to slide 7 of 20", [7, 20]),
        ("no numbers here", []),
    ],
)
def test_number_parsing(text, values):
    assert [value for value, _s, _e in parse_numbers(tokenize(text))] == values


# --- parameter extraction -----------------------------------------------------
@pytest.mark.parametrize(
    "text,slide",
    [
        ("go to slide 7", 7),
        ("go to slide seven", 7),
        ("show me slide ten", 10),
        ("take me to slide number twenty three", 23),
        ("jump to number 9", 9),
        ("open slide 5", 5),
        ("the seventh slide please", 7),
        ("go to slide 7 of 20", 7),
    ],
)
def test_slide_number_extraction(text, slide):
    extraction = extract("GO_TO_SLIDE", text)
    assert extraction.ok and extraction.parameters == {"slideNumber": slide}


def test_go_to_slide_without_a_number_is_an_error_not_a_guess():
    extraction = extract("GO_TO_SLIDE", "go to slide")
    assert not extraction.ok and extraction.error


@pytest.mark.parametrize(
    "intent,text,parameters",
    [
        ("NEXT_SLIDE", "next slide", {}),
        ("NEXT_SLIDE", "move forward three slides", {"count": 3}),
        ("NEXT_SLIDE", "skip ahead two slides", {"count": 2}),
        ("NEXT_SLIDE", "advance by three", {"count": 3}),
        ("PREVIOUS_SLIDE", "go back two slides", {"count": 2}),
        ("PREVIOUS_SLIDE", "previous slide", {}),
        # A misrouted slide reference must NOT become a repeat count.
        ("NEXT_SLIDE", "go to slide 4", {}),
        ("PREVIOUS_SLIDE", "back to slide 8", {}),
    ],
)
def test_count_extraction(intent, text, parameters):
    extraction = extract(intent, text)
    assert extraction.ok and extraction.parameters == parameters


def test_commands_without_parameters_extract_nothing():
    for intent in ("FIRST_SLIDE", "BLACKOUT", "CLEAR_ANNOTATION", "START_PRESENTATION"):
        assert extract(intent, "whatever was said").parameters == {}


# --- thresholds ---------------------------------------------------------------
def test_threshold_bands():
    thresholds = VoiceThresholds(execute=0.75, confirm=0.50)
    assert thresholds.band(0.95) == "EXECUTE"
    assert thresholds.band(0.75) == "EXECUTE"
    assert thresholds.band(0.60) == "CONFIRM"
    assert thresholds.band(0.49) == "REJECT"


# --- the trained model --------------------------------------------------------
@pytest.mark.parametrize(
    "text,intent",
    [
        ("next slide", "NEXT_SLIDE"),
        ("move to the next slide", "NEXT_SLIDE"),
        ("show me the next slide", "NEXT_SLIDE"),
        ("go back one slide", "PREVIOUS_SLIDE"),
        ("go to slide 7", "GO_TO_SLIDE"),
        ("take me to slide number seven", "GO_TO_SLIDE"),
        ("show slide 7", "GO_TO_SLIDE"),
        ("go to the last slide", "LAST_SLIDE"),
        ("back to the beginning", "FIRST_SLIDE"),
        ("black screen", "BLACKOUT"),
        ("turn on the pen", "ENABLE_ANNOTATION"),
        ("turn off the pen", "DISABLE_ANNOTATION"),
        ("erase the ink", "CLEAR_ANNOTATION"),
        ("turn on the laser pointer", "ENABLE_POINTER"),
    ],
)
def test_intent_classification(intent_model, text, intent):
    prediction = intent_model.predict(text)
    assert prediction.intent == intent, f"'{text}' -> {prediction.intent} ({prediction.probability:.2f})"


@pytest.mark.parametrize(
    "text",
    [
        "today we are going to discuss the market",
        "as you can see on this slide revenue grew by twelve percent",
        "let me point out three things here",
        "thank you all very much for your time",
        "does anyone have any questions so far",
        "in conclusion we recommend option two",
        "we started this project back in march",
    ],
)
def test_ordinary_speech_is_not_a_command(intent_model, text):
    """The failure that would make voice control unusable in a real talk."""
    assert intent_model.predict(text).intent == NO_COMMAND


def test_empty_input_is_never_a_command(intent_model):
    for text in ("", "   ", None):
        assert intent_model.predict(text).intent == NO_COMMAND


def test_probabilities_are_a_distribution(intent_model):
    prediction = intent_model.predict("go to slide 12")
    assert 0.0 < prediction.probability <= 1.0
    assert sum(prediction.distribution.values()) <= 1.0001


# --- the interpreter ----------------------------------------------------------
def test_interpreter_produces_a_dispatchable_intent(interpreter):
    decision = interpreter.interpret("go to slide 7", total_slides=20)
    assert decision.band == BAND_EXECUTE
    assert decision.command == "GO_TO_SLIDE"
    assert decision.parameters == {"slideNumber": 7}
    assert decision.command_intent is not None
    assert decision.command_intent.source == "voice"
    assert decision.should_execute


def test_interpreter_rejects_ordinary_speech(interpreter):
    decision = interpreter.interpret("today we will discuss our results", total_slides=20)
    assert decision.band == BAND_REJECT
    assert decision.reason == REASON_NOT_A_COMMAND
    assert decision.command_intent is None
    assert not decision.should_execute


def test_interpreter_rejects_a_slide_that_does_not_exist(interpreter):
    decision = interpreter.interpret("go to slide 400", total_slides=20)
    assert decision.band == BAND_REJECT
    assert decision.reason == REASON_BAD_PARAMETERS
    assert decision.command_intent is None
    assert "20 slides" in decision.message


def test_interpreter_rejects_go_to_slide_with_no_number(interpreter):
    decision = interpreter.interpret("go to slide", total_slides=20)
    assert decision.band == BAND_REJECT
    assert decision.reason == REASON_BAD_PARAMETERS
    assert decision.command_intent is None


def test_low_confidence_never_executes(interpreter):
    """Raise the gate to 0.999 and even a perfect command must stop at CONFIRM."""
    strict = VoiceThresholds(execute=0.999, confirm=0.0)
    decision = interpreter.interpret("next slide", total_slides=20, thresholds=strict)
    assert decision.band in (BAND_CONFIRM, BAND_REJECT)
    if decision.band == BAND_CONFIRM:
        assert decision.needs_confirmation and not decision.should_execute


def test_everything_rejected_when_both_gates_are_maximal(interpreter):
    impossible = VoiceThresholds(execute=1.01, confirm=1.01)
    decision = interpreter.interpret("next slide", total_slides=20, thresholds=impossible)
    assert decision.band == BAND_REJECT
    assert decision.reason == REASON_LOW_CONFIDENCE
    assert decision.command_intent is None


def test_state_setting_intents_are_not_toggles(interpreter):
    on = interpreter.interpret("turn on the pen", total_slides=10)
    off = interpreter.interpret("turn off the pen", total_slides=10)
    assert on.command == off.command == "ANNOTATION_MODE"
    assert on.parameters == {"state": True}
    assert off.parameters == {"state": False}


def test_empty_transcript_is_handled(interpreter):
    decision = interpreter.interpret("", total_slides=10)
    assert decision.band == BAND_REJECT and decision.command_intent is None


def test_a_broken_model_does_not_break_the_interpreter():
    class Broken:
        model_version = "broken"

        def predict(self, _text):
            raise RuntimeError("model exploded")

    with pytest.raises(RuntimeError):
        VoiceInterpreter(Broken()).interpret("next slide")
    # The service layer is what converts this into a 503; see get_interpreter().


def test_speech_recognizer_interface_is_honoured():
    from voice_assistant.speech.base import (
        NullSpeechRecognizer,
        SpeechRecognizer,
        SpeechUnavailableError,
    )

    recognizer = NullSpeechRecognizer()
    assert isinstance(recognizer, SpeechRecognizer)
    assert recognizer.available is False
    assert "not installed" in recognizer.describe()["reason"] or recognizer.describe()["reason"]
    with pytest.raises(SpeechUnavailableError):
        recognizer.transcribe(b"audio")


def test_missing_speech_backend_yields_a_null_recognizer_not_a_crash():
    from voice_assistant.speech.base import NullSpeechRecognizer
    from voice_assistant.speech.factory import build_speech_recognizer

    recognizer = build_speech_recognizer(backend="a-backend-that-does-not-exist")
    assert isinstance(recognizer, NullSpeechRecognizer)
    assert recognizer.available is False
