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
    ALL_COMMANDS,
    ANNOTATION_MODE,
    CLEAR_ANNOTATION,
)
from computer_vision.engine import EngineConfig, GestureEngine, MODE_ANNOTATE
from config.database import annotations as annotations_collection
from config.database import presentation_history
from config.settings import settings
from multimodal.command import CommandIntent, CommandParameterError, SOURCE_GESTURE, SOURCE_MANUAL
from multimodal.command import build as build_intent
from multimodal.context import context as multimodal_context
from presentation_controller.annotation import AnnotationController
from presentation_controller.dispatcher import CommandDispatcher
from presentation_controller.powerpoint import PowerPointController
from services.event_bus import bus
from utils.errors import EngineError

logger = logging.getLogger(__name__)


class EngineService:
    def __init__(self):
        self._lock = threading.RLock()
        # Persisting strokes has its own lock, deliberately NOT `_lock`.
        #
        # `_flush_annotations` runs on the camera thread (every 3 s while drawing)
        # and on Flask threads (when the pen turns off, and on stop()). Two
        # concurrent flushes both read `_saved_strokes`, both slice the same
        # pending strokes, and both insert them - duplicating every annotation in
        # MongoDB across the insert_many round trip.
        #
        # It cannot be `_lock`, because stop() holds `_lock` while joining the
        # camera thread: a camera thread blocked on `_lock` inside a flush would
        # deadlock the join. This one is only ever held by the flush itself.
        self._flush_lock = threading.RLock()
        self.engine: GestureEngine | None = None
        self.dispatcher: CommandDispatcher | None = None
        self.session: dict | None = None
        self._saved_strokes = 0
        self._annotations_made = 0
        self._last_pointer_persist = 0.0
        # Commands issued when the camera is not running (voice-only sessions).
        self._offline_commands = 0
        self._offline_counts: dict[str, int] = {}

    # --- session binding -----------------------------------------------------
    def _bind_session(self, user_id: str, session_doc: dict, presentation: dict | None,
                      options: dict) -> None:
        """Create the dispatcher and session record. No camera is touched here.

        Split out of `start()` so a voice-only session can exist without a camera:
        if the webcam is unavailable the presenter can still drive PowerPoint by
        voice, and both paths dispatch through the same CommandDispatcher.
        """
        controller = PowerPointController()
        self.dispatcher = CommandDispatcher(controller, AnnotationController())
        self.dispatcher.bind_presentation(
            current_slide=int(options.get("startSlide") or 1),
            total_slides=int((presentation or {}).get("totalSlides") or 0),
        )
        self._saved_strokes = 0
        self._annotations_made = 0
        self._offline_commands = 0
        self._offline_counts = {}
        self.session = {
            "sessionId": str(session_doc["_id"]),
            "userId": user_id,
            "presentationId": str(session_doc.get("presentationId") or ""),
            "presentationTitle": (presentation or {}).get("title", ""),
            "startedAt": time.time(),
        }
        multimodal_context.update_slide(self.dispatcher.current_slide)

    # --- lifecycle -----------------------------------------------------------
    def start(self, user_id: str, session_doc: dict, presentation: dict | None, preferences: dict,
              options: dict | None = None) -> dict:
        options = options or {}
        with self._lock:
            if self.engine and self.engine.is_running:
                if self.session and self.session.get("userId") != user_id:
                    raise EngineError("The camera is already in use by another VisionX session.")
                raise EngineError("A gesture session is already running. End it before starting a new one.")

            self._bind_session(user_id, session_doc, presentation, options)

            config = EngineConfig(
                camera_index=int(options.get("cameraIndex", settings.CV_CAMERA_INDEX)),
                frame_width=settings.CV_FRAME_WIDTH,
                frame_height=settings.CV_FRAME_HEIGHT,
                confidence_threshold=float(
                    options.get("confidenceThreshold", settings.CV_CONFIDENCE_THRESHOLD)
                ),
                debounce_frames=int(options.get("debounceFrames", settings.CV_DEBOUNCE_FRAMES)),
                cooldown_ms=int(options.get("cooldownMs", settings.CV_COOLDOWN_MS)),
                stabilizer_window=int(
                    options.get("stabilizerWindow", settings.CV_STABILIZER_WINDOW)
                ),
                # 0 means "derive it from the debounce requirement"; see EngineConfig.
                release_frames=int(
                    options.get("releaseFrames", settings.CV_RELEASE_FRAMES)
                ) or None,
                mirror=bool(options.get("mirror", True)),
                preferences=preferences,
                user_id=user_id,
                personalization_enabled=bool(options.get("personalizationEnabled", False)),
                intent_margin=float(options.get("intentMargin", settings.GESTURE_INTENT_MARGIN)),
            )

            self.engine = GestureEngine(
                config,
                on_command=self._on_command,
                on_event=bus.publish,
                on_pointer=self._on_pointer,
                on_pointer_lost=self._on_pointer_lost,
            )
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

    def start_voice_only(self, user_id: str, session_doc: dict, presentation: dict | None,
                         options: dict | None = None) -> dict:
        """Bind a session for voice control without opening the camera.

        Used when the presenter turns on voice but not gestures, and as the
        fallback when the camera is unavailable - losing the webcam must not
        also lose voice control.
        """
        options = options or {}
        with self._lock:
            if self.engine and self.engine.is_running:
                raise EngineError("A session is already running. End it before starting a new one.")
            if self.dispatcher and self.session and self.session.get("userId") != user_id:
                raise EngineError("A session belongs to another user.")
            # Drop any engine left over from a crashed camera loop. `_bind_session`
            # does not touch `self.engine`, so without this a voice-only session
            # kept the dead one: status() reported its stale ERROR state instead of
            # VOICE_ONLY, and `_count_command` incremented its counters, so the
            # summary double-counted the previous session's commands.
            self.engine = None
            self._bind_session(user_id, session_doc, presentation, options)
            bus.publish({"type": "state", **self.status()})
            return self.status()

    def stop(self, user_id: str | None = None) -> dict:
        with self._lock:
            if not self.engine and not self.dispatcher:
                raise EngineError("No gesture session is running.")
            if user_id and self.session and self.session.get("userId") != user_id:
                raise EngineError("This session belongs to another user.")

            if self.engine:
                self.engine.stop()
            # Whatever else happens, the session must not end with the mouse
            # button still held down on the presenter's desktop.
            if self.dispatcher:
                try:
                    self.dispatcher.end_stroke()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Could not lift the pen while stopping: %s", exc)
            self._flush_annotations(close_active=True)
            counts = dict(self._offline_counts)
            if self.engine:
                for command, count in self.engine.gesture_counts.items():
                    counts[command] = counts.get(command, 0) + count
            summary = {
                "slidesNavigated": self.dispatcher.slides_navigated if self.dispatcher else 0,
                "annotationsMade": self._annotations_made,
                "commandsFired": (self.engine.commands_fired if self.engine else 0) + self._offline_commands,
                "gestureCounts": counts,
                "currentSlide": self.dispatcher.current_slide if self.dispatcher else 1,
            }
            self._teardown()
            bus.publish({"type": "state", "state": "STOPPED", "mode": "IDLE", **summary})
            return summary

    def _teardown(self) -> None:
        # A half-spoken command must not survive into the next talk: without this
        # a session that ended mid-capture leaves the machine armed, and the first
        # words of the next session become a command.
        user_id = (self.session or {}).get("userId")
        if user_id:
            try:
                # Imported here, not at module scope: voice_service imports this
                # module, so a top-level import would be circular.
                from services import voice_service

                voice_service.reset_wake_session(user_id)
            except Exception as exc:  # noqa: BLE001 - never block a teardown
                logger.debug("Could not reset the wake session: %s", exc)

        self.engine = None
        self.dispatcher = None
        self.session = None
        self._saved_strokes = 0
        self._offline_commands = 0
        self._offline_counts = {}
        multimodal_context.reset()

    # --- queries -------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return bool(self.engine and self.engine.is_running)

    def status(self) -> dict:
        with self._lock:
            if not self.engine and not self.dispatcher:
                return {"running": False, "state": "STOPPED", "mode": "IDLE", "session": None,
                        "cameraActive": False, "voiceOnly": False}
            payload: dict = {
                "running": bool(self.engine and self.engine.is_running),
                "cameraActive": bool(self.engine and self.engine.is_running),
                "voiceOnly": self.engine is None,
                "session": self.session,
            }
            if self.engine:
                payload.update(self.engine.snapshot())
            else:
                payload.update({"state": "VOICE_ONLY", "mode": "IDLE", "error": None,
                                "fps": 0.0, "commandsFired": self._offline_commands,
                                "gestureCounts": dict(self._offline_counts)})
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
            multimodal_context.update_slide(self.dispatcher.current_slide)
            bus.publish({"type": "slide", "currentSlide": self.dispatcher.current_slide})
            return self.dispatcher.state()

    def execute_command(self, user_id: str, command: str, parameters: dict | None = None,
                        source: str = SOURCE_MANUAL) -> dict:
        """Manual command trigger (control bar / keyboard) - one shared dispatch path."""
        with self._lock:
            self._require_owner(user_id)
            if command not in ALL_COMMANDS:
                raise EngineError(f"Unknown command '{command}'.")
            try:
                intent = build_intent(
                    command, source, parameters,
                    total_slides=self.dispatcher.total_slides if self.dispatcher else 0,
                )
            except CommandParameterError as exc:
                raise EngineError(str(exc), code="VALIDATION_ERROR") from exc
            return self.execute_intent(user_id, intent)

    def execute_intent(self, user_id: str, intent: CommandIntent) -> dict:
        """Run a CommandIntent from any modality. Gesture, voice, manual and the
        keyboard fallback all end up here, so there is exactly one place where a
        VisionX command becomes a PowerPoint key press."""
        with self._lock:
            self._require_owner(user_id)
            record = self.dispatcher.execute_intent(intent)
            self._count_command(intent.intent)
            self._after_command(intent.intent, record, source=intent.source)
            # The HTTP response carries the same shape as the SSE `command` event -
            # record plus dispatcher state - so a caller that cannot listen on the
            # stream still learns where the deck ended up.
            return {**record, **self.dispatcher.state(), "intent": intent.as_dict()}

    def _count_command(self, command: str) -> None:
        """Every modality counts toward the session totals, so history stays complete."""
        if self.engine:
            self.engine.commands_fired += 1
            self.engine.gesture_counts[command] = self.engine.gesture_counts.get(command, 0) + 1
        else:
            self._offline_commands += 1
            self._offline_counts[command] = self._offline_counts.get(command, 0) + 1

    def _require_owner(self, user_id: str) -> None:
        if not self.dispatcher:
            raise EngineError("No session is running. Start a session first.")
        if not self.owns_session(user_id):
            raise EngineError("This session belongs to another user.")

    # --- engine callbacks ----------------------------------------------------
    def _on_command(self, command: str, payload: dict) -> dict | None:
        """Dispatch a gesture command and hand the outcome back to the engine.

        The return value matters: the engine reconciles its mode against it, so a
        command that was recognised but not delivered - the pen refused because
        PowerPoint is not presenting - cannot leave the engine in a mode
        PowerPoint was never put into.
        """
        if not self.dispatcher:
            return None
        record = self.dispatcher.execute(command, {**payload, "source": SOURCE_GESTURE})
        self._after_command(command, record, source=SOURCE_GESTURE)
        return record

    def _after_command(self, command: str, record: dict, source: str) -> None:
        # Only delete stored annotations if the erase actually reached PowerPoint.
        # A refused Clear used to wipe the database anyway, so the ink vanished
        # from VisionX while staying on the slide.
        if command == CLEAR_ANNOTATION and record.get("delivered"):
            self._clear_persisted_annotations(record["slide"])
        elif command == ANNOTATION_MODE and not record["annotationActive"]:
            # Terminal: the pen just went off, so the stroke really has ended.
            self._flush_annotations(close_active=True)

        if self.dispatcher:
            multimodal_context.update_slide(self.dispatcher.current_slide)

        # Every modality lands here, so this is the one place that can keep the
        # camera engine's mode in step with a command it did not issue itself.
        if self.engine:
            self.engine.sync_mode(record)

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
            # Periodic: persist what is finished, do not interrupt the live stroke.
            self._flush_annotations()

    def _on_pointer_lost(self) -> None:
        """The drawing hand left the frame - lift the pen and close the stroke.

        Without this the mouse button stays held down after the hand goes away and
        PowerPoint keeps drawing a line to wherever the cursor drifts next.
        """
        if not self.dispatcher:
            return
        try:
            self.dispatcher.end_stroke()
        except Exception as exc:  # noqa: BLE001 - never break the camera loop
            logger.warning("Could not end the stroke cleanly: %s", exc)

    # --- annotation persistence ---------------------------------------------
    def _flush_annotations(self, close_active: bool = False) -> None:
        with self._flush_lock:
            self._flush_annotations_locked(close_active)

    def _flush_annotations_locked(self, close_active: bool = False) -> None:
        """Persist completed strokes.

        `close_active` is False for the periodic flush, and that matters: ending
        the stroke in progress every 3 seconds chopped every annotation longer
        than 3 seconds into disjoint fragments, because `begin()` restarts the
        next one at a new point rather than continuing from the last. The periodic
        flush is a crash-safety measure and has no business changing what the
        presenter is drawing. Only a real end - mode change, hand gone, session
        over - closes the active stroke.
        """
        if not self.dispatcher or not self.session:
            return
        if close_active:
            self.dispatcher.end_stroke()
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
        # Shares the flush lock: it rewrites `_saved_strokes`, so it must not
        # interleave with a flush that is mid-insert.
        with self._flush_lock:
            self._clear_persisted_annotations_locked(slide)

    def _clear_persisted_annotations_locked(self, slide: int) -> None:
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
