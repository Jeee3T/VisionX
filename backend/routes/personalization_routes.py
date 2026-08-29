"""Personalized gesture recognition: settings, enrolment, training and deletion."""

from flask import Blueprint, g, request

from middleware.auth import require_auth
from services import enrollment_service, personalization_service
from utils.responses import success
from utils.validators import require_fields

personalization_bp = Blueprint("personalization", __name__, url_prefix="/api/personalization")


# --- settings -----------------------------------------------------------------
@personalization_bp.get("/")
@require_auth
def get_settings():
    return success(personalization_service.get(g.user_id))


@personalization_bp.put("/")
@require_auth
def update_settings():
    payload = request.get_json(silent=True) or {}
    return success(personalization_service.update(g.user_id, payload), "Settings saved.")


# --- enrolment ----------------------------------------------------------------
@personalization_bp.get("/enrollment")
@require_auth
def enrollment_plan():
    return success(enrollment_service.plan(g.user_id))


@personalization_bp.post("/enrollment/camera/start")
@require_auth
def enrollment_camera_start():
    payload = request.get_json(silent=True) or {}
    return success(
        enrollment_service.start_camera(g.user_id, payload.get("options") or {}),
        "Training camera started.",
    )


@personalization_bp.post("/enrollment/camera/stop")
@require_auth
def enrollment_camera_stop():
    return success(enrollment_service.stop_camera(g.user_id), "Training camera stopped.")


@personalization_bp.post("/enrollment/recording/start")
@require_auth
def recording_start():
    payload = request.get_json(silent=True) or {}
    require_fields(payload, ["label"])
    return success(
        enrollment_service.begin_recording(g.user_id, str(payload["label"]).upper(),
                                           payload.get("frames")),
        "Recording started.",
    )


@personalization_bp.get("/enrollment/recording")
@require_auth
def recording_status():
    return success(enrollment_service.recording_status(g.user_id))


@personalization_bp.post("/enrollment/recording/finish")
@require_auth
def recording_finish():
    return success(enrollment_service.finish_recording(g.user_id), "Recording saved.")


@personalization_bp.post("/enrollment/recording/cancel")
@require_auth
def recording_cancel():
    return success(enrollment_service.cancel_recording(g.user_id), "Recording cancelled.")


# --- training -----------------------------------------------------------------
@personalization_bp.post("/train")
@require_auth
def train():
    payload = request.get_json(silent=True) or {}
    return success(
        enrollment_service.train(g.user_id, int(payload.get("seed", 42))),
        "Training started. This runs in the background.",
    )


@personalization_bp.get("/train/status")
@require_auth
def train_status():
    return success(enrollment_service.training_status(g.user_id))


# --- deletion (Feature E: the user controls their own learning data) ----------
@personalization_bp.delete("/model")
@require_auth
def delete_model():
    removed = personalization_service.delete_gesture_data(
        g.user_id, delete_model=True, delete_recordings=False
    )
    return success(removed, "Personalized model deleted. The geometric recognizer is back in use.")


@personalization_bp.delete("/recordings")
@require_auth
def delete_recordings():
    return success(enrollment_service.delete_recordings(g.user_id), "Training recordings deleted.")


@personalization_bp.delete("/")
@require_auth
def delete_everything():
    removed = personalization_service.delete_gesture_data(
        g.user_id, delete_model=True, delete_recordings=True
    )
    return success(
        {**removed, "plan": enrollment_service.plan(g.user_id)},
        "All gesture personalization data deleted. Your presentations, sessions and "
        "pose bindings are untouched.",
    )
