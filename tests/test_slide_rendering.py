"""Slide rendering: what the audience actually sees.

VisionX now displays the presenter's deck itself, so the render path is on the
critical path for the presentation rather than being a nicety for library cards.
Two things therefore have to be true, and neither was before:

  * a slide is rendered at presentation resolution, not enlarged from a 1.6x
    library thumbnail, and
  * a .pptx converts on a machine without Microsoft Office installed, or the
    presentation window would have nothing to show at all.
"""

from pathlib import Path

import pytest


@pytest.fixture
def deck(tmp_path) -> Path:
    """A three-page PDF standing in for a converted deck."""
    fitz = pytest.importorskip("fitz")

    path = tmp_path / "deck.pdf"
    document = fitz.open()
    for index in range(3):
        page = document.new_page(width=960, height=540)      # 16:9
        page.insert_text((72, 144), f"VisionX slide {index + 1}", fontsize=28)
    document.save(str(path))
    document.close()
    return path


@pytest.fixture
def render_dir(tmp_path, monkeypatch):
    """Point the render cache at a temp directory, never the user's uploads."""
    from config.settings import settings

    slides = tmp_path / "slides"
    thumbs = tmp_path / "thumbnails"
    monkeypatch.setattr(settings, "SLIDE_DIR", slides)
    monkeypatch.setattr(settings, "THUMBNAIL_DIR", thumbs)
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    return slides


def size_of(path: Path) -> tuple[int, int]:
    fitz = pytest.importorskip("fitz")
    pixmap = fitz.Pixmap(str(path))
    return pixmap.width, pixmap.height


# ================================================= RESOLUTION ================
def test_a_slide_renders_at_the_requested_width(deck, render_dir):
    from utils.files import render_slide

    path = render_slide(deck, ".pdf", "deck.pdf", 1, 1920)
    assert path is not None and path.exists()
    width, height = size_of(path)
    assert width == pytest.approx(1920, abs=2)
    # And keeps the deck's own aspect ratio - a stretched slide is worse than a
    # small one.
    assert width / height == pytest.approx(960 / 540, rel=0.01)


def test_the_render_is_far_larger_than_the_library_thumbnail(deck, render_dir):
    """The reason this path exists at all.

    Thumbnails render at 1.6x, which is a 1536px-wide image for this deck - fine
    on a card, soft on a projector. Scaling one up is what would make a deck look
    blurry to an audience.
    """
    from utils.files import generate_thumbnails, render_slide

    thumbnails = generate_thumbnails(deck, ".pdf", "deck.pdf")
    assert thumbnails, "the thumbnail path is still expected to work"
    thumbnail_width, _ = size_of(render_dir.parent / "thumbnails" / thumbnails[0])

    rendered_width, _ = size_of(render_slide(deck, ".pdf", "deck.pdf", 1, 2560))
    assert rendered_width > thumbnail_width * 1.5


def test_a_render_is_never_upscaled_past_the_ceiling(deck, render_dir):
    from config.settings import settings
    from utils.files import render_slide

    path = render_slide(deck, ".pdf", "deck.pdf", 1, 99_999)
    width, _ = size_of(path)
    assert width <= settings.SLIDE_RENDER_MAX_WIDTH


# ================================================= CACHING ==================
def test_the_same_slide_and_width_is_rendered_once(deck, render_dir):
    """A prefetching presentation window asks for the same slide repeatedly."""
    from utils.files import render_slide

    first = render_slide(deck, ".pdf", "deck.pdf", 2, 1280)
    stamp = first.stat().st_mtime_ns
    second = render_slide(deck, ".pdf", "deck.pdf", 2, 1280)

    assert second == first
    assert second.stat().st_mtime_ns == stamp, "the slide was re-rendered on a cache hit"


def test_different_widths_are_cached_separately(deck, render_dir):
    from utils.files import render_slide

    small = render_slide(deck, ".pdf", "deck.pdf", 1, 1280)
    large = render_slide(deck, ".pdf", "deck.pdf", 1, 2560)
    assert small != large
    assert size_of(small)[0] < size_of(large)[0]


def test_deleting_a_presentation_removes_its_renders(deck, render_dir):
    """A deleted deck must not stay readable on disk as a pile of PNGs."""
    from utils.files import delete_files, render_slide

    for slide in (1, 2, 3):
        render_slide(deck, ".pdf", "deck.pdf", slide, 1280)
    assert list(render_dir.glob("deck_*.png"))

    delete_files("deck.pdf", [])
    assert list(render_dir.glob("deck_*.png")) == []


# ================================================= BOUNDS ===================
def test_a_slide_past_the_end_of_the_deck_is_not_rendered(deck, render_dir):
    from utils.files import render_slide

    assert render_slide(deck, ".pdf", "deck.pdf", 4, 1280) is None
    assert render_slide(deck, ".pdf", "deck.pdf", 0, 1280) is None


def test_an_unrenderable_file_reports_rather_than_raises(tmp_path, render_dir):
    """A corrupt upload must not take down the presentation window with a 500."""
    from utils.files import render_slide

    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is not a PDF")
    assert render_slide(broken, ".pdf", "broken.pdf", 1, 1280) is None


def _fake_converters(monkeypatch, soffice_works: bool = True,
                     powerpoint_works: bool = True) -> list[str]:
    """Replace both converters with recorders. Returns the call log."""
    from utils import files

    calls: list[str] = []

    def make(name: str, works: bool):
        def convert(path, target):
            calls.append(name)
            if not works:
                return None
            target.write_bytes(b"%PDF-1.4\n")
            return target
        return convert

    monkeypatch.setattr(files, "_convert_via_soffice", make("soffice", soffice_works))
    monkeypatch.setattr(files, "_convert_via_powerpoint", make("powerpoint", powerpoint_works))
    return calls


def test_libreoffice_is_tried_before_powerpoint(tmp_path, monkeypatch):
    """The order is the requirement, not an implementation detail.

    VisionX presents the deck itself, so Microsoft PowerPoint must never be
    *required* to get a .pptx on screen - and on a machine that has both
    installed, it must not be launched either. LibreOffice goes first and
    PowerPoint is never reached.
    """
    from config.settings import settings
    from utils import files

    monkeypatch.setattr(settings, "PPTX_CONVERTER", "auto")
    calls = _fake_converters(monkeypatch)

    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"not really a pptx")
    result = files.pdf_source(deck, ".pptx")

    assert calls == ["soffice"], "PowerPoint was involved when LibreOffice could convert"
    assert result == deck.with_suffix(".pdf")


def test_powerpoint_is_a_fallback_when_libreoffice_is_absent(tmp_path, monkeypatch):
    """Kept, but only for a machine that has Office and not LibreOffice."""
    from config.settings import settings
    from utils import files

    monkeypatch.setattr(settings, "PPTX_CONVERTER", "auto")
    calls = _fake_converters(monkeypatch, soffice_works=False)

    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"not really a pptx")
    result = files.pdf_source(deck, ".pptx")

    assert calls == ["soffice", "powerpoint"]
    assert result == deck.with_suffix(".pdf")


def test_the_converter_can_be_pinned_to_libreoffice(tmp_path, monkeypatch):
    """`VISIONX_PPTX_CONVERTER=libreoffice` forbids PowerPoint outright.

    For a deployment that wants the guarantee enforced rather than merely
    preferred - PowerPoint is not tried even if LibreOffice fails.
    """
    from config.settings import settings
    from utils import files

    monkeypatch.setattr(settings, "PPTX_CONVERTER", "libreoffice")
    calls = _fake_converters(monkeypatch, soffice_works=False)

    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"not really a pptx")

    assert files.pdf_source(deck, ".pptx") is None
    assert calls == ["soffice"], "PowerPoint was tried despite being disabled"


def test_a_pdf_upload_involves_no_converter_at_all(tmp_path, monkeypatch):
    """The simplest PowerPoint-free path: a PDF is already renderable."""
    from utils import files

    calls = _fake_converters(monkeypatch)
    deck = tmp_path / "deck.pdf"
    deck.write_bytes(b"%PDF-1.4\n")

    assert files.pdf_source(deck, ".pdf") == deck
    assert calls == []


def test_a_converted_pdf_is_reused_rather_than_reconverted(tmp_path, monkeypatch):
    """Conversion is the expensive step; it must happen once per upload."""
    from utils import files

    conversions: list[str] = []

    def fake_soffice(path, target):
        conversions.append(path.name)
        target.write_bytes(b"%PDF-1.4\n")
        return target

    monkeypatch.setattr(files, "_convert_via_powerpoint", lambda path, target: None)
    monkeypatch.setattr(files, "_convert_via_soffice", fake_soffice)

    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"not really a pptx")
    files.pdf_source(deck, ".pptx")
    files.pdf_source(deck, ".pptx")
    files.pdf_source(deck, ".pptx")

    assert conversions == ["deck.pptx"]


# ================================= LIBREOFFICE CONVERSION ===================
def test_the_profile_uri_is_valid_on_both_platforms():
    """`-env:UserInstallation` must be a real `file:///` URI.

    This is the one part of the LibreOffice command line that differs between
    Windows and POSIX, and it cannot be exercised on the other platform. Getting
    it wrong does not fail loudly: LibreOffice ignores the malformed profile,
    falls back to the presenter's own, and a second invocation then exits without
    converting anything - which surfaces as "some uploads have no slides".
    """
    from utils.files import profile_uri

    # A directory that really exists, so `resolve()` has nothing to invent. It is
    # deliberately not a hard-coded string: `resolve()` follows symlinks, and on
    # macOS /var is a link to /private/var - an assertion on the literal path
    # would be testing the platform's filesystem layout rather than this code.
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        profile = Path(directory) / ".soffice-profile"
        posix = profile_uri(profile)

    assert posix.startswith("file:///"), posix
    assert not posix.startswith("file:////"), "a POSIX path produced four slashes"
    assert posix.endswith("/.soffice-profile"), posix
    # An absolute path, which is what LibreOffice requires.
    assert Path(posix[len("file://"):]).is_absolute()

    # The Windows shape, without needing Windows: `as_posix()` on a drive-letter
    # path yields "C:/..." with no leading slash, which is why the helper adds
    # exactly three rather than stripping and hoping.
    class FakeWindowsPath:
        def resolve(self):
            return self

        def as_posix(self):
            return "C:/Users/pres/visionx/uploads/.soffice-profile"

    windows = profile_uri(FakeWindowsPath())
    assert windows == "file:///C:/Users/pres/visionx/uploads/.soffice-profile"


def test_the_soffice_command_is_headless_and_isolated(tmp_path):
    """Every flag on the command line earns its place."""
    from utils.files import soffice_command

    deck = tmp_path / "deck.pptx"
    command = soffice_command("/usr/bin/soffice", deck)

    assert command[0] == "/usr/bin/soffice"
    # Headless and non-interactive: this runs on a presenter's machine and must
    # never flash a window or a recovery dialog.
    for flag in ("--headless", "--norestore", "--invisible", "--nolockcheck"):
        assert flag in command
    # A private profile, so a LibreOffice the presenter already has open does not
    # make this invocation exit without converting anything.
    profile = next(a for a in command if a.startswith("-env:UserInstallation="))
    assert profile.endswith(".soffice-profile")
    # --outdir, not a target filename: soffice names its output after the input.
    assert command[command.index("--outdir") + 1] == str(deck.parent)
    assert command[-1] == str(deck)
    assert "--convert-to" in command and command[command.index("--convert-to") + 1] == "pdf"


def test_a_real_pptx_converts_and_renders_without_powerpoint(tmp_path, render_dir):
    """End to end on a machine with LibreOffice and no Microsoft Office.

    Skipped where LibreOffice is not installed - including this repository's
    development machine - so it is a real check on the target platform rather
    than a silent pass everywhere. `_convert_via_powerpoint` is disabled outright
    for the duration, so a pass cannot come from Office.
    """
    import subprocess

    from config.settings import settings
    from utils import files

    pptx = pytest.importorskip("pptx")
    if files._soffice_executable() is None:
        pytest.skip("LibreOffice is not installed on this machine")

    deck_path = tmp_path / "deck.pptx"
    presentation = pptx.Presentation()
    for index in range(3):
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = f"VisionX slide {index + 1}"
    presentation.save(str(deck_path))

    # PowerPoint is not merely unpreferred here, it is forbidden.
    settings.PPTX_CONVERTER = "libreoffice"
    launched: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(command, *args, **kwargs):
        launched.append(list(command))
        return real_run(command, *args, **kwargs)

    subprocess.run = recording_run
    try:
        rendered = files.render_slide(deck_path, ".pptx", "deck.pptx", 2, 1280)
    finally:
        subprocess.run = real_run
        settings.PPTX_CONVERTER = "auto"

    assert rendered is not None and rendered.exists(), "the deck did not render"
    assert size_of(rendered)[0] == pytest.approx(1280, abs=2)
    # Exactly one external process, and it was LibreOffice.
    assert len(launched) == 1
    assert "soffice" in launched[0][0].lower() or "libreoffice" in launched[0][0].lower()


# ============================= STAGING FILES (regressions) ==================
def test_a_failed_render_leaves_no_staging_file(deck, render_dir, monkeypatch):
    """A render that dies partway must not leave a `.part` orphan behind.

    Nothing else would ever clean it up: `delete_files` globbed only `*.png`, and
    the cache check never matches a staging name - so every failed render leaked
    a file for the life of the deployment.
    """
    from utils.files import render_slide

    fitz = pytest.importorskip("fitz")
    original_save = fitz.Pixmap.save

    def exploding_save(self, *args, **kwargs):
        original_save(self, *args, **kwargs)   # create the staging file...
        raise RuntimeError("disk went away")   # ...then fail

    monkeypatch.setattr(fitz.Pixmap, "save", exploding_save)

    assert render_slide(deck, ".pdf", "deck.pdf", 1, 1280) is None
    assert list(render_dir.glob("*.part")) == [], "a staging file was left behind"


def test_concurrent_renders_of_one_slide_do_not_corrupt_each_other(deck, render_dir):
    """Two windows asking for the same slide at once.

    A single shared staging filename meant both wrote to it and one moved a
    truncated file into place as the cached render - a permanently broken slide,
    served from cache forever after.
    """
    import threading

    from utils.files import render_slide

    results: list = []
    barrier = threading.Barrier(4)

    def render():
        barrier.wait()
        results.append(render_slide(deck, ".pdf", "deck.pdf", 1, 1280))

    threads = [threading.Thread(target=render) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(r is not None for r in results)
    assert len({str(r) for r in results}) == 1, "they disagreed about the cache path"
    # The file that survived is a whole, readable PNG.
    assert size_of(results[0])[0] == pytest.approx(1280, abs=2)
    assert list(render_dir.glob("*.part")) == []


def test_deleting_a_presentation_also_sweeps_staging_files(deck, render_dir):
    from utils.files import delete_files, render_slide

    render_slide(deck, ".pdf", "deck.pdf", 1, 1280)
    orphan = render_dir / "deck_1_1280.abcd1234.part"
    orphan.write_bytes(b"leftover")

    delete_files("deck.pdf", [])
    assert list(render_dir.glob("deck_*")) == []
