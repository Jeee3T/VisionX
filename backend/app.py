"""VisionX Flask API entry point.

Run with:  python app.py      (from the backend/ directory)
"""

import logging
import sys
from pathlib import Path

# The CV engine and presentation controller live beside backend/ in the repo root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
for path in (str(Path(__file__).resolve().parent), str(PROJECT_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from flask import Flask, jsonify  # noqa: E402
from flask_cors import CORS  # noqa: E402

from config import database  # noqa: E402
from config.settings import settings  # noqa: E402
from middleware.error_handler import register_error_handlers  # noqa: E402
from routes.analytics_routes import analytics_bp  # noqa: E402
from routes.annotation_routes import annotation_bp  # noqa: E402
from routes.auth_routes import auth_bp  # noqa: E402
from routes.engine_routes import engine_bp  # noqa: E402
from routes.gesture_routes import gesture_bp  # noqa: E402
from routes.personalization_routes import personalization_bp  # noqa: E402
from routes.presentation_routes import presentation_bp  # noqa: E402
from routes.session_routes import session_bp  # noqa: E402
from routes.user_routes import user_bp  # noqa: E402
from routes.voice_routes import voice_bp  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("visionx")


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = settings.MAX_CONTENT_LENGTH
    app.config["JSON_SORT_KEYS"] = False

    CORS(
        app,
        resources={r"/api/*": {"origins": [settings.FRONTEND_URL, "http://localhost:5173",
                                            "http://127.0.0.1:5173"]}},
        supports_credentials=True,
        expose_headers=["Content-Disposition"],
    )

    settings.ensure_dirs()

    for blueprint in (auth_bp, user_bp, presentation_bp, gesture_bp,
                      session_bp, annotation_bp, analytics_bp, engine_bp,
                      personalization_bp, voice_bp):
        app.register_blueprint(blueprint)

    register_error_handlers(app)

    @app.get("/api/health")
    def health():
        from services.engine_service import engine_service

        from voice_assistant.intent.classifier import model_status as intent_model_status
        from voice_assistant.speech.factory import probe as speech_probe

        return jsonify({
            "success": True,
            "data": {
                "status": "ok",
                "database": "connected" if database.is_connected() else "disconnected",
                "engine": engine_service.status().get("state", "STOPPED"),
                "uploadDir": str(settings.UPLOAD_DIR),
                "voiceIntentModel": intent_model_status().get("available", False),
                "speechBackends": speech_probe(),
            },
            "message": "VisionX API is running.",
        })

    try:
        database.connect()
    except Exception as exc:  # noqa: BLE001 - report clearly instead of a stack trace at boot
        logger.error(
            "Could not reach MongoDB (%s). Check MONGO_URI in backend/.env and that your "
            "IP is allow-listed in Atlas. The API will keep running and retry per request.",
            exc,
        )

    return app


app = create_app()


if __name__ == "__main__":
    logger.info("VisionX API listening on http://127.0.0.1:%s", settings.PORT)
    # threaded=True is required: SSE, the MJPEG preview and the CV engine all
    # need concurrent request handling. use_reloader is off so the camera thread
    # is never started twice.
    app.run(
        host="0.0.0.0",
        port=settings.PORT,
        debug=settings.DEBUG,
        threaded=True,
        use_reloader=False,
    )
