"""Pose name -> presentation command, driven entirely by the user's preferences."""

from computer_vision.gesture_recognition.poses import POSE_BY_NAME

NEXT_SLIDE = "NEXT_SLIDE"
PREVIOUS_SLIDE = "PREVIOUS_SLIDE"
VIRTUAL_POINTER = "VIRTUAL_POINTER"
ANNOTATION_MODE = "ANNOTATION_MODE"
CLEAR_ANNOTATION = "CLEAR_ANNOTATION"

COMMANDS = (NEXT_SLIDE, PREVIOUS_SLIDE, VIRTUAL_POINTER, ANNOTATION_MODE, CLEAR_ANNOTATION)

# GesturePreferences field name -> command
PREFERENCE_FIELDS = {
    "nextSlideGesture": NEXT_SLIDE,
    "previousSlideGesture": PREVIOUS_SLIDE,
    "pointerGesture": VIRTUAL_POINTER,
    "annotationGesture": ANNOTATION_MODE,
    "clearGesture": CLEAR_ANNOTATION,
}

COMMAND_LABELS = {
    NEXT_SLIDE: "Next slide",
    PREVIOUS_SLIDE: "Previous slide",
    VIRTUAL_POINTER: "Virtual pointer",
    ANNOTATION_MODE: "Annotation mode",
    CLEAR_ANNOTATION: "Clear annotation",
}

DEFAULT_PREFERENCES = {
    "nextSlideGesture": "PINKY_UP",
    "previousSlideGesture": "THUMB_UP",
    "pointerGesture": "INDEX_MIDDLE_UP",
    "annotationGesture": "INDEX_UP",
    "clearGesture": "THREE_FINGERS_UP",
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
