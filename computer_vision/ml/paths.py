"""Filesystem locations for VisionX-trained models and datasets.

Both are configurable so a deployment can put them on a data volume, and both
default to paths inside the repository that .gitignore excludes. Nothing here
imports the backend - the training CLIs must run without Flask or MongoDB.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_GESTURE_DATA_ROOT = PROJECT_ROOT / "data" / "gesture"
DEFAULT_VOICE_DATA_ROOT = PROJECT_ROOT / "data" / "voice_intents"
DEFAULT_USER_MODEL_ROOT = PROJECT_ROOT / "computer_vision" / "models" / "users"
DEFAULT_VOICE_MODEL_ROOT = PROJECT_ROOT / "voice_assistant" / "models"

DATASET_VERSION = "v1"

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else default


def gesture_data_root() -> Path:
    return _env_path("VISIONX_GESTURE_DATA_DIR", DEFAULT_GESTURE_DATA_ROOT)


def voice_data_root() -> Path:
    return _env_path("VISIONX_VOICE_DATA_DIR", DEFAULT_VOICE_DATA_ROOT)


def user_model_root() -> Path:
    return _env_path("VISIONX_USER_MODEL_DIR", DEFAULT_USER_MODEL_ROOT)


def voice_model_root() -> Path:
    return _env_path("VISIONX_VOICE_MODEL_DIR", DEFAULT_VOICE_MODEL_ROOT)


def safe_component(value: str) -> str:
    """Make an id safe to use as a single path component (never traverses)."""
    cleaned = _SAFE.sub("_", str(value or "").strip())[:64]
    # "." and ".." survive the character filter but still traverse, which would
    # point a user's model directory at its own parent - and delete_model()
    # rmtree()s whatever that resolves to.
    if not cleaned.strip("."):
        return "unknown"
    return cleaned or "unknown"


def gesture_dataset_dir(subject: str | None = None, version: str = DATASET_VERSION) -> Path:
    root = gesture_data_root() / version / "samples"
    return root / safe_component(subject) if subject else root


def user_model_dir(user_id: str) -> Path:
    return user_model_root() / safe_component(user_id)
