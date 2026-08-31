"""Annotation persistence. Ownership is enforced through the parent presentation."""

from datetime import datetime, timezone

from bson import ObjectId

from config.database import annotations, presentations
from utils.errors import NotFoundError, ValidationError
from utils.serializers import serialize, serialize_many

# Which coordinate space a stroke's points are in. Strokes come from two places
# that do NOT agree, and nothing recorded which was which:
#
#   "camera"  the fingertip, normalised over the camera frame. The presenter
#             cannot comfortably reach the edges of that frame, so it is inset by
#             a margin and stretched back over the slide when drawn.
#   "slide"   already normalised over the slide itself - a mouse or touch stroke
#             drawn straight onto the canvas. Stretching it moves it.
#
# Storing this is the fix for mouse-drawn ink reappearing somewhere other than
# where it was drawn. "camera" is the default because every stroke written before
# this field existed came from the gesture engine.
SPACE_CAMERA = "camera"
SPACE_SLIDE = "slide"
COORDINATE_SPACES = (SPACE_CAMERA, SPACE_SLIDE)


def _assert_owns_presentation(user_id: str, presentation_id: str) -> ObjectId:
    oid = ObjectId(presentation_id)
    owned = presentations().find_one({"_id": oid, "userId": ObjectId(user_id)}, {"_id": 1})
    if not owned:
        raise NotFoundError("Presentation not found.")
    return oid


def create(user_id: str, presentation_id: str, slide_number: int, annotation_data: dict,
           session_id: str | None = None) -> dict:
    presentation_oid = _assert_owns_presentation(user_id, presentation_id)

    space = str((annotation_data or {}).get("space") or SPACE_CAMERA).lower()
    if space not in COORDINATE_SPACES:
        raise ValidationError(
            f"Unknown annotation coordinate space '{space}'. "
            f"Expected one of: {', '.join(COORDINATE_SPACES)}."
        )

    points = (annotation_data or {}).get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise ValidationError("An annotation needs at least two points.")
    if len(points) > 5000:
        raise ValidationError("That annotation is too large to store.")
    for point in points:
        if not isinstance(point, dict) or "x" not in point or "y" not in point:
            raise ValidationError("Each annotation point needs an x and y value.")

    document = {
        "presentationId": presentation_oid,
        "userId": ObjectId(user_id),
        "sessionId": ObjectId(session_id) if session_id else None,
        "slideNumber": max(1, int(slide_number)),
        "annotationData": {
            "points": [{"x": float(p["x"]), "y": float(p["y"])} for p in points],
            "colour": str(annotation_data.get("colour", "#ef4444"))[:16],
            "width": int(annotation_data.get("width", 4)),
            "space": space,
        },
        "createdAt": datetime.now(timezone.utc),
    }
    result = annotations().insert_one(document)
    document["_id"] = result.inserted_id
    return serialize(document)


def list_for_slide(user_id: str, presentation_id: str, slide_number: int) -> list[dict]:
    presentation_oid = _assert_owns_presentation(user_id, presentation_id)
    cursor = annotations().find({
        "presentationId": presentation_oid,
        "slideNumber": int(slide_number),
    }).sort("createdAt", 1)
    return serialize_many(cursor)


def list_for_presentation(user_id: str, presentation_id: str) -> list[dict]:
    presentation_oid = _assert_owns_presentation(user_id, presentation_id)
    cursor = annotations().find({"presentationId": presentation_oid}).sort("createdAt", 1)
    return serialize_many(cursor)


def delete(user_id: str, annotation_id: str) -> None:
    result = annotations().delete_one({
        "_id": ObjectId(annotation_id),
        "userId": ObjectId(user_id),
    })
    if result.deleted_count == 0:
        raise NotFoundError("Annotation not found.")


def clear_slide(user_id: str, presentation_id: str, slide_number: int) -> int:
    presentation_oid = _assert_owns_presentation(user_id, presentation_id)
    result = annotations().delete_many({
        "presentationId": presentation_oid,
        "slideNumber": int(slide_number),
    })
    return result.deleted_count
