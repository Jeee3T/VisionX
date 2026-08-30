"""Voice assistant: status, transcription, interpretation and command history."""

from flask import Blueprint, g, request

from middleware.auth import require_auth
from services import voice_service
from utils.errors import ValidationError
from utils.responses import success
from utils.validators import require_fields

voice_bp = Blueprint("voice", __name__, url_prefix="/api/voice")


@voice_bp.get("/status")
@require_auth
def status():
    return success(voice_service.status(g.user_id))


@voice_bp.get("/commands")
@require_auth
def catalogue():
    return success(voice_service.catalogue())


@voice_bp.post("/utterance")
@require_auth
def utterance():
    """One short recording: transcribe it locally, interpret it, maybe run it.

    The audio is transcribed and discarded. It is never written to the uploads
    directory and never stored in MongoDB.
    """
    audio = request.files.get("audio")
    if audio is None:
        raise ValidationError("No audio was uploaded.")

    data = audio.read()
    execute = str(request.form.get("execute", "1")).lower() not in ("0", "false", "no")
    session_id = request.form.get("sessionId") or None

    return success(
        voice_service.transcribe_and_interpret(
            g.user_id, data, audio.filename or "utterance.webm",
            session_id=session_id, execute=execute,
        ),
        "Utterance processed.",
    )


@voice_bp.post("/interpret")
@require_auth
def interpret():
    """Interpret text directly - the typed fallback, and what the tests drive."""
    payload = request.get_json(silent=True) or {}
    require_fields(payload, ["text"])
    return success(
        voice_service.interpret(
            g.user_id, str(payload["text"]),
            session_id=payload.get("sessionId"),
            execute=bool(payload.get("execute", True)),
        ),
        "Interpreted.",
    )


@voice_bp.post("/confirm")
@require_auth
def confirm():
    """Run a command the presenter accepted from the confirmation band."""
    payload = request.get_json(silent=True) or {}
    require_fields(payload, ["text"])
    return success(
        voice_service.confirm(g.user_id, str(payload["text"]), payload.get("sessionId")),
        "Command executed.",
    )


# --- continuous listening -----------------------------------------------------
@voice_bp.post("/stream")
@require_auth
def stream_segment():
    """One segment from the always-on microphone.

    The browser posts short segments continuously; each is transcribed and offered
    to the wake-word machine. Most segments are ordinary speech and do nothing at
    all - only "Vision ... OK" reaches the trained intent model.

    Like /utterance, the audio is transcribed and discarded. Continuous listening
    is not continuous recording.
    """
    audio = request.files.get("audio")
    if audio is None:
        raise ValidationError("No audio was uploaded.")

    execute = str(request.form.get("execute", "1")).lower() not in ("0", "false", "no")
    session_id = request.form.get("sessionId") or None

    return success(
        voice_service.stream_segment(
            g.user_id, audio.read(), audio.filename or "segment.webm",
            session_id=session_id, execute=execute,
        ),
        "Segment processed.",
    )


@voice_bp.post("/stream/text")
@require_auth
def stream_text():
    """Text-mode continuous listening - the typed fallback, and what tests drive."""
    payload = request.get_json(silent=True) or {}
    require_fields(payload, ["text"])
    return success(
        voice_service.observe_segment(
            g.user_id, str(payload["text"]),
            session_id=payload.get("sessionId"),
            execute=bool(payload.get("execute", True)),
        ),
        "Segment processed.",
    )


@voice_bp.get("/wake")
@require_auth
def wake_status():
    """Where the wake-word machine currently is: listening, or mid-command."""
    return success(voice_service.wake_status(g.user_id))


@voice_bp.post("/wake/reset")
@require_auth
def reset_wake():
    """Abandon a half-captured command and go back to waiting for 'Vision'."""
    return success(voice_service.reset_wake_session(g.user_id), "Listening reset.")


@voice_bp.get("/history")
@require_auth
def history():
    return success(voice_service.history(
        g.user_id,
        limit=int(request.args.get("limit", 50)),
        session_id=request.args.get("sessionId") or None,
    ))


@voice_bp.delete("/history")
@require_auth
def clear_history():
    return success(voice_service.clear_history(g.user_id), "Voice command history deleted.")
