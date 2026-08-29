"""Landmark canonicalization: MediaPipe landmarks -> a model-ready feature vector.

This is the single, versioned, deterministic preprocessing step shared by data
collection, training, evaluation and live inference. If this file changes in a
way that changes the numbers it produces, FEATURE_VERSION must change too - a
model trained on v1 features must never be fed v2 features.

The transform, in order:

  0. undo the frame's aspect distortion (landmarks arrive normalised to the
     frame, so x is squashed by width/height exactly as the geometric
     recognizer already compensates for)
  1. translate the wrist to the origin          -> translation invariance
  2. divide by the palm length (wrist->middle MCP) -> scale invariance
  3. rotate in-plane so wrist->middle MCP points at +Y -> rotation invariance
  4. flatten to 63 numbers (21 landmarks x 3)
  5. optionally append 23 derived geometric features

Raw image pixels are never used. Only landmark geometry reaches the model.
"""

from __future__ import annotations

import numpy as np

from computer_vision.hand_detection.hand_detector import (
    INDEX_MCP,
    INDEX_PIP,
    INDEX_TIP,
    MIDDLE_MCP,
    MIDDLE_PIP,
    MIDDLE_TIP,
    PINKY_MCP,
    PINKY_PIP,
    PINKY_TIP,
    RING_PIP,
    RING_TIP,
    THUMB_IP,
    THUMB_TIP,
    WRIST,
)

FEATURE_VERSION = "gesture-canonical-v1"

LANDMARK_COUNT = 21
LANDMARK_DIMS = 3
BASE_DIMENSION = LANDMARK_COUNT * LANDMARK_DIMS      # 63
GEOMETRY_DIMENSION = 23
FULL_DIMENSION = BASE_DIMENSION + GEOMETRY_DIMENSION  # 86

_EPS = 1e-6

FINGER_TIPS = (THUMB_TIP, INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)
FINGER_PIPS = (THUMB_IP, INDEX_PIP, MIDDLE_PIP, RING_PIP, PINKY_PIP)


def feature_dimension(include_geometry: bool = True) -> int:
    """Length of the vector `canonical_features` returns."""
    return FULL_DIMENSION if include_geometry else BASE_DIMENSION


def as_points(landmarks) -> np.ndarray:
    """Coerce any accepted landmark container into a (21, 3) float32 array."""
    points = np.asarray(landmarks, dtype=np.float32)
    if points.ndim == 1:
        if points.size != LANDMARK_COUNT * LANDMARK_DIMS:
            raise ValueError(
                f"Expected {LANDMARK_COUNT * LANDMARK_DIMS} values, received {points.size}."
            )
        points = points.reshape(LANDMARK_COUNT, LANDMARK_DIMS)
    if points.shape == (LANDMARK_COUNT, 2):
        points = np.concatenate([points, np.zeros((LANDMARK_COUNT, 1), dtype=np.float32)], axis=1)
    if points.shape != (LANDMARK_COUNT, LANDMARK_DIMS):
        raise ValueError(f"Expected a (21, 3) landmark array, received {points.shape}.")
    if not np.all(np.isfinite(points)):
        raise ValueError("Landmark array contains NaN or infinite values.")
    return points.astype(np.float32, copy=True)


def canonicalize(landmarks, aspect: float = 1.0) -> np.ndarray:
    """Return the (21, 3) canonical pose: origin at the wrist, unit palm, upright.

    `aspect` is the frame's width/height. MediaPipe normalises x and y
    independently to 0..1, so on a 4:3 frame one unit of x is 1.333 times one
    unit of y; multiplying x back out makes distances metric again.
    """
    points = as_points(landmarks)
    points[:, 0] *= float(aspect)

    # 1. translate
    points -= points[WRIST]

    # 2. scale by palm length; fall back to knuckle span for a degenerate hand
    palm = float(np.linalg.norm(points[MIDDLE_MCP][:2]))
    if palm < _EPS:
        palm = float(np.linalg.norm(points[INDEX_MCP][:2] - points[PINKY_MCP][:2]))
    points /= max(palm, _EPS)

    # 3. rotate in-plane so wrist->middle MCP lands on +Y
    axis = points[MIDDLE_MCP][:2]
    norm = float(np.linalg.norm(axis))
    if norm >= _EPS:
        # Rotation that maps `axis` onto (0, 1).
        cos_t, sin_t = axis[1] / norm, axis[0] / norm
        rotation = np.array([[cos_t, -sin_t], [sin_t, cos_t]], dtype=np.float32)
        points[:, :2] = points[:, :2] @ rotation.T

    return points.astype(np.float32, copy=False)


def _geometry_features(canonical: np.ndarray) -> np.ndarray:
    """23 derived features that a flat coordinate list makes the model rediscover.

    10 pairwise fingertip distances, 5 finger extension ratios, 4 angles between
    adjacent finger directions, 3 bounding-box terms and the knuckle span.
    """
    tips = canonical[list(FINGER_TIPS), :2]
    pairwise = [
        float(np.linalg.norm(tips[i] - tips[j]))
        for i in range(len(FINGER_TIPS))
        for j in range(i + 1, len(FINGER_TIPS))
    ]

    extension = []
    for tip, pip in zip(FINGER_TIPS, FINGER_PIPS):
        d_tip = float(np.linalg.norm(canonical[tip][:2]))
        d_pip = float(np.linalg.norm(canonical[pip][:2]))
        extension.append(d_tip / max(d_pip, _EPS))

    directions = []
    for tip, mcp in zip(
        (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP),
        (INDEX_MCP, MIDDLE_MCP, PINKY_MCP, PINKY_MCP),
    ):
        vector = canonical[tip][:2] - canonical[mcp][:2]
        directions.append(vector / max(float(np.linalg.norm(vector)), _EPS))
    angles = [float(np.clip(np.dot(directions[i], directions[i + 1]), -1.0, 1.0)) for i in range(3)]
    angles.append(float(np.clip(np.dot(directions[0], directions[-1]), -1.0, 1.0)))

    xy = canonical[:, :2]
    box = xy.max(axis=0) - xy.min(axis=0)
    bbox = [float(box[0]), float(box[1]), float(box[0] / max(box[1], _EPS))]
    span = [float(np.linalg.norm(canonical[INDEX_MCP][:2] - canonical[PINKY_MCP][:2]))]

    return np.asarray(pairwise + extension + angles + bbox + span, dtype=np.float32)


def canonical_features(landmarks, aspect: float = 1.0, include_geometry: bool = True) -> np.ndarray:
    """The production feature vector: 86 floats (63 canonical + 23 geometric)."""
    canonical = canonicalize(landmarks, aspect)
    flat = canonical.reshape(-1)
    if not include_geometry:
        return flat
    return np.concatenate([flat, _geometry_features(canonical)]).astype(np.float32, copy=False)


def batch_features(rows, aspect: float = 1.0, include_geometry: bool = True) -> np.ndarray:
    """Stack `canonical_features` over an iterable of landmark arrays."""
    vectors = [canonical_features(row, aspect, include_geometry) for row in rows]
    if not vectors:
        return np.zeros((0, feature_dimension(include_geometry)), dtype=np.float32)
    return np.vstack(vectors).astype(np.float32, copy=False)


def describe() -> dict:
    """Serialisable description recorded in every dataset and model metadata file."""
    return {
        "featureVersion": FEATURE_VERSION,
        "landmarkCount": LANDMARK_COUNT,
        "baseDimension": BASE_DIMENSION,
        "geometryDimension": GEOMETRY_DIMENSION,
        "fullDimension": FULL_DIMENSION,
        "steps": [
            "aspect correction",
            "wrist translation to origin",
            "palm-length scale normalisation",
            "in-plane rotation to a +Y palm axis",
            "flatten 21x3",
            "append 23 geometric features",
        ],
    }
