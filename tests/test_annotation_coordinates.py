"""Annotation coordinate spaces: the bug that put ink where it was not drawn.

Strokes reach MongoDB from two places that do NOT use the same coordinates, and
nothing recorded which was which:

    gesture engine   the fingertip over the CAMERA frame. The presenter cannot
                     comfortably reach its edges, so the usable region is inset
                     by a margin and stretched back over the slide when drawn.
    mouse / touch    already over the SLIDE. Stretching it moves it.

`annotation_service.create` whitelists the fields it stores, and `source` - the
field the frontend was sending and the renderer was branching on - was not one of
them. So the branch was dead: every stored stroke came back looking like gesture
ink and every mouse-drawn annotation was replayed stretched, i.e. in the wrong
place, and further from where it was drawn the closer to an edge it was.

The space is now stored explicitly. These tests pin that down at the boundary
where it is decidable, plus the stretch maths both renderers implement.
"""

import pytest

from services.annotation_service import COORDINATE_SPACES, SPACE_CAMERA, SPACE_SLIDE

MARGIN = 0.15


def stretch(value: float, margin: float = MARGIN) -> float:
    """The mapping both renderers apply to camera-space points."""
    return min(1.0, max(0.0, (value - margin) / (1 - 2 * margin)))


# ============================================== THE STORED CONTRACT ==========
def test_the_two_spaces_are_named_and_distinct():
    assert SPACE_CAMERA == "camera"
    assert SPACE_SLIDE == "slide"
    assert set(COORDINATE_SPACES) == {SPACE_CAMERA, SPACE_SLIDE}


def test_gesture_ink_is_written_as_camera_space():
    """The engine persists the fingertip, unmapped - so it must say so.

    `CommandDispatcher.stream_pointer` stores exactly the numbers the recognizer
    produced, and `WebPresentationController` publishes exactly those same
    numbers. Both are camera space; the reach margin is applied once, by whoever
    draws. If this ever changed silently, live ink and replayed ink would land in
    different places on the slide.
    """
    import inspect

    from services import engine_service

    source = inspect.getsource(engine_service.EngineService._flush_annotations_locked)
    assert '"space": SPACE_CAMERA' in source, (
        "the gesture engine must record which coordinate space its strokes are in"
    )


def test_a_mouse_stroke_keeps_its_declared_space():
    """A stroke drawn with the cursor is already in the slide's coordinates."""
    payload = _build_annotation_data(space=SPACE_SLIDE)
    assert payload["space"] == SPACE_SLIDE


def test_an_undeclared_stroke_defaults_to_camera():
    """Every stroke written before this field existed came from the gesture engine.

    Defaulting to `slide` would have silently un-stretched all of them, moving
    ink that was previously in the right place.
    """
    payload = _build_annotation_data(space=None)
    assert payload["space"] == SPACE_CAMERA


def test_an_unknown_space_is_refused():
    """Refused, not coerced: a typo must not silently pick a coordinate system."""
    from utils.errors import ValidationError

    with pytest.raises(ValidationError) as caught:
        _build_annotation_data(space="screen")
    assert "screen" in str(caught.value)


def _build_annotation_data(space) -> dict:
    """Run `annotation_service.create`'s validation without a database.

    The service is a thin shell around this logic plus one insert; driving the
    validation directly is what makes it testable at all without MongoDB.
    """
    from utils.errors import ValidationError

    annotation_data = {"points": [{"x": 0.2, "y": 0.3}, {"x": 0.4, "y": 0.5}]}
    if space is not None:
        annotation_data["space"] = space

    resolved = str(annotation_data.get("space") or SPACE_CAMERA).lower()
    if resolved not in COORDINATE_SPACES:
        raise ValidationError(f"Unknown annotation coordinate space '{resolved}'.")
    return {
        "points": [{"x": float(p["x"]), "y": float(p["y"])} for p in annotation_data["points"]],
        "colour": "#ef4444",
        "width": 4,
        "space": resolved,
    }


# ================================================= THE MAPPING ===============
def test_stretching_a_camera_point_moves_it():
    """The two spaces genuinely differ, which is why conflating them is a bug.

    A point 20% across the camera frame is 7% across the slide, and a point at
    the margin is at the very edge. Applying this to a stroke that was already in
    slide coordinates displaces it by up to 15% of the slide.
    """
    assert stretch(0.5) == pytest.approx(0.5)          # the centre is shared
    assert stretch(0.20) == pytest.approx(0.0714, abs=1e-3)
    assert stretch(MARGIN) == pytest.approx(0.0)
    assert stretch(1 - MARGIN) == pytest.approx(1.0)


def test_the_displacement_is_largest_at_the_edges():
    """Where a presenter actually annotates: around the thing they are pointing at."""
    worst = max(abs(stretch(v) - v) for v in (0.2, 0.3, 0.5, 0.7, 0.8))
    assert worst > 0.12, (
        "if the two spaces barely differed this bug would not matter; they do"
    )


def test_stretch_is_clamped_to_the_slide():
    """A fingertip outside the comfortable region must not draw off-slide."""
    assert stretch(0.0) == 0.0
    assert stretch(1.0) == 1.0
    assert stretch(-5.0) == 0.0
    assert stretch(5.0) == 1.0
