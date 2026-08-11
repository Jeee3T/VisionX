"""Command dispatch: the single bridge between recognised commands and the OS.

The CV engine emits command *names*; only this layer decides what a name means
and only the controller below it talks to PyAutoGUI.
"""

import logging
import time

from computer_vision.command_mapping.gesture_mapper import (
    ANNOTATION_MODE,
    CLEAR_ANNOTATION,
    COMMANDS,
    NEXT_SLIDE,
    PREVIOUS_SLIDE,
    VIRTUAL_POINTER,
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
        payload = payload or {}
        if command not in COMMANDS:
            raise ValueError(f"Unknown command '{command}'")

        delivered = True
        message = ""

        try:
            if command == NEXT_SLIDE:
                self.controller.next_slide()
                self._advance(1)
            elif command == PREVIOUS_SLIDE:
                self.controller.previous_slide()
                self._advance(-1)
            elif command == VIRTUAL_POINTER:
                self.pointer_active = not self.pointer_active
                if self.pointer_active and self.annotation_active:
                    self.annotation_active = False
                    self.annotations.end()
                self.controller.set_pointer(self.pointer_active)
            elif command == ANNOTATION_MODE:
                self.annotation_active = not self.annotation_active
                if self.annotation_active and self.pointer_active:
                    self.pointer_active = False
                self.controller.set_annotation(self.annotation_active)
                if not self.annotation_active:
                    self.annotations.end()
            elif command == CLEAR_ANNOTATION:
                self.annotations.clear(self.current_slide)
                self.controller.clear_annotation()
                self.annotation_active = False
                self.pointer_active = False
        except PresentationControlError as exc:
            # The gesture was still recognised - we just could not reach the desktop.
            delivered = False
            message = str(exc)
            logger.warning("Command %s not delivered: %s", command, message)

        record = {
            "command": command,
            "slide": self.current_slide,
            "delivered": delivered,
            "message": message,
            "pointerActive": self.pointer_active,
            "annotationActive": self.annotation_active,
            "timestamp": time.time(),
        }
        self.history.append(record)
        if len(self.history) > 200:
            self.history.pop(0)
        return record

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
            "annotationCount": self.annotations.count,
            "controller": self.controller.describe(),
        }
