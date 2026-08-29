"""Per-user multimodal settings, model status and the data-deletion controls.

Kept in its own `personalization` collection rather than bolted onto
`gesture_preferences`, so that "delete my personalization data" can never take a
user's pose bindings, presentations or session history with it.

Consent is explicit and separate from the enable switch: `gestureLearningConsent`
must be true before any landmark data is recorded at all.
"""

import logging
from datetime import datetime, timezone

from bson import ObjectId

from computer_vision.gesture_recognition.poses import POSE_BY_NAME, pose_catalogue
from computer_vision.ml import paths, registry
from computer_vision.ml.dataset import GESTURE_CLASSES, NULL_CLASS, dataset_summary
from config.database import gesture_recordings, personalization, voice_commands
from config.settings import settings
from utils.errors import ForbiddenError, ValidationError
from utils.serializers import serialize
from voice_assistant.intent.intents import VoiceThresholds

logger = logging.getLogger(__name__)

BOOLEAN_FIELDS = (
    "gesturePersonalizationEnabled",
    "gestureLearningConsent",
    "voiceEnabled",
    "voiceTranscriptRetention",
)
FLOAT_FIELDS = {
    "gestureIntentMargin": (0.0, 0.6),
    "voiceExecuteThreshold": (0.4, 0.99),
    "voiceConfirmThreshold": (0.1, 0.95),
}


def defaults() -> dict:
    return {
        "gesturePersonalizationEnabled": settings.PERSONALIZATION_DEFAULT_ENABLED,
        "gestureLearningConsent": False,
        "gestureModelVersion": None,
        "gestureModelTrainedAt": None,
        "gestureIntentMargin": settings.GESTURE_INTENT_MARGIN,
        "voiceEnabled": settings.VOICE_DEFAULT_ENABLED,
        "voiceExecuteThreshold": settings.VOICE_EXECUTE_THRESHOLD,
        "voiceConfirmThreshold": settings.VOICE_CONFIRM_THRESHOLD,
        "voiceTranscriptRetention": settings.VOICE_RETAIN_TRANSCRIPTS,
    }


def ensure(user_id: str) -> dict:
    """Return the user's settings, creating defaults on first access."""
    oid = ObjectId(user_id)
    existing = personalization().find_one({"userId": oid})
    if existing:
        missing = {k: v for k, v in defaults().items() if k not in existing}
        if missing:
            personalization().update_one({"_id": existing["_id"]}, {"$set": missing})
            existing.update(missing)
        return existing

    document = {
        "userId": oid,
        **defaults(),
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }
    document["_id"] = personalization().insert_one(document).inserted_id
    return document


def update(user_id: str, payload: dict) -> dict:
    ensure(user_id)
    updates: dict = {}

    for field in BOOLEAN_FIELDS:
        if field in payload:
            updates[field] = bool(payload[field])

    for field, (low, high) in FLOAT_FIELDS.items():
        if field in payload:
            try:
                value = float(payload[field])
            except (TypeError, ValueError):
                raise ValidationError(f"'{field}' must be a number.") from None
            if not low <= value <= high:
                raise ValidationError(f"'{field}' must be between {low} and {high}.")
            updates[field] = value

    if not updates:
        raise ValidationError("Nothing to update.")

    if updates.get("voiceExecuteThreshold") is not None or updates.get("voiceConfirmThreshold") is not None:
        current = ensure(user_id)
        execute = updates.get("voiceExecuteThreshold", current["voiceExecuteThreshold"])
        confirm = updates.get("voiceConfirmThreshold", current["voiceConfirmThreshold"])
        if confirm > execute:
            raise ValidationError(
                "The confirmation threshold cannot be higher than the execute threshold."
            )

    # Turning consent off stops collection immediately; it does not delete what
    # has already been recorded - that is a separate, explicit action.
    if updates.get("gestureLearningConsent") is False:
        updates["gesturePersonalizationEnabled"] = False

    updates["updatedAt"] = datetime.now(timezone.utc)
    personalization().update_one({"userId": ObjectId(user_id)}, {"$set": updates})
    return get(user_id)


def record_model(user_id: str, model_version: str | None, trained_at: str | None) -> None:
    personalization().update_one(
        {"userId": ObjectId(user_id)},
        {"$set": {
            "gestureModelVersion": model_version,
            "gestureModelTrainedAt": trained_at,
            "updatedAt": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


# --- read models --------------------------------------------------------------
def gesture_status(user_id: str) -> dict:
    """Model + dataset state for one user, for the settings screen."""
    subject = subject_id(user_id)
    return {
        "model": registry.model_status(user_id),
        "dataset": dataset_summary(subject=subject),
        "subject": subject,
        "classes": [
            {
                "name": name,
                "label": (POSE_BY_NAME[name].label if name in POSE_BY_NAME
                          else "Other / no command"),
                "description": (POSE_BY_NAME[name].description if name in POSE_BY_NAME
                                else "Natural hand movement that must NOT fire a command"),
                "isNull": name == NULL_CLASS,
            }
            for name in GESTURE_CLASSES
        ],
        "framesPerRecording": settings.ENROLLMENT_FRAMES_PER_RECORDING,
        "recordingsPerGesture": settings.ENROLLMENT_RECORDINGS_PER_GESTURE,
        "poses": pose_catalogue(),
    }


def voice_status(user_id: str) -> dict:
    from voice_assistant.intent.classifier import model_status as intent_model_status
    from voice_assistant.speech.factory import probe

    backends = probe()
    return {
        "intentModel": intent_model_status(),
        "speechBackends": backends,
        "speechAvailable": any(backends.values()),
        "commandsLogged": voice_commands().count_documents({"userId": ObjectId(user_id)}),
    }


def get(user_id: str) -> dict:
    document = ensure(user_id)
    return {
        "settings": serialize(document),
        "gesture": gesture_status(user_id),
        "voice": voice_status(user_id),
        "storage": {
            "modelDir": str(paths.user_model_dir(user_id)),
            "datasetDir": str(paths.gesture_dataset_dir(subject_id(user_id))),
        },
    }


def subject_id(user_id: str) -> str:
    """Dataset subject for a user. Namespaced so it can never collide with 'synthetic:v1'."""
    return f"user:{user_id}"


def thresholds(user_id: str) -> VoiceThresholds:
    document = ensure(user_id)
    return VoiceThresholds(
        execute=float(document.get("voiceExecuteThreshold", settings.VOICE_EXECUTE_THRESHOLD)),
        confirm=float(document.get("voiceConfirmThreshold", settings.VOICE_CONFIRM_THRESHOLD)),
    )


def require_consent(user_id: str) -> None:
    if not ensure(user_id).get("gestureLearningConsent"):
        raise ForbiddenError(
            "Gesture learning is off for your account. Turn on 'Allow gesture "
            "learning/personalization' in Gesture settings before training.",
            code="CONSENT_REQUIRED",
        )


def personalization_enabled(user_id: str) -> bool:
    document = ensure(user_id)
    return bool(document.get("gesturePersonalizationEnabled")) and registry.has_model(user_id)


def voice_enabled(user_id: str) -> bool:
    return bool(ensure(user_id).get("voiceEnabled"))


def intent_margin(user_id: str) -> float:
    return float(ensure(user_id).get("gestureIntentMargin", settings.GESTURE_INTENT_MARGIN))


# --- deletion -----------------------------------------------------------------
def delete_gesture_data(user_id: str, delete_model: bool = True,
                        delete_recordings: bool = True) -> dict:
    """Remove personalization data. Session history is deliberately untouched."""
    removed = {"model": False, "recordings": 0, "files": 0}

    if delete_model:
        removed["model"] = registry.delete_model(user_id)
        record_model(user_id, None, None)

    if delete_recordings:
        directory = paths.gesture_dataset_dir(subject_id(user_id))
        if directory.exists():
            for path in sorted(directory.glob("*.jsonl")):
                path.unlink(missing_ok=True)
                removed["files"] += 1
            try:
                directory.rmdir()
            except OSError:
                pass
        removed["recordings"] = gesture_recordings().delete_many(
            {"userId": ObjectId(user_id)}
        ).deleted_count

    logger.info("Deleted personalization data for %s: %s", user_id, removed)
    return removed


def delete_voice_history(user_id: str) -> int:
    return voice_commands().delete_many({"userId": ObjectId(user_id)}).deleted_count
