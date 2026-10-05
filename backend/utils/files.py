"""Safe upload handling: validation, server-generated names, slide rendering.

The web presentation experience renders the presenter's own PPTX rather than
asking PowerPoint to display it, so this module is now on the critical path for
what the audience actually sees. Fidelity therefore matters:

    .pptx  --PowerPoint COM or LibreOffice-->  PDF  --PyMuPDF-->  PNG per slide
    .pdf   ------------------------------------------>  PNG per slide

Going through PDF is deliberate. PowerPoint (or LibreOffice) does the layout with
the real fonts, master slides, themes and embedded media, so what VisionX shows
is what the deck actually looks like - not a re-implementation of PowerPoint's
renderer in a browser, which is where every client-side .pptx library loses.
"""

import logging
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from werkzeug.datastructures import FileStorage

from config.settings import settings
from utils.errors import ValidationError

logger = logging.getLogger(__name__)


def validate_upload(file: FileStorage) -> str:
    """Validate extension + MIME type. Returns the lowercase extension."""
    if file is None or not file.filename:
        raise ValidationError("No file was uploaded.")

    ext = Path(file.filename).suffix.lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(settings.ALLOWED_EXTENSIONS))
        raise ValidationError(f"Unsupported file type '{ext or 'unknown'}'. Allowed: {allowed}")

    mime = (file.mimetype or "").lower()
    if mime and mime not in settings.ALLOWED_MIME_TYPES:
        raise ValidationError(f"Unsupported file content type '{mime}'.")

    return ext


def store_upload(file: FileStorage, ext: str) -> tuple[str, Path]:
    """Write the upload under a server-generated name. No client path ever touches disk."""
    settings.ensure_dirs()
    stored_name = f"{uuid.uuid4().hex}{ext}"
    destination = (settings.UPLOAD_DIR / stored_name).resolve()

    # Defence in depth: the resolved path must stay inside UPLOAD_DIR.
    if settings.UPLOAD_DIR.resolve() not in destination.parents:
        raise ValidationError("Invalid upload destination.")

    file.save(destination)
    if destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise ValidationError("The uploaded file is empty.")
    return stored_name, destination


def count_slides(path: Path, ext: str) -> int:
    """Read the real slide/page count from the file. Returns 0 when it cannot be read."""
    try:
        if ext == ".pdf":
            from pypdf import PdfReader

            return len(PdfReader(str(path)).pages)
        if ext == ".pptx":
            from pptx import Presentation as PptxPresentation

            return len(PptxPresentation(str(path)).slides)
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort, never fatal
        logger.warning("Could not read slide count for %s: %s", path.name, exc)
    return 0


def generate_thumbnails(path: Path, ext: str, stored_name: str, limit: int = 60) -> list[str]:
    """Render slide thumbnails when the format allows it.

    PDFs render directly through PyMuPDF. PPTX files are converted to PDF first,
    by the locally installed PowerPoint (Windows COM) or by LibreOffice.

    These are library previews. The presentation window does NOT use them - it
    asks for a full-resolution render via `render_slide` - because a 1.6x preview
    on a projector is exactly the blurry deck this change exists to avoid.
    """
    settings.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_source(path, ext)
    if pdf_path is None:
        return []

    try:
        import fitz  # PyMuPDF

        names: list[str] = []
        with fitz.open(str(pdf_path)) as doc:
            for index, page in enumerate(doc):
                if index >= limit:
                    break
                pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6))
                name = f"{Path(stored_name).stem}_{index + 1}.png"
                pix.save(str(settings.THUMBNAIL_DIR / name))
                names.append(name)
        return names
    except Exception as exc:  # noqa: BLE001
        logger.warning("Thumbnail generation failed for %s: %s", path.name, exc)
        return []


# Conversion is serialised: PowerPoint COM is a single application instance and
# two threads asking it to open a deck at once is how it ends up wedged. Two
# concurrent uploads convert one after the other instead of racing.
_convert_lock = threading.Lock()


def _convert_to_pdf(path: Path) -> Path | None:
    """Convert a PowerPoint file to PDF. **LibreOffice first.**

    The order is the point, not an accident. VisionX presents the deck itself, so
    Microsoft PowerPoint must never be *required* to get a `.pptx` on screen -
    and on a machine that has both, it must not be launched either. Headless
    LibreOffice is therefore the primary converter and PowerPoint COM is an
    optional legacy fallback for a machine that has Office but not LibreOffice.

    Both do the layout with the deck's real fonts, masters and themes, which is
    what makes the rendered slides faithful to the presenter's file rather than a
    re-implementation of it.

    Selectable with `VISIONX_PPTX_CONVERTER`; see config.settings.
    """
    target = path.with_suffix(".pdf")
    if target.exists() and target.stat().st_size > 0:
        return target

    with _convert_lock:
        # Re-checked inside the lock: the thread we queued behind may have been
        # converting this very file.
        if target.exists() and target.stat().st_size > 0:
            return target

        choice = settings.PPTX_CONVERTER
        if choice == "libreoffice":
            converters = (_convert_via_soffice,)
        elif choice == "powerpoint":
            converters = (_convert_via_powerpoint,)
        else:
            converters = (_convert_via_soffice, _convert_via_powerpoint)

        for convert in converters:
            result = convert(path, target)
            if result is not None:
                return result

        logger.warning(
            "Could not convert '%s' to PDF with converter policy '%s'. Install "
            "LibreOffice (or set VISIONX_SOFFICE_PATH) so VisionX can render this "
            "deck - no Microsoft Office is required.", path.name, choice,
        )
        return None


def _convert_via_powerpoint(path: Path, target: Path) -> Path | None:
    """Legacy fallback: ask the installed PowerPoint to export a PDF.

    Tried only after LibreOffice, and only on Windows. This is the one place in
    VisionX where a `.pptx` can still involve Microsoft Office, it runs at most
    once per upload, and it is never reached during a presentation - by then the
    deck is already a set of PNGs on disk.
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        import comtypes.client  # type: ignore
    except ImportError:
        logger.info("comtypes not installed - PowerPoint conversion unavailable.")
        return None

    powerpoint = None
    try:
        comtypes.CoInitialize()
        powerpoint = comtypes.client.CreateObject("Powerpoint.Application")
        deck = powerpoint.Presentations.Open(str(path), WithWindow=False)
        deck.SaveAs(str(target), 32)  # 32 = ppSaveAsPDF
        deck.Close()
        return target if target.exists() else None
    except Exception as exc:  # noqa: BLE001
        logger.info("PowerPoint conversion unavailable (%s).", exc)
        return None
    finally:
        try:
            if powerpoint is not None:
                powerpoint.Quit()
            comtypes.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


# Where LibreOffice was found, cached. `/api/health` reports converter readiness
# and is polled, and the lookup below walks PATH and stats several candidate
# paths - cheap once, wasteful on every poll. Installing LibreOffice therefore
# needs a backend restart to be picked up, which is the same contract the voice
# and gesture models already have.
_soffice_cache: tuple[bool, str | None] = (False, None)
_soffice_cache_lock = threading.Lock()


def _soffice_executable() -> str | None:
    """Find LibreOffice. Configured path first, then PATH, then the usual installs."""
    global _soffice_cache
    cached, value = _soffice_cache
    if cached:
        return value

    with _soffice_cache_lock:
        cached, value = _soffice_cache
        if cached:
            return value
        value = _find_soffice()
        _soffice_cache = (True, value)
        return value


def reset_soffice_cache() -> None:
    """Forget where LibreOffice was found. For tests, and after an install."""
    global _soffice_cache
    with _soffice_cache_lock:
        _soffice_cache = (False, None)


def _find_soffice() -> str | None:
    configured = settings.SOFFICE_PATH
    if configured and Path(configured).exists():
        return configured

    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found

    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/soffice",
    ]
    return next((c for c in candidates if Path(c).exists()), None)


def profile_uri(directory: Path) -> str:
    """A `file://` URI for LibreOffice's `-env:UserInstallation`.

    Split out and tested because it is the one piece of this that differs between
    Windows and POSIX and cannot be exercised on the other one:

        POSIX    /var/uploads/.soffice-profile  ->  file:///var/uploads/.soffice-profile
        Windows  C:\\uploads\\.soffice-profile   ->  file:///C:/uploads/.soffice-profile

    `as_posix()` gives `C:/uploads/...` on Windows and `/var/uploads/...` on
    POSIX; stripping the leading slash and re-adding exactly three normalises
    both to a valid `file:///` URI, and getting that wrong makes LibreOffice
    silently ignore the profile and convert nothing.
    """
    return "file:///" + directory.resolve().as_posix().lstrip("/")


def soffice_command(executable: str, path: Path) -> list[str]:
    """The exact command line used to convert one deck.

    A private user profile, not the presenter's own: a second soffice sharing the
    default profile exits immediately without converting anything, and on Windows
    it can attach to a LibreOffice the presenter already has open and convert
    nothing at all.

    `--outdir`, not a target filename: soffice always names its output after the
    input, so the PDF lands beside the upload as `<stored_name>.pdf`.
    """
    return [
        executable,
        f"-env:UserInstallation={profile_uri(settings.UPLOAD_DIR / '.soffice-profile')}",
        "--headless", "--norestore", "--invisible", "--nolockcheck",
        "--convert-to", "pdf",
        "--outdir", str(path.parent),
        str(path),
    ]


def _convert_via_soffice(path: Path, target: Path) -> Path | None:
    """The primary converter: headless LibreOffice. No Microsoft Office involved.

    This is what makes the web presentation mode PowerPoint-independent. It runs
    `soffice` as a subprocess with its own user profile, converts the deck to PDF
    once, and is never touched again - the presentation itself reads PNGs.
    """
    executable = _soffice_executable()
    if not executable:
        logger.info(
            "LibreOffice was not found, so '%s' cannot be converted by the primary "
            "path. Install it, or set VISIONX_SOFFICE_PATH.", path.name,
        )
        return None

    try:
        subprocess.run(
            soffice_command(executable, path), check=True, capture_output=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        logger.warning("LibreOffice timed out converting %s.", path.name)
        return None
    except Exception as exc:  # noqa: BLE001 - a failed conversion is not fatal
        logger.warning("LibreOffice could not convert %s: %s", path.name, exc)
        return None

    return target if target.exists() and target.stat().st_size > 0 else None


def pdf_source(path: Path, ext: str) -> Path | None:
    """The PDF to render this presentation from, converting once and caching it."""
    if ext == ".pdf":
        return path if path.exists() else None
    if ext in (".ppt", ".pptx"):
        return _convert_to_pdf(path)
    return None


def render_slide(path: Path, ext: str, stored_name: str, index: int, width: int) -> Path | None:
    """Render one slide at presentation resolution, cached on disk.

    Rendered on demand rather than at upload time: a 60-slide deck rendered at
    1920px up front is tens of megabytes and several seconds of the presenter's
    time, almost all of it wasted on slides they may never reach. The
    presentation window prefetches the neighbours of the current slide instead,
    so the render happens while the previous slide is still on screen.
    """
    width = max(320, min(int(width), settings.SLIDE_RENDER_MAX_WIDTH))
    cached = settings.SLIDE_DIR / f"{Path(stored_name).stem}_{index}_{width}.png"
    # The directory this actually writes to, rather than `ensure_dirs()`: that is
    # a classmethod over the class attributes, so it creates the *configured*
    # directories even when this call was pointed somewhere else.
    cached.parent.mkdir(parents=True, exist_ok=True)
    if cached.exists() and cached.stat().st_size > 0:
        return cached

    source = pdf_source(path, ext)
    if source is None:
        return None

    # A per-render temporary name. The PNG is written here and then moved into
    # place, because a half-written file served to the presentation window shows
    # as a broken slide - and the cache check above would then keep serving it.
    # `uuid` in the name so two concurrent requests for the same slide cannot
    # write to one staging file and truncate each other's output.
    staging = cached.with_name(f"{cached.stem}.{uuid.uuid4().hex[:8]}.part")
    try:
        import fitz  # PyMuPDF

        with fitz.open(str(source)) as doc:
            if index < 1 or index > doc.page_count:
                return None
            page = doc[index - 1]
            # Scale from the page's own width, so every slide comes out at
            # exactly the requested pixel width whatever the deck's aspect ratio.
            #
            # Rendering above 1:1 with the PDF's points is correct and not
            # "upscaling": the page is vector, so a larger matrix resamples the
            # source rather than enlarging pixels. `width` is already clamped to
            # SLIDE_RENDER_MAX_WIDTH above, which is what bounds the cost.
            scale = width / page.rect.width
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            # `output` is explicit because PyMuPDF otherwise infers the format
            # from the extension, and the staging name deliberately is not .png.
            pixmap.save(str(staging), output="png")
            staging.replace(cached)
        return cached
    except Exception as exc:  # noqa: BLE001 - a failed render is reported, not fatal
        logger.warning("Could not render slide %s of %s: %s", index, path.name, exc)
        return None
    finally:
        # A render that failed after creating the staging file would otherwise
        # leave it behind forever: `delete_files` only globs *.png, and the cache
        # check never matches it, so nothing else would ever clean it up.
        staging.unlink(missing_ok=True)


def delete_files(stored_name: str, thumbnails: list[str]) -> None:
    (settings.UPLOAD_DIR / stored_name).unlink(missing_ok=True)
    (settings.UPLOAD_DIR / Path(stored_name).with_suffix(".pdf").name).unlink(missing_ok=True)
    for thumb in thumbnails or []:
        (settings.THUMBNAIL_DIR / thumb).unlink(missing_ok=True)
    # Rendered slides are a cache keyed by slide number and width, so there is no
    # list of them on the document. Deleting the presentation must still remove
    # them, or the deck stays readable on disk after the user deleted it.
    stem = Path(stored_name).stem
    if stem:
        for pattern in (f"{stem}_*.png", f"{stem}_*.part"):
            for render in settings.SLIDE_DIR.glob(pattern):
                render.unlink(missing_ok=True)
