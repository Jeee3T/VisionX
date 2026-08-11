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

    @classmethod
    def ensure_dirs(cls) -> None:
        cls.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        cls.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
