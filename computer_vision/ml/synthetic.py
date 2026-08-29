"""A procedural hand-landmark generator for pipeline validation.

WHY THIS EXISTS
---------------
The personalized gesture model is trained on landmarks a real person produces in
front of a real webcam. That data cannot be committed to the repository (it is
per-user biometric-adjacent data) and cannot be produced in CI. This module
generates landmark sets from a small kinematic hand model so the training,
evaluation, export and inference pipeline can be exercised end to end - in
tests, and on a machine with no camera attached.

WHAT IT IS NOT
--------------
It is not a substitute for enrolment and its metrics are NOT a benchmark of how
well VisionX recognises real hands. Anything trained on it must be reported as a
synthetic smoke test. `verify_against_geometric_recognizer()` measures how often
the shipped geometric recognizer agrees with the generator's intended label,
which is the honest way to state how hand-like the output actually is.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from computer_vision.gesture_recognition.poses import POSE_BY_NAME, POSE_LIBRARY
from computer_vision.hand_detection.hand_detector import (
    INDEX_MCP,
    INDEX_PIP,
    INDEX_DIP,
    INDEX_TIP,
    MIDDLE_MCP,
    MIDDLE_PIP,
    MIDDLE_DIP,
    MIDDLE_TIP,
    PINKY_MCP,
    PINKY_PIP,
    PINKY_DIP,
    PINKY_TIP,
    RING_MCP,
    RING_PIP,
    RING_DIP,
    RING_TIP,
    THUMB_CMC,
    THUMB_IP,
    THUMB_MCP,
    THUMB_TIP,
    WRIST,
)
from computer_vision.ml.dataset import NULL_CLASS

SYNTHETIC_SUBJECT = "synthetic:v1"

# Hand-local skeleton, palm length (wrist -> middle MCP) normalised to 1.0.
# +Y is the palm direction, +X is the pinky side of a right hand, +Z is depth.
_MCP = {
    "index": np.array([-0.24, 0.92, 0.0]),
    "middle": np.array([0.00, 1.00, 0.0]),
    "ring": np.array([0.22, 0.95, 0.0]),
    "pinky": np.array([0.42, 0.82, 0.0]),
}
_SEGMENTS = {
    "index": (0.42, 0.26, 0.20),
    "middle": (0.46, 0.29, 0.21),
    "ring": (0.42, 0.27, 0.20),
    "pinky": (0.33, 0.21, 0.17),
}
_FAN = {"index": -0.12, "middle": 0.0, "ring": 0.10, "pinky": 0.22}  # radians

_CHAIN = {
    "index": (INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP),
    "middle": (MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP),
    "ring": (RING_MCP, RING_PIP, RING_DIP, RING_TIP),
    "pinky": (PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP),
}
_FINGER_ORDER = ("index", "middle", "ring", "pinky")

# Cumulative bend per joint, as a fraction of pi, at curl = 1.0.
_MAX_BEND = (0.52, 0.60, 0.42)
_DEPTH_SCALE = 0.55   # MediaPipe's z is foreshortened relative to x/y


@dataclass
class HandPose:
    """Continuous description of a hand: one curl value in 0..1 per finger."""

    thumb: float
    index: float
    middle: float
    ring: float
    pinky: float

    def curls(self) -> tuple[float, float, float, float, float]:
        return (self.thumb, self.index, self.middle, self.ring, self.pinky)


def _rotate_towards_depth(direction: np.ndarray, angle: float) -> np.ndarray:
    """Bend a finger by rotating its direction out of the image plane.

    A curled finger foreshortens: its tip projects back toward the knuckle, which
    is exactly the geometry the shipped geometric recognizer measures.
    """
    planar = direction.copy()
    planar[2] = 0.0
    norm = float(np.linalg.norm(planar))
    if norm < 1e-6:
        return direction
    planar /= norm
    depth = np.array([0.0, 0.0, 1.0])
    return math.cos(angle) * planar + math.sin(angle) * depth


def _build_finger(name: str, curl: float) -> list[np.ndarray]:
    base = _MCP[name]
    fan = _FAN[name]
    direction = base / np.linalg.norm(base)
    cos_f, sin_f = math.cos(fan), math.sin(fan)
    direction = np.array([
        direction[0] * cos_f - direction[1] * sin_f,
        direction[0] * sin_f + direction[1] * cos_f,
        0.0,
    ])

    points = [base.copy()]
    cursor = base.copy()
    cumulative = 0.0
    for index, length in enumerate(_SEGMENTS[name]):
        cumulative += _MAX_BEND[index] * math.pi * curl
        segment = _rotate_towards_depth(direction, cumulative) * length
        cursor = cursor + segment
        points.append(cursor.copy())
    return points


def _build_thumb(curl: float) -> list[np.ndarray]:
    """The thumb abducts sideways rather than curling, so it gets its own chain.

    curl = 0 -> abducted away from the pinky (the recognizer reads 'extended')
    curl = 1 -> folded across the palm toward the pinky knuckle
    """
    cmc = np.array([-0.26, 0.20, 0.0])
    # Direction sweeps from 'out and up' to 'across the palm'.
    angle = math.radians(-58.0 + 118.0 * curl)
    direction = np.array([math.sin(angle), math.cos(angle), 0.0])
    direction /= np.linalg.norm(direction)

    points = [cmc.copy()]
    cursor = cmc.copy()
    for index, length in enumerate((0.34, 0.26, 0.22)):
        bend = 0.30 * math.pi * curl * (index + 1) / 3.0
        segment = _rotate_towards_depth(direction, bend) * length
        cursor = cursor + segment
        points.append(cursor.copy())
    return points


def build_landmarks(pose: HandPose) -> np.ndarray:
    """Assemble a (21, 3) hand in the hand-local frame."""
    points = np.zeros((21, 3), dtype=np.float64)
    thumb = _build_thumb(pose.thumb)
    for slot, value in zip((THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP), thumb):
        points[slot] = value
    for name, curl in zip(_FINGER_ORDER, (pose.index, pose.middle, pose.ring, pose.pinky)):
        chain = _build_finger(name, curl)
        for slot, value in zip(_CHAIN[name], chain):
            points[slot] = value
    points[WRIST] = np.zeros(3)
    points[:, 2] *= _DEPTH_SCALE
    return points


def project_to_frame(
    points: np.ndarray,
    rotation_deg: float = 0.0,
    scale: float = 0.30,
    centre: tuple[float, float] = (0.5, 0.55),
    aspect: float = 4 / 3,
) -> np.ndarray:
    """Place a hand-local skeleton into a frame-normalised (0..1) landmark set.

    Mirrors what MediaPipe returns: x and y independently normalised to the frame,
    so x carries the frame's aspect distortion that canonicalization undoes.
    """
    theta = math.radians(rotation_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    rotated = points.copy()
    rotated[:, 0] = points[:, 0] * cos_t - points[:, 1] * sin_t
    rotated[:, 1] = points[:, 0] * sin_t + points[:, 1] * cos_t

    frame = np.zeros_like(rotated)
    # y grows downward in image space; the palm points up the frame.
    frame[:, 0] = centre[0] + (rotated[:, 0] * scale) / aspect
    frame[:, 1] = centre[1] - rotated[:, 1] * scale
    frame[:, 2] = rotated[:, 2] * scale
    return frame.astype(np.float32)


def pose_for_label(label: str, rng: random.Random) -> HandPose:
    """Sample a continuous hand pose consistent with a pose-library label.

    NULL_CLASS is sampled from the mid-bend region: hands that are moving, half
    open, or resting - the natural non-command movement the model must reject.
    """
    if label == NULL_CLASS:
        curls = [rng.uniform(0.28, 0.72) for _ in range(5)]
        # Often an almost-formed pose: a hand caught mid-transition is the hardest
        # negative there is, and these ranges deliberately overlap the pose classes
        # below so the null class is not trivially separable.
        if rng.random() < 0.45:
            template = rng.choice(POSE_LIBRARY).fingers
            curls = [
                (rng.uniform(0.10, 0.45) if bit else rng.uniform(0.62, 0.92))
                for bit in template
            ]
        return HandPose(*curls)

    pose = POSE_BY_NAME[label]
    curls = [
        (rng.uniform(0.00, 0.16) if bit else rng.uniform(0.86, 1.00))
        for bit in pose.fingers
    ]
    return HandPose(*curls)


def generate_recording(
    label: str,
    frames: int = 60,
    rng: random.Random | None = None,
    aspect: float = 4 / 3,
    noise: float = 0.0035,
) -> list[np.ndarray]:
    """One recording: a held pose with natural drift, jitter and framing.

    Frames inside a recording share the pose and the framing, exactly like a real
    capture - which is why recordings, not frames, are the unit of splitting.
    """
    rng = rng or random.Random()
    base = pose_for_label(label, rng)
    rotation = rng.uniform(-22.0, 22.0)
    scale = rng.uniform(0.20, 0.36)
    centre = (rng.uniform(0.35, 0.65), rng.uniform(0.42, 0.68))

    out: list[np.ndarray] = []
    for _ in range(frames):
        drift = [
            float(np.clip(value + rng.gauss(0.0, 0.035), 0.0, 1.0))
            for value in base.curls()
        ]
        landmarks = build_landmarks(HandPose(*drift))
        frame = project_to_frame(
            landmarks,
            rotation_deg=rotation + rng.gauss(0.0, 2.5),
            scale=scale * (1.0 + rng.gauss(0.0, 0.02)),
            centre=(centre[0] + rng.gauss(0.0, 0.006), centre[1] + rng.gauss(0.0, 0.006)),
            aspect=aspect,
        )
        frame += np.asarray(
            [[rng.gauss(0.0, noise), rng.gauss(0.0, noise), rng.gauss(0.0, noise)] for _ in range(21)],
            dtype=np.float32,
        )
        out.append(np.clip(frame, -0.2, 1.2).astype(np.float32))
    return out


def verify_against_geometric_recognizer(samples_per_class: int = 60, seed: int = 7) -> dict:
    """How often the shipped geometric recognizer agrees with the intended label.

    This is the honest measure of whether the generator produces hand-like
    geometry. It is reported alongside any synthetic training run so nobody
    mistakes a synthetic score for a real-world benchmark.
    """
    from computer_vision.gesture_recognition.gesture_recognizer import GestureRecognizer
    from computer_vision.hand_detection.hand_detector import HandLandmarks

    recognizer = GestureRecognizer()
    rng = random.Random(seed)
    aspect = 4 / 3
    report: dict[str, dict] = {}

    for pose in POSE_LIBRARY:
        agree = 0
        for _ in range(samples_per_class):
            frame = generate_recording(pose.name, frames=1, rng=rng, aspect=aspect)[0]
            hand = HandLandmarks(points=frame, handedness="Right", detection_score=0.95)
            result = recognizer.recognize(hand, aspect)
            agree += int(result.gesture == pose.name)
        report[pose.name] = {
            "agreement": round(agree / samples_per_class, 4),
            "samples": samples_per_class,
        }

    overall = sum(row["agreement"] for row in report.values()) / len(report)
    return {"perClass": report, "overallAgreement": round(overall, 4)}
