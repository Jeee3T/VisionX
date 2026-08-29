"""Gesture enrolment: guided capture, dataset persistence and background training.

Two rules shape this file:

  1. The camera loop never waits. Capture appends to memory inside the engine;
     writing the recording to disk and to MongoDB happens on a worker thread.
  2. Training never happens in a request. `train()` starts a worker and returns
     immediately; progress is polled and pushed over the existing SSE channel.

The training worker calls the same reproducible CLI entry point a developer runs
by hand (`computer_vision.ml.training.train_gesture_model`), so the UI and the
command line cannot drift apart.
"""

import io
import logging
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone

from bson import ObjectId

from computer_vision.gesture_recognition.poses import POSE_BY_NAME
from computer_vision.ml import paths, registry
from computer_vision.ml.collector import CaptureError
from computer_vision.ml.dataset import (
    GESTURE_CLASSES,
    NULL_CLASS,
    DatasetError,
    write_manifest,
    write_recording,
)
from config.database import gesture_recordings
from config.settings import settings
from services import personalization_service
from services.event_bus import bus
from utils.errors import EngineError, ValidationError

logger = logging.getLogger(__name__)

STATUS_IDLE = "IDLE"
STATUS_RUNNING = "RUNNING"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"

_lock = threading.Lock()
_training: dict[str, dict] = {}


# --- enrolment plan -----------------------------------------------------------
def plan(user_id: str) -> dict:
    """What the user still has to record, and why each class matters."""
    subject = personalization_service.subject_id(user_id)
    per_gesture = settings.ENROLLMENT_RECORDINGS_PER_GESTURE
    collected: dict[str, int] = {name: 0 for name in GESTURE_CLASSES}

    for row in gesture_recordings().find({"userId": ObjectId(user_id)}, {"label": 1}):
        label = row.get("label")
        if label in collected:
            collected[label] += 1

    steps = []
    for name in GESTURE_CLASSES:
        pose = POSE_BY_NAME.get(name)
        is_null = name == NULL_CLASS
        steps.append({
            "label": name,
            "title": pose.label if pose else "Other / no command",
            "description": pose.description if pose else (
                "Move your hands the way you naturally do while talking - do not form "
                "any of the poses above. This is what teaches the model to stay quiet."
            ),
            "fingers": list(pose.fingers) if pose else None,
            "isNull": is_null,
            "prompt": (
                "Talk with your hands as you normally would while presenting."
                if is_null else
                f"Hold the '{pose.label}' pose. Move your hand around, closer and further "
                "from the camera, and rotate it slightly."
            ),
            "recordingsNeeded": per_gesture,
            "recordingsCollected": collected[name],
            "complete": collected[name] >= per_gesture,
        })

    total_needed = per_gesture * len(GESTURE_CLASSES)
    total_done = sum(min(count, per_gesture) for count in collected.values())
    return {
        "subject": subject,
        "framesPerRecording": settings.ENROLLMENT_FRAMES_PER_RECORDING,
        "recordingsPerGesture": per_gesture,
        "steps": steps,
        "totalRecordingsNeeded": total_needed,
        "totalRecordingsCollected": total_done,
        "progress": round(total_done / max(1, total_needed), 3),
        "readyToTrain": _ready_to_train(collected),
    }


def _ready_to_train(collected: dict[str, int]) -> bool:
    """At least two recordings for at least three classes, one of them the null class."""
    usable = [name for name, count in collected.items() if count >= 2]
    return len(usable) >= 3 and collected.get(NULL_CLASS, 0) >= 2


# --- capture ------------------------------------------------------------------
def start_camera(user_id: str, options: dict | None = None) -> dict:
    """Open the camera in enrolment mode: no dispatch, no PowerPoint, just capture."""
    from computer_vision.engine import EngineConfig, GestureEngine
    from services.engine_service import engine_service

    personalization_service.require_consent(user_id)
    options = options or {}

    with engine_service._lock:  # noqa: SLF001 - same module family, one camera to guard
        if engine_service.engine and engine_service.engine.is_running:
            raise EngineError(
                "The camera is already in use by a session. End it before training gestures."
            )

        config = EngineConfig(
            camera_index=int(options.get("cameraIndex", settings.CV_CAMERA_INDEX)),
            frame_width=settings.CV_FRAME_WIDTH,
            frame_height=settings.CV_FRAME_HEIGHT,
            confidence_threshold=settings.CV_CONFIDENCE_THRESHOLD,
            debounce_frames=settings.CV_DEBOUNCE_FRAMES,
            cooldown_ms=settings.CV_COOLDOWN_MS,
            mirror=bool(options.get("mirror", True)),
            preferences={},
            user_id=user_id,
            # Enrolment always runs the geometric recognizer: the frames are being
            # collected to *train* the personalized model, so using that model to
            # collect them would feed its own output back into its training data.
            personalization_enabled=False,
            enrollment_mode=True,
        )
        engine = GestureEngine(config, on_command=None, on_event=bus.publish, on_pointer=None)
        engine_service.engine = engine
        engine_service.session = {"userId": user_id, "sessionId": None, "kind": "ENROLLMENT"}
        engine.start()

        deadline = time.time() + 6.0
        while time.time() < deadline and engine.state == "STARTING":
            time.sleep(0.05)

        if engine.state == "ERROR":
            message = engine.error or "The camera could not be opened."
            engine_service.engine = None
            engine_service.session = None
            raise EngineError(message, code="CAMERA_UNAVAILABLE")

    return {"engine": engine_service.status(), "plan": plan(user_id)}


def stop_camera(user_id: str) -> dict:
    from services.engine_service import engine_service

    engine = _require_enrollment_engine(user_id)
    engine.stop()
    with engine_service._lock:  # noqa: SLF001
        engine_service.engine = None
        engine_service.session = None
    return {"stopped": True}


def _require_enrollment_engine(user_id: str):
    from services.engine_service import engine_service

    engine = engine_service.engine
    session = engine_service.session or {}
    if not engine or not engine.is_running:
        raise EngineError("The training camera is not running.")
    if session.get("kind") != "ENROLLMENT":
        raise EngineError("A presentation session is running, not gesture training.")
    if session.get("userId") != user_id:
        raise EngineError("This training session belongs to another user.")
    return engine


def begin_recording(user_id: str, label: str, frames: int | None = None) -> dict:
    personalization_service.require_consent(user_id)
    engine = _require_enrollment_engine(user_id)
    try:
        return engine.begin_capture(
            label,
            personalization_service.subject_id(user_id),
            int(frames or settings.ENROLLMENT_FRAMES_PER_RECORDING),
        )
    except CaptureError as exc:
        raise ValidationError(str(exc)) from exc


def cancel_recording(user_id: str) -> dict:
    engine = _require_enrollment_engine(user_id)
    engine.cancel_capture()
    return {"cancelled": True}


def recording_status(user_id: str) -> dict:
    engine = _require_enrollment_engine(user_id)
    return {"capture": engine.capture_status(), "plan": plan(user_id)}


def finish_recording(user_id: str) -> dict:
    """Take the captured frames and persist them without blocking the camera loop."""
    personalization_service.require_consent(user_id)
    engine = _require_enrollment_engine(user_id)

    status = engine.capture_status()
    samples = engine.take_capture()
    if not samples:
        raise ValidationError(
            "No usable frames were captured. Check the lighting, keep your whole hand "
            "in frame, and try again."
        )
    if len(samples) < 20:
        raise ValidationError(
            f"Only {len(samples)} usable frames were captured; a recording needs at least 20. "
            f"{(status or {}).get('lastRejection') or ''}".strip()
        )

    result: dict = {}
    error: list[Exception] = []

    def persist() -> None:
        try:
            path = write_recording(samples)
            gesture_recordings().insert_one({
                "userId": ObjectId(user_id),
                "recordingId": samples[0].recording_id,
                "label": samples[0].label,
                "datasetVersion": paths.DATASET_VERSION,
                "featureVersion": samples[0].feature_version,
                "frames": len(samples),
                "rejectedFrames": int((status or {}).get("rejected", 0)),
                "path": str(path),
                "meanDetectionScore": round(
                    sum(s.detection_score for s in samples) / len(samples), 4),
                "meanBrightness": round(sum(s.brightness for s in samples) / len(samples), 2),
                "createdAt": datetime.now(timezone.utc),
            })
            result.update({"recordingId": samples[0].recording_id,
                           "frames": len(samples), "path": str(path)})
        except Exception as exc:  # noqa: BLE001 - reported to the caller below
            error.append(exc)

    worker = threading.Thread(target=persist, name="visionx-enrollment-save", daemon=True)
    worker.start()
    worker.join(timeout=15.0)

    if error:
        logger.exception("Could not save the enrolment recording", exc_info=error[0])
        raise EngineError(f"Could not save the recording: {error[0]}")
    if not result:
        raise EngineError("Saving the recording timed out.")

    payload = {**result, "plan": plan(user_id)}
    bus.publish({"type": "enrollment_saved", **result})
    return payload


def delete_recordings(user_id: str) -> dict:
    removed = personalization_service.delete_gesture_data(
        user_id, delete_model=False, delete_recordings=True
    )
    return {**removed, "plan": plan(user_id)}


# --- training -----------------------------------------------------------------
def training_status(user_id: str) -> dict:
    with _lock:
        state = dict(_training.get(user_id) or {"status": STATUS_IDLE})
    state["model"] = registry.model_status(user_id)
    return state


def train(user_id: str, seed: int = 42) -> dict:
    """Kick off training on a worker thread. Returns immediately."""
    personalization_service.require_consent(user_id)

    with _lock:
        current = _training.get(user_id)
        if current and current.get("status") == STATUS_RUNNING:
            raise EngineError("Training is already running for your account.")
        _training[user_id] = {
            "status": STATUS_RUNNING,
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "message": "Training your personalized gesture model...",
        }

    thread = threading.Thread(
        target=_train_worker, args=(user_id, seed), name="visionx-gesture-training", daemon=True
    )
    thread.start()
    bus.publish({"type": "training", "status": STATUS_RUNNING, "userId": user_id})
    return training_status(user_id)


def _train_worker(user_id: str, seed: int) -> None:
    from computer_vision.ml.training import train_gesture_model

    subject = personalization_service.subject_id(user_id)
    output = registry.model_dir(user_id)
    buffer = io.StringIO()
    started = time.time()

    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            code = train_gesture_model.main([
                "--subject", subject,
                "--output", str(output),
                "--seed", str(seed),
                "--quiet",
            ])
        log = buffer.getvalue()

        if code != 0:
            _finish(user_id, STATUS_FAILED, _readable_failure(log), log, started)
            return

        registry.invalidate(user_id)
        metadata = registry.read_metadata(user_id) or {}
        personalization_service.record_model(
            user_id, metadata.get("modelVersion"), metadata.get("trainedAt")
        )
        metrics = (metadata.get("metrics") or {}).get("test") or {}
        _finish(
            user_id, STATUS_DONE,
            f"Trained model {metadata.get('modelVersion')} "
            f"(test accuracy {metrics.get('accuracy', 0):.1%})",
            log, started, model_version=metadata.get("modelVersion"),
        )
    except DatasetError as exc:
        _finish(user_id, STATUS_FAILED, str(exc), buffer.getvalue(), started)
    except Exception as exc:  # noqa: BLE001 - a training crash must not kill the API
        logger.exception("Gesture training failed for %s", user_id)
        _finish(user_id, STATUS_FAILED, f"Training failed: {exc}", buffer.getvalue(), started)


def _readable_failure(log: str) -> str:
    for line in log.splitlines():
        if line.startswith("error:"):
            return line[len("error:"):].strip()
    return "Training did not complete. Record more gestures and try again."


def _finish(user_id: str, status: str, message: str, log: str, started: float,
            model_version: str | None = None) -> None:
    payload = {
        "status": status,
        "message": message,
        "finishedAt": datetime.now(timezone.utc).isoformat(),
        "durationSeconds": round(time.time() - started, 2),
        "log": log[-4000:],
        "modelVersion": model_version,
    }
    with _lock:
        existing = _training.get(user_id) or {}
        _training[user_id] = {**existing, **payload}
    bus.publish({"type": "training", "userId": user_id,
                 "status": status, "message": message, "modelVersion": model_version})
    logger.info("Gesture training for %s finished: %s (%s)", user_id, status, message)


def refresh_manifest() -> None:
    """Rewrite the dataset manifest across all subjects. Best effort."""
    try:
        from computer_vision.ml.dataset import load_dataset

        write_manifest(load_dataset(strict=False))
    except DatasetError:
        pass
