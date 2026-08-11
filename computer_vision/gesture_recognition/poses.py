"""The pose library.

Gesture -> command mapping is configuration, not code: the recognizer only ever
reports a *pose name* from this library. Which pose triggers which command lives
in the user's GesturePreferences document, so any pose can drive any command.
"""

from dataclasses import dataclass

NO_HAND = "NONE"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Pose:
    name: str
    label: str                       # human-friendly name shown in the UI
    fingers: tuple[int, int, int, int, int]  # thumb, index, middle, ring, pinky
    description: str


POSE_LIBRARY: tuple[Pose, ...] = (
    Pose("OPEN_PALM", "Open palm", (1, 1, 1, 1, 1), "All five fingers extended"),
    Pose("FIST", "Closed fist", (0, 0, 0, 0, 0), "All fingers curled in"),
    Pose("THUMB_UP", "Thumb only", (1, 0, 0, 0, 0), "Thumb extended, other fingers curled"),
    Pose("INDEX_UP", "Index finger", (0, 1, 0, 0, 0), "Index finger only"),
    Pose("INDEX_MIDDLE_UP", "Two fingers", (0, 1, 1, 0, 0), "Index and middle fingers"),
    Pose("THREE_FINGERS_UP", "Three fingers", (0, 1, 1, 1, 0), "Index, middle and ring fingers"),
    Pose("FOUR_FINGERS_UP", "Four fingers", (0, 1, 1, 1, 1), "All fingers except the thumb"),
    Pose("PINKY_UP", "Pinky only", (0, 0, 0, 0, 1), "Little finger only"),
    Pose("THUMB_PINKY", "Thumb + pinky", (1, 0, 0, 0, 1), "Thumb and little finger extended"),
    Pose("THUMB_INDEX", "Thumb + index", (1, 1, 0, 0, 0), "Thumb and index finger extended"),
)

POSE_BY_SIGNATURE = {pose.fingers: pose for pose in POSE_LIBRARY}
POSE_BY_NAME = {pose.name: pose for pose in POSE_LIBRARY}
POSE_NAMES = tuple(pose.name for pose in POSE_LIBRARY)


def pose_catalogue() -> list[dict]:
    """Serialisable pose list for the Gesture Settings screen."""
    return [
        {
            "name": pose.name,
            "label": pose.label,
            "description": pose.description,
            "fingers": list(pose.fingers),
        }
        for pose in POSE_LIBRARY
    ]


def signature_to_pose(signature: tuple[int, int, int, int, int]) -> str:
    pose = POSE_BY_SIGNATURE.get(tuple(signature))
    return pose.name if pose else UNKNOWN
