"""Command dispatch: the single bridge between recognised commands and the OS.

The CV engine emits command *names*; only this layer decides what a name means
and only the controller below it talks to PyAutoGUI.
"""

import logging
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
        state = parameters.get("state")
        self.pointer_active = (not self.pointer_active) if state is None else bool(state)
        if self.pointer_active and self.annotation_active:
            self.annotation_active = False
            self.annotations.end()
        self.controller.set_pointer(self.pointer_active)

    def _handle_annotation(self, parameters: dict) -> None:
        state = parameters.get("state")
        self.annotation_active = (not self.annotation_active) if state is None else bool(state)
        if self.annotation_active and self.pointer_active:
            self.pointer_active = False
        self.controller.set_annotation(self.annotation_active)
        if not self.annotation_active:
            self.annotations.end()

    def _handle_clear(self, _parameters: dict) -> None:
        self.annotations.clear(self.current_slide)
        self.controller.clear_annotation()
        self.annotation_active = False
        self.pointer_active = False

    def _handle_start(self, _parameters: dict) -> None:
        self.controller.start_presentation()
        self.blank_screen = None

    def _handle_end(self, _parameters: dict) -> None:
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
        """Called every frame while pointer or annotation mode is active."""
        if self.pointer_active or self.annotation_active:
            self.controller.move_pointer(x, y)
        if self.annotation_active:
            if not self.annotations.is_drawing:
                self.annotations.begin(self.current_slide)
            self.annotations.add_point(x, y)

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
