"""Application configuration, loaded once from environment / .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

load_dotenv(BACKEND_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "visionx_db")

    JWT_SECRET = os.getenv("JWT_SECRET", "visionx-dev-secret-change-me")
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRES_HOURS = _int("JWT_EXPIRES_HOURS", 24)

    PORT = _int("PORT", 5000)
    DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"

    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR") or (BACKEND_DIR / "uploads"))
    if not UPLOAD_DIR.is_absolute():
        UPLOAD_DIR = BACKEND_DIR / UPLOAD_DIR
    THUMBNAIL_DIR = UPLOAD_DIR / "thumbnails"
    # Full-resolution slide renders for the presentation window. Separate from
    # thumbnails: a thumbnail is a 1.6x preview for a card in the library, and
    # putting that on a projector is what makes a deck look blurry.
    SLIDE_DIR = UPLOAD_DIR / "slides"

    MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 50)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

    ALLOWED_EXTENSIONS = {".pptx", ".ppt", ".pdf"}
    ALLOWED_MIME_TYPES = {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint",
        "application/pdf",
        "application/octet-stream",  # some browsers send this for .pptx
    }

    # --- Presentation surface ------------------------------------------------
    # "web"        VisionX renders the deck in its own presentation window. The
    #              default, and what removes the Ctrl+P / focus / pointer-lag
    #              problems of driving PowerPoint through keyboard automation.
    # "powerpoint" drive the PowerPoint installed on this machine, as before.
    PRESENTATION_MODE = (os.getenv("VISIONX_PRESENTATION_MODE") or "web").strip().lower()

    # Width in pixels that slides are rendered at for the presentation window.
    # 1920 is a projector/laptop panel; the renderer never upscales past the
    # slide's own resolution, so a larger number costs nothing on a small deck.
    SLIDE_RENDER_WIDTH = _int("VISIONX_SLIDE_RENDER_WIDTH", 1920)
    SLIDE_RENDER_MAX_WIDTH = _int("VISIONX_SLIDE_RENDER_MAX_WIDTH", 2560)
    # Which backend converts .pptx -> PDF so VisionX can render the slides.
    #
    #   "libreoffice"  headless LibreOffice only. The PowerPoint-independent
    #                  path, and what the web presentation mode is built on.
    #   "auto"         LibreOffice, then PowerPoint COM if LibreOffice is not
    #                  installed. The default: LibreOffice is tried FIRST, so a
    #                  machine with both never launches PowerPoint.
    #   "powerpoint"   PowerPoint COM only. Legacy; needs Microsoft Office.
    #
    # Note the order in "auto". PowerPoint is a *fallback*, never a requirement:
    # a .pptx must be presentable on a machine with no Office install at all.
    PPTX_CONVERTER = (os.getenv("VISIONX_PPTX_CONVERTER") or "auto").strip().lower()

    # Where PPTX -> PDF conversion looks for LibreOffice. Empty means "search
    # PATH, then the usual install locations".
    SOFFICE_PATH = os.getenv("VISIONX_SOFFICE_PATH") or ""

    # --- Computer vision defaults (overridable per session) ------------------
    CV_CAMERA_INDEX = _int("CV_CAMERA_INDEX", 0)
    CV_FRAME_WIDTH = _int("CV_FRAME_WIDTH", 640)
    CV_FRAME_HEIGHT = _int("CV_FRAME_HEIGHT", 480)
    CV_CONFIDENCE_THRESHOLD = float(os.getenv("CV_CONFIDENCE_THRESHOLD", "0.72"))
    CV_DEBOUNCE_FRAMES = _int("CV_DEBOUNCE_FRAMES", 6)
    CV_COOLDOWN_MS = _int("CV_COOLDOWN_MS", 900)
    # Fingertip smoothing, 0..1. Governs the continuous pointer stream only -
    # never discrete commands, which is why the debounce settings above do not
    # touch it. The presentation window interpolates between the positions it
    # receives, so this only has to take the jitter out, not the motion.
    CV_POINTER_SMOOTHING = float(os.getenv("CV_POINTER_SMOOTHING", "0.5"))

    # --- temporal stability --------------------------------------------------
    # Plurality vote over this many frames before a pose reaches the command
    # mapper: one stray frame can never become a command, so INDEX+MIDDLE stays
    # the pointer instead of flickering into the pen. 5 at ~30 fps is ~165 ms.
    CV_STABILIZER_WINDOW = _int("CV_STABILIZER_WINDOW", 5)
    # Consecutive neutral frames before a held gesture may fire the same command
    # again. 0 means "half the debounce requirement", which is the default. A
    # single neutral frame used to be enough, and that is what made a held
    # gesture walk through the deck.
    CV_RELEASE_FRAMES = _int("CV_RELEASE_FRAMES", 0)

    # --- Personalized gesture recognition (opt-in, per user) -----------------
    # Off by default: a user has no model until they enrol, and the geometric
    # recognizer runs unchanged until they do.
    PERSONALIZATION_DEFAULT_ENABLED = os.getenv("PERSONALIZATION_DEFAULT_ENABLED", "0") == "1"
    GESTURE_INTENT_MARGIN = float(os.getenv("GESTURE_INTENT_MARGIN", "0.15"))
    ENROLLMENT_FRAMES_PER_RECORDING = _int("ENROLLMENT_FRAMES_PER_RECORDING", 60)
    ENROLLMENT_RECORDINGS_PER_GESTURE = _int("ENROLLMENT_RECORDINGS_PER_GESTURE", 3)
    GESTURE_DATA_DIR = os.getenv("VISIONX_GESTURE_DATA_DIR") or None
    USER_MODEL_DIR = os.getenv("VISIONX_USER_MODEL_DIR") or None

    # --- Voice assistant -----------------------------------------------------
    VOICE_DEFAULT_ENABLED = os.getenv("VOICE_DEFAULT_ENABLED", "0") == "1"
    VOICE_STT_BACKEND = os.getenv("VOICE_STT_BACKEND") or None   # None = auto-detect
    # Load Whisper and the intent model in a background thread at boot instead of
    # on the first command. Without it the first "Vision ... OK" of a talk pays
    # the model load - seconds, in front of an audience - and every later one is
    # fast, which is the worst possible place to put that cost.
    VOICE_PREWARM = os.getenv("VOICE_PREWARM", "1") == "1"
    VOICE_WHISPER_MODEL = os.getenv("VISIONX_WHISPER_MODEL", "base.en")
    VOICE_EXECUTE_THRESHOLD = float(os.getenv("VOICE_EXECUTE_THRESHOLD", "0.75"))
    VOICE_CONFIRM_THRESHOLD = float(os.getenv("VOICE_CONFIRM_THRESHOLD", "0.50"))
    # Transcripts are command-level telemetry, not audio. Raw audio is never stored.
    VOICE_RETAIN_TRANSCRIPTS = os.getenv("VOICE_RETAIN_TRANSCRIPTS", "1") == "1"
    MAX_UTTERANCE_MB = _int("MAX_UTTERANCE_MB", 8)

    @classmethod
    def ensure_dirs(cls) -> None:
        cls.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        cls.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
        cls.SLIDE_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
