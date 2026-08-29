"""The voice assistant service: audio in, CommandIntent out, dispatcher does the rest.

    audio bytes -> SpeechRecognizer -> transcript
                -> VoiceInterpreter -> VoiceDecision (intent + confidence + parameters)
                -> EngineService.execute_intent -> CommandDispatcher -> PowerPoint

This module contains no PowerPoint logic whatsoever. It cannot: the only way it
can affect a slideshow is by handing a CommandIntent to the same dispatcher the
gesture engine uses.

Privacy: raw audio is transcribed and discarded - it is never written to disk by
this service and never stored in MongoDB. The transcript is stored only when the
user has left transcript retention on.
"""

import logging
import time
from datetime import datetime, timezone

from bson import ObjectId

from config.database import voice_commands
from config.settings import settings
from multimodal.context import context as multimodal_context
from services import personalization_service
from services.engine_service import engine_service
from services.event_bus import bus
from utils.errors import ApiError, ForbiddenError, ValidationError
from utils.serializers import serialize_many
from voice_assistant.intent.interpreter import (
    BAND_CONFIRM,
    BAND_EXECUTE,
    VoiceDecision,
    get_interpreter,
    interpreter_error,
)
from voice_assistant.speech.base import SpeechRecognizerError, SpeechUnavailableError
from voice_assistant.speech.factory import get_speech_recognizer

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 400


class VoiceUnavailableError(ApiError):
    status_code = 503
    code = "VOICE_UNAVAILABLE"


def _require_enabled(user_id: str) -> None:
    if not personalization_service.voice_enabled(user_id):
        raise ForbiddenError(
            "The voice assistant is off for your account. Turn it on in Voice settings.",
            code="VOICE_DISABLED",
        )


def _require_interpreter():
    interpreter = get_interpreter()
    if interpreter is None:
        raise VoiceUnavailableError(
            interpreter_error()
            or "The voice intent model is not available. Train it with "
               "`python -m voice_assistant.training.train_intent_model`."
        )
    return interpreter


def status(user_id: str) -> dict:
    """Everything the voice UI needs, including why it might not work."""
    from voice_assistant.intent.classifier import model_status
    from voice_assistant.speech.factory import probe

    settings_doc = personalization_service.ensure(user_id)
    backends = probe()
    recognizer_available = any(backends.values())

    reasons = []
    if not settings_doc.get("voiceEnabled"):
        reasons.append("The voice assistant is turned off for your account.")
    intent_state = model_status()
    if not intent_state["available"]:
        reasons.append(
            intent_state.get("error")
            or "The intent model has not been trained yet "
               "(`python -m voice_assistant.training.train_intent_model`)."
        )
    if not recognizer_available:
        reasons.append(
            "No speech-to-text backend is installed "
            "(`pip install -r backend/requirements-voice.txt`)."
        )

    thresholds = personalization_service.thresholds(user_id)
    return {
        "enabled": bool(settings_doc.get("voiceEnabled")),
        "ready": bool(settings_doc.get("voiceEnabled")) and intent_state["available"] and recognizer_available,
        # Text interpretation works without speech-to-text, so the UI can offer a
        # typed-command fallback when a microphone backend is missing.
        "canInterpretText": bool(settings_doc.get("voiceEnabled")) and intent_state["available"],
        "blockers": reasons,
        "intentModel": intent_state,
        "speechBackends": backends,
        "speechConfigured": settings.VOICE_STT_BACKEND or "auto",
        "whisperModel": settings.VOICE_WHISPER_MODEL,
        "thresholds": {"execute": thresholds.execute, "confirm": thresholds.confirm},
        "transcriptRetention": bool(settings_doc.get("voiceTranscriptRetention")),
        "sessionActive": bool(engine_service.session and engine_service.dispatcher),
    }


def catalogue() -> dict:
    from voice_assistant.intent.intents import intent_catalogue
    from voice_assistant.data.utterances import UTTERANCES

    return {
        "intents": [
            {**row, "examples": UTTERANCES.get(row["intent"], [])[:4]}
            for row in intent_catalogue()
        ]
    }


# --- the pipeline -------------------------------------------------------------
def transcribe(user_id: str, audio: bytes, filename: str = "utterance.webm") -> dict:
    """Speech-to-text only. Audio is discarded as soon as it is transcribed."""
    _require_enabled(user_id)
    if not audio:
        raise ValidationError("The recording was empty.")
    if len(audio) > settings.MAX_UTTERANCE_MB * 1024 * 1024:
        raise ValidationError(
            f"That recording is larger than {settings.MAX_UTTERANCE_MB} MB. Voice commands "
            "are short utterances."
        )

    recognizer = get_speech_recognizer(settings.VOICE_STT_BACKEND, settings.VOICE_WHISPER_MODEL)
    try:
        return recognizer.transcribe(audio, filename).as_dict()
    except SpeechUnavailableError as exc:
        raise VoiceUnavailableError(str(exc)) from exc
    except SpeechRecognizerError as exc:
        raise ValidationError(str(exc)) from exc


def interpret(user_id: str, transcript: str, session_id: str | None = None,
              execute: bool = True, speech: dict | None = None,
              latency_ms: float | None = None) -> dict:
    """Classify a transcript and, if it clears the execute gate, dispatch it."""
    _require_enabled(user_id)
    interpreter = _require_interpreter()

    total_slides = engine_service.dispatcher.total_slides if engine_service.dispatcher else 0
    started = time.perf_counter()
    decision = interpreter.interpret(
        transcript, total_slides=total_slides,
        thresholds=personalization_service.thresholds(user_id),
    )
    decision_ms = (time.perf_counter() - started) * 1000.0

    executed = False
    result: dict | None = None
    error: str | None = None

    if execute and decision.should_execute:
        try:
            result = engine_service.execute_intent(user_id, decision.command_intent)
            executed = True
        except ApiError as exc:
            error = exc.message
            logger.info("Voice command not dispatched: %s", exc.message)

    payload = {
        **decision.as_dict(),
        "executed": executed,
        "result": result,
        "error": error,
        "requiresConfirmation": decision.needs_confirmation,
        "speech": speech,
        "context": multimodal_context.snapshot(),
        "latency": {
            "intentMs": round(decision_ms, 2),
            "totalMs": round(latency_ms, 2) if latency_ms is not None else round(decision_ms, 2),
        },
    }

    _log_command(user_id, session_id, decision, executed, result, error, speech, payload["latency"])
    bus.publish({"type": "voice", **{k: v for k, v in payload.items() if k != "distribution"}})
    return payload


def confirm(user_id: str, transcript: str, session_id: str | None = None) -> dict:
    """Execute a command the presenter accepted from the confirmation band.

    The transcript is re-interpreted rather than trusting a client-supplied
    command: the browser can ask VisionX to run what it heard, not to run an
    arbitrary command of its choosing.
    """
    _require_enabled(user_id)
    interpreter = _require_interpreter()

    total_slides = engine_service.dispatcher.total_slides if engine_service.dispatcher else 0
    decision = interpreter.interpret(
        transcript, total_slides=total_slides,
        thresholds=personalization_service.thresholds(user_id),
    )
    if decision.band not in (BAND_EXECUTE, BAND_CONFIRM) or decision.command_intent is None:
        raise ValidationError(decision.message or "That is no longer a runnable command.")

    result = engine_service.execute_intent(user_id, decision.command_intent)
    payload = {**decision.as_dict(), "executed": True, "result": result, "confirmed": True}
    _log_command(user_id, session_id, decision, True, result, None, None, None, confirmed=True)
    bus.publish({"type": "voice", **{k: v for k, v in payload.items() if k != "distribution"}})
    return payload


def transcribe_and_interpret(user_id: str, audio: bytes, filename: str,
                             session_id: str | None = None, execute: bool = True) -> dict:
    started = time.perf_counter()
    speech = transcribe(user_id, audio, filename)
    return interpret(
        user_id, speech["text"], session_id=session_id, execute=execute,
        speech=speech, latency_ms=(time.perf_counter() - started) * 1000.0,
    )


# --- telemetry ----------------------------------------------------------------
def _log_command(user_id: str, session_id: str | None, decision: VoiceDecision,
                 executed: bool, result: dict | None, error: str | None,
                 speech: dict | None, latency: dict | None, confirmed: bool = False) -> None:
    """Store command-level telemetry. Never audio; transcript only if retained."""
    retain = bool(personalization_service.ensure(user_id).get("voiceTranscriptRetention"))
    document = {
        "userId": ObjectId(user_id),
        "sessionId": ObjectId(session_id) if session_id else None,
        "transcript": decision.transcript[:MAX_TRANSCRIPT_CHARS] if retain else None,
        "transcriptRetained": retain,
        "intent": decision.intent,
        "command": decision.command,
        "parameters": decision.parameters,
        "confidence": round(float(decision.probability), 4),
        "band": decision.band,
        "reason": decision.reason,
        "executed": executed,
        "confirmed": confirmed,
        "delivered": bool(result and result.get("delivered")),
        "error": error,
        "modelVersion": decision.model_version,
        "sttBackend": (speech or {}).get("backend"),
        "latencyMs": (latency or {}).get("totalMs"),
        "createdAt": datetime.now(timezone.utc),
    }
    try:
        voice_commands().insert_one(document)
    except Exception as exc:  # noqa: BLE001 - telemetry must never break a command
        logger.warning("Could not record voice telemetry: %s", exc)


def history(user_id: str, limit: int = 50, session_id: str | None = None) -> dict:
    query: dict = {"userId": ObjectId(user_id)}
    if session_id:
        query["sessionId"] = ObjectId(session_id)
    cursor = voice_commands().find(query).sort("createdAt", -1).limit(max(1, min(limit, 200)))
    return {"commands": serialize_many(cursor)}


def clear_history(user_id: str) -> dict:
    return {"deleted": personalization_service.delete_voice_history(user_id)}
