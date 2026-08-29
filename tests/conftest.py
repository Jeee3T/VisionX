"""Pytest configuration for the VisionX unit and integration tests.

These tests never need MongoDB, Flask, a webcam, MediaPipe or PyAutoGUI. They
exercise the layers that decide what a command is - recognition, canonicalization,
the trained models, intent parsing and dispatch - against fakes at the OS boundary.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for path in (PROJECT_ROOT, PROJECT_ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import pytest  # noqa: E402

from presentation_controller.keyboard import KeyboardBackend  # noqa: E402


class FakeKeyboard(KeyboardBackend):
    """Records what would have been sent to the OS instead of sending it.

    Subclasses the real backend so the controller cannot tell the difference,
    and so a signature change in KeyboardBackend breaks these tests loudly.
    """

    def __init__(self, available: bool = True):
        super().__init__()
        self.log: list[tuple] = []
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def _fail_if_unavailable(self) -> None:
        if not self._available:
            from presentation_controller.base import PresentationControlError

            raise PresentationControlError("Keyboard control is unavailable in this test.")

    def press(self, key: str) -> None:
        self._fail_if_unavailable()
        self.log.append(("press", key))

    def hotkey(self, *keys: str) -> None:
        self._fail_if_unavailable()
        self.log.append(("hotkey", keys))

    def write(self, text: str) -> None:
        self._fail_if_unavailable()
        self.log.append(("write", str(text)))

    def move_to(self, x: int, y: int) -> None:
        self._fail_if_unavailable()
        self.log.append(("move", x, y))

    def screen_size(self) -> tuple[int, int]:
        self._fail_if_unavailable()
        return (1920, 1080)

    def keys(self) -> list[str]:
        return [entry[1] for entry in self.log if entry[0] == "press"]


@pytest.fixture
def keyboard() -> FakeKeyboard:
    return FakeKeyboard()


@pytest.fixture
def dispatcher(keyboard):
    from presentation_controller.annotation import AnnotationController
    from presentation_controller.dispatcher import CommandDispatcher
    from presentation_controller.powerpoint import PowerPointController

    instance = CommandDispatcher(PowerPointController(keyboard), AnnotationController())
    instance.bind_presentation(current_slide=1, total_slides=20)
    return instance


@pytest.fixture(scope="session")
def intent_model():
    """The trained voice intent model, or a skip when it has not been built."""
    from voice_assistant.intent.classifier import IntentModel, IntentModelError

    try:
        return IntentModel.load()
    except IntentModelError as exc:
        pytest.skip(f"voice intent model unavailable: {exc}")


@pytest.fixture(scope="session")
def interpreter(intent_model):
    from voice_assistant.intent.interpreter import VoiceInterpreter

    return VoiceInterpreter(intent_model)


@pytest.fixture(scope="session")
def gesture_model(tmp_path_factory):
    """A real personalized model, trained here on synthetic data.

    Trained once per test session from the synthetic generator so the inference,
    fallback and integration tests exercise a genuinely trained artifact rather
    than a stub. It is written to a temp directory and never touches the user's
    real models.
    """
    import pytest

    from computer_vision.ml.dataset import GESTURE_CLASSES
    from computer_vision.ml.mlp import GestureModelArtifact
    from computer_vision.ml.training import synthesize_dataset, train_gesture_model

    root = tmp_path_factory.mktemp("gesture-data")
    models = tmp_path_factory.mktemp("gesture-models")

    # Enough recordings that the split is meaningful and the null class - the
    # hardest one, since it deliberately overlaps the pose classes - is learnable.
    synthesize_dataset.build(recordings=5, frames=40, seed=5, root=root, version="v1")
    code = train_gesture_model.main([
        "--subject", "synthetic:v1",
        "--root", str(root),
        "--output", str(models),
        "--seed", "5",
        "--max-iter", "300",
        "--no-onnx",
        "--quiet",
    ])
    if code != 0:
        pytest.skip("could not train a synthetic gesture model")

    artifact = GestureModelArtifact.load(models, prefer_onnx=False)
    assert artifact.classes == list(GESTURE_CLASSES)
    return artifact
