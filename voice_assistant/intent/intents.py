"""The voice intent vocabulary, derived from commands VisionX can actually run.

Every intent below maps onto a command in `computer_vision.command_mapping`
that `PowerPointController` genuinely implements. No intent exists that the
dispatcher cannot execute.

Intents are not the same as commands, because natural speech distinguishes
things a toggle does not: "turn on the pen" and "turn off the pen" are two
intents that both resolve to ANNOTATION_MODE with an explicit `state`. Toggling
on a voice command would be wrong - saying "turn on the pen" twice must leave
the pen on.
"""

from __future__ import annotations

from dataclasses import dataclass

from computer_vision.command_mapping.gesture_mapper import (
    ANNOTATION_MODE,
    BLACKOUT,
    CLEAR_ANNOTATION,
    COMMAND_LABELS,
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

NO_COMMAND = "NO_COMMAND"

# intent -> (command, parameters fixed by the intent itself)
INTENT_TO_COMMAND: dict[str, tuple[str, dict]] = {
    "NEXT_SLIDE": (NEXT_SLIDE, {}),
    "PREVIOUS_SLIDE": (PREVIOUS_SLIDE, {}),
    "GO_TO_SLIDE": (GO_TO_SLIDE, {}),
    "FIRST_SLIDE": (FIRST_SLIDE, {}),
    "LAST_SLIDE": (LAST_SLIDE, {}),
    "START_PRESENTATION": (START_PRESENTATION, {}),
    "END_PRESENTATION": (END_PRESENTATION, {}),
    "BLACKOUT": (BLACKOUT, {}),
    "WHITEOUT": (WHITEOUT, {}),
    "ENABLE_POINTER": (VIRTUAL_POINTER, {"state": True}),
    "DISABLE_POINTER": (VIRTUAL_POINTER, {"state": False}),
    "ENABLE_ANNOTATION": (ANNOTATION_MODE, {"state": True}),
    "DISABLE_ANNOTATION": (ANNOTATION_MODE, {"state": False}),
    "CLEAR_ANNOTATION": (CLEAR_ANNOTATION, {}),
}

# NO_COMMAND is a real class the model is trained to predict, not an error path.
# Most of what a presenter says is not a command, and the model must say so.
INTENT_CLASSES: tuple[str, ...] = tuple(INTENT_TO_COMMAND) + (NO_COMMAND,)
INTENT_INDEX = {name: index for index, name in enumerate(INTENT_CLASSES)}

# Intents whose meaning depends on a number in the utterance.
PARAMETERIZED_INTENTS = {"GO_TO_SLIDE", "NEXT_SLIDE", "PREVIOUS_SLIDE"}

INTENT_LABELS = {
    "NEXT_SLIDE": "Next slide",
    "PREVIOUS_SLIDE": "Previous slide",
    "GO_TO_SLIDE": "Go to a slide",
    "FIRST_SLIDE": "First slide",
    "LAST_SLIDE": "Last slide",
    "START_PRESENTATION": "Start the slideshow",
    "END_PRESENTATION": "End the slideshow",
    "BLACKOUT": "Black screen",
    "WHITEOUT": "White screen",
    "ENABLE_POINTER": "Turn the pointer on",
    "DISABLE_POINTER": "Turn the pointer off",
    "ENABLE_ANNOTATION": "Turn the pen on",
    "DISABLE_ANNOTATION": "Turn the pen off",
    "CLEAR_ANNOTATION": "Erase the ink",
    NO_COMMAND: "Not a command",
}


@dataclass(frozen=True)
class VoiceThresholds:
    """How confident the classifier must be before anything happens.

    Three bands, because a probability is not a guarantee of correctness:

        p >= execute   run it
        p >= confirm   show it and wait for the presenter to accept
        otherwise      NO_COMMAND - say nothing, do nothing

    Defaults are chosen from the held-out test sweep in the training report, not
    picked by feel; `voice_assistant/training/train_intent_model.py` prints the
    false-command rate at each gate.
    """

    execute: float = 0.75
    confirm: float = 0.50

    def band(self, probability: float) -> str:
        if probability >= self.execute:
            return "EXECUTE"
        if probability >= self.confirm:
            return "CONFIRM"
        return "REJECT"


def command_for(intent: str) -> tuple[str, dict]:
    """Resolve an intent to (command, parameters). Raises for NO_COMMAND."""
    if intent not in INTENT_TO_COMMAND:
        raise KeyError(f"'{intent}' does not map to a VisionX command.")
    command, parameters = INTENT_TO_COMMAND[intent]
    return command, dict(parameters)


def intent_catalogue() -> list[dict]:
    """Serialisable list for the voice settings screen."""
    return [
        {
            "intent": intent,
            "label": INTENT_LABELS[intent],
            "command": INTENT_TO_COMMAND.get(intent, (None, {}))[0],
            "commandLabel": COMMAND_LABELS.get(INTENT_TO_COMMAND.get(intent, (None, {}))[0]),
            "parameterized": intent in PARAMETERIZED_INTENTS,
        }
        for intent in INTENT_CLASSES
    ]
