"""Presentation sessions - stored in the PresentationHistory collection."""

from datetime import datetime, timezone

from bson import ObjectId

from config.database import annotations, presentation_history, presentations
from utils.errors import NotFoundError, ValidationError
from utils.serializers import serialize, serialize_many

STATUS_READY = "READY"
STATUS_ACTIVE = "ACTIVE"
STATUS_COMPLETED = "COMPLETED"
VALID_STATUSES = (STATUS_READY, STATUS_ACTIVE, STATUS_COMPLETED)


def create(user_id: str, presentation_id: str | None) -> dict:
    presentation = None
    if presentation_id:
        presentation = presentations().find_one({
            "_id": ObjectId(presentation_id),
            "userId": ObjectId(user_id),
        })
        if not presentation:
            raise NotFoundError("Presentation not found.")

    document = {
        "userId": ObjectId(user_id),
        "presentationId": presentation["_id"] if presentation else None,
        "presentationTitle": presentation["title"] if presentation else "Untitled session",
        "status": STATUS_READY,
        "startTime": datetime.now(timezone.utc),
        "endTime": None,
        "duration": 0,
        "slidesNavigated": 0,
        "annotationsMade": 0,
        "gestureCounts": {},
    }
    result = presentation_history().insert_one(document)
    document["_id"] = result.inserted_id
    return document


def get_owned(user_id: str, session_id: str) -> dict:
    document = presentation_history().find_one({
        "_id": ObjectId(session_id),
        "userId": ObjectId(user_id),
    })
    if not document:
        raise NotFoundError("Session not found.")
    return document


def list_for_user(user_id: str, limit: int = 50, status: str | None = None) -> list[dict]:
    query: dict = {"userId": ObjectId(user_id)}
    if status:
        query["status"] = status
    cursor = presentation_history().find(query).sort("startTime", -1).limit(limit)
    return serialize_many(cursor)


def update(user_id: str, session_id: str, payload: dict) -> dict:
    updates: dict = {}
    if "status" in payload:
        status = str(payload["status"]).upper()
        if status not in VALID_STATUSES:
            raise ValidationError(f"Status must be one of: {', '.join(VALID_STATUSES)}")
        updates["status"] = status
    for numeric in ("slidesNavigated", "annotationsMade", "currentSlide"):
        if numeric in payload:
            updates[numeric] = max(0, int(payload[numeric]))
    if not updates:
        raise ValidationError("Nothing to update.")

    presentation_history().update_one(
        {"_id": ObjectId(session_id), "userId": ObjectId(user_id)},
        {"$set": updates},
    )
    return serialize(get_owned(user_id, session_id))


def mark_active(user_id: str, session_id: str) -> None:
    presentation_history().update_one(
        {"_id": ObjectId(session_id), "userId": ObjectId(user_id)},
        {"$set": {"status": STATUS_ACTIVE, "startTime": datetime.now(timezone.utc)}},
    )


def complete(user_id: str, session_id: str, summary: dict) -> dict:
    document = get_owned(user_id, session_id)
    end_time = datetime.now(timezone.utc)
    start_time = document.get("startTime") or end_time
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    duration = max(0, int((end_time - start_time).total_seconds()))

    updates = {
        "status": STATUS_COMPLETED,
        "endTime": end_time,
        "duration": duration,
        "slidesNavigated": int(summary.get("slidesNavigated", document.get("slidesNavigated", 0))),
        "gestureCounts": summary.get("gestureCounts", document.get("gestureCounts", {})),
        "commandsFired": int(summary.get("commandsFired", 0)),
    }
    if summary.get("annotationsMade") is not None:
        updates["annotationsMade"] = max(
            int(document.get("annotationsMade", 0)),
            int(summary.get("annotationsMade", 0)),
        )

    presentation_history().update_one({"_id": document["_id"]}, {"$set": updates})
    return serialize(get_owned(user_id, session_id))


def detail(user_id: str, session_id: str) -> dict:
    document = get_owned(user_id, session_id)
    payload = serialize(document)
    if document.get("presentationId"):
        payload["annotationCount"] = annotations().count_documents({
            "presentationId": document["presentationId"],
            "sessionId": document["_id"],
        })
    return payload
