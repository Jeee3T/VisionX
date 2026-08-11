from flask import Blueprint, g, request

from middleware.auth import require_auth
from services import user_service
from utils.responses import success
from utils.validators import clean_str, require_fields, validate_password

user_bp = Blueprint("users", __name__, url_prefix="/api/users")


@user_bp.get("/me")
@require_auth
def get_me():
    return success({"user": user_service.get_profile(g.user_id)})


@user_bp.put("/me")
@require_auth
def update_me():
    payload = request.get_json(silent=True) or {}
    user = user_service.update_profile(
        g.user_id,
        name=clean_str(payload, "name", 80) or None,
        profile_photo=payload.get("profilePhoto"),
    )
    return success({"user": user}, "Profile updated.")


@user_bp.put("/me/password")
@require_auth
def update_password():
    payload = request.get_json(silent=True) or {}
    require_fields(payload, ["currentPassword", "newPassword"])
    user_service.change_password(
        g.user_id,
        payload["currentPassword"],
        validate_password(payload["newPassword"]),
    )
    return success(message="Password updated.")
