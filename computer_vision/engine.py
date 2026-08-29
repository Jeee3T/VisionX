"""The gesture engine: one background thread that owns the camera loop.

It never runs inside a Flask request thread and it never touches PyAutoGUI.
Recognised commands leave through the `on_command` callback; the caller decides
what to do with them (VisionX hands them to the CommandDispatcher).
"""

import logging
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from computer_vision.camera.camera_stream import CameraStream, CameraUnavailableError
from computer_vision.command_mapping.gesture_mapper import (
    ANNOTATION_MODE,
    CLEAR_ANNOTATION,
    GestureMapper,
    VIRTUAL_POINTER,
)
from computer_vision.gesture_recognition.debouncer import (
    GestureDebouncer,
    STATUS_EXECUTED,
    STATUS_IDLE,
)
from computer_vision.gesture_recognition.recognizer_factory import build_recognizer
from computer_vision.hand_detection.hand_detector import HandDetector, draw_landmarks
from computer_vision.ml.collector import GestureSampleCollector
from computer_vision.ml.intent_gate import REJECT_AMBIGUOUS, GestureIntentGate
from computer_vision.preprocessing.frame_processor import preprocess
from multimodal.context import context as multimodal_context

logger = logging.getLogger(__name__)

STATE_STOPPED = "STOPPED"
STATE_STARTING = "STARTING"
STATE_RUNNING = "RUNNING"
STATE_ERROR = "ERROR"

MODE_IDLE = "IDLE"
MODE_POINTER = "POINTER"
MODE_ANNOTATE = "ANNOTATE"

LOW_LIGHT_THRESHOLD = 45.0     # mean luma below which we surface the lighting hint
TELEMETRY_HZ = 12.0            # bounded event rate - the UI never sees frame-rate traffic


@dataclass
class EngineConfig:
    camera_index: int = 0
    frame_width: int = 640
    frame_height: int = 480
    confidence_threshold: float = 0.75
    debounce_frames: int = 6
    cooldown_ms: int = 900
    mirror: bool = True
    preferences: dict = field(default_factory=dict)
    # --- personalized recognition (optional) --------------------------------
    user_id: str | None = None
    personalization_enabled: bool = False
    intent_margin: float = 0.15
    # An ENROLLMENT engine runs the camera and the collector but dispatches nothing.
    enrollment_mode: bool = False


class GestureEngine:
    def __init__(self, config: EngineConfig, on_command=None, on_event=None, on_pointer=None):
        self.config = config
        self.on_command = on_command or (lambda command, payload: None)
        self.on_event = on_event or (lambda event: None)
        # Called at full frame rate while pointer/annotation mode is active so the
        # on-screen pointer tracks the hand smoothly (telemetry is rate-limited).
        self.on_pointer = on_pointer or (lambda x, y, mode: None)

        self.mapper = GestureMapper(config.preferences)
        # Personalized when the user has a model and has opted in; the geometric
        # recognizer otherwise. Both satisfy the same interface, so nothing below
        # this line knows or cares which one is running.
        self.recognizer, self.recognizer_info = build_recognizer(
            config.user_id, config.personalization_enabled
        )
        self.intent_gate = GestureIntentGate(
            min_margin=config.intent_margin,
            enabled=bool(self.recognizer_info.get("personalized")),
        )
        self.collector = GestureSampleCollector()
        self.debouncer = GestureDebouncer(config.debounce_frames, config.cooldown_ms)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

        self.state = STATE_STOPPED
        self.mode = MODE_IDLE
        self.error: str | None = None
        self.fps = 0.0
        self.started_at: float | None = None
        self.commands_fired = 0
        self.gesture_counts: dict[str, int] = {}

        self._preview_jpeg: bytes | None = None
        self._pointer_smooth: tuple[float, float] | None = None
        self._last_hand_seen = 0.0
        self._last_telemetry = 0.0
        self._gated = 0

    # --- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state = STATE_STARTING
        self.error = None
        self.commands_fired = 0
        self.gesture_counts = {}
        self.debouncer.reset()
        self.mode = MODE_IDLE
        self._thread = threading.Thread(target=self._run, name="visionx-cv-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=4.0)
        self._thread = None
        self.state = STATE_STOPPED
        self.mode = MODE_IDLE
        self.started_at = None
        self._preview_jpeg = None
        self.collector.cancel()
        multimodal_context.reset()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def apply_preferences(self, preferences: dict) -> None:
        """Re-bind poses without restarting the camera."""
        self.config.preferences = preferences
        self.mapper.load(preferences)
        self.debouncer.reset()

    def recognizer_stats(self) -> dict:
        stats = getattr(self.recognizer, "stats", None)
        return stats() if callable(stats) else {"source": "geometric"}

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self.mode = mode

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "mode": self.mode,
            "error": self.error,
            "fps": round(self.fps, 1),
            "commandsFired": self.commands_fired,
            "gestureCounts": dict(self.gesture_counts),
            "bindings": self.mapper.bindings,
            "confidenceThreshold": self.config.confidence_threshold,
            "debounceFrames": self.config.debounce_frames,
            "cameraIndex": self.config.camera_index,
            "uptime": round(time.time() - self.started_at, 1) if self.started_at else 0,
            "recognizer": self.recognizer_info,
            "intentGate": self.intent_gate.describe(),
            "gatedFrames": self._gated,
            "enrollment": self.collector.status(),
            "mode_kind": "ENROLLMENT" if self.config.enrollment_mode else "SESSION",
        }

    def preview_frame(self) -> bytes | None:
        with self._lock:
            return self._preview_jpeg

    # --- enrolment capture ---------------------------------------------------
    def begin_capture(self, label: str, subject_id: str, target_frames: int,
                      session_id: str | None = None) -> dict:
        """Start collecting labelled frames. Runs inside the existing camera loop."""
        state = self.collector.start(label, subject_id, target_frames, session_id)
        self._emit({"type": "enrollment", **state.as_dict()})
        return state.as_dict()

    def cancel_capture(self) -> None:
        self.collector.cancel()
        self._emit({"type": "enrollment", "cancelled": True})

    def take_capture(self):
        """Hand the captured samples to the caller, which persists them off-thread."""
        return self.collector.take()

    def capture_status(self) -> dict | None:
        return self.collector.status()

    # --- main loop -----------------------------------------------------------
    def _run(self) -> None:
        camera = CameraStream(self.config.camera_index, self.config.frame_width, self.config.frame_height)
        detector: HandDetector | None = None

        try:
            camera.open()
            detector = HandDetector(
                max_hands=1,
                detection_confidence=0.6,
                tracking_confidence=0.6,
            )
        except CameraUnavailableError as exc:
            self._fail(str(exc), "CAMERA_UNAVAILABLE")
            camera.release()
            return
        except Exception as exc:  # noqa: BLE001 - e.g. mediapipe not installed
            self._fail(f"Gesture engine could not start: {exc}", "ENGINE_START_FAILED")
            camera.release()
            return

        self.state = STATE_RUNNING
        self.started_at = time.time()
        self._emit({"type": "state", **self.snapshot()})
        logger.info("Gesture engine running (camera %s)", self.config.camera_index)

        frame_times: list[float] = []
        last_capture_count = -1

        try:
            while not self._stop.is_set():
                loop_start = time.time()
                frame = camera.read()

                if frame is None:
                    if camera.is_lost:
                        self._emit({
                            "type": "camera",
                            "state": STATE_ERROR,
                            "message": "Camera disconnected - trying to reconnect.",
                        })
                        if not camera.reconnect():
                            time.sleep(0.4)
                            continue
                    else:
                        time.sleep(0.01)
                    continue

                processed = preprocess(frame, self.config.frame_width, self.config.mirror)
                hand = detector.detect(processed.rgb)
                result = self.recognizer.recognize(hand, processed.aspect)

                # Enrolment: collect labelled frames without dispatching anything.
                # Pure in-memory work - the recording is written to disk elsewhere.
                capture = self.collector.offer(hand, processed.aspect, processed.brightness)
                if capture is not None and capture.accepted != last_capture_count:
                    last_capture_count = capture.accepted
                    self._emit({"type": "enrollment", **capture.as_dict()})

                # The intent gate turns an ambiguous frame into the neutral state the
                # debouncer already handles. It never invents a command.
                result, gate_reason = self.intent_gate.apply(result)
                if gate_reason == REJECT_AMBIGUOUS:
                    self._gated += 1

                command = self.mapper.map(result.gesture) if result.hand_detected else None
                decision = self.debouncer.submit(
                    result.gesture,
                    command,
                    result.confidence,
                    self.config.confidence_threshold,
                )

                if result.hand_detected:
                    self._last_hand_seen = loop_start
                multimodal_context.update_hand(result.hand_detected)

                pointer = self._track_pointer(result.pointer)
                if pointer is not None:
                    # Published for multimodal commands ("highlight this") to resolve
                    # against; publishing is a lock + two floats.
                    multimodal_context.update_pointer(pointer[0], pointer[1], self.mode)

                if decision.fire and decision.command and not self.config.enrollment_mode:
                    self._handle_command(decision.command, pointer)

                if pointer is not None and self.mode != MODE_IDLE:
                    try:
                        self.on_pointer(pointer[0], pointer[1], self.mode)
                    except Exception:  # noqa: BLE001
                        logger.debug("Pointer subscriber raised", exc_info=True)

                # --- preview frame (display only) --------------------------
                if hand is not None:
                    draw_landmarks(processed.bgr, hand)
                self._render_preview(processed.bgr, result, decision)

                # --- bounded-rate telemetry --------------------------------
                now = time.time()
                if now - self._last_telemetry >= 1.0 / TELEMETRY_HZ or decision.fire:
                    self._last_telemetry = now
                    self._emit({
                        "type": "telemetry",
                        "gesture": result.gesture,
                        "confidence": round(result.confidence, 3),
                        "status": decision.status,
                        "progress": round(decision.progress, 2),
                        "command": decision.command,
                        "executed": decision.fire,
                        "mode": self.mode,
                        "handDetected": result.hand_detected,
                        "pointer": {"x": pointer[0], "y": pointer[1]} if pointer else None,
                        "source": result.source,
                        "modelVersion": result.model_version,
                        "margin": result.margin,
                        "gateReason": gate_reason,
                        "lowLight": processed.brightness < LOW_LIGHT_THRESHOLD,
                        "idleSeconds": round(now - self._last_hand_seen, 1) if self._last_hand_seen else None,
                        "fps": round(self.fps, 1),
                        "timestamp": now,
                    })

                # --- fps ---------------------------------------------------
                frame_times.append(time.time() - loop_start)
                if len(frame_times) > 30:
                    frame_times.pop(0)
                mean = sum(frame_times) / len(frame_times)
                self.fps = 1.0 / mean if mean > 0 else 0.0

        except Exception as exc:  # noqa: BLE001 - the loop must never take the API down
            logger.exception("Gesture engine crashed: %s", exc)
            self._fail(f"Gesture engine stopped unexpectedly: {exc}", "ENGINE_CRASHED")
        finally:
            camera.release()
            if detector is not None:
                detector.close()
            if self.state != STATE_ERROR:
                self.state = STATE_STOPPED
            self._emit({"type": "state", **self.snapshot()})
            logger.info("Gesture engine stopped")

    # --- internals -----------------------------------------------------------
    def _handle_command(self, command: str, pointer) -> None:
        self.commands_fired += 1
        self.gesture_counts[command] = self.gesture_counts.get(command, 0) + 1

        if command == VIRTUAL_POINTER:
            self.set_mode(MODE_IDLE if self.mode == MODE_POINTER else MODE_POINTER)
        elif command == ANNOTATION_MODE:
            self.set_mode(MODE_IDLE if self.mode == MODE_ANNOTATE else MODE_ANNOTATE)
        elif command == CLEAR_ANNOTATION:
            self.set_mode(MODE_IDLE)

        payload = {
            "mode": self.mode,
            "pointer": {"x": pointer[0], "y": pointer[1]} if pointer else None,
            "timestamp": time.time(),
        }
        try:
            self.on_command(command, payload)
        except Exception as exc:  # noqa: BLE001 - a bad dispatch must not kill the loop
            logger.exception("Command dispatch failed for %s: %s", command, exc)
            self._emit({"type": "command_error", "command": command, "message": str(exc)})

    def _track_pointer(self, pointer):
        """Exponentially smooth the fingertip so the on-screen dot does not jitter."""
        if pointer is None:
            self._pointer_smooth = None
            return None
        if self._pointer_smooth is None:
            self._pointer_smooth = pointer
        else:
            alpha = 0.35
            self._pointer_smooth = (
                self._pointer_smooth[0] * (1 - alpha) + pointer[0] * alpha,
                self._pointer_smooth[1] * (1 - alpha) + pointer[1] * alpha,
            )
        return round(self._pointer_smooth[0], 4), round(self._pointer_smooth[1], 4)

    def _render_preview(self, bgr: np.ndarray, result, decision) -> None:
        colour = (34, 197, 94) if decision.status == STATUS_EXECUTED else (
            (245, 158, 11) if decision.status not in (STATUS_IDLE,) else (148, 163, 184)
        )
        label = result.gesture if result.hand_detected else "no hand"
        cv2.rectangle(bgr, (0, bgr.shape[0] - 34), (bgr.shape[1], bgr.shape[0]), (17, 24, 39), -1)
        cv2.putText(
            bgr,
            f"{label}  {int(result.confidence * 100)}%",
            (10, bgr.shape[0] - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            colour,
            2,
            cv2.LINE_AA,
        )
        ok, buffer = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ok:
            with self._lock:
                self._preview_jpeg = buffer.tobytes()

    def _fail(self, message: str, code: str) -> None:
        self.state = STATE_ERROR
        self.error = message
        logger.error("%s: %s", code, message)
        self._emit({"type": "error", "code": code, "message": message, "state": self.state})

    def _emit(self, event: dict) -> None:
        try:
            self.on_event(event)
        except Exception:  # noqa: BLE001
            logger.debug("Event subscriber raised", exc_info=True)
