"""Owns the single gesture engine instance and wires it to the database.

Wiring, top to bottom:
    GestureEngine (recognition)  ->  CommandDispatcher (dispatch)
                                 ->  PowerPointController (control, PyAutoGUI)
    engine events                ->  EventBus  ->  SSE stream to the browser
    fired commands / strokes     ->  MongoDB (history + annotations)

Only one engine may run at a time: there is one webcam and one desktop to drive.
"""

import logging
import threading
import time
from datetime import datetime, timezone

from bson import ObjectId

from computer_vision.command_mapping.gesture_mapper import (
    ANNOTATION_MODE,
    CLEAR_ANNOTATION,
    COMMANDS,
)
from computer_vision.engine import EngineConfig, GestureEngine, MODE_ANNOTATE
from config.database import annotations as annotations_collection
from config.database import presentation_history
from config.settings import settings
from presentation_controller.annotation import AnnotationController
from presentation_controller.dispatcher import CommandDispatcher
from presentation_controller.powerpoint import PowerPointController
from services.event_bus import bus
from utils.errors import EngineError

logger = logging.getLogger(__name__)


class EngineService:
    def __init__(self):
        self._lock = threading.RLock()
        self.engine: GestureEngine | None = None
        self.dispatcher: CommandDispatcher | None = None
        self.session: dict | None = None
        self._saved_strokes = 0
        self._annotations_made = 0
        self._last_pointer_persist = 0.0

    # --- lifecycle -----------------------------------------------------------
    def start(self, user_id: str, session_doc: dict, presentation: dict | None, preferences: dict,
              options: dict | None = None) -> dict:
        options = options or {}
        with self._lock:
            if self.engine and self.engine.is_running:
                if self.session and self.session.get("userId") != user_id:
                    raise EngineError("The camera is already in use by another VisionX session.")
                raise EngineError("A gesture session is already running. End it before starting a new one.")

            controller = PowerPointController()
            self.dispatcher = CommandDispatcher(controller, AnnotationController())
            self.dispatcher.bind_presentation(
                current_slide=int(options.get("startSlide") or 1),
                total_slides=int((presentation or {}).get("totalSlides") or 0),
            )

            config = EngineConfig(
                camera_index=int(options.get("cameraIndex", settings.CV_CAMERA_INDEX)),
                frame_width=settings.CV_FRAME_WIDTH,
                frame_height=settings.CV_FRAME_HEIGHT,
                confidence_threshold=float(
                    options.get("confidenceThreshold", settings.CV_CONFIDENCE_THRESHOLD)
                ),
                debounce_frames=int(options.get("debounceFrames", settings.CV_DEBOUNCE_FRAMES)),
                cooldown_ms=int(options.get("cooldownMs", settings.CV_COOLDOWN_MS)),
                mirror=bool(options.get("mirror", True)),
                preferences=preferences,
            )

            self.engine = GestureEngine(
                config,
                on_command=self._on_command,
                on_event=bus.publish,
                on_pointer=self._on_pointer,
            )
            self._saved_strokes = 0
            self._annotations_made = 0
            self.session = {
                "sessionId": str(session_doc["_id"]),
                "userId": user_id,
                "presentationId": str(session_doc.get("presentationId") or ""),
                "presentationTitle": (presentation or {}).get("title", ""),
                "startedAt": time.time(),
            }
            self.engine.start()

            # Give the camera a moment so the client gets a truthful first status.
            deadline = time.time() + 6.0
            while time.time() < deadline and self.engine.state == "STARTING":
                time.sleep(0.05)

            if self.engine.state == "ERROR":
                message = self.engine.error or "The gesture engine failed to start."
                self._teardown()
                raise EngineError(message, code="CAMERA_UNAVAILABLE")

            return self.status()

    def stop(self, user_id: str | None = None) -> dict:
        with self._lock:
            if not self.engine:
                raise EngineError("No gesture session is running.")
            if user_id and self.session and self.session.get("userId") != user_id:
                raise EngineError("This session belongs to another user.")

            self.engine.stop()
            self._flush_annotations()
            summary = {
                "slidesNavigated": self.dispatcher.slides_navigated if self.dispatcher else 0,
                "annotationsMade": self._annotations_made,
                "commandsFired": self.engine.commands_fired,
                "gestureCounts": dict(self.engine.gesture_counts),
                "currentSlide": self.dispatcher.current_slide if self.dispatcher else 1,
            }
            self._teardown()
            bus.publish({"type": "state", "state": "STOPPED", "mode": "IDLE", **summary})
            return summary

    def _teardown(self) -> None:
        self.engine = None
        self.dispatcher = None
        self.session = None
        self._saved_strokes = 0

    # --- queries -------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return bool(self.engine and self.engine.is_running)

    def status(self) -> dict:
        with self._lock:
            if not self.engine:
                return {"running": False, "state": "STOPPED", "mode": "IDLE", "session": None}
            payload = {"running": self.engine.is_running, "session": self.session}
            payload.update(self.engine.snapshot())
            if self.dispatcher:
                payload.update(self.dispatcher.state())
            payload["annotationsMade"] = self._annotations_made
            return payload

    def preview_frame(self) -> bytes | None:
        engine = self.engine
        return engine.preview_frame() if engine else None

    def owns_session(self, user_id: str) -> bool:
        return bool(self.session and self.session.get("userId") == user_id)

    # --- controls ------------------------------------------------------------
    def apply_preferences(self, user_id: str, preferences: dict) -> None:
        """A remap saved mid-session takes effect immediately for that user."""
        with self._lock:
            if self.engine and self.owns_session(user_id):
                self.engine.apply_preferences(preferences)
                bus.publish({"type": "state", **self.status()})

    def set_slide(self, user_id: str, slide: int) -> dict:
        with self._lock:
            self._require_owner(user_id)
            self.dispatcher.current_slide = max(1, int(slide))
            bus.publish({"type": "slide", "currentSlide": self.dispatcher.current_slide})
            return self.dispatcher.state()

    def execute_command(self, user_id: str, command: str) -> dict:
        """Manual command trigger (keyboard/UI fallback) - same dispatch path as a gesture."""
        with self._lock:
            self._require_owner(user_id)
            if command not in COMMANDS:
                raise EngineError(f"Unknown command '{command}'.")
            record = self.dispatcher.execute(command)
            # Manual commands travel the same dispatch path as gestures and count
            # toward the session totals, so history and analytics stay complete.
            self.engine.commands_fired += 1
            self.engine.gesture_counts[command] = self.engine.gesture_counts.get(command, 0) + 1
            self._after_command(command, record, source="manual")
            return record

    def _require_owner(self, user_id: str) -> None:
        if not self.engine or not self.dispatcher:
            raise EngineError("No gesture session is running.")
        if not self.owns_session(user_id):
            raise EngineError("This session belongs to another user.")

    # --- engine callbacks ----------------------------------------------------
    def _on_command(self, command: str, payload: dict) -> None:
        if not self.dispatcher:
            return
        record = self.dispatcher.execute(command, payload)
        self._after_command(command, record, source="gesture")

    def _after_command(self, command: str, record: dict, source: str) -> None:
        if command == CLEAR_ANNOTATION:
            self._clear_persisted_annotations(record["slide"])
        elif command == ANNOTATION_MODE and not record["annotationActive"]:
            self._flush_annotations()

        bus.publish({
            "type": "command",
            "source": source,
            **record,
            **(self.dispatcher.state() if self.dispatcher else {}),
        })

    def _on_pointer(self, x: float, y: float, mode: str) -> None:
        if not self.dispatcher:
            return
        self.dispatcher.stream_pointer(x, y)
        # Persist finished strokes periodically so a crash mid-talk loses nothing.
        if mode == MODE_ANNOTATE and time.time() - self._last_pointer_persist > 3.0:
            self._last_pointer_persist = time.time()
            self._flush_annotations()

    # --- annotation persistence ---------------------------------------------
    def _flush_annotations(self) -> None:
        if not self.dispatcher or not self.session:
            return
        self.dispatcher.annotations.end()
        strokes = self.dispatcher.annotations.strokes()
        pending = strokes[self._saved_strokes:]
        if not pending:
            return

        presentation_id = self.session.get("presentationId")
        if not presentation_id:
            self._saved_strokes = len(strokes)
            return

        docs = [
            {
                "presentationId": ObjectId(presentation_id),
                "userId": ObjectId(self.session["userId"]),
                "sessionId": ObjectId(self.session["sessionId"]),
                "slideNumber": stroke["slide"],
                "annotationData": {
                    "points": stroke["points"],
                    "colour": stroke["colour"],
                    "width": stroke["width"],
                },
                "createdAt": datetime.now(timezone.utc),
            }
            for stroke in pending
        ]
        try:
            annotations_collection().insert_many(docs)
            self._saved_strokes = len(strokes)
            self._annotations_made += len(docs)
            presentation_history().update_one(
                {"_id": ObjectId(self.session["sessionId"])},
                {"$inc": {"annotationsMade": len(docs)}},
            )
            bus.publish({"type": "annotations_saved", "count": len(docs)})
        except Exception as exc:  # noqa: BLE001 - drawing must never break the session
            logger.warning("Could not persist annotations: %s", exc)

    def _clear_persisted_annotations(self, slide: int) -> None:
        if not self.session or not self.session.get("presentationId"):
            return
        try:
            annotations_collection().delete_many({
                "presentationId": ObjectId(self.session["presentationId"]),
                "userId": ObjectId(self.session["userId"]),
                "slideNumber": slide,
            })
            self._saved_strokes = len(self.dispatcher.annotations.strokes()) if self.dispatcher else 0
            bus.publish({"type": "annotations_cleared", "slide": slide})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not clear annotations: %s", exc)


engine_service = EngineService()
