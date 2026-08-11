"""Presentation upload, listing and lifecycle. Every query is user-scoped."""

from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId

from config.database import annotations, presentation_history, presentations
from config.settings import settings
from utils.errors import NotFoundError, ValidationError
from utils.files import count_slides, delete_files, generate_thumbnails, store_upload, validate_upload
from utils.serializers import serialize, serialize_many


def create(user_id: str, file, title: str) -> dict:
    ext = validate_upload(file)
    stored_name, path = store_upload(file, ext)

    total_slides = count_slides(path, ext)
    thumbnails = generate_thumbnails(path, ext, stored_name)
    if total_slides == 0 and thumbnails:
        total_slides = len(thumbnails)

    document = {
        "userId": ObjectId(user_id),
        "title": title or Path(file.filename).stem,
        "fileName": file.filename,
        "storedName": stored_name,
        "filePath": str(path),
        "fileType": ext.lstrip("."),
        "totalSlides": total_slides,
        "thumbnails": thumbnails,
        "uploadedAt": datetime.now(timezone.utc),
    }
    result = presentations().insert_one(document)
    document["_id"] = result.inserted_id
    return serialize(document, drop=("filePath",))


def list_for_user(user_id: str, search: str = "", limit: int = 100) -> list[dict]:
    query: dict = {"userId": ObjectId(user_id)}
    if search:
        query["title"] = {"$regex": search, "$options": "i"}
    cursor = presentations().find(query).sort("uploadedAt", -1).limit(limit)
    return serialize_many(cursor, drop=("filePath",))


def get_owned(user_id: str, presentation_id: str) -> dict:
    document = presentations().find_one({
        "_id": ObjectId(presentation_id),
        "userId": ObjectId(user_id),
    })
    if not document:
        raise NotFoundError("Presentation not found.")
    return document


def get_detail(user_id: str, presentation_id: str) -> dict:
    document = get_owned(user_id, presentation_id)
    sessions = list(
        presentation_history()
        .find({"presentationId": document["_id"], "userId": ObjectId(user_id)})
        .sort("startTime", -1)
        .limit(10)
    )
    annotation_count = annotations().count_documents({"presentationId": document["_id"]})
    payload = serialize(document, drop=("filePath",))
    payload["recentSessions"] = serialize_many(sessions)
    payload["annotationCount"] = annotation_count
    payload["fileExists"] = Path(document["filePath"]).exists()
    return payload


def rename(user_id: str, presentation_id: str, title: str, total_slides: int | None = None) -> dict:
    updates: dict = {}
    if title:
        updates["title"] = title
    if total_slides is not None:
        if total_slides < 0:
            raise ValidationError("Slide count cannot be negative.")
        updates["totalSlides"] = int(total_slides)
    if not updates:
        raise ValidationError("Nothing to update.")

    result = presentations().update_one(
        {"_id": ObjectId(presentation_id), "userId": ObjectId(user_id)},
        {"$set": updates},
    )
    if result.matched_count == 0:
        raise NotFoundError("Presentation not found.")
    return get_detail(user_id, presentation_id)


def delete(user_id: str, presentation_id: str) -> None:
    document = get_owned(user_id, presentation_id)
    delete_files(document.get("storedName", ""), document.get("thumbnails", []))
    presentations().delete_one({"_id": document["_id"]})
    annotations().delete_many({"presentationId": document["_id"]})
    presentation_history().update_many(
        {"presentationId": document["_id"]},
        {"$set": {"presentationDeleted": True}},
    )


def thumbnail_path(user_id: str, presentation_id: str, index: int) -> Path:
    document = get_owned(user_id, presentation_id)
    thumbs = document.get("thumbnails") or []
    if index < 1 or index > len(thumbs):
        raise NotFoundError("Slide preview not available.")
    path = settings.THUMBNAIL_DIR / thumbs[index - 1]
    if not path.exists():
        raise NotFoundError("Slide preview not available.")
    return path


def file_path(user_id: str, presentation_id: str) -> tuple[Path, str]:
    document = get_owned(user_id, presentation_id)
    path = Path(document["filePath"])
    if not path.exists():
        raise NotFoundError("The stored file is missing from the server.")
    return path, document.get("fileName", path.name)
