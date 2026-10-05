"""Pose name -> presentation command, driven entirely by the user's preferences."""

from computer_vision.gesture_recognition.poses import POSE_BY_NAME

NEXT_SLIDE = "NEXT_SLIDE"
PREVIOUS_SLIDE = "PREVIOUS_SLIDE"
VIRTUAL_POINTER = "VIRTUAL_POINTER"
ANNOTATION_MODE = "ANNOTATION_MODE"
CLEAR_ANNOTATION = "CLEAR_ANNOTATION"
RESET_ANNOTATION = "RESET_ANNOTATION"

# Commands a hand pose can be bound to: six poses, six commands, which is what
# the gesture settings screen and GesturePreferences describe.
#
# RESET_ANNOTATION is the "stop everything" escape hatch. CLEAR_ANNOTATION erases
# ink and deliberately leaves the pen armed so the presenter can carry on drawing
# on a clean slide; RESET_ANNOTATION erases the ink *and* leaves pen and pointer
# mode, putting the session back in its default state. An open palm is the
# default binding because it is the pose a presenter already makes when they
# stop gesturing and show the audience an empty hand.
COMMANDS = (
    NEXT_SLIDE, PREVIOUS_SLIDE, VIRTUAL_POINTER,
    ANNOTATION_MODE, CLEAR_ANNOTATION, RESET_ANNOTATION,
)

# Commands that exist but are not bound to a pose: they need a parameter, or they
# are awkward to hold a hand still for. Voice, the control bar and the keyboard
# fallback can all issue them, and every one is a real PowerPoint shortcut that
# PowerPointController actually implements - nothing here is aspirational.
GO_TO_SLIDE = "GO_TO_SLIDE"
FIRST_SLIDE = "FIRST_SLIDE"
LAST_SLIDE = "LAST_SLIDE"
START_PRESENTATION = "START_PRESENTATION"
END_PRESENTATION = "END_PRESENTATION"
BLACKOUT = "BLACKOUT"
WHITEOUT = "WHITEOUT"

UNBOUND_COMMANDS = (
    GO_TO_SLIDE, FIRST_SLIDE, LAST_SLIDE,
    START_PRESENTATION, END_PRESENTATION, BLACKOUT, WHITEOUT,
)

ALL_COMMANDS = COMMANDS + UNBOUND_COMMANDS

# GesturePreferences field name -> command
PREFERENCE_FIELDS = {
    "nextSlideGesture": NEXT_SLIDE,
    "previousSlideGesture": PREVIOUS_SLIDE,
    "pointerGesture": VIRTUAL_POINTER,
    "annotationGesture": ANNOTATION_MODE,
    "clearGesture": CLEAR_ANNOTATION,
    "resetGesture": RESET_ANNOTATION,
}

COMMAND_LABELS = {
    NEXT_SLIDE: "Next slide",
    PREVIOUS_SLIDE: "Previous slide",
    VIRTUAL_POINTER: "Virtual pointer",
    ANNOTATION_MODE: "Annotation mode",
    CLEAR_ANNOTATION: "Clear annotation",
    RESET_ANNOTATION: "Exit annotation",
    GO_TO_SLIDE: "Go to slide",
    FIRST_SLIDE: "First slide",
    LAST_SLIDE: "Last slide",
    START_PRESENTATION: "Start slideshow",
    END_PRESENTATION: "End slideshow",
    BLACKOUT: "Black screen",
    WHITEOUT: "White screen",
}

# Parameters each command accepts. The voice layer and the dispatcher agree on
# this table, so a parameter can never be produced that dispatch cannot consume.
COMMAND_PARAMETERS = {
    NEXT_SLIDE: ("count",),
    PREVIOUS_SLIDE: ("count",),
    GO_TO_SLIDE: ("slideNumber",),
    VIRTUAL_POINTER: ("state",),
    ANNOTATION_MODE: ("state",),
}


def command_catalogue() -> list[dict]:
    """Serialisable command list for the settings and voice screens."""
    return [
        {
            "command": command,
            "label": COMMAND_LABELS[command],
            "bindable": command in COMMANDS,
            "parameters": list(COMMAND_PARAMETERS.get(command, ())),
        }
        for command in ALL_COMMANDS
    ]


DEFAULT_PREFERENCES = {
    "nextSlideGesture": "PINKY_UP",
    "previousSlideGesture": "THUMB_UP",
    "pointerGesture": "INDEX_MIDDLE_UP",
    "annotationGesture": "INDEX_UP",
    "clearGesture": "THREE_FINGERS_UP",
    "resetGesture": "OPEN_PALM",
}


class GestureMapper:
    """Immutable view over one user's gesture bindings."""

    def __init__(self, preferences: dict | None = None):
        self._pose_to_command: dict[str, str] = {}
        self.load(preferences or DEFAULT_PREFERENCES)

    def load(self, preferences: dict) -> None:
        mapping: dict[str, str] = {}
        for field, command in PREFERENCE_FIELDS.items():
            pose = preferences.get(field) or DEFAULT_PREFERENCES[field]
            if pose in POSE_BY_NAME:
                mapping[pose] = command
        self._pose_to_command = mapping

    def map(self, pose: str) -> str | None:
        return self._pose_to_command.get(pose)

    def is_bound(self, pose: str) -> bool:
        return pose in self._pose_to_command

    @property
    def bindings(self) -> dict[str, str]:
        return dict(self._pose_to_command)


def validate_preferences(preferences: dict) -> tuple[bool, str]:
    """Every command needs a pose, and no pose may drive two commands."""
    poses = []
    for field in PREFERENCE_FIELDS:
        pose = preferences.get(field)
        if not pose:
            return False, f"'{field}' is required."
        if pose not in POSE_BY_NAME:
            return False, f"'{pose}' is not a recognised hand pose."
        poses.append(pose)

    if len(set(poses)) != len(poses):
        return False, "Each hand pose can only be assigned to one command."
    return True, ""
