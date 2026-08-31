"""The web presentation controller: VisionX renders the deck, so nothing is automated.

This is the controller behind the web-based presentation experience. It replaces
`PowerPointController` for a web session and it is deliberately the *opposite*
kind of object: it sends no keystrokes, moves no mouse, and asks no COM server
for anything. A command "reaches the device" by being published to the browser
window that is showing the slides.

That single change is what removes the whole family of problems the PowerPoint
integration had:

    Ctrl+P outside a slideshow opening Print   ->  no keystrokes at all
    the pen refusing because PowerPoint is not presenting
                                               ->  the pen is a canvas flag
    laggy pointer (a PyAutoGUI syscall per frame on the camera thread)
                                               ->  a coalesced pointer event
    Clear Annotation not erasing               ->  one event, one clearRect
    Windows focus stealing the key press       ->  nothing has focus to steal

Layering is unchanged: the dispatcher still owns slide bookkeeping and still
calls exactly the same interface. Only the bottom of the stack moved from "the
operating system" to "the presentation window".

### The pointer path

`move_pointer` is called at camera frame rate (~30 Hz) from the camera thread.
It must therefore be cheap and it must never block, so it publishes onto the
event bus' *coalescing* pointer channel: a slow browser gets the newest position
rather than a backlog of stale ones. That is the difference between a pointer
that follows the fingertip and one that arrives in delayed jumps.

Pointer movement is a continuous stream and is deliberately not subject to the
gesture debouncer - the debouncer governs discrete commands, and letting it
throttle fingertip movement is what made the old pointer feel laggy.
"""

from __future__ import annotations

import logging
import threading
import time

from presentation_controller.base import SLIDESHOW_CONFIRMED, PresentationController

logger = logging.getLogger(__name__)

# Coordinates published here are the engine's own camera-normalised (0..1)
# fingertip position, NOT slide coordinates. The presentation window applies the
# reach margin (the hand cannot comfortably reach the edges of the camera frame)
# when it draws.
#
# That split is deliberate. `CommandDispatcher.stream_pointer` persists the same
# raw numbers to MongoDB, so mapping them here would make a live stroke and the
# replay of that same saved stroke land in different places on the slide. One
# mapping, applied once, at the surface that draws.


class WebPresentationController(PresentationController):
    """Drives the VisionX presentation window. Sole owner of pointer/pen state.

    Every command is delivered: there is no OS layer that can refuse one. What
    the dispatcher records as `delivered` is therefore the truth, which was never
    quite true of the PowerPoint controller - and the UI can stop hedging.
    """

    name = "web"

    def __init__(self, publish=None, publish_pointer=None):
        # Injected rather than imported so this class stays testable without Flask
        # and without the event bus - the tests drive it with plain lists.
        self._publish = publish or (lambda event: None)
        self._publish_pointer = publish_pointer or (lambda event: None)

        self._lock = threading.RLock()
        self._pointer_active = False
        self._annotation_active = False
        self._pen_down = False
        self._presenting = True
        # The last position published, so a pen-down can be reported at the point
        # the stroke really starts rather than at the origin.
        self._last_point: tuple[float, float] | None = None

    # --- state ---------------------------------------------------------------
    @property
    def pointer_active(self) -> bool:
        return self._pointer_active

    @property
    def annotation_active(self) -> bool:
        return self._annotation_active

    @property
    def pen_is_down(self) -> bool:
        return self._pen_down

    def slideshow_state(self) -> str:
        """Always CONFIRMED: VisionX is the presentation surface.

        The PowerPoint controller had to ask whether a slideshow was running,
        because sending the pen keystroke to a non-slideshow window opened Print.
        Here there is no keystroke and no other window, so the question - and the
        entire class of failure behind it - does not arise.
        """
        return SLIDESHOW_CONFIRMED

    # --- navigation ----------------------------------------------------------
    # The dispatcher owns slide bookkeeping and publishes the resulting state
    # itself (services.engine_service._after_command), so these only have to
    # succeed. Overriding them is still required: the base class raises
    # "not supported" for the optional ones.
    def next_slide(self) -> None:
        self._note("next")

    def previous_slide(self) -> None:
        self._note("previous")

    def goto_slide(self, number: int) -> None:
        self._note("goto", slide=int(number))

    def first_slide(self) -> None:
        self._note("first")

    def last_slide(self) -> None:
        self._note("last")

    def start_presentation(self) -> None:
        with self._lock:
            self._presenting = True
        self._publish_safely({"type": "presentation", "action": "START"})

    def end_presentation(self) -> None:
        with self._lock:
            self._presenting = False
            self._pointer_active = False
            self._annotation_active = False
            self._pen_down = False
        self._publish_safely({"type": "presentation", "action": "END"})

    def blackout(self) -> None:
        self._note("blackout")

    def whiteout(self) -> None:
        self._note("whiteout")

    # --- pointer -------------------------------------------------------------
    def set_pointer(self, active: bool) -> None:
        with self._lock:
            self._pointer_active = bool(active)
            if active:
                # The arrow and the pen are mutually exclusive, exactly as they are
                # in a slideshow: turning one on turns the other off.
                self._annotation_active = False
                self._pen_down = False
            elif not self._annotation_active:
                self._pen_down = False
        self._publish_mode()

    def move_pointer(self, x: float, y: float) -> None:
        """One fingertip position. Called at frame rate; must stay cheap.

        Published raw (camera-normalised), for the reason at the top of the file:
        this is the same number the dispatcher writes to MongoDB, and the reach
        margin is applied once, by whoever draws.
        """
        with self._lock:
            if not (self._pointer_active or self._annotation_active):
                return
            point = (round(float(x), 4), round(float(y), 4))
            self._last_point = point
            drawing = self._annotation_active and self._pen_down
        # Published outside the lock: the bus fans out to every subscriber and
        # must not hold the camera thread's lock while it does.
        self._publish_pointer_safely({
            "type": "pointer",
            "x": point[0],
            "y": point[1],
            "drawing": drawing,
            "t": time.time(),
        })

    # --- annotation ----------------------------------------------------------
    def set_annotation(self, active: bool) -> None:
        with self._lock:
            self._annotation_active = bool(active)
            if active:
                self._pointer_active = False
            else:
                self._pen_down = False
        self._publish_mode()

    def pen_down(self) -> None:
        """Begin a stroke. In web mode this is a flag, not a held mouse button.

        The PowerPoint pen needed a real button held down for the whole stroke,
        and a lost release stranded that button on the presenter's desktop. Here
        the worst a lost release can do is leave a boolean set, which the next
        mode change clears.
        """
        with self._lock:
            if self._pen_down or not self._annotation_active:
                return
            self._pen_down = True
            point = self._last_point
        self._publish_safely({
            "type": "ink",
            "action": "BEGIN",
            **({"x": point[0], "y": point[1]} if point else {}),
        })

    def pen_up(self) -> None:
        with self._lock:
            if not self._pen_down:
                return
            self._pen_down = False
        self._publish_safely({"type": "ink", "action": "END"})

    def clear_annotation(self) -> None:
        """Erase the ink on the current slide.

        Unconditional, and that is the point: the PowerPoint eraser refused
        whenever no slideshow was running, which is how Clear Annotation came to
        look broken. There is nothing here that can refuse.
        """
        with self._lock:
            self._pen_down = False
        self._publish_safely({"type": "ink", "action": "CLEAR"})

    # --- reporting -----------------------------------------------------------
    def capabilities(self) -> set[str]:
        """Every command, including the ones PowerPoint could only do by keystroke."""
        from computer_vision.command_mapping.gesture_mapper import ALL_COMMANDS

        return set(ALL_COMMANDS)

    def describe(self) -> dict:
        return {
            "controller": self.name,
            "capabilities": sorted(self.capabilities()),
            "surface": "VisionX presentation window",
            "presenting": self._presenting,
            # Nothing here talks to the operating system, so the UI can say so
            # rather than warning about focus, DPI scaling and the Print dialog.
            "automation": "none",
        }

    # --- internals -----------------------------------------------------------
    def _note(self, action: str, **extra) -> None:
        self._publish_safely({"type": "presentation", "action": action.upper(), **extra})

    def _publish_mode(self) -> None:
        self._publish_safely({
            "type": "mode",
            "pointerActive": self._pointer_active,
            "annotationActive": self._annotation_active,
        })

    def _publish_safely(self, event: dict) -> None:
        try:
            self._publish(event)
        except Exception:  # noqa: BLE001 - a subscriber must never break a command
            logger.debug("Presentation event subscriber raised", exc_info=True)

    def _publish_pointer_safely(self, event: dict) -> None:
        try:
            self._publish_pointer(event)
        except Exception:  # noqa: BLE001 - never break the camera loop
            logger.debug("Pointer subscriber raised", exc_info=True)
