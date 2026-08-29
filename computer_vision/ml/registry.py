"""Per-user personalized model storage, with a process-wide cache.

Models are keyed by user id and never committed to source control (see
.gitignore). The cache is invalidated by the weights file's modification time,
so retraining takes effect on the next frame without a restart.

    computer_vision/models/users/<user_id>/gesture_model.npz
                                          /gesture_model.onnx
                                          /gesture_model.metadata.json
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path

from computer_vision.ml import paths
from computer_vision.ml.mlp import (
    METADATA_FILE,
    ONNX_FILE,
    WEIGHTS_FILE,
    GestureModelArtifact,
    ModelLoadError,
)

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, GestureModelArtifact]] = {}
_failed: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()


def model_dir(user_id: str) -> Path:
    return paths.user_model_dir(user_id)


def has_model(user_id: str) -> bool:
    return (model_dir(user_id) / WEIGHTS_FILE).exists()


def load_model(user_id: str) -> GestureModelArtifact | None:
    """Return the user's model, or None if there is none or it will not load.

    A corrupt model is logged once per version and treated exactly like a missing
    one: the caller falls back to the geometric recognizer. An ML failure must
    never take the presentation engine down.
    """
    directory = model_dir(user_id)
    weights = directory / WEIGHTS_FILE
    if not weights.exists():
        return None

    stamp = weights.stat().st_mtime
    with _lock:
        cached = _cache.get(user_id)
        if cached and cached[0] == stamp:
            return cached[1]
        failed = _failed.get(user_id)
        if failed and failed[0] == stamp:
            return None

    try:
        artifact = GestureModelArtifact.load(directory)
    except ModelLoadError as exc:
        logger.error("Personalized gesture model for %s is unusable: %s", user_id, exc)
        with _lock:
            _failed[user_id] = (stamp, str(exc))
            _cache.pop(user_id, None)
        return None
    except Exception as exc:  # noqa: BLE001 - defensive: never propagate into the camera loop
        logger.exception("Unexpected error loading the model for %s: %s", user_id, exc)
        with _lock:
            _failed[user_id] = (stamp, str(exc))
        return None

    with _lock:
        _cache[user_id] = (stamp, artifact)
        _failed.pop(user_id, None)
    logger.info(
        "Loaded personalized gesture model %s for %s (%s runtime, %s classes)",
        artifact.model_version, user_id, artifact.runtime, len(artifact.classes),
    )
    return artifact


def load_error(user_id: str) -> str | None:
    with _lock:
        failed = _failed.get(user_id)
    return failed[1] if failed else None


def invalidate(user_id: str | None = None) -> None:
    with _lock:
        if user_id is None:
            _cache.clear()
            _failed.clear()
        else:
            _cache.pop(user_id, None)
            _failed.pop(user_id, None)


def read_metadata(user_id: str) -> dict | None:
    path = model_dir(user_id) / METADATA_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def model_status(user_id: str) -> dict:
    """Everything the settings screen needs to describe the user's model."""
    directory = model_dir(user_id)
    weights = directory / WEIGHTS_FILE
    if not weights.exists():
        return {
            "available": False,
            "modelVersion": None,
            "error": None,
            "runtime": None,
            "onnx": False,
        }

    metadata = read_metadata(user_id) or {}
    artifact = load_model(user_id)
    return {
        "available": artifact is not None,
        "modelVersion": metadata.get("modelVersion"),
        "featureVersion": metadata.get("featureVersion"),
        "trainedAt": metadata.get("trainedAt"),
        "classes": metadata.get("classes"),
        "metrics": metadata.get("metrics"),
        "trainingSamples": metadata.get("trainingSamples"),
        "architecture": metadata.get("architecture"),
        "synthetic": bool(metadata.get("synthetic")),
        "runtime": artifact.runtime if artifact else None,
        "onnx": (directory / ONNX_FILE).exists(),
        "sizeBytes": weights.stat().st_size,
        "error": load_error(user_id),
    }


def delete_model(user_id: str) -> bool:
    """Remove the user's model directory. Session history is untouched."""
    directory = model_dir(user_id)
    invalidate(user_id)
    if not directory.exists():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    logger.info("Deleted the personalized gesture model for %s", user_id)
    return True
