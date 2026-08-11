from flask import Blueprint, g, request

from middleware.auth import require_auth
from services import gesture_service
from utils.responses import success

gesture_bp = Blueprint("gestures", __name__, url_prefix="/api/gestures")


@gesture_bp.get("/preferences")
@require_auth
def get_preferences():
    return success(gesture_service.get_preferences(g.user_id))


@gesture_bp.put("/preferences")
@require_auth
def update_preferences():
    payload = request.get_json(silent=True) or {}
    data = gesture_service.update_preferences(g.user_id, payload)
    return success(data, "Gesture mapping saved.")
