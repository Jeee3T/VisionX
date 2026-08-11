"""Registration, login and profile management."""

from datetime import datetime, timezone

from bson import ObjectId

from config.database import users
from services.gesture_service import ensure_preferences
from utils.errors import AuthError, ConflictError, NotFoundError, ValidationError
from utils.security import create_token, hash_password, verify_password
from utils.serializers import serialize

PUBLIC_DROP = ("password",)


def _public(user: dict) -> dict:
    return serialize(user, drop=PUBLIC_DROP)


def register(name: str, email: str, password: str) -> dict:
    if users().find_one({"email": email}):
        raise ConflictError("An account with that email already exists.")

    now = datetime.now(timezone.utc)
    document = {
        "name": name,
        "email": email,
        "password": hash_password(password),
        "profilePhoto": "",
        "createdAt": now,
    }
    result = users().insert_one(document)
    document["_id"] = result.inserted_id

    # Every user starts with a complete, usable gesture map.
    ensure_preferences(str(result.inserted_id))

    return {"token": create_token(str(result.inserted_id)), "user": _public(document)}


def login(email: str, password: str) -> dict:
    user = users().find_one({"email": email})
    if not user or not verify_password(password, user.get("password", "")):
        # Identical message either way - never reveal which accounts exist.
        raise AuthError("Incorrect email or password.")
    ensure_preferences(str(user["_id"]))
    return {"token": create_token(str(user["_id"])), "user": _public(user)}


def get_profile(user_id: str) -> dict:
    user = users().find_one({"_id": ObjectId(user_id)})
    if not user:
        raise NotFoundError("User not found.")
    return _public(user)


def update_profile(user_id: str, name: str | None, profile_photo: str | None) -> dict:
    updates: dict = {}
    if name:
        updates["name"] = name
    if profile_photo is not None:
        updates["profilePhoto"] = profile_photo
    if not updates:
        raise ValidationError("Nothing to update.")

    users().update_one({"_id": ObjectId(user_id)}, {"$set": updates})
    return get_profile(user_id)


def change_password(user_id: str, current_password: str, new_password: str) -> None:
    user = users().find_one({"_id": ObjectId(user_id)})
    if not user or not verify_password(current_password, user.get("password", "")):
        raise AuthError("Your current password is incorrect.")
    users().update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"password": hash_password(new_password)}},
    )
