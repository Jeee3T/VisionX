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

    MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 50)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

    ALLOWED_EXTENSIONS = {".pptx", ".ppt", ".pdf"}
    ALLOWED_MIME_TYPES = {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint",
        "application/pdf",
        "application/octet-stream",  # some browsers send this for .pptx
    }

    # --- Computer vision defaults (overridable per session) ------------------
    CV_CAMERA_INDEX = _int("CV_CAMERA_INDEX", 0)
    CV_FRAME_WIDTH = _int("CV_FRAME_WIDTH", 640)
    CV_FRAME_HEIGHT = _int("CV_FRAME_HEIGHT", 480)
    CV_CONFIDENCE_THRESHOLD = float(os.getenv("CV_CONFIDENCE_THRESHOLD", "0.72"))
    CV_DEBOUNCE_FRAMES = _int("CV_DEBOUNCE_FRAMES", 6)
    CV_COOLDOWN_MS = _int("CV_COOLDOWN_MS", 900)

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


settings = Settings()
