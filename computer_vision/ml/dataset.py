"""Gesture dataset: on-disk format, quality gates and leak-free splitting.

Format
------
One JSON object per line (JSONL). A *recording* is one continuous capture of a
single label; every frame in it shares a `recordingId`. Recordings are the unit
of splitting - frames from one recording are highly correlated, so splitting
individual frames across train/val/test would leak and inflate every metric.

    data/gesture/v1/samples/<subject>/<recordingId>.jsonl
    data/gesture/v1/manifest.json

Each line:

    {
      "schemaVersion": 1,
      "sampleId":      "<recordingId>#<frame index>",
      "recordingId":   "rec_20260829T101500_a1b2",
      "subjectId":     "user:66f0..." | "synthetic:v1",
      "label":         "PINKY_UP",
      "featureVersion":"gesture-canonical-v1",
      "features":      [86 floats],
      "landmarks":     [[x, y, z] x 21],      # optional raw metadata
      "aspect":        1.3333,
      "detectionScore":0.97,
      "brightness":    118.4,
      "handBoxArea":   0.081,
      "handedness":    "Right",
      "capturedAt":    "2026-08-29T10:15:00+00:00",
      "sessionId":     null
    }
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from computer_vision.gesture_recognition.poses import POSE_NAMES, UNKNOWN
from computer_vision.ml import paths
from computer_vision.ml.canonicalization import (
    FEATURE_VERSION,
    canonical_features,
    feature_dimension,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# The class list is derived from the repository's real pose library - it is never
# hard-coded here. UNKNOWN doubles as the explicit NULL / OTHER class: the existing
# debouncer already treats an UNKNOWN pose as a neutral, non-command state, so a
# model that predicts it needs no special handling downstream.
NULL_CLASS = UNKNOWN
GESTURE_CLASSES: tuple[str, ...] = tuple(POSE_NAMES) + (NULL_CLASS,)
CLASS_INDEX = {name: index for index, name in enumerate(GESTURE_CLASSES)}


class DatasetError(RuntimeError):
    """Raised when a dataset is missing, malformed or unusable for training."""


@dataclass
class QualityConfig:
    """Gate applied to every captured frame before it enters the dataset."""

    min_detection_score: float = 0.55
    min_brightness: float = 25.0
    min_hand_box_area: float = 0.010   # hand smaller than 1% of frame: too far away
    max_hand_box_area: float = 0.80
    duplicate_epsilon: float = 1e-3    # canonical feature L-inf distance to the previous frame


@dataclass
class GestureSample:
    label: str
    features: list[float]
    recording_id: str
    subject_id: str
    sample_id: str = ""
    feature_version: str = FEATURE_VERSION
    schema_version: int = SCHEMA_VERSION
    landmarks: list[list[float]] | None = None
    aspect: float = 1.0
    detection_score: float = 1.0
    brightness: float = 0.0
    hand_box_area: float = 0.0
    handedness: str = ""
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str | None = None

    def to_json(self) -> dict:
        payload = {
            "schemaVersion": self.schema_version,
            "sampleId": self.sample_id,
            "recordingId": self.recording_id,
            "subjectId": self.subject_id,
            "label": self.label,
            "featureVersion": self.feature_version,
            "features": [round(float(v), 6) for v in self.features],
            "aspect": round(float(self.aspect), 5),
            "detectionScore": round(float(self.detection_score), 4),
            "brightness": round(float(self.brightness), 2),
            "handBoxArea": round(float(self.hand_box_area), 5),
            "handedness": self.handedness,
            "capturedAt": self.captured_at,
            "sessionId": self.session_id,
        }
        if self.landmarks is not None:
            payload["landmarks"] = [[round(float(v), 5) for v in point] for point in self.landmarks]
        return payload

    @classmethod
    def from_json(cls, row: dict) -> "GestureSample":
        return cls(
            label=str(row["label"]),
            features=[float(v) for v in row["features"]],
            recording_id=str(row.get("recordingId") or "unknown"),
            subject_id=str(row.get("subjectId") or "unknown"),
            sample_id=str(row.get("sampleId") or ""),
            feature_version=str(row.get("featureVersion") or FEATURE_VERSION),
            schema_version=int(row.get("schemaVersion") or SCHEMA_VERSION),
            landmarks=row.get("landmarks"),
            aspect=float(row.get("aspect") or 1.0),
            detection_score=float(row.get("detectionScore") or 1.0),
            brightness=float(row.get("brightness") or 0.0),
            hand_box_area=float(row.get("handBoxArea") or 0.0),
            handedness=str(row.get("handedness") or ""),
            captured_at=str(row.get("capturedAt") or ""),
            session_id=row.get("sessionId"),
        )


# --- quality ------------------------------------------------------------------
def hand_box_area(landmarks) -> float:
    """Fraction of the frame covered by the hand's bounding box (0..1)."""
    points = np.asarray(landmarks, dtype=np.float32).reshape(-1, 3)[:, :2]
    box = points.max(axis=0) - points.min(axis=0)
    return float(np.clip(box[0] * box[1], 0.0, 1.0))


def assess_frame(
    landmarks,
    detection_score: float,
    brightness: float,
    previous_features: np.ndarray | None = None,
    aspect: float = 1.0,
    config: QualityConfig | None = None,
) -> tuple[bool, str, np.ndarray | None]:
    """Accept or reject one captured frame. Returns (accepted, reason, features)."""
    config = config or QualityConfig()
    try:
        features = canonical_features(landmarks, aspect)
    except ValueError as exc:
        return False, f"invalid landmarks: {exc}", None

    if detection_score < config.min_detection_score:
        return False, "low detection confidence", None
    if brightness and brightness < config.min_brightness:
        return False, "too dark", None

    area = hand_box_area(landmarks)
    if area < config.min_hand_box_area:
        return False, "hand too far from the camera", None
    if area > config.max_hand_box_area:
        return False, "hand too close to the camera", None

    if previous_features is not None:
        if float(np.abs(features - previous_features).max()) < config.duplicate_epsilon:
            return False, "duplicate of the previous frame", None

    return True, "", features


# --- persistence --------------------------------------------------------------
def new_recording_id(prefix: str = "rec") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}_{stamp}_{random.getrandbits(20):05x}"


def write_recording(samples: list[GestureSample], root: Path | None = None,
                    version: str = paths.DATASET_VERSION) -> Path:
    """Append one recording to the versioned dataset. Never overwrites an existing file."""
    if not samples:
        raise DatasetError("Cannot write an empty recording.")
    recording_id = samples[0].recording_id
    subject = samples[0].subject_id
    directory = (root or paths.gesture_data_root()) / version / "samples" / paths.safe_component(subject)
    directory.mkdir(parents=True, exist_ok=True)

    target = directory / f"{paths.safe_component(recording_id)}.jsonl"
    if target.exists():
        raise DatasetError(f"Recording {recording_id} already exists at {target}.")

    with target.open("w", encoding="utf-8") as handle:
        for index, sample in enumerate(samples):
            if not sample.sample_id:
                sample.sample_id = f"{recording_id}#{index}"
            handle.write(json.dumps(sample.to_json()) + "\n")
    return target


def dataset_files(root: Path | None = None, version: str = paths.DATASET_VERSION,
                  subject: str | None = None) -> list[Path]:
    base = (root or paths.gesture_data_root()) / version / "samples"
    if subject:
        base = base / paths.safe_component(subject)
    if not base.exists():
        return []
    return sorted(base.rglob("*.jsonl"))


def load_dataset(root: Path | None = None, version: str = paths.DATASET_VERSION,
                 subject: str | None = None, feature_version: str = FEATURE_VERSION,
                 strict: bool = True) -> list[GestureSample]:
    """Load every sample, rejecting rows whose feature version does not match."""
    files = dataset_files(root, version, subject)
    if not files:
        raise DatasetError(
            f"No gesture recordings found under {(root or paths.gesture_data_root()) / version / 'samples'}. "
            "Collect samples through gesture enrolment, or generate a synthetic dataset with "
            "`python -m computer_vision.ml.training.synthesize_dataset`."
        )

    samples: list[GestureSample] = []
    skipped_version = 0
    skipped_label = 0
    expected_dim = feature_dimension(True)

    for path in files:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                sample = GestureSample.from_json(row)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if strict:
                    raise DatasetError(f"{path}:{line_number} is malformed: {exc}") from exc
                logger.warning("Skipping malformed sample %s:%s (%s)", path, line_number, exc)
                continue

            if sample.feature_version != feature_version:
                skipped_version += 1
                continue
            if sample.label not in CLASS_INDEX:
                skipped_label += 1
                continue
            if len(sample.features) != expected_dim:
                skipped_version += 1
                continue
            samples.append(sample)

    if skipped_version:
        logger.warning("Skipped %s sample(s) with a mismatched feature version/width.", skipped_version)
    if skipped_label:
        logger.warning("Skipped %s sample(s) with an unknown label.", skipped_label)
    if not samples:
        raise DatasetError("Every sample was rejected - check the feature version and labels.")
    return samples


# --- shaping ------------------------------------------------------------------
def to_matrix(samples: list[GestureSample]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Return (X, y, recording ids). y holds class indexes into GESTURE_CLASSES."""
    features = np.asarray([s.features for s in samples], dtype=np.float32)
    labels = np.asarray([CLASS_INDEX[s.label] for s in samples], dtype=np.int64)
    groups = [s.recording_id for s in samples]
    return features, labels, groups


def class_counts(samples: list[GestureSample]) -> dict[str, int]:
    counts = {name: 0 for name in GESTURE_CLASSES}
    for sample in samples:
        counts[sample.label] += 1
    return counts


def recording_counts(samples: list[GestureSample]) -> dict[str, int]:
    per_label: dict[str, set[str]] = {name: set() for name in GESTURE_CLASSES}
    for sample in samples:
        per_label[sample.label].add(sample.recording_id)
    return {label: len(recordings) for label, recordings in per_label.items()}


def split_by_recording(
    samples: list[GestureSample],
    ratios: tuple[float, float, float] = (0.7, 0.15, 0.15),
    seed: int = 42,
) -> tuple[list[GestureSample], list[GestureSample], list[GestureSample]]:
    """Stratified split whose unit is the *recording*, never the frame.

    Frames inside one recording are near-duplicates of each other. Splitting them
    individually would put almost-identical frames on both sides of the boundary
    and report an accuracy the model has not earned. Every frame of a recording
    therefore lands in exactly one split.
    """
    if abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError("Split ratios must sum to 1.0")

    by_label: dict[str, list[str]] = {}
    for sample in samples:
        by_label.setdefault(sample.label, [])
        if sample.recording_id not in by_label[sample.label]:
            by_label[sample.label].append(sample.recording_id)

    rng = random.Random(seed)
    assignment: dict[str, int] = {}

    for label in sorted(by_label):
        recordings = sorted(by_label[label])
        rng.shuffle(recordings)
        total = len(recordings)
        if total == 1:
            # A single recording cannot be split; training gets it and the class is
            # reported as unevaluated rather than silently leaking into validation.
            assignment[recordings[0]] = 0
            continue
        n_train = max(1, int(round(total * ratios[0])))
        n_val = int(round(total * ratios[1]))
        if total - n_train - n_val < 1 and total >= 3:
            n_val = max(0, total - n_train - 1)
        if n_train + n_val > total:
            n_train, n_val = max(1, total - 1), 0
        for index, recording in enumerate(recordings):
            if index < n_train:
                assignment[recording] = 0
            elif index < n_train + n_val:
                assignment[recording] = 1
            else:
                assignment[recording] = 2

    buckets: tuple[list, list, list] = ([], [], [])
    for sample in samples:
        buckets[assignment.get(sample.recording_id, 0)].append(sample)
    return buckets


def assert_no_leakage(*splits: list[GestureSample]) -> None:
    """Fail loudly if one recording appears in more than one split."""
    seen: dict[str, int] = {}
    for index, split in enumerate(splits):
        for sample in split:
            previous = seen.setdefault(sample.recording_id, index)
            if previous != index:
                raise DatasetError(
                    f"Recording {sample.recording_id} appears in split {previous} and {index}."
                )


def write_manifest(samples: list[GestureSample], root: Path | None = None,
                   version: str = paths.DATASET_VERSION) -> Path:
    """(Re)write the dataset manifest. Datasets are versioned, never overwritten."""
    base = (root or paths.gesture_data_root()) / version
    base.mkdir(parents=True, exist_ok=True)
    manifest = {
        "datasetVersion": version,
        "schemaVersion": SCHEMA_VERSION,
        "featureVersion": FEATURE_VERSION,
        "featureDimension": feature_dimension(True),
        "classes": list(GESTURE_CLASSES),
        "sampleCount": len(samples),
        "recordingCount": len({s.recording_id for s in samples}),
        "subjects": sorted({s.subject_id for s in samples}),
        "samplesPerClass": class_counts(samples),
        "recordingsPerClass": recording_counts(samples),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    target = base / "manifest.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return target


def dataset_summary(root: Path | None = None, version: str = paths.DATASET_VERSION,
                    subject: str | None = None) -> dict:
    """Cheap summary for the UI - never raises when the dataset is empty."""
    try:
        samples = load_dataset(root, version, subject, strict=False)
    except DatasetError:
        return {
            "datasetVersion": version,
            "sampleCount": 0,
            "recordingCount": 0,
            "samplesPerClass": {name: 0 for name in GESTURE_CLASSES},
            "recordingsPerClass": {name: 0 for name in GESTURE_CLASSES},
            "classes": list(GESTURE_CLASSES),
        }
    return {
        "datasetVersion": version,
        "sampleCount": len(samples),
        "recordingCount": len({s.recording_id for s in samples}),
        "samplesPerClass": class_counts(samples),
        "recordingsPerClass": recording_counts(samples),
        "classes": list(GESTURE_CLASSES),
    }
