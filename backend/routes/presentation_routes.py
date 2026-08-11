from flask import Blueprint, g, request, send_file

from middleware.auth import require_auth
from services import presentation_service
from utils.responses import success
from utils.validators import clean_str, to_object_id

presentation_bp = Blueprint("presentations", __name__, url_prefix="/api/presentations")


@presentation_bp.post("")
@require_auth
def upload():
    file = request.files.get("file")
    title = clean_str(request.form.to_dict(), "title", 120)
    presentation = presentation_service.create(g.user_id, file, title)
    return success({"presentation": presentation}, "Presentation uploaded.", 201)


@presentation_bp.get("")
@require_auth
def list_presentations():
    search = request.args.get("search", "").strip()[:80]
    items = presentation_service.list_for_user(g.user_id, search)
    return success({"presentations": items, "count": len(items)})


@presentation_bp.get("/<presentation_id>")
@require_auth
def get_presentation(presentation_id):
    to_object_id(presentation_id, "presentation id")
    return success({"presentation": presentation_service.get_detail(g.user_id, presentation_id)})


@presentation_bp.put("/<presentation_id>")
@require_auth
def update_presentation(presentation_id):
    to_object_id(presentation_id, "presentation id")
    payload = request.get_json(silent=True) or {}
    total = payload.get("totalSlides")
    presentation = presentation_service.rename(
        g.user_id,
        presentation_id,
        clean_str(payload, "title", 120),
        int(total) if total is not None else None,
    )
    return success({"presentation": presentation}, "Presentation updated.")


@presentation_bp.delete("/<presentation_id>")
@require_auth
def delete_presentation(presentation_id):
    to_object_id(presentation_id, "presentation id")
    presentation_service.delete(g.user_id, presentation_id)
    return success(message="Presentation deleted.")


@presentation_bp.get("/<presentation_id>/slides/<int:slide_number>")
@require_auth
def slide_image(presentation_id, slide_number):
    to_object_id(presentation_id, "presentation id")
    path = presentation_service.thumbnail_path(g.user_id, presentation_id, slide_number)
    return send_file(path, mimetype="image/png", max_age=3600)


@presentation_bp.get("/<presentation_id>/file")
@require_auth
def download(presentation_id):
    to_object_id(presentation_id, "presentation id")
    path, filename = presentation_service.file_path(g.user_id, presentation_id)
    return send_file(path, as_attachment=True, download_name=filename)
