"""Safe upload handling: validation, server-generated names, slide metadata."""

import logging
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

    PDFs render directly through PyMuPDF. PPTX files are converted to PDF first via
    the locally installed PowerPoint (Windows COM); when PowerPoint is unavailable the
    presentation still works end to end, it simply has no in-app previews.
    """
    settings.ensure_dirs()
    pdf_path = path

    if ext in (".ppt", ".pptx"):
        pdf_path = _convert_to_pdf(path)
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


def _convert_to_pdf(path: Path) -> Path | None:
    """Convert a PowerPoint file to PDF using the installed PowerPoint application."""
    try:
        import comtypes.client  # type: ignore
    except ImportError:
        logger.info("comtypes not installed - skipping PPTX thumbnail rendering.")
        return None

    target = path.with_suffix(".pdf")
    powerpoint = None
    try:
        comtypes.CoInitialize()
        powerpoint = comtypes.client.CreateObject("Powerpoint.Application")
        deck = powerpoint.Presentations.Open(str(path), WithWindow=False)
        deck.SaveAs(str(target), 32)  # 32 = ppSaveAsPDF
        deck.Close()
        return target
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


def delete_files(stored_name: str, thumbnails: list[str]) -> None:
    (settings.UPLOAD_DIR / stored_name).unlink(missing_ok=True)
    (settings.UPLOAD_DIR / Path(stored_name).with_suffix(".pdf").name).unlink(missing_ok=True)
    for thumb in thumbnails or []:
        (settings.THUMBNAIL_DIR / thumb).unlink(missing_ok=True)
