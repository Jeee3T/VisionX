from flask import Blueprint, g

from middleware.auth import require_auth
from services import analytics_service
from utils.responses import success

analytics_bp = Blueprint("analytics", __name__, url_prefix="/api/analytics")


@analytics_bp.get("/dashboard")
@require_auth
def dashboard():
    return success(analytics_service.dashboard(g.user_id))


@analytics_bp.get("/presentations")
@require_auth
def presentation_analytics():
    return success(analytics_service.presentation_analytics(g.user_id))


@analytics_bp.get("/gestures")
@require_auth
def gesture_analytics():
    return success(analytics_service.gesture_analytics(g.user_id))
