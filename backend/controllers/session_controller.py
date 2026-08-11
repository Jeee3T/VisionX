"""Start / end of a live gesture session (session store + preferences + engine)."""

import logging

from bson import ObjectId

from config.database import presentations
from services import gesture_service, session_service
from services.engine_service import engine_service
from utils.errors import NotFoundError
from utils.serializers import serialize

logger = logging.getLogger(__name__)


def start_engine(user_id: str, session_id: str, options: dict | None = None) -> dict:
    session = session_service.get_owned(user_id, session_id)

    presentation = None
    if session.get("presentationId"):
        presentation = presentations().find_one({
            "_id": ObjectId(session["presentationId"]),
            "userId": ObjectId(user_id),
        })
        if not presentation:
            raise NotFoundError("The presentation for this session no longer exists.")

    preferences = gesture_service.preferences_for_engine(user_id)
    status = engine_service.start(user_id, session, presentation, preferences, options or {})
    session_service.mark_active(user_id, session_id)

    return {
        "engine": status,
        "session": serialize(session_service.get_owned(user_id, session_id)),
        "presentation": serialize(presentation, drop=("filePath",)) if presentation else None,
    }


def complete_session(user_id: str, session_id: str, client_summary: dict | None = None) -> dict:
    """Close a session.

    The engine's own counters are authoritative whenever it actually ran this
    session; the client-reported summary is only a fallback for sessions closed
    after the engine had already stopped (page reload, backend restart).
    """
    summary = dict(client_summary or {})

    if engine_service.owns_session(user_id) and str(
        (engine_service.session or {}).get("sessionId")
    ) == str(session_id):
        try:
            summary.update(engine_service.stop(user_id))
        except Exception as exc:  # noqa: BLE001 - a stuck engine must not block session close
            logger.warning("Engine stop during session completion failed: %s", exc)

    session = session_service.complete(user_id, session_id, summary)
    return {"session": session, "summary": summary}
