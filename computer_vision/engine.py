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
    RESET_ANNOTATION,
    VIRTUAL_POINTER,
)
from computer_vision.gesture_recognition.debouncer import (
    GestureDebouncer,
    STATUS_EXECUTED,
    STATUS_IDLE,
)
from computer_vision.gesture_recognition.recognizer_factory import build_recognizer
from computer_vision.gesture_recognition.stabilizer import GestureStabilizer
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
    # --- temporal stability --------------------------------------------------
    # A plurality vote over this many frames before a pose reaches the mapper, and
    # this many neutral frames before a held gesture may repeat. Both exist to stop
    # one stray frame from becoming a command; see stabilizer.py / debouncer.py.
    stabilizer_window: int = 5
    release_frames: int | None = None
    # --- pointer ------------------------------------------------------------
    # Exponential smoothing factor for the fingertip, 0..1: higher follows the
    # hand more closely, lower is steadier. This governs the *continuous* pointer
    # stream only and is deliberately independent of the debounce/cooldown rules
    # above, which govern discrete commands. Letting the debouncer throttle
    # fingertip movement is what made the old pointer feel laggy.
    pointer_smoothing: float = 0.35
    # --- personalized recognition (optional) --------------------------------
    user_id: str | None = None
    personalization_enabled: bool = False
    intent_margin: float = 0.15
    # An ENROLLMENT engine runs the camera and the collector but dispatches nothing.
    enrollment_mode: bool = False


class GestureEngine:
    def __init__(self, config: EngineConfig, on_command=None, on_event=None, on_pointer=None,
                 on_pointer_lost=None):
        self.config = config
        self.on_command = on_command or (lambda command, payload: None)
        self.on_event = on_event or (lambda event: None)
        # Called at full frame rate while pointer/annotation mode is active so the
        # on-screen pointer tracks the hand smoothly (telemetry is rate-limited).
        self.on_pointer = on_pointer or (lambda x, y, mode: None)
        # Called once when the hand that was drawing leaves the frame, so the pen
        # is lifted instead of leaving PowerPoint in a drag that follows the mouse
        # around the slide.
        self.on_pointer_lost = on_pointer_lost or (lambda: None)

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
        self.debouncer = GestureDebouncer(
            config.debounce_frames, config.cooldown_ms, config.release_frames,
        )
        # Smooths the recognizer's per-frame output before it reaches the mapper.
        # Runs for both recognizers: the geometric one confuses INDEX_UP with
        # INDEX_MIDDLE_UP at the extension threshold just as readily as a model does.
        self.stabilizer = GestureStabilizer(config.stabilizer_window)

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
        self._pointer_streaming = False

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
        self.stabilizer.reset()
        self.mode = MODE_IDLE
        self._pointer_streaming = False
        self._thread = threading.Thread(target=self._run, name="visionx-cv-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # Drop out of pointer/annotation mode *before* joining. The join has a
        # timeout, so the camera thread may still run a frame or two after it
        # returns - and in IDLE mode that frame releases the pointer instead of
        # calling pen_down() again after the caller already lifted it.
        self.mode = MODE_IDLE
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=4.0)
        self._thread = None
        # Never end a session mid-stroke: the pen must be lifted before the
        # controller goes away, or the mouse button stays held on the desktop.
        self._release_pointer()
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
        # A remap mid-session must not let frames recorded under the old bindings
        # vote for a command under the new ones.
        self.stabilizer.reset()

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
            "stabilizer": self.stabilizer.describe(),
            "releaseFrames": self.debouncer.release_frames,
            "cooldownMs": self.config.cooldown_ms,
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

                raw = result
                result, gate_reason, decision = self.decide(result)

                if raw.hand_detected:
                    self._last_hand_seen = loop_start
                multimodal_context.update_hand(raw.hand_detected)

                # The stabilised result carries the *live* fingertip whenever the
                # frame had one, and the last known fingertip when it did not - so
                # this adds no lag while the hand is visible, and does not blank
                # the pointer for a single dropped frame.
                #
                # Using `raw.pointer` here instead looks equivalent and is not: one
                # lost MediaPipe frame mid-stroke made `pointer` None, which took
                # the `_release_pointer` branch below, lifted the pen, and split
                # the stroke into two fragments with a gap between them.
                pointer = self._track_pointer(result.pointer)
                if pointer is not None:
                    # Published for multimodal commands ("highlight this") to resolve
                    # against; publishing is a lock + two floats.
                    multimodal_context.update_pointer(pointer[0], pointer[1], self.mode)

                if decision.fire and decision.command and not self.config.enrollment_mode:
                    self._handle_command(decision.command, pointer)

                if pointer is not None and self.mode != MODE_IDLE:
                    self._pointer_streaming = True
                    try:
                        self.on_pointer(pointer[0], pointer[1], self.mode)
                    except Exception:  # noqa: BLE001
                        logger.debug("Pointer subscriber raised", exc_info=True)
                elif self._pointer_streaming:
                    # The hand left the frame, or the mode went idle, while a stroke
                    # was in progress. Lift the pen exactly once.
                    self._release_pointer()

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
                        # `gesture` is the STABILISED pose - what VisionX actually
                        # believes and acts on. Publishing the raw per-frame
                        # prediction here is what made the on-screen action label
                        # flicker between poses several times a second.
                        "gesture": result.gesture,
                        "rawGesture": raw.gesture,
                        "confidence": round(result.confidence, 3),
                        "status": decision.status,
                        "progress": round(decision.progress, 2),
                        "releaseProgress": round(decision.release_progress, 2),
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

    # --- per-frame decision --------------------------------------------------
    def decide(self, result):
        """One frame's journey from a raw classification to a fire/hold decision.

        Split out of the camera loop so the part that decides whether a command
        happens can be driven directly by tests, with no webcam and no thread. The
        loop calls exactly this, so a test that drives it is testing the shipped
        path rather than a re-implementation of it.

        Three filters, in this order, each doing one thing:

            intent gate   this frame is ambiguous            -> neutral
            stabilizer    this frame disagrees with its neighbours -> outvoted
            debouncer     this pose has not been held / has not been released
        """
        # The intent gate turns an ambiguous frame into the neutral state the
        # debouncer already handles. It never invents a command.
        gated, gate_reason = self.intent_gate.apply(result)
        if gate_reason == REJECT_AMBIGUOUS:
            self._gated += 1

        # Then smooth over time. One stray frame - a middle finger that dipped
        # below the extension threshold, a hand lost for a frame - can no longer
        # reach the mapper, so INDEX+MIDDLE stays the pointer rather than
        # flickering into the pen.
        stable = self.stabilizer.update(gated)

        command = self.mapper.map(stable.gesture) if stable.hand_detected else None
        decision = self.debouncer.submit(
            stable.gesture,
            command,
            stable.confidence,
            self.config.confidence_threshold,
        )
        return stable, gate_reason, decision

    # --- internals -----------------------------------------------------------
    def _handle_command(self, command: str, pointer) -> None:
        self.commands_fired += 1
        self.gesture_counts[command] = self.gesture_counts.get(command, 0) + 1

        # The engine and the dispatcher both used to toggle a mode independently,
        # from the same event, and could therefore end up disagreeing about which
        # way the toggle went - the pointer "on" here and "off" there. The engine
        # decides once and sends the decision as an explicit `state` parameter, so
        # the dispatcher sets rather than guesses.
        parameters: dict = {}

        if command == VIRTUAL_POINTER:
            target = self.mode != MODE_POINTER
            if not target:
                self._release_pointer()
            self.set_mode(MODE_POINTER if target else MODE_IDLE)
            parameters["state"] = target
        elif command == ANNOTATION_MODE:
            target = self.mode != MODE_ANNOTATE
            if not target:
                self._release_pointer()
            self.set_mode(MODE_ANNOTATE if target else MODE_IDLE)
            parameters["state"] = target
        elif command == CLEAR_ANNOTATION:
            # Erasing ink lifts the pen for the stroke in progress but is not a
            # mode change: PowerPoint stays in whatever pointer mode it was in, so
            # the presenter can carry on drawing on a now-clean slide.
            self._release_pointer()
        elif command == RESET_ANNOTATION:
            # The escape hatch: whatever mode we were in, the engine goes back to
            # IDLE. Unlike the two toggles above this computes no target - there is
            # nothing to toggle, so a presenter who has lost track of the current
            # mode cannot make it worse by repeating the gesture. `sync_mode` below
            # still reconciles against what the dispatcher reports.
            self._release_pointer()
            self.set_mode(MODE_IDLE)

        payload = {
            "mode": self.mode,
            "parameters": parameters,
            "pointer": {"x": pointer[0], "y": pointer[1]} if pointer else None,
            "timestamp": time.time(),
        }
        try:
            record = self.on_command(command, payload)
        except Exception as exc:  # noqa: BLE001 - a bad dispatch must not kill the loop
            logger.exception("Command dispatch failed for %s: %s", command, exc)
            self._emit({"type": "command_error", "command": command, "message": str(exc)})
            return

        # Adopt what actually happened. A command can be recognised and still not
        # delivered - the pen is refused when PowerPoint is not presenting - and
        # the engine must not go on believing it is in a mode PowerPoint was never
        # put into. Without this the UI shows ANNOTATE while nothing draws.
        self.sync_mode(record)

    def sync_mode(self, record) -> None:
        """Reconcile the engine's mode with the dispatcher's reported state.

        Called after *every* dispatch, from any modality - not just gestures. A
        voice command or the on-screen control bar can turn the pointer off
        underneath a running engine, and if the engine did not hear about it, its
        stale mode would invert the next gesture: the presenter makes the pointer
        gesture expecting it on, the engine computes "toggle off from POINTER",
        and nothing appears to happen.
        """
        if not isinstance(record, dict):
            return   # a subscriber that reports nothing leaves the mode as decided
        pointer = record.get("pointerActive")
        annotation = record.get("annotationActive")
        if pointer is None and annotation is None:
            return

        if annotation:
            actual = MODE_ANNOTATE
        elif pointer:
            actual = MODE_POINTER
        else:
            actual = MODE_IDLE

        if actual != self.mode:
            logger.debug("Engine mode corrected from %s to %s", self.mode, actual)
            if actual == MODE_IDLE:
                self._release_pointer()
            self.set_mode(actual)

    def _release_pointer(self) -> None:
        """Tell the subscriber the pointer stream has ended. Idempotent."""
        if not self._pointer_streaming:
            return
        self._pointer_streaming = False
        try:
            self.on_pointer_lost()
        except Exception:  # noqa: BLE001 - lifting the pen must not kill the loop
            logger.debug("Pointer-lost subscriber raised", exc_info=True)

    def _track_pointer(self, pointer):
        """Exponentially smooth the fingertip so the on-screen dot does not jitter.

        `pointer_smoothing` trades steadiness against lag. It is kept low enough
        that the residual delay is a couple of frames: the web presentation window
        interpolates between the positions it receives, so heavy smoothing here
        would only add lag the client cannot undo.
        """
        if pointer is None:
            self._pointer_smooth = None
            return None
        if self._pointer_smooth is None:
            self._pointer_smooth = pointer
        else:
            alpha = min(1.0, max(0.05, float(self.config.pointer_smoothing)))
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
