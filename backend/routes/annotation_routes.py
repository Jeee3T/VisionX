from flask import Blueprint, g, request

from middleware.auth import require_auth
from services import annotation_service
from utils.responses import success
from utils.validators import require_fields, to_object_id

annotation_bp = Blueprint("annotations", __name__, url_prefix="/api/annotations")


@annotation_bp.post("")
@require_auth
def create_annotation():
    payload = request.get_json(silent=True) or {}
    require_fields(payload, ["presentationId"])
    to_object_id(payload["presentationId"], "presentation id")
    annotation = annotation_service.create(
        g.user_id,
        payload["presentationId"],
        int(payload.get("slideNumber", 1)),
        payload.get("annotationData") or {},
        payload.get("sessionId"),
    )
    return success({"annotation": annotation}, "Annotation saved.", 201)


@annotation_bp.get("/<presentation_id>/<int:slide_number>")
@require_auth
def list_for_slide(presentation_id, slide_number):
    to_object_id(presentation_id, "presentation id")
    items = annotation_service.list_for_slide(g.user_id, presentation_id, slide_number)
    return success({"annotations": items, "count": len(items)})


@annotation_bp.get("/presentation/<presentation_id>")
@require_auth
def list_for_presentation(presentation_id):
    to_object_id(presentation_id, "presentation id")
    items = annotation_service.list_for_presentation(g.user_id, presentation_id)
    return success({"annotations": items, "count": len(items)})


@annotation_bp.delete("/<annotation_id>")
@require_auth
def delete_annotation(annotation_id):
    to_object_id(annotation_id, "annotation id")
    annotation_service.delete(g.user_id, annotation_id)
    return success(message="Annotation deleted.")


@annotation_bp.delete("/<presentation_id>/<int:slide_number>")
@require_auth
def clear_slide(presentation_id, slide_number):
    to_object_id(presentation_id, "presentation id")
    removed = annotation_service.clear_slide(g.user_id, presentation_id, slide_number)
    return success({"removed": removed}, "Slide annotations cleared.")
