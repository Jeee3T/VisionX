"""Regression tests: the web presentation mode does not depend on PowerPoint.

VisionX *is* the presentation engine. During a presentation, Microsoft PowerPoint
must not be opened, controlled, focused, or required. That is a property of the
system, not of any one function, so asserting it needs tests that watch the whole
process rather than a return value:

  * the **import graph** reachable from the web controller,
  * `sys.modules` after a complete web session has run,
  * every OS-automation entry point, poisoned so that touching it fails loudly,
  * `subprocess`, so nothing launches an application behind our back.

These are deliberately paranoid. A dependency on PowerPoint is easy to
reintroduce by accident - one convenience import at the top of a module is
enough - and the symptom in production is not a test failure but a Print dialog
in front of an audience.
"""

import subprocess
import sys

import pytest

from computer_vision.command_mapping.gesture_mapper import (
    ANNOTATION_MODE,
    BLACKOUT,
    CLEAR_ANNOTATION,
    END_PRESENTATION,
    FIRST_SLIDE,
    GO_TO_SLIDE,
    LAST_SLIDE,
    NEXT_SLIDE,
    PREVIOUS_SLIDE,
    START_PRESENTATION,
    VIRTUAL_POINTER,
    WHITEOUT,
)
from presentation_controller.annotation import AnnotationController
from presentation_controller.dispatcher import CommandDispatcher
from presentation_controller.web import WebPresentationController

# Modules that mean "PowerPoint, COM, or OS input automation is in play".
FORBIDDEN_MODULES = (
    "comtypes",
    "win32com",
    "pythoncom",
    "pyautogui",
    "pyscreeze",
    "pygetwindow",
    "presentation_controller.windows",
    "presentation_controller.powerpoint",
    "presentation_controller.keyboard",
    "presentation_controller.pointer",
)


def offending(modules) -> list[str]:
    """Which of `modules` are forbidden. Submodules count (`comtypes.client`)."""
    return sorted(
        name for name in modules
        if any(name == bad or name.startswith(bad + ".") for bad in FORBIDDEN_MODULES)
    )


# ============================================ 1. THE IMPORT GRAPH ============
def test_the_web_controller_module_imports_nothing_powerpoint():
    """`import presentation_controller.web` in a clean interpreter.

    A subprocess, not this one: the test suite has already imported the
    PowerPoint controller for its own tests, so `sys.modules` here proves
    nothing. This is the only way to ask the question honestly.
    """
    code = (
        "import sys, json;"
        "import presentation_controller.web;"
        "print(json.dumps(sorted(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, check=True, cwd=_repo_root(),
    )
    import json

    loaded = json.loads(result.stdout)
    assert offending(loaded) == [], (
        "importing the web presentation controller pulled in PowerPoint / OS "
        f"automation modules: {offending(loaded)}"
    )


def test_a_whole_web_session_imports_nothing_powerpoint():
    """The stronger version: build a session and run every command in a subprocess.

    Not just the import - the actual dispatch path a presenter drives. If any
    command reaches for COM or PyAutoGUI lazily, it shows up here and nowhere
    else.
    """
    code = """
import json, sys
from presentation_controller.annotation import AnnotationController
from presentation_controller.dispatcher import CommandDispatcher
from presentation_controller.web import WebPresentationController

controller = WebPresentationController()
dispatcher = CommandDispatcher(controller, AnnotationController())
dispatcher.bind_presentation(current_slide=1, total_slides=20)

for command, parameters in [
    ("START_PRESENTATION", {}), ("NEXT_SLIDE", {}), ("PREVIOUS_SLIDE", {}),
    ("GO_TO_SLIDE", {"slideNumber": 7}), ("FIRST_SLIDE", {}), ("LAST_SLIDE", {}),
    ("VIRTUAL_POINTER", {}), ("ANNOTATION_MODE", {}), ("CLEAR_ANNOTATION", {}),
    ("BLACKOUT", {}), ("WHITEOUT", {}), ("END_PRESENTATION", {}),
]:
    dispatcher.execute(command, {"parameters": parameters, "source": "gesture"})

dispatcher.execute("ANNOTATION_MODE", {"parameters": {"state": True}, "source": "voice"})
for step in range(50):
    dispatcher.stream_pointer(0.3 + step * 0.01, 0.5)
dispatcher.end_stroke()

print(json.dumps(sorted(sys.modules)))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=_repo_root(),
    )
    assert result.returncode == 0, result.stderr
    import json

    loaded = json.loads(result.stdout.strip().splitlines()[-1])
    assert offending(loaded) == [], (
        "running a full web presentation session loaded PowerPoint / OS automation "
        f"modules: {offending(loaded)}"
    )


def _repo_root() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[1])


def test_the_whole_backend_in_web_mode_imports_nothing_powerpoint():
    """The strongest form: boot the actual Flask app and look at `sys.modules`.

    Not just the controller in isolation - the whole API, every blueprint, every
    service, the health endpoint. A single convenience import anywhere in the
    backend would show up here.

    `VISIONX_PRESENTATION_MODE=web` is the default, and is set explicitly so the
    test states its own premise rather than inheriting the developer's `.env`.
    """
    import json
    import os

    code = """
import json, sys
sys.path[:0] = ['backend', '.']
import app as flask_app
flask_app.app.test_client().get('/api/health')
print(json.dumps(sorted(sys.modules)))
"""
    environment = {
        **os.environ,
        "VISIONX_PRESENTATION_MODE": "web",
        # The prewarm thread and the database are irrelevant here and only add
        # time and noise; the app is designed to start without either.
        "VOICE_PREWARM": "0",
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=_repo_root(), env=environment, timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]

    loaded = json.loads(result.stdout.strip().splitlines()[-1])
    assert offending(loaded) == [], (
        "the VisionX backend in web mode loaded PowerPoint / OS automation "
        f"modules: {offending(loaded)}"
    )


def test_the_health_endpoint_does_not_probe_powerpoint_in_web_mode():
    """Health must not attach to PowerPoint to answer a question about VisionX.

    The probe used to run on every health check. In web mode the answer is not
    merely unknown, it is irrelevant - so the endpoint says so instead of
    reporting a scary UNKNOWN about something that is not being used.
    """
    import sys as _sys
    from pathlib import Path as _Path

    root = _repo_root()
    for path in (root, str(_Path(root) / "backend")):
        if path not in _sys.path:
            _sys.path.insert(0, path)

    import app as flask_app
    from config.settings import settings

    original = settings.PRESENTATION_MODE
    try:
        settings.PRESENTATION_MODE = "web"
        payload = flask_app.app.test_client().get("/api/health").json["data"]
    finally:
        settings.PRESENTATION_MODE = original

    assert payload["presentationMode"] == "web"
    assert payload["powerpoint"]["slideshow"] == "NOT_USED"

    # And it reports the converter honestly, without consulting PowerPoint to do
    # so. `ready` is what an operator needs before their first upload: the web
    # mode needs no Office at presentation time, but it does need *a* converter
    # at upload time, and finding that out from a silently slide-less upload is
    # the worst possible moment.
    converter = payload["pptxConverter"]
    assert converter["policy"] in ("auto", "libreoffice", "powerpoint")
    assert converter["pdfNeedsNoConverter"] is True
    assert isinstance(converter["ready"], bool)


def test_health_never_loads_com_to_answer(monkeypatch):
    """Answering "can this machine convert a deck?" must not attach to PowerPoint.

    The health endpoint is polled; a probe that constructs a COM bridge on every
    call is how a health check ends up launching an application.
    """
    import subprocess as _subprocess
    import sys as _sys

    code = """
import json, sys, os
os.environ['VISIONX_PRESENTATION_MODE'] = 'web'
os.environ['VOICE_PREWARM'] = '0'
sys.path[:0] = ['backend', '.']
import app as flask_app
for _ in range(3):
    flask_app.app.test_client().get('/api/health')
print(json.dumps(sorted(sys.modules)))
"""
    result = _subprocess.run(
        [_sys.executable, "-c", code],
        capture_output=True, text=True, cwd=_repo_root(), timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]

    import json as _json

    loaded = _json.loads(result.stdout.strip().splitlines()[-1])
    assert offending(loaded) == [], (
        f"the health endpoint loaded PowerPoint / OS automation: {offending(loaded)}"
    )


# ================================== 2. NOTHING IS LAUNCHED ===================
def test_no_process_is_launched_during_a_web_presentation(monkeypatch):
    """No PowerPoint, no soffice, no anything.

    Conversion happens once, at upload. By the time a presentation is running the
    deck is PNGs on disk, so a subprocess at this point could only be VisionX
    launching an application - which is exactly what this change removed.
    """
    launched: list = []
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(
            subprocess, name,
            lambda *args, **kwargs: launched.append(args) or pytest.fail(
                f"subprocess.{name} was called during a web presentation: {args}"
            ),
        )

    controller = WebPresentationController()
    dispatcher = CommandDispatcher(controller, AnnotationController())
    dispatcher.bind_presentation(current_slide=1, total_slides=20)

    for command in (START_PRESENTATION, NEXT_SLIDE, PREVIOUS_SLIDE, FIRST_SLIDE,
                    LAST_SLIDE, VIRTUAL_POINTER, ANNOTATION_MODE, CLEAR_ANNOTATION,
                    BLACKOUT, WHITEOUT, END_PRESENTATION):
        dispatcher.execute(command, {"source": "gesture"})
    dispatcher.execute(GO_TO_SLIDE, {"parameters": {"slideNumber": 4}, "source": "voice"})

    assert launched == []


# ============================ 3. NO AUTOMATION, EVEN IF AVAILABLE ============
@pytest.fixture
def poisoned_automation(monkeypatch):
    """Make every OS-automation entry point fail loudly if it is ever reached.

    Stronger than asserting a module was not imported: this survives the case
    where something *else* in the process has already imported PyAutoGUI, which
    is exactly the situation in a real backend that also supports legacy mode.
    """
    from presentation_controller.keyboard import KeyboardBackend
    from presentation_controller.windows import PowerPointComBridge

    def forbidden(name):
        def boom(*args, **kwargs):
            raise AssertionError(
                f"the web presentation mode called {name}() - it must never touch "
                "the keyboard, the mouse, or PowerPoint COM"
            )
        return boom

    for method in ("press", "hotkey", "write", "move_to", "mouse_down", "mouse_up",
                   "screen_size", "_gui"):
        monkeypatch.setattr(KeyboardBackend, method, forbidden(f"KeyboardBackend.{method}"))
    for method in ("probe", "set_pointer_type", "erase_ink", "next_slide",
                   "previous_slide", "goto_slide", "activate", "_connect"):
        monkeypatch.setattr(PowerPointComBridge, method,
                            forbidden(f"PowerPointComBridge.{method}"))
    return True


def test_every_command_runs_with_all_automation_poisoned(poisoned_automation):
    """The behavioural proof, command by command.

    If any of the twelve reached for a keystroke or COM call, the fixture above
    turns it into a failure naming the exact method.
    """
    controller = WebPresentationController()
    dispatcher = CommandDispatcher(controller, AnnotationController())
    dispatcher.bind_presentation(current_slide=1, total_slides=20)

    commands = [
        (START_PRESENTATION, {}), (NEXT_SLIDE, {}), (PREVIOUS_SLIDE, {}),
        (GO_TO_SLIDE, {"slideNumber": 12}), (FIRST_SLIDE, {}), (LAST_SLIDE, {}),
        (VIRTUAL_POINTER, {}), (ANNOTATION_MODE, {}), (CLEAR_ANNOTATION, {}),
        (BLACKOUT, {}), (WHITEOUT, {}), (END_PRESENTATION, {}),
    ]
    for command, parameters in commands:
        record = dispatcher.execute(command, {"parameters": parameters, "source": "gesture"})
        assert record["delivered"], f"{command}: {record['message']}"


def test_pointer_and_annotation_run_with_all_automation_poisoned(poisoned_automation):
    """The two features that previously *needed* a real mouse."""
    controller = WebPresentationController()
    dispatcher = CommandDispatcher(controller, AnnotationController())
    dispatcher.bind_presentation(current_slide=1, total_slides=20)

    dispatcher.execute(VIRTUAL_POINTER, {"source": "gesture"})
    for step in range(30):
        dispatcher.stream_pointer(0.3 + step * 0.01, 0.5)

    dispatcher.execute(ANNOTATION_MODE, {"source": "gesture"})
    for step in range(30):
        dispatcher.stream_pointer(0.3 + step * 0.01, 0.4)
    dispatcher.end_stroke()

    assert len(dispatcher.annotations.strokes()) == 1
    dispatcher.execute(CLEAR_ANNOTATION, {"source": "voice"})
    assert dispatcher.annotations.count == 0


# ================================ 4. THE STRUCTURAL GUARANTEE ================
def test_the_web_controller_owns_no_automation_objects():
    """It cannot drive PowerPoint because it holds nothing that could."""
    controller = WebPresentationController()
    for attribute in ("keyboard", "pointer", "com", "bridge", "app", "powerpoint"):
        assert not hasattr(controller, attribute)
    assert controller.describe()["automation"] == "none"


def test_the_web_controller_is_not_a_powerpoint_controller():
    """No inheritance either - it shares the interface, not the implementation."""
    from presentation_controller.base import PresentationController
    from presentation_controller.powerpoint import PowerPointController

    assert issubclass(WebPresentationController, PresentationController)
    assert not issubclass(WebPresentationController, PowerPointController)


def test_the_web_controller_never_reports_a_denied_state():
    """Nothing can refuse a command, because nothing else is involved.

    `DENIED` is PowerPoint saying "no slideshow is running", and it is what made
    the pen and the eraser look broken. The web surface is always ready.
    """
    from presentation_controller.base import SLIDESHOW_CONFIRMED

    controller = WebPresentationController()
    assert controller.slideshow_state() == SLIDESHOW_CONFIRMED
    controller.end_presentation()
    assert controller.slideshow_state() == SLIDESHOW_CONFIRMED


def test_the_web_controller_can_deliver_every_command():
    """Twelve of twelve. The PowerPoint controller cannot say this."""
    from computer_vision.command_mapping.gesture_mapper import ALL_COMMANDS

    assert WebPresentationController().capabilities() == set(ALL_COMMANDS)
