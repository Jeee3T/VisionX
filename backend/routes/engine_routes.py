"""Live gesture-engine control, SSE telemetry and the camera preview stream."""

import json
import queue
import time

from flask import Blueprint, Response, g, request, stream_with_context

from controllers import session_controller
from middleware.auth import require_auth
from services.engine_service import engine_service
from services.event_bus import bus
from utils.errors import EngineError
from utils.responses import success
from utils.validators import require_fields, to_object_id

engine_bp = Blueprint("engine", __name__, url_prefix="/api/engine")

HEARTBEAT_SECONDS = 15
PREVIEW_FPS = 15


@engine_bp.post("/start")
@require_auth
def start_engine():
    payload = request.get_json(silent=True) or {}
    require_fields(payload, ["sessionId"])
    to_object_id(payload["sessionId"], "session id")
    result = session_controller.start_engine(g.user_id, payload["sessionId"], payload.get("options") or {})
    return success(result, "Gesture engine started.")


@engine_bp.post("/stop")
@require_auth
def stop_engine():
    summary = engine_service.stop(g.user_id)
    return success({"summary": summary}, "Gesture engine stopped.")


@engine_bp.get("/status")
@require_auth
def status():
    payload = engine_service.status()
    payload["ownedByMe"] = engine_service.owns_session(g.user_id)
    return success(payload)


@engine_bp.post("/command")
@require_auth
def manual_command():
    payload = request.get_json(silent=True) or {}
    require_fields(payload, ["command"])
    record = engine_service.execute_command(g.user_id, payload["command"].upper())
    return success({"result": record}, "Command executed.")


@engine_bp.post("/slide")
@require_auth
def set_slide():
    payload = request.get_json(silent=True) or {}
    state = engine_service.set_slide(g.user_id, int(payload.get("slide", 1)))
    return success(state)


@engine_bp.get("/cameras")
@require_auth
def cameras():
    from computer_vision.camera.camera_stream import list_available_cameras

    return success({"cameras": list_available_cameras()})


@engine_bp.get("/stream")
@require_auth
def stream():
    """Server-Sent Events: the interim live channel (see README > Live updates).

    Telemetry is rate-limited inside the engine, so this stream carries roughly
    12 events per second regardless of camera frame rate.
    """
    subscriber = bus.subscribe()

    @stream_with_context
    def generate():
        last_beat = time.time()
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                try:
                    event = subscriber.get(timeout=1.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    if time.time() - last_beat >= HEARTBEAT_SECONDS:
                        last_beat = time.time()
                        yield ": keep-alive\n\n"
        except GeneratorExit:  # client navigated away
            pass
        finally:
            bus.unsubscribe(subscriber)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@engine_bp.get("/preview")
@require_auth
def preview():
    """MJPEG camera preview - the small corner thumbnail on the session screen."""
    if not engine_service.is_running:
        raise EngineError("The camera is not running.")

    @stream_with_context
    def generate():
        interval = 1.0 / PREVIEW_FPS
        while engine_service.is_running:
            frame = engine_service.preview_frame()
            if frame:
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                yield str(len(frame)).encode()
                yield b"\r\n\r\n" + frame + b"\r\n"
            time.sleep(interval)

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
