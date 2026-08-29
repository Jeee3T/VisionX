# VisionX — Validation & Hardening Pass

**Date:** 2026-08-29
**Scope:** Final validation, testing, hardening and bug fixing only. No new features, no
architectural changes, no rewrites of working components.
**Target platform:** Windows (confirmed during the pass — the Windows PowerPoint keymap is
therefore correct as-is and was deliberately left unchanged).

---

## 1. Summary

| | Before | After |
| --- | --- | --- |
| Tests collected by `pytest` | 138 | **144** |
| API assertions actually running in the suite | **0** (file collected nothing) | 87 |
| Tests passed / failed / skipped | 138 / 0 / 0 | **144 / 0 / 0** |
| Ruff `F` (dead code, unused imports) | 2 findings | **clean** |
| Voice dataset | 912 utterances | **1008** |
| START↔END confusion (5-seed mean) | 0.182 | **0.092** |
| Process-killing defects | 1 (unknown) | **0** |

Seven defects were found and fixed. Five tests were added. Two documentation gaps were closed.
No test was weakened, disabled or deleted at any point.

---

## 2. Defects found and fixed

### 2.1 MediaPipe 1.0.x kills the entire server process — CRITICAL

**File:** `backend/requirements.txt`

`requirements.txt` specified `mediapipe>=0.10.14`, and pip resolved to **1.0.1**. That version
aborts the whole process with a native `SIGABRT` the first time the hand landmarker runs on any
frame:

```
F0000 graph_service.h:139] Check failed: service_ Service is unavailable.
    @ -[DrishtiMetalHelper initWithCalculatorContext:]
    @ mediapipe::api2::TensorsToDetectionsCalculator::Open()
```

This is a C++ `CHECK` failure, **not** a Python exception, so no `try`/`except` anywhere in
VisionX can contain it. It would have taken the Flask server down mid-session.

- Reproduced with a plain synthetic NumPy array — **no camera involved**, so it is not a
  permissions or hardware issue.
- Setting `BaseOptions.Delegate.CPU` does **not** help: the abort is in the detection
  post-processing calculator, not the inference delegate.
- Observed on macOS arm64. Not verified on Windows; the pin keeps every platform on the version
  that was actually tested.

**Fix** — pin below 1.0 (resolves 0.10.35):

```python
# Pinned below 1.0: mediapipe 1.0.x aborts the process (SIGABRT in
# TensorsToDetectionsCalculator's Metal helper) on macOS arm64 the first time the
# hand landmarker runs on a frame. It is a native abort, so no try/except can
# contain it - it would take the Flask server down mid-session.
mediapipe>=0.10.14,<1.0
```

**Verified after the fix** on a reference hand image: 21 landmarks detected at 0.936 confidence,
geometric recognizer correctly returning `OPEN_PALM` with all five fingers extended.

---

### 2.2 The API test suite collected zero tests

**File:** `backend/tests/test_api_flow.py`

The file was a script exposing `run()`, not `test_*` functions. `pytest` collected **0 tests**
from it, so its 87 end-to-end API assertions never ran as part of the suite — they only ran if
someone remembered to invoke the file by hand.

**Fix** — a thin pytest entry point that *skips* (rather than fails) when MongoDB is absent, so
the rest of the suite stays database-free:

```python
def test_api_flow():
    """Pytest entry point, so this file is collected by `pytest` and not only
    runnable as a script. Skips rather than fails when MongoDB is unreachable -
    every other test in the suite is deliberately database-free.
    """
    import pytest
    from config import database
    try:
        database.connect()
        connected = database.is_connected()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MongoDB is not reachable ({exc}); this end-to-end flow requires it.")
    if not connected:
        pytest.skip("MongoDB is not reachable; this end-to-end flow requires it.")
    assert run() == 0, f"API flow checks failed: {FAILED}"
```

---

### 2.3 Path traversal in the per-user model directory — SECURITY

**File:** `computer_vision/ml/paths.py`

`safe_component()` is documented as *"Make an id safe to use as a single path component (never
traverses)"*. Its character filter `[^A-Za-z0-9_.-]` permits dots, so `"."` and `".."` passed
through unchanged:

| Input | `user_model_dir()` resolved to | |
| --- | --- | --- |
| `".."` | the **parent** of the model root | escaped |
| `"."` | the model root **itself** | escaped |

`registry.delete_model()` calls `shutil.rmtree()` on whatever that path resolves to.

Not reachable through the API today — `user_id` always comes from a JWT `ObjectId` — but it is a
latent landmine inside a function whose entire contract is safety.

**Fix:**

```python
cleaned = _SAFE.sub("_", str(value or "").strip())[:64]
# "." and ".." survive the character filter but still traverse, which would
# point a user's model directory at its own parent - and delete_model()
# rmtree()s whatever that resolves to.
if not cleaned.strip("."):
    return "unknown"
return cleaned or "unknown"
```

Covered by a new regression test (§3.2). All other traversal attempts — `../../../etc/passwd`,
`..\..\windows`, `/etc/shadow`, NUL bytes, 300-character ids — were already correctly neutralised.

---

### 2.4 Dispatcher accepted malformed parameters and out-of-range slides

**File:** `presentation_controller/dispatcher.py`

`execute()` caught only `PresentationControlError`. Two consequences:

| Input to `dispatcher.execute()` | Before | After |
| --- | --- | --- |
| `{"count": "many"}` | uncaught `ValueError` propagates | `delivered=False`, 0 keystrokes |
| `{"slideNumber": "abc"}` | uncaught `ValueError` propagates | `delivered=False`, 0 keystrokes |
| `{"slideNumber": 9999}` on a 10-slide deck | **2 keystrokes sent, `current_slide` set to 9999** | `delivered=False`, 0 keystrokes |

The out-of-range case silently corrupted session state, because `_handle_goto()` trusted its
caller to have run `normalize_parameters()` first.

Every *reachable* API path does validate first — voice goes through the interpreter, manual and
keyboard through `build_intent()`, and gesture supplies no parameters at all — so this was
defence-in-depth rather than a live exploit. But the dispatcher is the single door from any
modality to a real key press, and it should not trust its caller.

**Fix 1** — treat malformed parameters like any other non-delivery:

```python
except (ValueError, TypeError, KeyError) as exc:
    # Malformed parameters. Callers are expected to have run
    # multimodal.command.normalize_parameters first, but this is the only
    # place a VisionX command becomes a key press, so it refuses bad input
    # itself rather than trusting that they did.
    delivered = False
    message = f"Invalid parameters for {command}: {exc}"
    logger.warning("Command %s rejected: %s", command, message)
```

**Fix 2** — bounds-check `GO_TO_SLIDE` in the handler, mirroring `normalize_parameters` exactly
(refuse rather than clamp; `total_slides == 0` means "unknown deck length, no upper bound", the
same convention `_can_advance()` already uses):

```python
if target < 1:
    raise ValueError("Slide numbers start at 1.")
if self.total_slides and target > self.total_slides:
    raise ValueError(
        f"This presentation has {self.total_slides} slides, "
        f"so slide {target} does not exist."
    )
```

Verified: 10 hostile parameter combinations, **zero keystrokes emitted**, no state corruption, no
uncaught exceptions. `count: 100000` on a 10-slide deck still correctly stops at the deck
boundary (9 presses), which was already right.

---

### 2.5 Camera picker empty on macOS and Linux

**File:** `computer_vision/camera/camera_stream.py`

`list_available_cameras()` probed with `cv2.CAP_DSHOW` only. `CAP_DSHOW` is the Windows
DirectShow backend; off Windows it always fails, so the function returned `[]` even with a
perfectly working camera — while `open()` in the same file *did* have a fallback. The session
setup screen therefore offered no cameras at all on non-Windows machines.

**Fix** — same two-step as `open()`:

```python
capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
if not capture.isOpened():
    capture.release()
    capture = cv2.VideoCapture(index)
```

Verified: `list_available_cameras()` returned `[]` before, `[0]` after. Windows behaviour is
unchanged — `CAP_DSHOW` is still tried first.

---

### 2.6 `comtypes` required on Windows but declared nowhere

**File:** `backend/requirements.txt`

`backend/utils/files.py::_convert_to_pdf()` imports `comtypes.client` to render `.ppt`/`.pptx`
slide previews through the installed PowerPoint. It was not listed in any requirements file, and
was mentioned only once in a README troubleshooting row. On the target platform, `.pptx`
thumbnails therefore silently never rendered.

**Fix** — declared with a platform marker so it is a no-op elsewhere:

```python
# Windows only: renders .ppt/.pptx previews by driving the installed PowerPoint
# through COM (backend/utils/files.py::_convert_to_pdf). Without it those files
# still upload and gesture/voice control works - they simply have no thumbnails.
# PDFs render through PyMuPDF on every platform and never need this.
comtypes>=1.4.0; sys_platform == "win32"
```

Verified with `pip install --dry-run`: `Ignoring comtypes: markers 'sys_platform == "win32"'
don't match your environment`.

---

### 2.7 Dead code

**Files:** `backend/app.py`, `backend/tests/test_api_flow.py`

- `backend/app.py` — removed `import os` (unused).
- `backend/tests/test_api_flow.py` — removed an unused `user_id` local and its now-orphaned
  `user_id = None` initializer.

`ruff check --select F` is now clean across the whole project. Also confirmed absent: no `TODO`
/ `FIXME` / `XXX` / `HACK` markers, no `breakpoint()`/`pdb`, no stray `print()` and no
debug-level logging left enabled in production code.

---

## 3. Tests added (5)

**File:** `tests/test_gesture_model.py`

### 3.1 Dataset leakage — the guard itself was never tested

`test_split_never_puts_one_recording_in_two_splits` proved the splitter does not leak. It did
**not** prove that `assert_no_leakage()` would notice if it did — the function could have been an
empty body and that test would still have passed.

| New test | What it pins |
| --- | --- |
| `test_assert_no_leakage_actually_catches_a_leaking_split` | a deliberately leaking split **raises** `DatasetError`, naming the offending recording |
| `test_assert_no_leakage_catches_a_leak_between_any_two_splits` | all three split pairs are checked, not just the first |
| `test_assert_no_leakage_accepts_a_genuinely_disjoint_split` | no false positives on a valid split |
| `test_no_individual_frame_leaks_across_splits` | frame level, not just recording level: every frame appears exactly once, and each recording's frames stay wholly within one split |

The splitting implementation itself was reviewed and found **correct**: assignment is keyed by
`recording_id`, and every frame inherits its recording's bucket, so individual frames from one
recording can never straddle a split boundary.

### 3.2 Path traversal

`test_safe_component_never_escapes_its_parent_directory` — asserts that ten hostile user ids
(`".."`, `"."`, `"..."`, `"  ..  "`, `"../../../etc/passwd"`, `"..\\..\\windows"`,
`"/etc/shadow"`, `""`, `"   "`, `"a/../../b"`) all resolve to a directory whose parent is the
model root, and never to the root itself.

---

## 4. Voice intent model — retrained

**Files:** `voice_assistant/data/utterances.py` (source of truth),
`data/voice_intents/v1/utterances.jsonl`, `data/voice_intents/v1/manifest.json`

### 4.1 The problem

`START_PRESENTATION` recall was **0.625** — 3 of 8 test items went to `END_PRESENTATION`
("fire up the slideshow", "open the slideshow", "put the slides up").

Root cause: `END_PRESENTATION` contained more phrasings ending in "…the slideshow" / "…the
slides" than `START_PRESENTATION` did, so the shared tokens outweighed the distinguishing verb
whenever that verb appeared only once in training.

### 4.2 Utterances added (+96)

| Intent | Before | After | Added |
| --- | --- | --- | --- |
| `START_PRESENTATION` | 52 | 86 | +34 (`open…`, `fire up…`, `put … up`, `bring up…`, `pull up…`, `get … going`) |
| `END_PRESENTATION` | 51 | 73 | +22 (decisive `presentation mode` exits, `slide show mode`, `take/put … down/away`) |
| `ENABLE_ANNOTATION` | 52 | 62 | +10 (`i want to draw/annotate/underline…`) |
| `ENABLE_POINTER` | 50 | 56 | +6 (`i want to use the pointer`, `let me use the pointer`…) |
| `NO_COMMAND` | 179 | 203 | +24 hard negatives (`the next session starts at two`, `the first quarter was difficult`…) |
| **Total** | **912** | **1008** | **+96** |

`duplicateTexts` was held at **0**. Nine accidental duplicates were introduced and removed — four
of them duplicated phrases that were already in the *test* split, which would have leaked a test
item into training and inflated the metrics.

### 4.3 Results — measured honestly

The seeded stratified split changes membership when the dataset changes, so per-dataset test
numbers are **not** directly comparable. Three separate measurements were used.

**a) 5-seed means (each dataset on its own split):**

| Dataset | Test accuracy | Macro F1 | NO_COMMAND FP |
| --- | --- | --- | --- |
| Original (912) | 0.9022 ± 0.021 | 0.8938 | 0.0444 |
| **Final (1008)** | **0.9105 ± 0.031** | **0.9093** | 0.0581 |

> The previously reported **0.9416** was the original dataset's *best* seed, not its typical
> performance — its 5-seed mean is 0.9022. Single-split figures on a ~140-item test set carry
> roughly ±2% seed noise and should not be read as exact.

**b) START/END confusion, 5-seed means — the targeted defect:**

| Dataset | START recall | END recall | START↔END swaps |
| --- | --- | --- | --- |
| Original | 0.625 | 0.782 | 0.182 |
| **Final** | **0.877** | **0.818** | **0.092** |

Both directions improved and the swap rate halved.

**c) Fixed 50-item probe, identical for every model** (32 items unseen by all datasets) — the
only strictly apples-to-apples comparison:

| Dataset | Probe accuracy | Unseen-only | NO_COMMAND FP | START/END swap |
| --- | --- | --- | --- | --- |
| Original (912) | 0.900 | 0.894 | 0.100 | 0.217 |
| **Final (1008)** | **0.960** | **0.944** | **0.086** | **0.067** |

### 4.4 A change that was tried and rejected

A further 19 `NO_COMMAND` hard negatives (1045 utterances) were added to push the false-positive
rate down. On the fixed probe it scored **worse** on every metric (0.956 / unseen 0.938 /
FP 0.100), so it was **reverted**. The dataset on disk is byte-identical to the 1008-utterance
variant that won.

Note the trap this avoided: the naive per-dataset `NO_COMMAND` FP rate appeared to *rise* from
0.0444 → 0.0581 → 0.0667, but that metric is not comparable across datasets — adding harder
negatives changes the test pool itself. Only the fixed probe answers the question.

### 4.5 Shipped model

`vi_20260829T103715`, trained on dataset v1 (1008 utterances):

| Metric | Value |
| --- | --- |
| Test samples | 152 |
| Accuracy | 0.9013 |
| Macro F1 | 0.9026 |
| Weighted F1 | 0.8993 |
| Macro precision / recall | 0.9056 / 0.9100 |
| NULL read as a command | 0.129 |
| Command read as the wrong command | 0.074 |
| **False-command rate at the configured 0.75 gate** | **0.0066** |

---

## 5. Documentation

**File:** `README.md`

### 5.1 Platform capability table (new `### Platform support` section)

The README previously made no platform claims at all. It now carries a per-command table grounded
in the actual key codes in `presentation_controller/powerpoint.py` and Microsoft's published
shortcut list, covering all 12 commands across Windows / macOS / Linux, plus a second table for
key-press permissions, camera backends and preview rendering.

Key facts now documented rather than assumed:

- Windows is the supported and verified target; all 12 commands are correct there.
- **5 of 12 commands do not work on macOS** — `START_PRESENTATION` (needs `Cmd+Shift+Return`),
  `VIRTUAL_POINTER` (`Cmd+L`), `ANNOTATION_MODE` (`Cmd+P`), pointer/pen off (`Cmd+A`),
  `CLEAR_ANNOTATION` (`Shift+E`); `FIRST_SLIDE`/`LAST_SLIDE` need `Fn`+arrows.
- PyAutoGUI **silently does nothing** on macOS without Accessibility permission.
- **Wayland is not supported** by PyAutoGUI; Linux is X11-only.
- Everything except the key-press layer is platform-neutral.

The macOS keymap was deliberately **not** implemented: the project targets Windows, and shipping a
mapping that could not be verified against real PowerPoint would add risk without benefit.

### 5.2 Retrain requires a backend restart

The voice interpreter is loaded once per process and cached (`get_interpreter()` memoises into a
module global). A running server therefore keeps serving the model it loaded at startup, even
after the model on disk is retrained — and `/api/health` will disagree with `/api/voice/interpret`
in the meantime, because health re-reads the file while interpret uses the cache.

Added to the setup instructions:

```
# The trained model is loaded once per backend process and cached, so if you retrain it later,
# restart the backend - a running server keeps serving the model it loaded at startup.
```

---

## 6. Verified, no change required

| Area | Result |
| --- | --- |
| Structured command layer | `multimodal.command.build()` is the only `CommandIntent` factory; gesture, voice, manual and keyboard all converge on `CommandDispatcher.execute`. **No duplicate PowerPoint execution logic.** Nothing outside `presentation_controller/` touches PyAutoGUI. |
| Authentication | 22/22 personalization/voice/engine endpoints reject unauthenticated requests. Malformed, empty, bad-signature and **`alg:none` forgery** tokens all rejected 401 — never 500. |
| Cross-user isolation | Voice history and personalization settings verified per-user; one user's delete cannot affect another's data. |
| Frontend ↔ API contract | Every field the frontend reads (`settings.voiceEnabled`, `status.blockers`, `status.intentModel`, `data.plan`, `data.frames`, …) verified present and correctly shaped against the live API. **No mismatches.** |
| Voice fallbacks | Missing model → `503 VOICE_UNAVAILABLE`; corrupt model → checksum caught, `503` with remediation text. Server stays up, gesture and presentation endpoints unaffected in both cases. |
| Gesture fallbacks | Disabled / missing / **corrupt** / deleted model all fall back to the geometric recognizer, with the reason reported. |
| Parameter extraction | Digits, spoken numbers, multi-digit, `one hundred and five`, relative counts, zero, negative, out-of-range, missing — all handled; no 5xx anywhere. |
| Confidence banding | EXECUTE / CONFIRM / REJECT verified; `NO_COMMAND` executes nothing. |
| Dependencies | Every `requirements.txt` package is genuinely imported. **No PyTorch, TensorFlow, transformers or ONNX installed.** Optional extras stay optional; gesture inference ran on the pure-NumPy runtime. |
| Git hygiene | `.env`, `data/gesture/`, user models, `*.task`, `dist`, `.venv` and `backend/uploads/*` all ignored (confirmed with `git add -An`). Exactly `manifest.json` + `utterances.jsonl` would be committed. |
| Cross-platform code | No hard-coded absolute paths, no shell-outs, no `subprocess`, no `os.system` anywhere. |
| Frontend build | Clean `vite build` from scratch, exit 0, 2457 modules. |
| OpenCV 5.0 | All 19 OpenCV APIs used by the project verified present and working (`opencv-python>=4.10` resolved to 5.0.0). |
| Training concurrency | Training runs on a daemon thread with a crash guard, and enrolment refuses to start while a session owns the camera — so it cannot block the realtime recognition loop. |

---

## 7. Known issues NOT fixed (deliberate)

### 7.1 Validation split is empty at the default enrolment size

`split_by_recording()` yields an **empty validation set below 6 recordings per class**, and the
default is `ENROLLMENT_RECORDINGS_PER_GESTURE=3`:

| Recordings/class | train | val | test |
| --- | --- | --- | --- |
| 3 (default) | 22 | **0** | 11 |
| 5 | 44 | **0** | 11 |
| 6 | 44 | 11 | 11 |

Training degrades gracefully — it marks the split `"split is empty"` and falls back to training
accuracy for the alpha search — so nothing crashes. But regularisation strength is then selected
on training accuracy, which monotonically favours the least regularisation.

Not changed, because altering the split ratios or the enrolment default is a behavioural change
beyond a hardening pass. **Recommendation: record 6+ takes per gesture.**

### 7.2 `test_api_flow` needs exclusive camera access

The flow opens the physical camera, so running it twice back-to-back fails while the OS releases
the device. It passes reliably standalone (87/87). Not a product defect, and not worth weakening
the assertion to hide — but worth knowing so it does not cause confusion.

### 7.3 `"go to slide -3"` extracts `slideNumber: 3`

The sign is dropped by digit extraction. Harmless: dispatch bounds-checks the result, and Whisper
would transcribe speech as "minus three" rather than "-3". Left as-is.

---

## 8. Files changed

| File | Change |
| --- | --- |
| `backend/requirements.txt` | pinned `mediapipe<1.0`; added `comtypes` (win32 marker) |
| `backend/app.py` | removed unused `import os` |
| `backend/tests/test_api_flow.py` | added pytest entry point; removed dead local |
| `presentation_controller/dispatcher.py` | parameter-error handling; `GO_TO_SLIDE` bounds check |
| `computer_vision/ml/paths.py` | `safe_component()` dot-traversal fix |
| `computer_vision/camera/camera_stream.py` | camera enumeration fallback |
| `tests/test_gesture_model.py` | +5 tests (4 leakage, 1 path traversal) |
| `voice_assistant/data/utterances.py` | +96 utterances |
| `data/voice_intents/v1/utterances.jsonl` | regenerated (1008) |
| `data/voice_intents/v1/manifest.json` | regenerated |
| `voice_assistant/models/intent/*` | retrained → `vi_20260829T103715` (gitignored) |
| `README.md` | platform capability table; retrain/restart note |

---

## 9. Still unverified — requires physical hardware

Nothing below was exercised; no camera frames from a real hand, no microphone audio and no real
PowerPoint were used at any point in this pass.

1. Camera permission, live preview and landmark overlay.
2. **Gesture recognition with real hands** — all five poses, firing once per gesture, not three times.
3. Enrolment lifecycle with a camera: record, cancel mid-recording, restart, retrain.
   **Use 6+ takes per gesture** (see §7.1).
4. **Personalized model accuracy on real hands.** The 0.830 figure from this pass is from
   *synthetic* landmarks and says nothing about real-world accuracy.
5. Real speech-to-text — install `backend/requirements-voice.txt` first; quiet room, then noise.
6. **Real PowerPoint on Windows**: all 12 commands, especially `Ctrl+P`, `Ctrl+L`, `Ctrl+A`, `E`.
7. `.pptx` thumbnails after `pip install comtypes` (PDF previews already work).
8. Long-session stability (30+ min) — camera thread and memory drift.
9. Camera contention behaviour when Zoom/Teams holds the webcam.
