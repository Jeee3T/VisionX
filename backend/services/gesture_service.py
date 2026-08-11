"""Gesture preference storage - the single source of truth for pose bindings."""

from bson import ObjectId

from computer_vision.command_mapping.gesture_mapper import (
    COMMAND_LABELS,
    DEFAULT_PREFERENCES,
    PREFERENCE_FIELDS,
    validate_preferences,
)
from computer_vision.gesture_recognition.poses import pose_catalogue
from config.database import gesture_preferences
from utils.errors import ValidationError
from utils.serializers import serialize


def ensure_preferences(user_id: str) -> dict:
    """Return the user's bindings, creating the defaults on first access."""
    oid = ObjectId(user_id)
    existing = gesture_preferences().find_one({"userId": oid})
    if existing:
        # Backfill any field added after the document was first written.
        missing = {k: v for k, v in DEFAULT_PREFERENCES.items() if not existing.get(k)}
        if missing:
            gesture_preferences().update_one({"_id": existing["_id"]}, {"$set": missing})
            existing.update(missing)
        return existing

    document = {"userId": oid, **DEFAULT_PREFERENCES}
    result = gesture_preferences().insert_one(document)
    document["_id"] = result.inserted_id
    return document


def get_preferences(user_id: str) -> dict:
    document = ensure_preferences(user_id)
    return {
        "preferences": serialize(document),
        "poses": pose_catalogue(),
        "commands": [
            {"field": field, "command": command, "label": COMMAND_LABELS[command]}
            for field, command in PREFERENCE_FIELDS.items()
        ],
        "defaults": DEFAULT_PREFERENCES,
    }


def update_preferences(user_id: str, payload: dict) -> dict:
    candidate = {field: payload.get(field) for field in PREFERENCE_FIELDS}
    ok, message = validate_preferences(candidate)
    if not ok:
        raise ValidationError(message)

    gesture_preferences().update_one(
        {"userId": ObjectId(user_id)},
        {"$set": candidate},
        upsert=True,
    )

    # If this user has a live session, re-bind it without a restart.
    from services.engine_service import engine_service

    engine_service.apply_preferences(user_id, candidate)
    return get_preferences(user_id)


def preferences_for_engine(user_id: str) -> dict:
    document = ensure_preferences(user_id)
    return {field: document.get(field, DEFAULT_PREFERENCES[field]) for field in PREFERENCE_FIELDS}
