"""VisionX Flask API entry point.

Run with:  python app.py      (from the backend/ directory)
"""

import logging
import sys
import threading
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


_health_bridge = None
_health_bridge_lock = threading.Lock()


def _powerpoint_health() -> dict:
    """Whether a PowerPoint slideshow is reachable. Never raises.

    One shared bridge rather than one per request: building a new one on every
    health check would attach to PowerPoint again on every request thread. The
    bridge holds its interface pointer thread-locally, so sharing it here is safe.
    """
    global _health_bridge
    try:
        from presentation_controller.windows import PowerPointComBridge

        if _health_bridge is None:
            with _health_bridge_lock:
                if _health_bridge is None:
                    _health_bridge = PowerPointComBridge()
        return {"slideshow": _health_bridge.probe(),
                "reason": _health_bridge.unavailable_reason}
    except Exception as exc:  # noqa: BLE001 - health must never 500
        return {"slideshow": "UNKNOWN", "reason": str(exc)}


def _converter_health() -> dict:
    """Which .pptx -> PDF converter this machine can actually use. Never raises."""
    try:
        from utils.files import _soffice_executable

        soffice = _soffice_executable()
    except Exception as exc:  # noqa: BLE001 - health must never 500
        return {"policy": settings.PPTX_CONVERTER, "ready": False, "error": str(exc)}

    # `sys.platform`, not an import from the presentation layer: this is a
    # question about the host, and reaching into `presentation_controller.windows`
    # to ask it would load the COM module during a health check in web mode.
    powerpoint = sys.platform.startswith("win") and \
        settings.PPTX_CONVERTER in ("auto", "powerpoint")
    libreoffice = bool(soffice) and settings.PPTX_CONVERTER in ("auto", "libreoffice")
    return {
        "policy": settings.PPTX_CONVERTER,
        # The PowerPoint-independent path, and the one the web mode is built on.
        "libreOffice": soffice or None,
        "powerPointFallback": powerpoint,
        "ready": libreoffice or powerpoint,
        # A .pdf never needs any of this.
        "pdfNeedsNoConverter": True,
    }


def _enable_dpi_awareness_for_powerpoint_mode() -> None:
    """Per-monitor DPI awareness - only needed when driving PowerPoint.

    In legacy PowerPoint mode VisionX moves the real mouse, so it has to see the
    display the way Windows really lays it out: every laptop ships scaled to 125%
    or 150%, and without this the virtual pointer lands at roughly 80% of where
    the presenter is pointing. It must be set before anything asks for the screen
    size.

    In web mode there is no such mapping - the browser scales the slide and the
    pointer is a fraction of it - so this is skipped, and with it the import of
    `presentation_controller.windows`. A web-mode process must be able to show
    that it never loaded the COM layer at all, and an unconditional import here
    would have made that false before the first request arrived.
    """
    if settings.PRESENTATION_MODE != "powerpoint":
        return

    from presentation_controller.windows import IS_WINDOWS, enable_dpi_awareness

    if IS_WINDOWS and not enable_dpi_awareness():
        logger.warning(
            "Could not enable per-monitor DPI awareness. On a scaled display the "
            "virtual pointer may not line up with the presenter's fingertip."
        )


def _prewarm_voice() -> None:
    """Load the speech and intent models now, on a background thread.

    Both are process-wide singletons that load lazily on first use. Lazily is the
    wrong time: the first "Vision ... OK" of a talk would pay the Whisper model
    load and its first-inference graph build - several seconds, in front of an
    audience - while every command after it is fast. Moving that cost to boot is
    the single largest reduction in *perceived* voice latency, and it costs the
    presenter nothing because the API is already starting.

    Daemon thread, every failure swallowed: a machine with no speech backend
    installed must still start the API and serve gesture control.
    """
    if not settings.VOICE_PREWARM:
        return

    def warm() -> None:
        try:
            from voice_assistant.intent.interpreter import get_interpreter

            get_interpreter()
        except Exception as exc:  # noqa: BLE001 - never block startup
            logger.debug("Intent model prewarm skipped: %s", exc)
        try:
            from voice_assistant.speech.factory import get_speech_recognizer

            recognizer = get_speech_recognizer(
                settings.VOICE_STT_BACKEND, settings.VOICE_WHISPER_MODEL,
            )
            # Not just constructed: the first real transcription is slower than
            # the rest because the runtime builds its graph on it. warm_up feeds
            # it a second of silence so the presenter's first command does not.
            warm_up = getattr(recognizer, "warm_up", None)
            if callable(warm_up):
                warm_up()
            if recognizer.name in ("none", "null"):
                # `get_speech_recognizer` returns a null recognizer rather than
                # raising, so "it returned something" is not the same as "voice
                # will work". Saying "warm" here would be a lie the operator only
                # discovers mid-talk.
                logger.info(
                    "No speech-to-text backend is installed, so there is nothing to "
                    "warm up. Voice commands will report themselves unavailable; "
                    "gesture control is unaffected."
                )
            else:
                logger.info("Voice pipeline warm (%s)", recognizer.name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Speech prewarm skipped: %s", exc)

    threading.Thread(target=warm, name="visionx-voice-prewarm", daemon=True).start()


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

    _enable_dpi_awareness_for_powerpoint_mode()

    _prewarm_voice()

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
                # Which surface a new session will drive. "web" means VisionX
                # renders the deck itself and nothing below is relevant.
                "presentationMode": settings.PRESENTATION_MODE,
                # Whether this machine can turn a .pptx into slides. The web
                # presentation mode needs no Microsoft Office at presentation
                # time, but it does need *a* converter at upload time - and an
                # operator should learn that here rather than from an upload
                # that silently produces no slides.
                "pptxConverter": _converter_health(),
                # Whether VisionX can see a PowerPoint slideshow right now. Only
                # meaningful in "powerpoint" mode - in web mode the pen and the
                # eraser do not depend on PowerPoint at all, so the probe is
                # skipped rather than reporting a scary UNKNOWN about something
                # that is not being used.
                "powerpoint": (_powerpoint_health() if settings.PRESENTATION_MODE == "powerpoint"
                               else {"slideshow": "NOT_USED",
                                     "reason": "VisionX is rendering the presentation itself."}),
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
