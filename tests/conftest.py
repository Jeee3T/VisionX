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

    def mouse_down(self, button: str = "left") -> None:
        self._fail_if_unavailable()
        if self._mouse_down:
            return
        self._mouse_down = True
        self.log.append(("mouseDown", button))

    def mouse_up(self, button: str = "left") -> None:
        if not self._mouse_down:
            return
        self._mouse_down = False
        self._fail_if_unavailable()
        self.log.append(("mouseUp", button))

    def screen_size(self) -> tuple[int, int]:
        self._fail_if_unavailable()
        return (1920, 1080)

    def keys(self) -> list[str]:
        return [entry[1] for entry in self.log if entry[0] == "press"]

    def hotkeys(self) -> list[tuple]:
        return [entry[1] for entry in self.log if entry[0] == "hotkey"]


class FakeCom:
    """Stands in for PowerPointComBridge with a scriptable slideshow state.

    Three configurations matter, and each corresponds to a real machine:

        connected + running    Windows, PowerPoint presenting  -> CONFIRMED
        connected + not running Windows, deck open but not presenting -> DENIED
        not connected          no COM binding / not Windows     -> UNKNOWN
    """

    def __init__(self, connected: bool = False, slideshow: bool = False,
                 slides: int = 20):
        self.connected = connected
        self.slideshow = slideshow
        self.slides = slides
        self.pointer_type_value: int | None = None
        self.erased = 0
        self.calls: list[tuple] = []

    # --- the PowerPointComBridge interface -----------------------------------
    def probe(self) -> str:
        from presentation_controller.windows import (
            SLIDESHOW_CONFIRMED,
            SLIDESHOW_DENIED,
            SLIDESHOW_UNKNOWN,
        )

        if not self.connected:
            return SLIDESHOW_UNKNOWN
        return SLIDESHOW_CONFIRMED if self.slideshow else SLIDESHOW_DENIED

    def invalidate(self) -> None:
        self.calls.append(("invalidate",))

    def _live(self) -> bool:
        return self.connected and self.slideshow

    def set_pointer_type(self, pointer_type: int) -> bool:
        self.calls.append(("pointerType", pointer_type))
        if not self._live():
            return False
        self.pointer_type_value = int(pointer_type)
        return True

    def pointer_type(self) -> int | None:
        return self.pointer_type_value if self._live() else None

    def erase_ink(self) -> bool:
        self.calls.append(("erase",))
        if not self._live():
            return False
        self.erased += 1
        return True

    def next_slide(self) -> bool:
        self.calls.append(("next",))
        return self._live()

    def previous_slide(self) -> bool:
        self.calls.append(("previous",))
        return self._live()

    def goto_slide(self, number: int) -> bool:
        self.calls.append(("goto", int(number)))
        return self._live()

    def slide_count(self) -> int | None:
        return self.slides if self.connected else None

    def activate(self) -> bool:
        self.calls.append(("activate",))
        return self._live()

    def describe(self) -> dict:
        return {"fake": True, "connected": self.connected, "slideshow": self.slideshow}


@pytest.fixture
def keyboard() -> FakeKeyboard:
    return FakeKeyboard()


@pytest.fixture
def com() -> FakeCom:
    """No COM binding, i.e. the UNKNOWN case - what the dev machines and CI see."""
    return FakeCom(connected=False)


def build_dispatcher(keyboard, com=None, total_slides: int = 20):
    from presentation_controller.annotation import AnnotationController
    from presentation_controller.dispatcher import CommandDispatcher
    from presentation_controller.powerpoint import PowerPointController

    controller = PowerPointController(keyboard, com=com if com is not None else FakeCom())
    instance = CommandDispatcher(controller, AnnotationController())
    instance.bind_presentation(current_slide=1, total_slides=total_slides)
    return instance


@pytest.fixture
def dispatcher(keyboard, com):
    return build_dispatcher(keyboard, com)


@pytest.fixture
def presenting():
    """Windows with PowerPoint actually presenting - the real deployment."""
    return FakeCom(connected=True, slideshow=True)


@pytest.fixture
def not_presenting():
    """Windows with PowerPoint open but NOT presenting.

    This is the configuration in which Ctrl+P means Print, and therefore the one
    every pen test has to cover.
    """
    return FakeCom(connected=True, slideshow=False)


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
