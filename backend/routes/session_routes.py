from flask import Blueprint, g, request

from controllers import session_controller
from middleware.auth import require_auth
from services import session_service
from utils.responses import success
from utils.serializers import serialize
from utils.validators import to_object_id

session_bp = Blueprint("sessions", __name__, url_prefix="/api/sessions")


@session_bp.post("")
@require_auth
def create_session():
    payload = request.get_json(silent=True) or {}
    presentation_id = payload.get("presentationId")
    if presentation_id:
        to_object_id(presentation_id, "presentation id")
    session = session_service.create(g.user_id, presentation_id)
    return success({"session": serialize(session)}, "Session created.", 201)


@session_bp.get("")
@require_auth
def list_sessions():
    limit = min(int(request.args.get("limit", 50) or 50), 200)
    status = request.args.get("status")
    sessions = session_service.list_for_user(g.user_id, limit, status)
    return success({"sessions": sessions, "count": len(sessions)})


@session_bp.get("/<session_id>")
@require_auth
def get_session(session_id):
    to_object_id(session_id, "session id")
    return success({"session": session_service.detail(g.user_id, session_id)})


@session_bp.put("/<session_id>")
@require_auth
def update_session(session_id):
    to_object_id(session_id, "session id")
    payload = request.get_json(silent=True) or {}
    return success({"session": session_service.update(g.user_id, session_id, payload)}, "Session updated.")


@session_bp.post("/<session_id>/complete")
@require_auth
def complete_session(session_id):
    to_object_id(session_id, "session id")
    payload = request.get_json(silent=True) or {}
    result = session_controller.complete_session(g.user_id, session_id, payload)
    return success(result, "Session completed.")
