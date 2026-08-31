"""Windows + Microsoft PowerPoint control: fixes.md §2, §3 and §5.

The three reported failures, and what each one actually was:

  §2  the Virtual Pointer opened the Print dialog
      -> `Ctrl+P` was sent whenever the pen was armed. In a slideshow that is the
         pen; on an ordinary PowerPoint window it is **Print**. Nothing checked
         which one was in front.

  §3  annotation did not work: no pen, no drawing, no clear
      -> the pen was armed by a keystroke that often landed on the wrong window;
         drawing streamed cursor *moves* with no mouse button held, which draws
         nothing at all; and Clear pressed `E`, which only erases while the pen is
         already selected.

  §5  Windows-specific behaviour
      -> DPI awareness, COM attachment to the running PowerPoint, foreground
         checks.

`FakeCom` (conftest) scripts the three machine states that matter. `FakeKeyboard`
records what would have reached the OS.
"""

import pytest

from presentation_controller.base import PresentationControlError
from presentation_controller.powerpoint import PowerPointController
from presentation_controller.windows import (
    IS_WINDOWS,
    PP_POINTER_ARROW,
    PP_POINTER_PEN,
    SLIDESHOW_CONFIRMED,
    SLIDESHOW_DENIED,
    SLIDESHOW_UNKNOWN,
    PowerPointComBridge,
    enable_dpi_awareness,
    foreground_window_title,
)
from tests.conftest import FakeCom, FakeKeyboard, build_dispatcher

PRINT_HOTKEY = ("ctrl", "p")


def _controller(keyboard, com):
    return PowerPointController(keyboard, com=com)


# ================================================ §2 THE PRINT DIALOG BUG =====
def test_the_pointer_never_sends_ctrl_p_in_any_machine_state():
    """The single most important guarantee in this file.

    VIRTUAL_POINTER, on or off, from any starting state, on any of the three
    machine configurations, must never emit Ctrl+P.
    """
    for com in (FakeCom(False, False), FakeCom(True, True), FakeCom(True, False)):
        for pen_first in (False, True):
            keyboard = FakeKeyboard()
            controller = _controller(keyboard, com)

            if pen_first:
                try:
                    controller.set_annotation(True)
                except PresentationControlError:
                    pass
                keyboard.log.clear()      # only the pointer's own keys are at issue

            controller.set_pointer(True)
            controller.set_pointer(False)
            controller.set_pointer(True)

            assert PRINT_HOTKEY not in keyboard.hotkeys(), (
                f"the pointer sent Ctrl+P (com={com.connected}/{com.slideshow}, "
                f"pen_first={pen_first})"
            )


def test_the_pen_is_refused_rather_than_printing_when_no_slideshow_is_running(not_presenting):
    """The root cause, stated directly.

    PowerPoint is open but not presenting. Arming the pen must fail loudly instead
    of opening the Print dialog.
    """
    keyboard = FakeKeyboard()
    controller = _controller(keyboard, not_presenting)

    with pytest.raises(PresentationControlError) as excinfo:
        controller.set_annotation(True)

    assert PRINT_HOTKEY not in keyboard.hotkeys()
    assert "print" in str(excinfo.value).lower()
    assert controller.annotation_active is False


def test_repeatedly_arming_the_pen_without_a_slideshow_never_prints(not_presenting):
    """The report said the Print dialog opened *repeatedly*. Twenty attempts, no
    Ctrl+P - not one, not the first."""
    keyboard = FakeKeyboard()
    controller = _controller(keyboard, not_presenting)

    for _ in range(20):
        with pytest.raises(PresentationControlError):
            controller.set_annotation(True)

    assert keyboard.hotkeys() == []
    assert keyboard.keys() == []


def test_a_refused_pen_reaches_the_ui_as_undelivered_not_as_a_crash(keyboard, not_presenting):
    """A refusal must be reported, not thrown into the camera loop."""
    dispatcher = build_dispatcher(keyboard, not_presenting)
    record = dispatcher.execute("ANNOTATION_MODE")

    assert record["delivered"] is False
    assert "slideshow" in record["message"].lower()
    assert record["annotationActive"] is False
    assert PRINT_HOTKEY not in keyboard.hotkeys()


def test_the_pen_uses_com_and_no_keystroke_when_a_slideshow_is_running(presenting):
    """On the real deployment the pen is set through COM, so Ctrl+P is never sent
    even in the case where it would have been safe."""
    keyboard = FakeKeyboard()
    controller = _controller(keyboard, presenting)

    controller.set_annotation(True)

    assert controller.annotation_active is True
    assert presenting.pointer_type_value == PP_POINTER_PEN
    assert keyboard.hotkeys() == []


def test_ctrl_p_is_still_sent_when_the_slideshow_state_is_unknowable(keyboard, com):
    """Degradation, not removal.

    Off Windows, or with no COM binding, VisionX cannot ask - and there is no
    evidence of danger - so it behaves exactly as it always has. Removing the
    keystroke everywhere would have broken every non-Windows setup.
    """
    controller = _controller(keyboard, com)
    controller.set_annotation(True)

    assert com.probe() == SLIDESHOW_UNKNOWN
    assert PRINT_HOTKEY in keyboard.hotkeys()
    assert controller.annotation_active is True


def test_switching_pointer_to_pen_and_back_leaves_consistent_state(presenting):
    keyboard = FakeKeyboard()
    controller = _controller(keyboard, presenting)

    controller.set_pointer(True)
    assert controller.pointer_active and not controller.annotation_active

    controller.set_annotation(True)
    assert controller.annotation_active and not controller.pointer_active
    assert presenting.pointer_type_value == PP_POINTER_PEN

    controller.set_pointer(True)
    assert controller.pointer_active and not controller.annotation_active
    assert presenting.pointer_type_value == PP_POINTER_ARROW
    assert PRINT_HOTKEY not in keyboard.hotkeys()


# ==================================================== §3 ANNOTATION WORKS =====
def test_drawing_holds_the_mouse_button_down(keyboard, presenting):
    """The reason nothing was ever drawn.

    PowerPoint's pen draws on a *drag*. Streaming positions with no button held
    walks the pen across the slide and leaves no mark.
    """
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    assert dispatcher.annotation_active

    keyboard.log.clear()
    for step in range(5):
        dispatcher.stream_pointer(0.3 + step * 0.05, 0.5)

    kinds = [entry[0] for entry in keyboard.log]
    assert "mouseDown" in kinds, "the pen never touched the slide"
    assert kinds.count("mouseDown") == 1, "the button was pressed more than once"
    assert kinds.index("move") < kinds.index("mouseDown"), (
        "the button went down before the first move, which drags a line in from "
        "wherever the cursor happened to be"
    )
    assert kinds.count("move") == 5


def test_the_pen_is_lifted_when_the_hand_leaves_the_frame(keyboard, presenting):
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    dispatcher.stream_pointer(0.3, 0.5)
    dispatcher.stream_pointer(0.4, 0.5)
    assert keyboard.mouse_is_down

    dispatcher.end_stroke()
    assert not keyboard.mouse_is_down
    assert ("mouseUp", "left") in keyboard.log


def test_turning_annotation_off_lifts_the_pen(keyboard, presenting):
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    dispatcher.stream_pointer(0.3, 0.5)
    dispatcher.stream_pointer(0.4, 0.5)

    dispatcher.execute("ANNOTATION_MODE")
    assert dispatcher.annotation_active is False
    assert not keyboard.mouse_is_down


def test_switching_to_the_pointer_lifts_the_pen(keyboard, presenting):
    """Otherwise the button stays held and PowerPoint keeps drawing while the
    presenter is trying to point at something."""
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    dispatcher.stream_pointer(0.3, 0.5)
    dispatcher.stream_pointer(0.4, 0.5)
    assert keyboard.mouse_is_down

    dispatcher.execute("VIRTUAL_POINTER")
    assert not keyboard.mouse_is_down
    assert dispatcher.annotation_active is False
    assert dispatcher.pointer_active is True


def test_the_pointer_moves_without_ever_pressing_a_button(keyboard, presenting):
    """Pointing is not drawing."""
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("VIRTUAL_POINTER")
    keyboard.log.clear()
    for step in range(5):
        dispatcher.stream_pointer(0.3 + step * 0.05, 0.5)

    kinds = [entry[0] for entry in keyboard.log]
    assert kinds == ["move"] * 5
    assert not keyboard.mouse_is_down


def test_clear_annotation_erases_the_ink_through_com(keyboard, presenting):
    """`EraseDrawing()` is the real thing and works in any pointer mode."""
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    dispatcher.stream_pointer(0.3, 0.5)
    dispatcher.stream_pointer(0.4, 0.5)

    record = dispatcher.execute("CLEAR_ANNOTATION")

    assert record["delivered"] is True
    assert presenting.erased == 1
    assert "e" not in keyboard.keys(), "the fallback keystroke was sent as well"
    assert not keyboard.mouse_is_down


def test_clear_annotation_falls_back_to_the_e_key_when_com_is_absent(keyboard, com):
    dispatcher = build_dispatcher(keyboard, com)
    dispatcher.execute("ANNOTATION_MODE")
    dispatcher.execute("CLEAR_ANNOTATION")
    assert "e" in keyboard.keys()


def test_clear_annotation_is_refused_when_there_is_no_slideshow(keyboard, not_presenting):
    """Pressing `E` into a normal PowerPoint window types the letter E."""
    dispatcher = build_dispatcher(keyboard, not_presenting)
    record = dispatcher.execute("CLEAR_ANNOTATION")
    assert record["delivered"] is False
    assert "e" not in keyboard.keys()


def test_reset_leaves_pen_mode_and_erases_through_com(keyboard, presenting):
    """The open-palm escape hatch against a real slideshow."""
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    dispatcher.stream_pointer(0.3, 0.5)
    dispatcher.stream_pointer(0.4, 0.5)

    record = dispatcher.execute("RESET_ANNOTATION")

    assert record["delivered"] is True
    assert dispatcher.annotation_active is False
    assert dispatcher.pointer_active is False
    assert presenting.erased == 1
    assert not keyboard.mouse_is_down
    assert PRINT_HOTKEY not in keyboard.hotkeys(), "reset must never go near Ctrl+P"


def test_a_refused_erase_still_leaves_annotation_mode(keyboard, not_presenting):
    """The half of reset the presenter actually asked for cannot be held hostage
    by the half that can refuse.

    With no slideshow running PowerPoint will not erase - so the command reports
    itself undelivered, exactly as Clear does. But the pen and the pointer must be
    off regardless, or the one command whose job is to restore a known state has
    instead left an unknown one.
    """
    dispatcher = build_dispatcher(keyboard, not_presenting)
    dispatcher.execute("VIRTUAL_POINTER")
    assert dispatcher.pointer_active is True

    record = dispatcher.execute("RESET_ANNOTATION")

    assert record["delivered"] is False
    assert record["pointerActive"] is False
    assert record["annotationActive"] is False
    assert dispatcher.pointer_active is False
    assert "e" not in keyboard.keys()
    assert not keyboard.mouse_is_down


def test_clearing_ink_is_not_a_mode_change(keyboard, presenting):
    """Erasing must leave the pen exactly as it was.

    The old code claimed the pen was off after Clear while PowerPoint had it
    still on - so the next annotation gesture turned it *off* instead of on, and
    the presenter concluded that annotation did not work.
    """
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    assert dispatcher.annotation_active is True

    dispatcher.execute("CLEAR_ANNOTATION")
    assert dispatcher.annotation_active is True, (
        "VisionX reported the pen off while PowerPoint still had it on"
    )
    assert presenting.pointer_type_value == PP_POINTER_PEN


def test_clear_annotation_drops_the_buffered_strokes_for_the_slide(keyboard, presenting):
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    for step in range(6):
        dispatcher.stream_pointer(0.2 + step * 0.05, 0.4)
    dispatcher.end_stroke()
    assert dispatcher.annotations.count == 1

    dispatcher.execute("CLEAR_ANNOTATION")
    assert dispatcher.annotations.strokes(dispatcher.current_slide) == []


def test_strokes_are_recorded_while_drawing(keyboard, presenting):
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    for step in range(10):
        dispatcher.stream_pointer(0.2 + step * 0.04, 0.4 + step * 0.02)
    stroke = dispatcher.end_stroke()

    assert stroke is not None
    assert len(stroke.points) == 10
    assert stroke.slide == dispatcher.current_slide


def test_end_stroke_is_idempotent(keyboard, presenting):
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    dispatcher.stream_pointer(0.3, 0.5)
    dispatcher.stream_pointer(0.4, 0.5)
    dispatcher.end_stroke()
    dispatcher.end_stroke()
    assert not keyboard.mouse_is_down


def test_ending_the_presentation_lifts_the_pen(keyboard, presenting):
    """A talk must never end with the mouse button held down on the desktop."""
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    dispatcher.stream_pointer(0.3, 0.5)
    dispatcher.stream_pointer(0.4, 0.5)
    assert keyboard.mouse_is_down

    dispatcher.execute("END_PRESENTATION")
    assert not keyboard.mouse_is_down
    assert dispatcher.annotation_active is False


def test_a_dead_keyboard_mid_stroke_does_not_leave_the_button_stuck():
    """If PyAutoGUI dies while the button is held, the flag must still clear or it
    can never be released again."""
    keyboard = FakeKeyboard()
    controller = _controller(keyboard, FakeCom(True, True))
    controller.set_annotation(True)
    controller.pen_down()
    assert keyboard.mouse_is_down

    keyboard._available = False
    controller.pen_up()
    assert not keyboard.mouse_is_down


# ==================================== STATE STAYS CONSISTENT ACROSS LAYERS ====
def test_gesture_toggles_cannot_desync_the_engine_from_the_dispatcher(keyboard, presenting):
    """The engine sends the state it decided on, so a toggle cannot go two ways.

    Both used to toggle independently from the same event, which is how the
    pointer ended up 'on' in one layer and 'off' in the other.
    """
    dispatcher = build_dispatcher(keyboard, presenting)

    for expected in (True, False, True, False, True):
        dispatcher.execute("VIRTUAL_POINTER", {"parameters": {"state": expected}})
        assert dispatcher.pointer_active is expected
        assert dispatcher.controller.pointer_active is expected


def test_a_refused_command_does_not_leave_the_dispatcher_lying(keyboard, not_presenting):
    """The dispatcher must describe what happened, not what was asked for."""
    dispatcher = build_dispatcher(keyboard, not_presenting)
    for _ in range(3):
        dispatcher.execute("ANNOTATION_MODE")
        assert dispatcher.annotation_active is False
        assert dispatcher.controller.annotation_active is False


def test_voice_state_setting_stays_idempotent_with_a_live_slideshow(keyboard, presenting):
    dispatcher = build_dispatcher(keyboard, presenting)
    for _ in range(3):
        dispatcher.execute("ANNOTATION_MODE", {"parameters": {"state": True}})
        assert dispatcher.annotation_active is True
    dispatcher.execute("ANNOTATION_MODE", {"parameters": {"state": False}})
    assert dispatcher.annotation_active is False


def test_gesture_toggles_still_toggle_with_a_live_slideshow(keyboard, presenting):
    dispatcher = build_dispatcher(keyboard, presenting)
    assert dispatcher.annotation_active is False
    dispatcher.execute("ANNOTATION_MODE")
    assert dispatcher.annotation_active is True
    dispatcher.execute("ANNOTATION_MODE")
    assert dispatcher.annotation_active is False


# ============================================== §5 WINDOWS NAVIGATION =========
def test_navigation_prefers_com_when_a_slideshow_is_running(presenting):
    keyboard = FakeKeyboard()
    controller = _controller(keyboard, presenting)

    controller.next_slide()
    controller.previous_slide()
    controller.goto_slide(7)
    controller.first_slide()
    controller.last_slide()

    assert keyboard.log == [], "keystrokes were sent although COM handled it"
    assert ("goto", 7) in presenting.calls
    assert ("goto", 1) in presenting.calls
    assert ("goto", presenting.slides) in presenting.calls


def test_navigation_falls_back_to_keystrokes_without_com(keyboard, com):
    controller = _controller(keyboard, com)
    controller.next_slide()
    controller.previous_slide()
    controller.goto_slide(7)
    controller.first_slide()
    controller.last_slide()

    assert keyboard.keys() == ["right", "left", "enter", "home", "end"]
    assert ("write", "7") in keyboard.log


def test_goto_slide_rejects_a_slide_before_the_first(keyboard, com):
    controller = _controller(keyboard, com)
    with pytest.raises(PresentationControlError):
        controller.goto_slide(0)


def test_describe_reports_the_windows_situation(keyboard, presenting):
    controller = _controller(keyboard, presenting)
    described = controller.describe()
    assert described["slideshow"] == SLIDESHOW_CONFIRMED
    assert described["penDown"] is False
    assert "windows" in described


# ================================================== §5 THE PLATFORM LAYER =====
def test_the_com_bridge_is_inert_off_windows():
    """Every method must be total: a value or None, never an exception."""
    bridge = PowerPointComBridge()
    assert bridge.probe() == (SLIDESHOW_UNKNOWN if not IS_WINDOWS else bridge.probe())
    if not IS_WINDOWS:
        assert bridge.probe() == SLIDESHOW_UNKNOWN
        assert bridge.set_pointer_type(PP_POINTER_PEN) is False
        assert bridge.erase_ink() is False
        assert bridge.next_slide() is False
        assert bridge.previous_slide() is False
        assert bridge.goto_slide(3) is False
        assert bridge.slide_count() is None
        assert bridge.current_slide() is None
        assert bridge.pointer_type() is None
        assert bridge.activate() is False
        bridge.invalidate()


def test_a_disabled_bridge_reports_unknown_not_denied():
    """UNKNOWN and DENIED must never be confused: DENIED blocks the keystroke,
    UNKNOWN allows it. Getting this backwards would break every dev machine."""
    assert PowerPointComBridge(enabled=False).probe() == SLIDESHOW_UNKNOWN


def test_dpi_awareness_and_foreground_lookup_never_raise():
    """Both are best-effort, and both run during start-up on every platform."""
    assert enable_dpi_awareness() in (True, False)
    title = foreground_window_title()
    assert title is None or isinstance(title, str)


def test_the_keyboard_backend_pauses_between_keys_only_on_windows():
    from presentation_controller.keyboard import WINDOWS_KEY_PAUSE

    assert WINDOWS_KEY_PAUSE > 0, (
        "PowerPoint's slideshow window drops keystrokes sent with no gap at all"
    )


def test_slideshow_probe_values_are_the_three_documented_ones():
    assert {SLIDESHOW_CONFIRMED, SLIDESHOW_DENIED, SLIDESHOW_UNKNOWN} == {
        "CONFIRMED", "DENIED", "UNKNOWN",
    }


# ============================ REGRESSIONS FOUND IN REVIEW OF THIS CHANGE SET ==
def test_turning_the_pointer_off_lifts_the_pen_and_clears_annotation(keyboard, presenting):
    """Found in review: `set_pointer(False)` restored the arrow but left the pen
    "on" and the mouse button held.

    PowerPoint was then in ARROW mode with the button down - where a drag
    **advances the slide** - so every attempted stroke skipped slides while the UI
    still reported the pen as active. Reachable from voice ("turn the pointer
    off" -> VIRTUAL_POINTER {state: False}) and from the control bar.
    """
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")
    dispatcher.stream_pointer(0.3, 0.5)
    dispatcher.stream_pointer(0.4, 0.5)
    assert keyboard.mouse_is_down and dispatcher.annotation_active

    record = dispatcher.execute("VIRTUAL_POINTER", {"parameters": {"state": False}})

    assert not keyboard.mouse_is_down, "the mouse button was left held down"
    assert dispatcher.annotation_active is False
    assert dispatcher.controller.annotation_active is False
    assert record["annotationActive"] is False
    assert presenting.pointer_type_value == PP_POINTER_ARROW

    # And no further movement drags anything.
    keyboard.log.clear()
    dispatcher.stream_pointer(0.5, 0.5)
    assert keyboard.log == []


def test_a_refused_clear_does_not_delete_the_ink_it_failed_to_erase(keyboard, not_presenting):
    """Found in review: the in-memory buffer was cleared *before* the controller
    was asked, so a refused erase threw the annotations away while leaving the ink
    on the slide."""
    dispatcher = build_dispatcher(keyboard, not_presenting)
    dispatcher.annotations.begin(dispatcher.current_slide)
    for step in range(6):
        dispatcher.annotations.add_point(0.2 + step * 0.05, 0.4)
    dispatcher.annotations.end()
    assert dispatcher.annotations.count == 1

    record = dispatcher.execute("CLEAR_ANNOTATION")

    assert record["delivered"] is False
    assert dispatcher.annotations.count == 1, (
        "annotations were deleted although nothing was erased in PowerPoint"
    )


def test_the_mouse_button_survives_concurrent_pen_traffic(presenting):
    """Found in review: `mouse_down` was check-then-set across two threads.

    The camera thread draws (`stream_pointer` -> `pen_down`) while a Flask thread
    switches modes (`set_annotation(False)` -> `pen_up`). An interleaving could
    lose the release and strand the physical button down on the desktop.
    """
    import threading

    keyboard = FakeKeyboard()
    dispatcher = build_dispatcher(keyboard, presenting)
    dispatcher.execute("ANNOTATION_MODE")

    stop = threading.Event()
    errors: list[Exception] = []

    def draw():
        try:
            while not stop.is_set():
                dispatcher.stream_pointer(0.3, 0.5)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def toggle():
        try:
            for _ in range(200):
                dispatcher.execute("ANNOTATION_MODE", {"parameters": {"state": False}})
                dispatcher.execute("ANNOTATION_MODE", {"parameters": {"state": True}})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    drawer = threading.Thread(target=draw)
    toggler = threading.Thread(target=toggle)
    drawer.start()
    toggler.start()
    toggler.join()
    stop.set()
    drawer.join()

    assert not errors, errors

    # Whatever the interleaving, turning the pen off must leave the button up.
    dispatcher.execute("ANNOTATION_MODE", {"parameters": {"state": False}})
    assert not keyboard.mouse_is_down, "the mouse button was stranded down"


def test_the_com_bridge_holds_its_connection_per_thread(presenting):
    """Found in review: one COM interface pointer was shared across threads.

    COM apartments are per-thread, so a proxy obtained on the camera thread and
    used from a Flask request thread raises RPC_E_WRONG_THREAD - which this class
    catches and reports as UNKNOWN, the one probe result that lets Ctrl+P through.
    A status poll could therefore re-open the Print-dialog path. The pointer is
    now thread-local, so no thread can invalidate another's.
    """
    import threading

    from presentation_controller.windows import PowerPointComBridge

    bridge = PowerPointComBridge()
    # Same object, two threads: each must keep its own (here: absent) connection
    # and never blow up.
    results: list[str] = []

    def probe():
        results.append(bridge.probe())

    threads = [threading.Thread(target=probe) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 4
    assert set(results) <= {SLIDESHOW_CONFIRMED, SLIDESHOW_DENIED, SLIDESHOW_UNKNOWN}
    # The interface pointer lives on thread-local storage, not on the instance.
    assert "application" not in PowerPointComBridge.__dict__


def test_the_annotation_buffer_survives_concurrent_drawing_and_flushing():
    """The camera loop draws while a Flask thread ends and reads strokes.

    Unguarded, `add_point` saw `_active` set on entry and None by the time it
    dereferenced it - `AttributeError: 'NoneType' object has no attribute
    'points'` raised inside the camera loop. The engine swallows subscriber
    exceptions, so drawing simply stopped, silently, with the pen still held down.
    """
    import threading

    from presentation_controller.annotation import AnnotationController

    annotations = AnnotationController()
    annotations.begin(1)
    stop = threading.Event()
    errors: list[Exception] = []

    def draw():
        index = 0
        try:
            while not stop.is_set():
                if not annotations.is_drawing:
                    annotations.begin(1)
                annotations.add_point(0.1 + (index % 500) * 0.001, 0.2)
                index += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def flush():
        try:
            for _ in range(400):
                annotations.end()
                for stroke in annotations.strokes():
                    # Iterating a returned stroke must not race a live append.
                    sum(point["x"] for point in stroke["points"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    drawer = threading.Thread(target=draw)
    flusher = threading.Thread(target=flush)
    drawer.start()
    flusher.start()
    flusher.join()
    stop.set()
    drawer.join()

    assert not errors, errors


def test_strokes_are_handed_out_as_copies():
    """A caller serialising strokes to MongoDB must not be holding the same list
    the camera thread is still appending to."""
    from presentation_controller.annotation import AnnotationController

    annotations = AnnotationController()
    annotations.begin(1)
    annotations.add_point(0.1, 0.1)
    annotations.add_point(0.2, 0.2)
    annotations.end()

    first = annotations.strokes()[0]["points"]
    second = annotations.strokes()[0]["points"]
    assert first == second
    assert first is not second, "callers share one mutable points list"


def test_one_thread_failing_to_attach_cannot_make_another_send_ctrl_p():
    """The Print-dialog bug, reintroduced by a cache — and fixed.

    Making the COM connection thread-local while leaving the probe *cache* shared
    means a thread that failed to attach caches UNKNOWN, and the camera thread
    then reads UNKNOWN instead of its own DENIED. UNKNOWN is the one result that
    lets `Ctrl+P` through, so a single rejected COM call on a status-polling
    thread would put the Print dialog back on screen.
    """
    import threading

    class Bridge(PowerPointComBridge):
        """PowerPoint open but NOT presenting, except on one thread where the COM
        attach is rejected (which really happens: RPC_E_CALL_REJECTED when
        PowerPoint is busy)."""

        def __init__(self):
            super().__init__(enabled=True)
            self.enabled = True

        def _connect(self):
            if threading.current_thread().name == "flaky":
                return None
            class App:                       # noqa: N801 - a COM stand-in
                class SlideShowWindows:      # noqa: N801
                    Count = 0
            return App()

    bridge = Bridge()
    seen: dict[str, str] = {}

    def probe(name):
        seen[name] = bridge.probe()

    for name in ("flaky", "camera"):
        thread = threading.Thread(target=probe, args=(name,), name=name)
        thread.start()
        thread.join()

    assert seen["flaky"] == SLIDESHOW_UNKNOWN
    assert seen["camera"] == SLIDESHOW_DENIED, (
        "the camera thread inherited another thread's UNKNOWN and would send Ctrl+P"
    )


def test_invalidate_clears_the_cache_on_every_thread():
    """A slideshow that just started is news for all threads, not just the one
    that pressed F5."""
    import threading

    class Bridge(PowerPointComBridge):
        def __init__(self):
            super().__init__(enabled=True)
            self.enabled = True
            self.presenting = False

        def _connect(self):
            outer = self
            class App:                       # noqa: N801
                class SlideShowWindows:      # noqa: N801
                    pass
            App.SlideShowWindows.Count = 1 if outer.presenting else 0
            return App

    bridge = Bridge()
    results: list[str] = []

    def probe():
        results.append(bridge.probe())

    for _ in range(2):
        thread = threading.Thread(target=probe)
        thread.start()
        thread.join()
    assert results == [SLIDESHOW_DENIED, SLIDESHOW_DENIED]

    bridge.presenting = True
    bridge.invalidate()

    results.clear()
    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    assert results == [SLIDESHOW_CONFIRMED], "a stale cache survived invalidate()"


def test_turning_the_pointer_off_closes_the_stroke_so_the_pen_works_again(keyboard, presenting):
    """Found in review: `_handle_pointer` closed the stroke on the *on* path only.

    `set_pointer(False)` also leaves pen mode, so the dispatcher ended up with
    `annotation_active == False` while the buffer still had `is_drawing == True`.
    `stream_pointer` guards `pen_down` behind `if not is_drawing`, so the pen then
    never touched the slide again for the rest of the session - silently, which is
    exactly the "annotation does not work" symptom.
    """
    dispatcher = build_dispatcher(keyboard, presenting)

    dispatcher.execute("ANNOTATION_MODE", {"parameters": {"state": True}})
    dispatcher.stream_pointer(0.3, 0.5)
    dispatcher.stream_pointer(0.4, 0.5)

    dispatcher.execute("VIRTUAL_POINTER", {"parameters": {"state": False}})
    assert dispatcher.annotations.is_drawing is False, (
        "the stroke was left open while the mode was off"
    )

    dispatcher.execute("ANNOTATION_MODE", {"parameters": {"state": True}})
    keyboard.log.clear()
    for step in range(10):
        dispatcher.stream_pointer(0.2 + step * 0.02, 0.6)

    assert any(entry[0] == "mouseDown" for entry in keyboard.log), (
        "the second stroke moved the pen without ever pressing the button"
    )


def test_an_unexpected_input_error_is_reported_not_raised(presenting):
    """A PyAutoGUI failure outside the expected exception types used to escape the
    dispatcher - a 500 for the browser, or straight into the camera loop."""
    class ExplodingKeyboard(FakeKeyboard):
        def press(self, key):
            raise OSError("the display went away")

    dispatcher = build_dispatcher(ExplodingKeyboard(), FakeCom(connected=False))
    record = dispatcher.execute("NEXT_SLIDE")

    assert record["delivered"] is False
    assert "could not be delivered" in record["message"]


def test_dpi_awareness_reports_failure_rather_than_claiming_success():
    """`windll` returns a failing HRESULT instead of raising, so an unchecked call
    reported success on a machine where nothing had been set - and the reliable
    user32 fallback was never tried."""
    import presentation_controller.windows as windows

    if windows.IS_WINDOWS:               # pragma: no cover - only meaningful there
        pytest.skip("exercised for real on Windows")

    # Off Windows the function must still be total and honest.
    assert windows.enable_dpi_awareness() is False
