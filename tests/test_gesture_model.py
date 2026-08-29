"""The personalized gesture model: dataset hygiene, inference, and fallback.

Nothing here asserts a real-world accuracy number. The model under test is
trained on synthetic landmarks (see computer_vision/ml/synthetic.py); what is
being verified is that the pipeline is correct, not that VisionX recognises
real hands well.
"""

import json
import random

import numpy as np
import pytest

from computer_vision.gesture_recognition.gesture_recognizer import GestureRecognizer
from computer_vision.gesture_recognition.poses import POSE_LIBRARY, UNKNOWN
from computer_vision.gesture_recognition.recognizer_factory import (
    REASON_DISABLED,
    REASON_LOAD_FAILED,
    REASON_NO_MODEL,
    build_recognizer,
)
from computer_vision.hand_detection.hand_detector import HandLandmarks
from computer_vision.ml import registry
from computer_vision.ml.canonicalization import canonical_features
from computer_vision.ml.collector import CaptureError, GestureSampleCollector
from computer_vision.ml.dataset import (
    GESTURE_CLASSES,
    NULL_CLASS,
    DatasetError,
    GestureSample,
    QualityConfig,
    assert_no_leakage,
    assess_frame,
    split_by_recording,
)
from computer_vision.ml.intent_gate import REJECT_AMBIGUOUS, REJECT_NULL_CLASS, GestureIntentGate
from computer_vision.ml.mlp import GestureModelArtifact, ModelLoadError
from computer_vision.ml.personalized_recognizer import PersonalizedRecognizer
from computer_vision.ml.synthetic import generate_recording


def _hand(label: str, rng: random.Random, aspect: float = 4 / 3, score: float = 0.95):
    frame = generate_recording(label, frames=1, rng=rng, aspect=aspect)[0]
    return HandLandmarks(points=frame, handedness="Right", detection_score=score)


# --- classes ------------------------------------------------------------------
def test_classes_come_from_the_repository_pose_library():
    """The class list is derived, never hard-coded - adding a pose adds a class."""
    assert list(GESTURE_CLASSES[:-1]) == [pose.name for pose in POSE_LIBRARY]
    assert GESTURE_CLASSES[-1] == NULL_CLASS == UNKNOWN


# --- dataset ------------------------------------------------------------------
def _sample(label: str, recording: str) -> GestureSample:
    return GestureSample(
        label=label, features=[0.0] * 86, recording_id=recording, subject_id="test",
    )


def test_split_never_puts_one_recording_in_two_splits():
    samples = [
        _sample(label, f"{label}-r{index}")
        for label in GESTURE_CLASSES
        for index in range(6)
        for _ in range(10)
    ]
    train, validation, test = split_by_recording(samples, seed=1)
    assert_no_leakage(train, validation, test)
    assert train and validation and test

    ids = [{s.recording_id for s in split} for split in (train, validation, test)]
    assert not ids[0] & ids[1] and not ids[0] & ids[2] and not ids[1] & ids[2]


def test_safe_component_never_escapes_its_parent_directory():
    """A user id must always name a directory *inside* the model root.

    "." and ".." pass a plain character filter but still traverse, and
    registry.delete_model() rmtree()s whatever the path resolves to.
    """
    from computer_vision.ml import paths

    root = paths.user_model_root().resolve()
    for hostile in ("..", ".", "...", "  ..  ", "../../../etc/passwd",
                    "..\\..\\windows", "/etc/shadow", "", "   ", "a/../../b"):
        resolved = paths.user_model_dir(hostile).resolve()
        assert resolved.parent == root, f"{hostile!r} escaped to {resolved}"
        assert resolved != root, f"{hostile!r} resolved to the root itself"


def test_assert_no_leakage_actually_catches_a_leaking_split():
    """The guard must fail on a bad split, or the test above proves nothing.

    Without this, assert_no_leakage() could be an empty function and
    test_split_never_puts_one_recording_in_two_splits would still pass.
    """
    shared = _sample("FIST", "rec-shared")
    train = [shared, _sample("FIST", "rec-train")]
    validation = [_sample("FIST", "rec-val")]
    # The very same recording id also appears in the test split.
    test = [_sample("FIST", "rec-shared")]

    with pytest.raises(DatasetError, match="rec-shared"):
        assert_no_leakage(train, validation, test)


def test_assert_no_leakage_catches_a_leak_between_any_two_splits():
    a = [_sample("FIST", "r1")]
    b = [_sample("FIST", "r2")]
    c = [_sample("FIST", "r3")]
    for first, second in ((0, 1), (0, 2), (1, 2)):
        splits = [list(a), list(b), list(c)]
        splits[second].append(_sample("FIST", splits[first][0].recording_id))
        with pytest.raises(DatasetError):
            assert_no_leakage(*splits)


def test_assert_no_leakage_accepts_a_genuinely_disjoint_split():
    assert_no_leakage(
        [_sample("FIST", "r1")], [_sample("FIST", "r2")], [_sample("FIST", "r3")]
    )


def test_no_individual_frame_leaks_across_splits():
    """Frame level, not just recording level: every frame of a recording moves together."""
    samples = [
        GestureSample(
            label=label, features=[0.0] * 86,
            recording_id=f"{label}-r{index}", subject_id="test",
            sample_id=f"{label}-r{index}#{frame}",
        )
        for label in GESTURE_CLASSES
        for index in range(6)
        for frame in range(10)
    ]
    parts = split_by_recording(samples, seed=3)

    # Every frame appears exactly once across all three splits.
    all_ids = [s.sample_id for part in parts for s in part]
    assert len(all_ids) == len(samples)
    assert len(set(all_ids)) == len(samples)

    # And each recording's frames are wholly contained in one split.
    location: dict[str, int] = {}
    for index, part in enumerate(parts):
        for sample in part:
            assert location.setdefault(sample.recording_id, index) == index


def test_split_is_deterministic_for_a_seed():
    samples = [_sample("FIST", f"r{i}") for i in range(10) for _ in range(5)]
    first = [{s.recording_id for s in part} for part in split_by_recording(samples, seed=7)]
    second = [{s.recording_id for s in part} for part in split_by_recording(samples, seed=7)]
    assert first == second


def test_quality_gate_rejects_bad_frames():
    rng = random.Random(3)
    hand = _hand("FIST", rng)
    config = QualityConfig()

    ok, _, features = assess_frame(hand.points, 0.95, 120.0, config=config)
    assert ok and features is not None

    assert assess_frame(hand.points, 0.10, 120.0, config=config)[0] is False   # bad detection
    assert assess_frame(hand.points, 0.95, 5.0, config=config)[0] is False     # too dark
    # An identical consecutive frame carries no new information.
    duplicate_ok, reason, _ = assess_frame(hand.points, 0.95, 120.0, previous_features=features,
                                           config=config)
    assert duplicate_ok is False and "duplicate" in reason


def test_quality_gate_rejects_a_hand_that_is_too_far_away():
    tiny = np.full((21, 3), 0.5, dtype=np.float32)
    tiny[:, 0] += np.linspace(0, 0.01, 21)
    ok, reason, _ = assess_frame(tiny, 0.95, 120.0)
    assert ok is False and "far" in reason


# --- collector ----------------------------------------------------------------
def test_collector_gathers_frames_and_reports_progress():
    collector = GestureSampleCollector()
    collector.start("FIST", "user:test", target_frames=20)
    rng = random.Random(11)

    for _ in range(60):
        collector.offer(_hand("FIST", rng), 4 / 3, 120.0)

    status = collector.status()
    assert status["complete"] and status["accepted"] == 20
    samples = collector.take()
    assert len(samples) == 20
    assert all(s.label == "FIST" for s in samples)
    assert len({s.recording_id for s in samples}) == 1
    assert collector.status() is None


def test_collector_rejects_an_unknown_label():
    with pytest.raises(CaptureError):
        GestureSampleCollector().start("NOT_A_POSE", "user:test")


def test_collector_counts_frames_with_no_hand_as_rejected():
    collector = GestureSampleCollector()
    collector.start("FIST", "user:test", target_frames=20)
    state = collector.offer(None, 4 / 3, 120.0)
    assert state.accepted == 0 and state.rejected == 1


# --- artifact -----------------------------------------------------------------
def test_model_predicts_the_classes_it_was_trained_on(gesture_model):
    rng = random.Random(21)
    correct = 0
    total = 0
    for pose in POSE_LIBRARY:
        for _ in range(5):
            features = canonical_features(_hand(pose.name, rng).points, 4 / 3)
            label, probability, distribution = gesture_model.predict(features)
            assert 0.0 <= probability <= 1.0
            assert abs(sum(distribution.values()) - 1.0) < 1e-3
            correct += int(label == pose.name)
            total += 1
    # A synthetic smoke test: the point is that the pipeline works end to end.
    assert correct / total > 0.8


def test_model_predicts_the_null_class_for_non_command_hands(gesture_model):
    rng = random.Random(33)
    predictions = [
        gesture_model.predict(canonical_features(_hand(NULL_CLASS, rng).points, 4 / 3))[0]
        for _ in range(30)
    ]
    assert predictions.count(NULL_CLASS) / len(predictions) > 0.6


def test_model_round_trips_through_disk(gesture_model, tmp_path):
    probe = np.random.default_rng(2).normal(size=(4, gesture_model.feature_dimension)).astype("float32")
    before = gesture_model.predict_proba(probe)

    gesture_model.save(tmp_path)
    reloaded = GestureModelArtifact.load(tmp_path, prefer_onnx=False)
    assert np.allclose(reloaded.predict_proba(probe), before, atol=1e-6)
    assert reloaded.classes == gesture_model.classes


def test_model_rejects_the_wrong_feature_width(gesture_model):
    with pytest.raises(ValueError):
        gesture_model.predict_proba(np.zeros((1, 10), dtype="float32"))


def test_corrupt_model_raises_rather_than_returning_nonsense(tmp_path):
    (tmp_path / "gesture_model.npz").write_bytes(b"this is not an npz archive")
    with pytest.raises(ModelLoadError):
        GestureModelArtifact.load(tmp_path)


def test_model_with_a_mismatched_feature_version_is_refused(gesture_model, tmp_path):
    gesture_model.save(tmp_path)
    metadata = json.loads((tmp_path / "gesture_model.metadata.json").read_text())
    metadata["featureVersion"] = "gesture-canonical-v0-from-the-past"
    (tmp_path / "gesture_model.metadata.json").write_text(json.dumps(metadata))

    with pytest.raises(ModelLoadError, match="feature version"):
        GestureModelArtifact.load(tmp_path)


# --- personalized recognizer --------------------------------------------------
def test_personalized_recognizer_matches_the_geometric_interface(gesture_model):
    recognizer = PersonalizedRecognizer(gesture_model)
    rng = random.Random(41)
    hand = _hand("PINKY_UP", rng)

    result = recognizer.recognize(hand, 4 / 3)
    reference = GestureRecognizer().recognize(hand, 4 / 3)

    assert set(reference.as_dict()).issubset(set(result.as_dict()))
    assert result.hand_detected and result.pointer is not None
    assert result.source == "personalized"
    assert result.model_version == gesture_model.model_version
    assert result.probabilities and result.margin is not None


def test_personalized_recognizer_handles_no_hand(gesture_model):
    result = PersonalizedRecognizer(gesture_model).recognize(None, 4 / 3)
    assert result.gesture == "NONE" and result.hand_detected is False


def test_personalized_recognizer_degrades_instead_of_raising(gesture_model):
    """An exploding model must fall back, not take the camera loop down."""

    class Exploding:
        classes = gesture_model.classes
        metadata = {"modelVersion": "broken"}
        runtime = "numpy"
        model_version = "broken"
        feature_dimension = gesture_model.feature_dimension

        def predict_proba(self, _features):
            raise RuntimeError("inference exploded")

    recognizer = PersonalizedRecognizer(Exploding(), fallback=GestureRecognizer())
    rng = random.Random(53)
    for _ in range(15):
        result = recognizer.recognize(_hand("FIST", rng), 4 / 3)
        assert result.gesture in GESTURE_CLASSES

    assert recognizer.degraded is True
    assert recognizer.source == "geometric"


def test_detection_score_still_gates_a_confident_classification(gesture_model):
    recognizer = PersonalizedRecognizer(gesture_model)
    rng = random.Random(61)
    frame = generate_recording("PINKY_UP", frames=1, rng=rng)[0]

    confident = recognizer.recognize(HandLandmarks(frame, "Right", 0.99), 4 / 3)
    doubtful = recognizer.recognize(HandLandmarks(frame, "Right", 0.20), 4 / 3)
    assert doubtful.confidence < confident.confidence


# --- intent gate --------------------------------------------------------------
def test_intent_gate_neutralises_an_ambiguous_frame(gesture_model):
    from computer_vision.gesture_recognition.gesture_recognizer import GestureResult

    gate = GestureIntentGate(min_margin=0.15)
    ambiguous = GestureResult(
        gesture="INDEX_UP", confidence=0.51, hand_detected=True, margin=0.04,
        probabilities={"INDEX_UP": 0.51, "INDEX_MIDDLE_UP": 0.47},
    )
    result, reason = gate.apply(ambiguous)
    assert reason == REJECT_AMBIGUOUS
    assert result.gesture == UNKNOWN   # the neutral state the debouncer already handles

    clear = GestureResult(gesture="INDEX_UP", confidence=0.95, hand_detected=True, margin=0.9)
    assert gate.apply(clear) == (clear, None)


def test_intent_gate_is_inert_without_probabilities():
    """The geometric recognizer reports no margin, so the gate must not interfere."""
    gate = GestureIntentGate(min_margin=0.15)
    rng = random.Random(71)
    result = GestureRecognizer().recognize(_hand("FIST", rng), 4 / 3)
    gated, reason = gate.apply(result)
    assert gated is result and reason in (None, REJECT_NULL_CLASS)


# --- fallback selection -------------------------------------------------------
def test_factory_falls_back_when_personalization_is_off():
    recognizer, info = build_recognizer("some-user", personalization_enabled=False)
    assert isinstance(recognizer, GestureRecognizer)
    assert info["reason"] == REASON_DISABLED and info["personalized"] is False


def test_factory_falls_back_when_the_user_has_no_model(monkeypatch, tmp_path):
    monkeypatch.setenv("VISIONX_USER_MODEL_DIR", str(tmp_path))
    registry.invalidate()
    recognizer, info = build_recognizer("nobody-has-this-id", personalization_enabled=True)
    assert isinstance(recognizer, GestureRecognizer)
    assert info["reason"] == REASON_NO_MODEL


def test_factory_falls_back_when_the_model_is_corrupt(monkeypatch, tmp_path):
    monkeypatch.setenv("VISIONX_USER_MODEL_DIR", str(tmp_path))
    registry.invalidate()
    user = "corrupt-user"
    directory = tmp_path / user
    directory.mkdir(parents=True)
    (directory / "gesture_model.npz").write_bytes(b"garbage")

    recognizer, info = build_recognizer(user, personalization_enabled=True)
    assert isinstance(recognizer, GestureRecognizer)
    assert info["reason"] == REASON_LOAD_FAILED
    assert info["error"]
    registry.invalidate()


def test_factory_uses_the_model_when_one_exists(monkeypatch, tmp_path, gesture_model):
    monkeypatch.setenv("VISIONX_USER_MODEL_DIR", str(tmp_path))
    registry.invalidate()
    user = "real-user"
    gesture_model.save(tmp_path / user)

    recognizer, info = build_recognizer(user, personalization_enabled=True)
    assert isinstance(recognizer, PersonalizedRecognizer)
    assert info["personalized"] is True
    assert info["modelVersion"] == gesture_model.model_version

    assert registry.delete_model(user) is True
    registry.invalidate()
