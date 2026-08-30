"""Windows platform layer for driving Microsoft PowerPoint.

VisionX is a Windows application: the presenter runs the backend on the same
machine that shows the deck, and this module is where "the machine" is spoken to
directly instead of through a generic keystroke.

Two capabilities live here, both of which fix real bugs:

1.  **Slideshow verification.** Almost every PowerPoint shortcut VisionX sends
    means something *completely different* outside a running slideshow. The worst
    is `Ctrl+P`: in a slideshow it is the pen, on a normal PowerPoint window it is
    **Print**. Sending it blind is what opened the Print dialog. `probe()` asks
    PowerPoint itself whether a slideshow is running, so the controller can refuse
    rather than guess.

2.  **Real annotation control.** `View.PointerType` and `View.EraseDrawing()` set
    the pen and erase ink directly. No keystroke, no focus race, no Print dialog -
    and erase actually erases, which pressing `E` at the wrong moment does not.

Everything degrades cleanly. On a machine without Windows, without PowerPoint, or
without a COM binding, every call returns "unknown" and the controller falls back
to the keystrokes VisionX has always sent. Nothing here may raise into the camera
loop.
"""

from __future__ import annotations

import logging
import sys
import threading

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# --- slideshow probe outcomes -------------------------------------------------
# The distinction between DENIED and UNKNOWN is the whole point: DENIED is
# PowerPoint telling us there is no slideshow, and a shortcut must not be sent.
# UNKNOWN is "we could not ask" - on a non-Windows dev box, or with no COM
# binding installed - and there the historical behaviour is the safest one.
SLIDESHOW_CONFIRMED = "CONFIRMED"
SLIDESHOW_DENIED = "DENIED"
SLIDESHOW_UNKNOWN = "UNKNOWN"

# PowerPoint's PpSlideShowPointerType enumeration.
PP_POINTER_NONE = 0
PP_POINTER_ARROW = 1
PP_POINTER_PEN = 2
PP_POINTER_ALWAYS_HIDDEN = 3
PP_POINTER_AUTO_ARROW = 4
PP_POINTER_ERASER = 5

_PROBE_CACHE_SECONDS = 0.5   # PowerPoint is asked at most twice a second


def enable_dpi_awareness() -> bool:
    """Make this process per-monitor DPI aware.

    Without this, a Windows display scaled to 125%/150% - which is the default on
    most laptops - reports a *virtualised* screen size to PyAutoGUI. The pointer
    then lands at roughly 80% of where the presenter is pointing and drifts
    further the closer they get to the edges. Called once, before the first
    PyAutoGUI call, and harmless to call again.
    """
    if not IS_WINDOWS:
        return False
    try:
        import ctypes

        # PROCESS_PER_MONITOR_DPI_AWARE (Windows 8.1+).
        #
        # `windll` (unlike `oledll`) does NOT raise on a failing HRESULT - it
        # returns it - so the result has to be checked by hand. Treating a
        # non-zero HRESULT as success meant the reliable user32 fallback was
        # never tried and this function reported True on a machine where nothing
        # had been set: the pointer then lands ~80% of the way to the fingertip
        # on a scaled display, which is the bug it exists to prevent.
        S_OK = 0
        E_ACCESSDENIED = 0x80070005   # already set by a manifest - a success here
        E_INVALIDARG = 0x80070057
        try:
            result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
            if result in (S_OK, E_ACCESSDENIED):
                return True
            if result != E_INVALIDARG:
                logger.debug("SetProcessDpiAwareness returned 0x%08X", result & 0xFFFFFFFF)
        except Exception as exc:  # noqa: BLE001 - pre-8.1: shcore.dll is absent
            logger.debug("SetProcessDpiAwareness unavailable (%s); trying SetProcessDPIAware", exc)

        # Vista-era fallback: system-wide DPI awareness. Returns BOOL, not HRESULT.
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except Exception as exc:  # noqa: BLE001 - never fatal
        logger.debug("Could not enable DPI awareness: %s", exc)
        return False


def foreground_window_title() -> str | None:
    """Title of the window that currently has focus, or None off Windows.

    Uses ctypes against user32 rather than pywin32, so it adds no dependency.
    """
    if not IS_WINDOWS:
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        if not handle:
            return None
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read the foreground window title: %s", exc)
        return None


class PowerPointComBridge:
    """Talks to the running PowerPoint through COM, or reports that it cannot.

    Thread-affine by necessity: COM apartments are per-thread, so the bridge
    initialises COM on whichever thread first uses it and remembers that it has.
    The gesture engine owns exactly one thread, which is the thread that matters.

    Every public method is total: it returns a value or None/False, and never
    raises. A COM failure mid-talk must degrade to keystrokes, not end the talk.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled) and IS_WINDOWS
        self._lock = threading.RLock()
        # The interface pointer is THREAD-LOCAL, not shared. COM apartments are
        # per-thread: a proxy obtained on the camera thread and used from a Flask
        # request thread raises RPC_E_WRONG_THREAD, which this class would catch
        # and report as UNKNOWN - the one probe result that lets Ctrl+P through.
        # A `GET /api/engine/status` poll could therefore re-open the Print-dialog
        # path this whole module exists to close. Each thread connects for itself.
        self._local = threading.local()
        self._unavailable_reason: str | None = None if self.enabled else (
            "PowerPoint COM automation is only available on Windows."
        )
        # The probe cache is thread-local for the same reason the connection is:
        # it *describes* that connection. Sharing it means a thread that failed to
        # attach caches UNKNOWN, and a thread that did attach then reads UNKNOWN
        # instead of its own DENIED - and UNKNOWN is the one result that lets
        # Ctrl+P through. That is the Print-dialog bug, reintroduced by a cache.
        #
        # `_cache_epoch` is shared and bumped by `invalidate()`, so starting or
        # ending a slideshow still invalidates *every* thread's cache at once.
        self._cache_epoch = 0

    # --- connection ----------------------------------------------------------
    @property
    def _application(self):
        return getattr(self._local, "application", None)

    @_application.setter
    def _application(self, value) -> None:
        self._local.application = value

    @property
    def _probe_cache(self):
        """This thread's `(monotonic, epoch, result)`, or None."""
        return getattr(self._local, "probe_cache", None)

    @_probe_cache.setter
    def _probe_cache(self, value) -> None:
        self._local.probe_cache = value

    @property
    def available(self) -> bool:
        return self._application is not None or (
            self.enabled and self._unavailable_reason is None
        )

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    def _co_initialize(self) -> None:
        """Initialise COM once per thread. Safe to call repeatedly."""
        if getattr(self._local, "initialized", False):
            return
        try:
            import comtypes

            comtypes.CoInitialize()
        except Exception:  # noqa: BLE001 - pythoncom is the pywin32 equivalent
            try:
                import pythoncom  # type: ignore

                pythoncom.CoInitialize()
            except Exception as exc:  # noqa: BLE001
                logger.debug("CoInitialize failed on this thread: %s", exc)
        self._local.initialized = True

    def _connect(self):
        """Return the live PowerPoint.Application, or None.

        Deliberately `GetActiveObject`, never `CreateObject`: VisionX attaches to
        the PowerPoint the presenter already has open. Launching a second, hidden
        instance and driving *that* would look exactly like "nothing happens".
        """
        if not self.enabled:
            return None
        # No lock around the connection itself: it is thread-local, so there is
        # nothing to contend for. The lock only guards the shared fields below.
        if self._application is not None:
            return self._application

        self._co_initialize()
        errors: list[str] = []

        for loader in (self._via_comtypes, self._via_win32com):
            try:
                application = loader()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{loader.__name__}: {exc}")
                continue
            if application is not None:
                self._application = application
                with self._lock:
                    self._unavailable_reason = None
                logger.info("Connected to the running PowerPoint through COM.")
                return application

        with self._lock:
            self._unavailable_reason = (
                "Could not attach to a running PowerPoint through COM "
                f"({'; '.join(errors) if errors else 'PowerPoint is not running'})."
            )
        return None

    @staticmethod
    def _via_comtypes():
        import comtypes.client  # type: ignore

        return comtypes.client.GetActiveObject("PowerPoint.Application")

    @staticmethod
    def _via_win32com():
        import win32com.client  # type: ignore

        return win32com.client.GetActiveObject("PowerPoint.Application")

    def _forget(self, exc: Exception) -> None:
        """Drop this thread's dead connection so the next call reconnects.

        Only *this* thread's interface pointer and cache are discarded. Tearing
        down another thread's working connection because ours failed is how one
        bad status poll used to take the camera loop's COM access with it.
        """
        self._application = None
        self._local.probe_cache = None
        with self._lock:
            self._unavailable_reason = f"The PowerPoint COM connection was lost ({exc})."
        logger.debug("PowerPoint COM connection dropped: %s", exc)

    # --- slideshow -----------------------------------------------------------
    def _slideshow_view(self):
        """The active slideshow's View object, or None when nothing is running."""
        application = self._connect()
        if application is None:
            return None
        try:
            windows = application.SlideShowWindows
            if int(windows.Count) < 1:
                return None
            return windows.Item(1).View
        except Exception as exc:  # noqa: BLE001
            self._forget(exc)
            return None

    def probe(self) -> str:
        """CONFIRMED / DENIED / UNKNOWN - is a slideshow running right now?

        Cached briefly: this is called before every pen or erase command, and a
        COM round trip per camera frame would be visible in the frame rate.
        """
        import time

        if not self.enabled:
            return SLIDESHOW_UNKNOWN

        now = time.monotonic()
        with self._lock:
            epoch = self._cache_epoch
        cached = self._probe_cache
        if cached is not None and cached[1] == epoch and now - cached[0] < _PROBE_CACHE_SECONDS:
            return cached[2]

        application = self._connect()
        if application is None:
            result = SLIDESHOW_UNKNOWN
        else:
            try:
                result = (
                    SLIDESHOW_CONFIRMED
                    if int(application.SlideShowWindows.Count) >= 1
                    else SLIDESHOW_DENIED
                )
            except Exception as exc:  # noqa: BLE001
                self._forget(exc)
                result = SLIDESHOW_UNKNOWN

        self._probe_cache = (now, epoch, result)
        return result

    def invalidate(self) -> None:
        """Forget the cached probe on every thread.

        Bumping the shared epoch rather than clearing one thread's cache: a
        slideshow that just started or ended is news for all of them.
        """
        with self._lock:
            self._cache_epoch += 1
        self._local.probe_cache = None

    def current_slide(self) -> int | None:
        view = self._slideshow_view()
        if view is None:
            return None
        try:
            return int(view.CurrentShowPosition)
        except Exception as exc:  # noqa: BLE001
            self._forget(exc)
            return None

    # --- pointer / pen -------------------------------------------------------
    def set_pointer_type(self, pointer_type: int) -> bool:
        """Set the slideshow pointer directly. True when PowerPoint accepted it."""
        view = self._slideshow_view()
        if view is None:
            return False
        try:
            view.PointerType = int(pointer_type)
            return True
        except Exception as exc:  # noqa: BLE001
            self._forget(exc)
            return False

    def pointer_type(self) -> int | None:
        view = self._slideshow_view()
        if view is None:
            return None
        try:
            return int(view.PointerType)
        except Exception as exc:  # noqa: BLE001
            self._forget(exc)
            return None

    def erase_ink(self) -> bool:
        """Erase every annotation on the current slide. The real Clear Annotation.

        `View.EraseDrawing()` removes the ink whatever mode the show is in, which
        is what the presenter asked for. Pressing `E` only works while the pen is
        already selected and the slideshow window has focus.
        """
        view = self._slideshow_view()
        if view is None:
            return False
        try:
            view.EraseDrawing()
            return True
        except Exception as exc:  # noqa: BLE001
            self._forget(exc)
            return False

    # --- navigation ----------------------------------------------------------
    def next_slide(self) -> bool:
        view = self._slideshow_view()
        if view is None:
            return False
        try:
            view.Next()
            return True
        except Exception as exc:  # noqa: BLE001
            self._forget(exc)
            return False

    def previous_slide(self) -> bool:
        view = self._slideshow_view()
        if view is None:
            return False
        try:
            view.Previous()
            return True
        except Exception as exc:  # noqa: BLE001
            self._forget(exc)
            return False

    def goto_slide(self, number: int) -> bool:
        view = self._slideshow_view()
        if view is None:
            return False
        try:
            view.GotoSlide(int(number))
            return True
        except Exception as exc:  # noqa: BLE001
            self._forget(exc)
            return False

    def slide_count(self) -> int | None:
        application = self._connect()
        if application is None:
            return None
        try:
            return int(application.ActivePresentation.Slides.Count)
        except Exception as exc:  # noqa: BLE001
            self._forget(exc)
            return None

    # --- focus ---------------------------------------------------------------
    def activate(self) -> bool:
        """Bring the slideshow window to the foreground so keystrokes land on it."""
        application = self._connect()
        if application is None:
            return False
        try:
            windows = application.SlideShowWindows
            if int(windows.Count) < 1:
                return False
            windows.Item(1).Activate()
            return True
        except Exception as exc:  # noqa: BLE001
            self._forget(exc)
            return False

    def describe(self) -> dict:
        return {
            "platform": sys.platform,
            "windows": IS_WINDOWS,
            "comEnabled": self.enabled,
            "comConnected": self._application is not None,   # on this thread
            "slideshow": self.probe(),
            "reason": self._unavailable_reason,
        }
