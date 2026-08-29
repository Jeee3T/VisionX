"""The structured command every input modality produces.

Gesture and voice are two front ends onto one pipeline. Neither talks to
PowerPoint: both emit a `CommandIntent`, and the existing CommandDispatcher is
the only thing that turns one into a key press.

    gesture -> CommandIntent -.
                               >-- CommandDispatcher -> PresentationController
    voice   -> CommandIntent -'

Adding a third modality means producing a CommandIntent, and nothing else.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

SOURCE_GESTURE = "gesture"
SOURCE_VOICE = "voice"
SOURCE_MANUAL = "manual"     # the on-screen control bar
SOURCE_KEYBOARD = "keyboard"  # the keyboard fallback during a session
SOURCES = (SOURCE_GESTURE, SOURCE_VOICE, SOURCE_MANUAL, SOURCE_KEYBOARD)

MAX_REPEAT_COUNT = 20   # "skip forward five slides" is fine; 500 is a typo or a misread


class CommandParameterError(ValueError):
    """A command's parameters are missing, malformed or out of range."""


@dataclass
class CommandIntent:
    """One resolved instruction, whatever produced it."""

    intent: str
    source: str
    parameters: dict = field(default_factory=dict)
    confidence: float = 1.0
    transcript: str | None = None      # voice: what was heard
    model_version: str | None = None   # which VisionX-trained model decided this
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        payload = {
            "source": self.source,
            "intent": self.intent,
            "parameters": dict(self.parameters),
            "confidence": round(float(self.confidence), 4),
            "timestamp": self.created_at,
        }
        if self.transcript is not None:
            payload["transcript"] = self.transcript
        if self.model_version:
            payload["modelVersion"] = self.model_version
        return payload


def normalize_parameters(command: str, parameters: dict | None, total_slides: int = 0) -> dict:
    """Validate and coerce parameters into what the dispatcher accepts.

    Raises CommandParameterError rather than guessing. A voice command that says
    "go to slide 400" in a 20-slide deck is rejected, not clamped: silently going
    somewhere the presenter did not ask for is worse than doing nothing.
    """
    from computer_vision.command_mapping.gesture_mapper import (
        ANNOTATION_MODE,
        GO_TO_SLIDE,
        NEXT_SLIDE,
        PREVIOUS_SLIDE,
        VIRTUAL_POINTER,
    )

    parameters = dict(parameters or {})
    clean: dict = {}

    if command in (NEXT_SLIDE, PREVIOUS_SLIDE):
        if parameters.get("count") is not None:
            try:
                count = int(parameters["count"])
            except (TypeError, ValueError):
                raise CommandParameterError("'count' must be a whole number.") from None
            if count < 1:
                raise CommandParameterError("'count' must be at least 1.")
            if count > MAX_REPEAT_COUNT:
                raise CommandParameterError(
                    f"'count' must be at most {MAX_REPEAT_COUNT}; received {count}."
                )
            clean["count"] = count

    elif command == GO_TO_SLIDE:
        raw = parameters.get("slideNumber", parameters.get("slide_number"))
        if raw is None:
            raise CommandParameterError("A slide number is required.")
        try:
            slide = int(raw)
        except (TypeError, ValueError):
            raise CommandParameterError(f"'{raw}' is not a slide number.") from None
        if slide < 1:
            raise CommandParameterError("Slide numbers start at 1.")
        if total_slides and slide > total_slides:
            raise CommandParameterError(
                f"This presentation has {total_slides} slides, so slide {slide} does not exist."
            )
        clean["slideNumber"] = slide

    elif command in (VIRTUAL_POINTER, ANNOTATION_MODE):
        if parameters.get("state") is not None:
            clean["state"] = bool(parameters["state"])

    return clean


def build(
    command: str,
    source: str,
    parameters: dict | None = None,
    confidence: float = 1.0,
    transcript: str | None = None,
    model_version: str | None = None,
    total_slides: int = 0,
) -> CommandIntent:
    """Validated constructor - the only way any modality should build an intent."""
    from computer_vision.command_mapping.gesture_mapper import ALL_COMMANDS

    if command not in ALL_COMMANDS:
        raise CommandParameterError(f"'{command}' is not a VisionX command.")
    if source not in SOURCES:
        raise CommandParameterError(f"'{source}' is not a known command source.")
    return CommandIntent(
        intent=command,
        source=source,
        parameters=normalize_parameters(command, parameters, total_slides),
        confidence=float(confidence),
        transcript=transcript,
        model_version=model_version,
    )
