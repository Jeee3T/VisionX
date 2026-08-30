"""Command dispatch: the single bridge between recognised commands and the OS.

The CV engine emits command *names*; only this layer decides what a name means
and only the controller below it talks to PyAutoGUI.
"""

import logging
import threading
import time

from computer_vision.command_mapping.gesture_mapper import (
    ALL_COMMANDS,
    ANNOTATION_MODE,
    BLACKOUT,
    CLEAR_ANNOTATION,
    END_PRESENTATION,
    FIRST_SLIDE,
    GO_TO_SLIDE,
    LAST_SLIDE,
    NEXT_SLIDE,
    PREVIOUS_SLIDE,
    START_PRESENTATION,
    VIRTUAL_POINTER,
    WHITEOUT,
)
from presentation_controller.annotation import AnnotationController
from presentation_controller.base import PresentationControlError, PresentationController

logger = logging.getLogger(__name__)


class CommandDispatcher:
    def __init__(self, controller: PresentationController, annotations: AnnotationController | None = None):
        # Two threads reach this object: the camera loop (gestures, and the
        # pointer/pen stream at frame rate) and Flask request threads (voice, the
        # control bar, the keyboard fallback). Every mutating entry point is
        # serialised here, because they share one PowerPointController and one
        # mouse button - and an interleaved pen_down/pen_up loses the release,
        # stranding the physical button down on the presenter's desktop.
        self._lock = threading.RLock()
        self.controller = controller
        self.annotations = annotations or AnnotationController()
        self.current_slide = 1
        self.total_slides = 0
        self.slides_navigated = 0
        self.pointer_active = False
        self.annotation_active = False
        self.blank_screen: str | None = None   # None | "BLACK" | "WHITE"
        self.history: list[dict] = []

    # --- slide bookkeeping ---------------------------------------------------
    def bind_presentation(self, current_slide: int = 1, total_slides: int = 0) -> None:
        self.current_slide = max(1, current_slide)
        self.total_slides = max(0, total_slides)

    def _advance(self, delta: int) -> None:
        target = self.current_slide + delta
        if target < 1:
            target = 1
        elif self.total_slides and target > self.total_slides:
            target = self.total_slides
        if target != self.current_slide:
            self.current_slide = target
            self.slides_navigated += 1

    # --- dispatch ------------------------------------------------------------
    def execute(self, command: str, payload: dict | None = None) -> dict:
        """Run one command. The only path from any modality to the desktop.

        `payload["parameters"]` carries the optional, validated arguments a voice
        command can supply (count, slideNumber, state). A gesture supplies none,
        so its behaviour is bit-for-bit what it has always been.
        """
        payload = payload or {}
        if command not in ALL_COMMANDS:
            raise ValueError(f"Unknown command '{command}'")
        with self._lock:
            return self._execute_locked(command, payload)

    def _execute_locked(self, command: str, payload: dict) -> dict:
        parameters = dict(payload.get("parameters") or {})
        source = str(payload.get("source") or "gesture")
        delivered = True
        message = ""

        try:
            self._handlers()[command](parameters)
        except PresentationControlError as exc:
            # The command was still recognised - we just could not reach the desktop.
            delivered = False
            message = str(exc)
            logger.warning("Command %s not delivered: %s", command, message)
        except (ValueError, TypeError, KeyError) as exc:
            # Malformed parameters. Callers are expected to have run
            # multimodal.command.normalize_parameters first, but this is the only
            # place a VisionX command becomes a key press, so it refuses bad input
            # itself rather than trusting that they did.
            delivered = False
            message = f"Invalid parameters for {command}: {exc}"
            logger.warning("Command %s rejected: %s", command, message)
        except Exception as exc:  # noqa: BLE001
            # Anything else the OS input layer can throw - PyAutoGUI raising an
            # OSError after the display went away, a COM error the bridge did not
            # convert. This is the only place a VisionX command becomes a key
            # press, and a failure to deliver one is a reportable outcome, not a
            # 500 for the browser or a dead camera loop.
            delivered = False
            message = f"{command} could not be delivered: {exc}"
            logger.exception("Command %s failed unexpectedly", command)

        record = {
            "command": command,
            "source": source,
            "parameters": parameters,
            "slide": self.current_slide,
            "delivered": delivered,
            "message": message,
            "pointerActive": self.pointer_active,
            "annotationActive": self.annotation_active,
            "blankScreen": self.blank_screen,
            "timestamp": time.time(),
        }
        self.history.append(record)
        if len(self.history) > 200:
            self.history.pop(0)
        return record

    def execute_intent(self, intent) -> dict:
        """Run a `multimodal.command.CommandIntent`, whatever produced it."""
        return self.execute(
            intent.intent,
            {
                "parameters": intent.parameters,
                "source": intent.source,
                "confidence": intent.confidence,
            },
        )

    def _handlers(self) -> dict:
        return {
            NEXT_SLIDE: self._handle_next,
            PREVIOUS_SLIDE: self._handle_previous,
            GO_TO_SLIDE: self._handle_goto,
            FIRST_SLIDE: self._handle_first,
            LAST_SLIDE: self._handle_last,
            VIRTUAL_POINTER: self._handle_pointer,
            ANNOTATION_MODE: self._handle_annotation,
            CLEAR_ANNOTATION: self._handle_clear,
            START_PRESENTATION: self._handle_start,
            END_PRESENTATION: self._handle_end,
            BLACKOUT: self._handle_blackout,
            WHITEOUT: self._handle_whiteout,
        }

    # --- handlers ------------------------------------------------------------
    def _repeat(self, action, delta: int, count: int) -> None:
        """Press once, then keep going only while the deck has room.

        The first press always happens, exactly as it always has: at the last
        slide PowerPoint itself decides what Right Arrow means. Only the extra
        presses of a "forward three slides" voice command stop at the boundary,
        so a miscounted repeat cannot run off the end of the deck.
        """
        for index in range(max(1, count)):
            if index and not self._can_advance(delta):
                break
            action()
            self._advance(delta)

    def _can_advance(self, delta: int) -> bool:
        target = self.current_slide + delta
        if target < 1:
            return False
        return not (self.total_slides and target > self.total_slides)

    def _handle_next(self, parameters: dict) -> None:
        self._repeat(self.controller.next_slide, 1, int(parameters.get("count") or 1))

    def _handle_previous(self, parameters: dict) -> None:
        self._repeat(self.controller.previous_slide, -1, int(parameters.get("count") or 1))

    def _handle_goto(self, parameters: dict) -> None:
        target = int(parameters["slideNumber"])
        # Mirror normalize_parameters: refuse rather than clamp, and treat an
        # unknown deck length (total_slides == 0) as "no upper bound", the same
        # convention _can_advance uses.
        if target < 1:
            raise ValueError("Slide numbers start at 1.")
        if self.total_slides and target > self.total_slides:
            raise ValueError(
                f"This presentation has {self.total_slides} slides, "
                f"so slide {target} does not exist."
            )
        self.controller.goto_slide(target)
        if target != self.current_slide:
            self.current_slide = target
            self.slides_navigated += 1

    def _handle_first(self, _parameters: dict) -> None:
        self.controller.first_slide()
        if self.current_slide != 1:
            self.current_slide = 1
            self.slides_navigated += 1

    def _handle_last(self, _parameters: dict) -> None:
        self.controller.last_slide()
        target = self.total_slides or self.current_slide
        if target != self.current_slide:
            self.current_slide = target
            self.slides_navigated += 1

    def _handle_pointer(self, parameters: dict) -> None:
        """Virtual pointer on/off.

        `state` absent means toggle (a gesture); `state` present means set (voice,
        and the engine, which sends the state it has just computed so the two can
        never disagree about which way the toggle went).
        """
        target = (not self.pointer_active) if parameters.get("state") is None \
            else bool(parameters["state"])
        if target and self.annotation_active:
            # Leaving annotation mode always closes the stroke in progress.
            self.annotations.end()
        try:
            self.controller.set_pointer(target)
        finally:
            # As with the pen: report what happened, not what was asked for.
            self._sync_from_controller(
                pointer=target, annotation=False if target else None,
            )
        if not self.annotation_active:
            # Turning the pointer *off* also leaves pen mode (the arrow is not the
            # pen), so the stroke in progress has to be closed here too - exactly
            # as `_handle_annotation` does. Without this the buffer kept
            # `is_drawing` True while the mode was off, and `stream_pointer`'s
            # `if not is_drawing` guard then never issued `pen_down` again: the
            # pen moved across the slide for the rest of the session without
            # leaving a mark.
            self.annotations.end()

    def _handle_annotation(self, parameters: dict) -> None:
        target = (not self.annotation_active) if parameters.get("state") is None \
            else bool(parameters["state"])
        try:
            self.controller.set_annotation(target)
        finally:
            # Even when the controller refuses - no slideshow, so no pen - the
            # dispatcher must end up describing what actually happened, not what
            # was asked for. `_sync_from_controller` reads the truth back.
            self._sync_from_controller(
                pointer=False if target else None, annotation=target,
            )
        if not self.annotation_active:
            self.annotations.end()

    def _handle_clear(self, _parameters: dict) -> None:
        # Erase in PowerPoint FIRST. `clear_annotation` refuses when no slideshow
        # is running, and dropping our own buffer before finding that out threw
        # away ink that is still on the slide - the presenter loses annotations
        # and nothing is erased.
        self.controller.clear_annotation()
        self.annotations.clear(self.current_slide)
        # Erasing ink is not a mode change: PowerPoint stays in whatever pointer
        # mode it was in. Claiming otherwise is what previously left the pen on in
        # PowerPoint while VisionX reported it off.
        self._sync_from_controller()

    def _sync_from_controller(self, pointer: bool | None = None,
                              annotation: bool | None = None) -> None:
        """Adopt the controller's pointer/pen state as the dispatcher's own.

        The controller talks to PowerPoint, so it is the only layer that knows
        whether a mode change actually took effect. Mirroring it here - rather
        than tracking a parallel copy that a refused command silently invalidates -
        is what stops the UI claiming the pen is on when PowerPoint disagrees.

        `pointer` / `annotation` are the states the caller *asked* for. They are
        used only for a controller that does not publish its own state, so a
        minimal PresentationController implementation still toggles correctly.
        """
        reported_pointer = getattr(self.controller, "pointer_active", None)
        reported_annotation = getattr(self.controller, "annotation_active", None)

        resolved_pointer = reported_pointer if reported_pointer is not None else pointer
        resolved_annotation = reported_annotation if reported_annotation is not None else annotation

        if resolved_pointer is not None:
            self.pointer_active = bool(resolved_pointer)
        if resolved_annotation is not None:
            self.annotation_active = bool(resolved_annotation)

    def _handle_start(self, _parameters: dict) -> None:
        self.controller.start_presentation()
        self.blank_screen = None

    def _handle_end(self, _parameters: dict) -> None:
        self.annotations.end()
        self.controller.end_presentation()
        self.blank_screen = None
        self.pointer_active = False
        self.annotation_active = False

    def _handle_blackout(self, _parameters: dict) -> None:
        self.controller.blackout()
        self.blank_screen = None if self.blank_screen == "BLACK" else "BLACK"

    def _handle_whiteout(self, _parameters: dict) -> None:
        self.controller.whiteout()
        self.blank_screen = None if self.blank_screen == "WHITE" else "WHITE"

    # --- pointer / drawing stream -------------------------------------------
    def stream_pointer(self, x: float, y: float) -> None:
        """Called every frame while pointer or annotation mode is active.

        The ordering here is what makes the PowerPoint pen actually draw:

            move to the first point  ->  press the button  ->  keep moving

        PowerPoint draws on a *drag*. Streaming positions with no button held -
        which is what this method used to do - walks the pen across the slide and
        leaves nothing behind. Pressing the button before the first move would
        instead draw a line in from wherever the cursor happened to be.
        """
        with self._lock:
            if not (self.pointer_active or self.annotation_active):
                return

            self.controller.move_pointer(x, y)

            if not self.annotation_active:
                return

            if not self.annotations.is_drawing:
                self.annotations.begin(self.current_slide)
                # The cursor is now on the first point of the stroke, so the
                # button can go down without dragging in from the previous
                # position.
                self.controller.pen_down()
            self.annotations.add_point(x, y)

    def end_stroke(self):
        """Lift the pen and close the stroke - the hand left the frame, or the
        presenter switched modes. Idempotent; returns the finished Stroke, if any."""
        with self._lock:
            self.controller.pen_up()
            return self.annotations.end()

    def state(self) -> dict:
        return {
            "currentSlide": self.current_slide,
            "totalSlides": self.total_slides,
            "slidesNavigated": self.slides_navigated,
            "pointerActive": self.pointer_active,
            "annotationActive": self.annotation_active,
            "blankScreen": self.blank_screen,
            "annotationCount": self.annotations.count,
            "controller": self.controller.describe(),
        }
