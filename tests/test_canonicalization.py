"""Landmark canonicalization: the invariances the whole model depends on.

If these fail, every trained model is invalid - the features it learned no longer
mean what they meant at training time.
"""

import numpy as np
import pytest

from computer_vision.ml.canonicalization import (
    BASE_DIMENSION,
    FEATURE_VERSION,
    FULL_DIMENSION,
    canonical_features,
    canonicalize,
    describe,
    feature_dimension,
)
from computer_vision.hand_detection.hand_detector import MIDDLE_MCP, WRIST
from computer_vision.ml.synthetic import build_landmarks, project_to_frame, HandPose


@pytest.fixture
def hand():
    return project_to_frame(build_landmarks(HandPose(0.0, 0.0, 1.0, 1.0, 1.0)), aspect=1.0)


def test_feature_dimensions():
    assert feature_dimension(False) == BASE_DIMENSION == 63
    assert feature_dimension(True) == FULL_DIMENSION == 86
    assert describe()["featureVersion"] == FEATURE_VERSION


def test_translation_invariance(hand):
    reference = canonical_features(hand, aspect=1.0)
    for offset in ([0.30, -0.20, 0.10], [-0.15, 0.25, -0.05]):
        moved = hand + np.asarray(offset, dtype=np.float32)
        assert np.allclose(canonical_features(moved, aspect=1.0), reference, atol=1e-5)


def test_scale_invariance(hand):
    reference = canonical_features(hand, aspect=1.0)
    for factor in (0.4, 0.75, 1.5, 2.5):
        # Scale about the wrist so the hand only changes size, not position.
        scaled = (hand - hand[WRIST]) * factor + hand[WRIST]
        assert np.allclose(canonical_features(scaled, aspect=1.0), reference, atol=1e-4)


@pytest.mark.parametrize("degrees", [-45, -20, -5, 5, 20, 45, 90])
def test_in_plane_rotation_is_normalized(hand, degrees):
    reference = canonical_features(hand, aspect=1.0)
    theta = np.deg2rad(degrees)
    rotation = np.asarray(
        [[np.cos(theta), -np.sin(theta), 0], [np.sin(theta), np.cos(theta), 0], [0, 0, 1]],
        dtype=np.float32,
    )
    rotated = (hand - hand[WRIST]) @ rotation.T + hand[WRIST]
    assert np.allclose(canonical_features(rotated, aspect=1.0), reference, atol=1e-4)


def test_canonical_frame_is_exactly_defined(hand):
    """The wrist sits at the origin and the middle knuckle at (0, 1)."""
    canonical = canonicalize(hand, aspect=1.0)
    assert np.allclose(canonical[WRIST], [0.0, 0.0, 0.0], atol=1e-5)
    assert np.allclose(canonical[MIDDLE_MCP][:2], [0.0, 1.0], atol=1e-4)


def test_aspect_correction_changes_the_result(hand):
    """Aspect is a real input, not decoration: 4:3 and 1:1 must differ."""
    square = canonical_features(hand, aspect=1.0)
    wide = canonical_features(hand, aspect=4 / 3)
    assert not np.allclose(square, wide, atol=1e-3)


def test_different_poses_produce_different_features():
    fist = canonical_features(project_to_frame(build_landmarks(HandPose(1, 1, 1, 1, 1))))
    palm = canonical_features(project_to_frame(build_landmarks(HandPose(0, 0, 0, 0, 0))))
    assert np.abs(fist - palm).max() > 0.1


def test_rejects_malformed_input():
    with pytest.raises(ValueError):
        canonical_features(np.zeros((5, 3)))
    with pytest.raises(ValueError):
        canonical_features(np.full((21, 3), np.nan))


def test_accepts_flat_and_two_dimensional_input(hand):
    reference = canonical_features(hand, aspect=1.0)
    assert np.allclose(canonical_features(hand.reshape(-1), aspect=1.0), reference)
    assert canonical_features(hand[:, :2], aspect=1.0).shape == (FULL_DIMENSION,)
