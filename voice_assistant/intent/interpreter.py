"""Transcript -> decision. The whole voice pipeline above speech-to-text.

    text -> intent classifier -> confidence band -> parameter extraction
         -> validation -> CommandIntent (or nothing at all)

Nothing here executes anything. It returns a `VoiceDecision` describing what it
believes and how sure it is; the backend service decides whether to dispatch it,
ask the presenter to confirm, or stay silent.

Three bands, because a probability is not a promise:

    EXECUTE   p >= thresholds.execute   dispatch it
    CONFIRM   p >= thresholds.confirm   show it, wait for a tap
    REJECT    otherwise                 do nothing, say nothing

Ordinary speech must land in REJECT. A presenter saying "let's move on to the
next topic" cannot be allowed to skip a slide.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from multimodal.command import CommandIntent, CommandParameterError, SOURCE_VOICE, build
from voice_assistant.intent.classifier import IntentModel, IntentModelError, default_model_dir
from voice_assistant.intent.intents import (
    INTENT_LABELS,
    NO_COMMAND,
    VoiceThresholds,
    command_for,
)
from voice_assistant.intent.normalize import normalize
from voice_assistant.intent.parameters import extract

logger = logging.getLogger(__name__)

BAND_EXECUTE = "EXECUTE"
BAND_CONFIRM = "CONFIRM"
BAND_REJECT = "REJECT"

REASON_OK = "ok"
REASON_EMPTY = "empty_transcript"
REASON_NOT_A_COMMAND = "not_a_command"
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_BAD_PARAMETERS = "invalid_parameters"

from computer_vision.command_mapping.gesture_mapper import COMMAND_LABELS  # noqa: E402


@dataclass
class VoiceDecision:
    transcript: str
    normalized: str = ""
    intent: str = NO_COMMAND
    probability: float = 0.0
    band: str = BAND_REJECT
    reason: str = REASON_NOT_A_COMMAND
    message: str = ""
    command: str | None = None
    parameters: dict = field(default_factory=dict)
    command_intent: CommandIntent | None = None
    distribution: dict = field(default_factory=dict)
    model_version: str = ""

    @property
    def should_execute(self) -> bool:
        return self.band == BAND_EXECUTE and self.command_intent is not None

    @property
    def needs_confirmation(self) -> bool:
        return self.band == BAND_CONFIRM and self.command_intent is not None

    def as_dict(self) -> dict:
        return {
            "transcript": self.transcript,
            "normalized": self.normalized,
            "intent": self.intent,
            "intentLabel": INTENT_LABELS.get(self.intent, self.intent),
            "probability": round(float(self.probability), 4),
            "band": self.band,
            "reason": self.reason,
            "message": self.message,
            "command": self.command,
            "commandLabel": COMMAND_LABELS.get(self.command) if self.command else None,
            "parameters": dict(self.parameters),
            "distribution": self.distribution,
            "modelVersion": self.model_version,
        }


class VoiceInterpreter:
    def __init__(self, model: IntentModel, thresholds: VoiceThresholds | None = None):
        self.model = model
        self.thresholds = thresholds or VoiceThresholds()

    def interpret(self, transcript: str, total_slides: int = 0,
                  thresholds: VoiceThresholds | None = None) -> VoiceDecision:
        thresholds = thresholds or self.thresholds
        normalized = normalize(transcript)

        if not normalized:
            return VoiceDecision(
                transcript=transcript or "", normalized="", reason=REASON_EMPTY,
                message="I did not hear anything.", model_version=self.model.model_version,
            )

        prediction = self.model.predict(transcript)
        decision = VoiceDecision(
            transcript=transcript,
            normalized=normalized,
            intent=prediction.intent,
            probability=prediction.probability,
            distribution=prediction.distribution,
            model_version=prediction.model_version,
        )

        if prediction.intent == NO_COMMAND:
            decision.reason = REASON_NOT_A_COMMAND
            decision.message = "That did not sound like a command."
            return decision

        band = thresholds.band(prediction.probability)
        if band == BAND_REJECT:
            decision.reason = REASON_LOW_CONFIDENCE
            decision.message = (
                f"I am only {prediction.probability:.0%} sure you meant "
                f"'{INTENT_LABELS.get(prediction.intent, prediction.intent)}', so I did nothing."
            )
            return decision

        command, fixed = command_for(prediction.intent)
        extraction = extract(prediction.intent, transcript)
        if not extraction.ok:
            decision.band = BAND_REJECT
            decision.reason = REASON_BAD_PARAMETERS
            decision.command = command
            decision.message = extraction.error or "I could not work out the details."
            return decision

        parameters = {**fixed, **extraction.parameters}
        try:
            command_intent = build(
                command, SOURCE_VOICE, parameters,
                confidence=prediction.probability, transcript=transcript,
                model_version=prediction.model_version, total_slides=total_slides,
            )
        except CommandParameterError as exc:
            decision.band = BAND_REJECT
            decision.reason = REASON_BAD_PARAMETERS
            decision.command = command
            decision.parameters = parameters
            decision.message = str(exc)
            return decision

        decision.band = band
        decision.reason = REASON_OK
        decision.command = command
        decision.parameters = command_intent.parameters
        decision.command_intent = command_intent
        decision.message = _describe(command, command_intent.parameters)
        return decision


def _describe(command: str, parameters: dict) -> str:
    label = COMMAND_LABELS.get(command, command)
    if "slideNumber" in parameters:
        return f"{label} {parameters['slideNumber']}"
    if parameters.get("count", 1) > 1:
        return f"{label} x{parameters['count']}"
    return label


# --- process-wide instance ----------------------------------------------------
_lock = threading.Lock()
_interpreter: VoiceInterpreter | None = None
_load_error: str | None = None


def get_interpreter(directory: Path | None = None) -> VoiceInterpreter | None:
    """Load the intent model once. Returns None (never raises) when unavailable."""
    global _interpreter, _load_error
    with _lock:
        if _interpreter is not None:
            return _interpreter
        try:
            _interpreter = VoiceInterpreter(IntentModel.load(directory or default_model_dir()))
            _load_error = None
            logger.info("Voice intent model %s loaded", _interpreter.model.model_version)
        except IntentModelError as exc:
            _load_error = str(exc)
            logger.warning("Voice intent model unavailable: %s", exc)
        except Exception as exc:  # noqa: BLE001 - a broken model must not break the API
            _load_error = str(exc)
            logger.exception("Unexpected error loading the voice intent model")
        return _interpreter


def interpreter_error() -> str | None:
    return _load_error


def reset_interpreter() -> None:
    global _interpreter, _load_error
    with _lock:
        _interpreter = None
        _load_error = None
