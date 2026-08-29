"""Chooses which recognizer a session runs on.

    personalized model exists AND the user enabled personalization
        -> PersonalizedRecognizer
    otherwise (no model, personalization off, corrupt model, ML import failure)
        -> GestureRecognizer, exactly as VisionX has always worked

Both satisfy the same interface, so everything downstream is identical. The
geometric recognizer is never removed and is always the safety net.
"""

from __future__ import annotations

import logging

from computer_vision.gesture_recognition.gesture_recognizer import GestureRecognizer

logger = logging.getLogger(__name__)

REASON_DISABLED = "personalization_disabled"
REASON_NO_MODEL = "no_model"
REASON_LOAD_FAILED = "model_unavailable"
REASON_ML_UNAVAILABLE = "ml_stack_unavailable"
REASON_ACTIVE = "personalized"


def build_recognizer(user_id: str | None = None, personalization_enabled: bool = False):
    """Return `(recognizer, info)`. `info` is safe to publish in engine telemetry."""
    geometric = GestureRecognizer()
    base = {"source": "geometric", "modelVersion": None, "personalized": False}

    if not personalization_enabled or not user_id:
        return geometric, {**base, "reason": REASON_DISABLED}

    try:
        from computer_vision.ml import registry
        from computer_vision.ml.personalized_recognizer import PersonalizedRecognizer
    except Exception as exc:  # noqa: BLE001 - numpy/onnx missing must not break sessions
        logger.warning("Personalized recognition unavailable (%s); using the geometric recognizer.", exc)
        return geometric, {**base, "reason": REASON_ML_UNAVAILABLE, "error": str(exc)}

    if not registry.has_model(user_id):
        return geometric, {**base, "reason": REASON_NO_MODEL}

    artifact = registry.load_model(user_id)
    if artifact is None:
        return geometric, {
            **base, "reason": REASON_LOAD_FAILED, "error": registry.load_error(user_id),
        }

    recognizer = PersonalizedRecognizer(artifact, fallback=geometric)
    return recognizer, {
        "source": "personalized",
        "personalized": True,
        "reason": REASON_ACTIVE,
        "modelVersion": artifact.model_version,
        "runtime": artifact.runtime,
        "classes": artifact.classes,
        "trainedAt": artifact.metadata.get("trainedAt"),
        "synthetic": bool(artifact.metadata.get("synthetic")),
    }
