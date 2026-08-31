# VisionX

**AI-Powered Multimodal Intelligent Presentation Control System**

Control a presentation with your hands or your voice, using a standard webcam and microphone — no
clicker, no sensor glove, no depth camera. VisionX detects hand landmarks with MediaPipe, classifies
them (either geometrically or with a model trained on *your* hands), transcribes speech locally with
Whisper, classifies what you said with a VisionX-trained intent model, and drives the presentation
it is showing in its own dedicated window.

Both modalities converge on **one** command pipeline. Neither can reach the presentation any other
way.

```
  webcam ─► OpenCV ─► MediaPipe ─► recognizer ─► intent gate ─► stabilizer ─┐
                                   (geometric OR         per-frame  5-frame  │
                                    personalized MLP)     ambiguity   vote   │
                                                                             ├─► debouncer ─► mapper ─┐
                                                                             │   hold · release ·     │
                                                                             ┘   cooldown             ▼
                                                                                              CommandIntent
                                                                                             {source, intent,
   microphone ─► Whisper (STT) ─► wake word ─► intent classifier ─► parameters ───────────►    parameters,
    continuous    pretrained     "Vision…OK"   VisionX-trained    confidence gate              confidence}
                                                                                                      │
                                                                                                      ▼
                                                                                             CommandDispatcher
                                                                                                      │
                                                                                                      ▼
                                                                                      WebPresentationController
                                                                                       (default; no automation)
                                                                                                      │
                                                                                                      ▼
                                                                                        presentation window ─┐
                     ↕                                                                                       │
         Flask REST API ↔ MongoDB ↔ React frontend  ◄── SSE: telemetry + a coalesced pointer channel ────────┘
```

`WebPresentationController` is one implementation of `PresentationController`; `PowerPointController`
(COM + PyAutoGUI + the slideshow guard) is the other, still shipped and selectable with
`VISIONX_PRESENTATION_MODE=powerpoint`. Nothing above the interface knows which one is running.

**Three kinds of component, never conflated:**

| | What it is | Where |
| --- | --- | --- |
| **Pretrained, third-party** | MediaPipe hand landmarker · Whisper speech-to-text | `computer_vision/hand_detection/` · `voice_assistant/speech/` |
| **Trained by VisionX** | personalized gesture MLP · voice intent classifier | `computer_vision/ml/` · `voice_assistant/intent/` |
| **Rule-based** | geometric recognizer · stabilizer · debouncer · intent gate · wake word · parameter extraction · dispatcher · web presentation controller | `computer_vision/gesture_recognition/` · `computer_vision/ml/intent_gate.py` · `voice_assistant/wake/` · `voice_assistant/intent/parameters.py` · `presentation_controller/` |

---

## 1. What it does

> ### Web Presentation Mode is fully PowerPoint-independent
>
> VisionX **is** the presentation engine. In the default web mode, Microsoft PowerPoint is never
> opened, controlled, focused, or required: no keystrokes, no mouse synthesis, no COM, no PyAutoGUI.
> A command changes VisionX's own presentation state and the presentation window redraws.
>
> **The only remaining rendering dependency is a headless converter at upload time** — LibreOffice
> (`soffice`), used once to turn a `.pptx` into a PDF. A `.pdf` upload needs nothing at all. Nothing
> is required at presentation time: by then the deck is a set of PNGs on disk. See §2 and §4.

VisionX presents your deck itself, in a dedicated presentation window you put on the projector. See
§4 for what that changed and why.

| Gesture (default pose)      | Command             | Effect                                  |
| --------------------------- | ------------------- | --------------------------------------- |
| Pinky only                  | `NEXT_SLIDE`        | Next slide in the presentation window   |
| Thumb only                  | `PREVIOUS_SLIDE`    | Previous slide                          |
| Index + middle              | `VIRTUAL_POINTER`   | Toggles the pointer; the on-slide dot follows your fingertip at frame rate |
| Index only                  | `ANNOTATION_MODE`   | Toggles the pen; your fingertip draws on the slide canvas |
| Index + middle + ring       | `CLEAR_ANNOTATION`  | Erases the ink on the current slide, leaving the pen as it was |
| Open palm                   | `RESET_ANNOTATION`  | Back to the default state: erases the ink **and** leaves pen and pointer mode |

Poses are **not hardcoded to commands** — every binding lives in the user's `GesturePreferences`
document and can be reassigned in the UI. A saved remap applies to a running session immediately.

`CLEAR_ANNOTATION` and `RESET_ANNOTATION` differ in exactly one thing, and it is the reason both
exist: Clear erases the ink and deliberately leaves the pen armed, so you can carry on drawing on a
clean slide. Reset erases the ink and leaves pen *and* pointer mode, so whatever mode you had lost
track of, an open palm puts you back at a known state. It computes no toggle — repeating it cannot
make things worse — and it never moves the deck.

Seven more commands exist that a pose cannot express, because they take a parameter or are awkward
to hold a hand still for. Voice, the on-screen control bar and the keyboard fallback can all issue
them:

| Command | Parameters | Effect |
| --- | --- | --- |
| `GO_TO_SLIDE` | `slideNumber` | jump to a slide, refused rather than clamped if it does not exist |
| `FIRST_SLIDE` / `LAST_SLIDE` | — | first / last slide |
| `START_PRESENTATION` / `END_PRESENTATION` | — | enter / leave presentation mode |
| `BLACKOUT` / `WHITEOUT` | — | black / white screen |

### Voice

Turn the microphone on once at the start of the talk. From then on, say **"Vision"**, the command,
then **"OK"** — with no interaction with the web app at any point:

> "Vision **next slide** OK" · "Vision **go back two slides** OK" · "Vision **go to slide seven** OK" ·
> "Vision **back to the beginning** OK" · "Vision **black screen** OK" · "Vision **turn on the pen** OK" ·
> "Vision **erase the ink** OK" · "Vision **start the presentation** OK"

Anything you say that is not framed that way — "as you can see on this slide, revenue grew twelve
percent", or even "next slide please" — never reaches the intent model. And a phrase that *is*
framed that way still has to clear the classifier's confidence gate, so a wake word picked up by
accident cannot move the deck either. That is the whole difficulty of the problem, and it is what
§6 and §7 are about.

---

## 2. Requirements

- **Python 3.11 – 3.13** (3.13 verified)
- **Node.js 18+** (24 verified)
- **MongoDB** — MongoDB Atlas, or a local `mongod` for development
- A webcam
- **To upload a `.pptx`/`.ppt`: LibreOffice** on the machine that runs the backend. It is invoked
  headlessly, once per upload, to convert the deck to PDF so VisionX can render its slides.
  - Windows: <https://www.libreoffice.org/download/> — or set `VISIONX_SOFFICE_PATH` if it is
    installed somewhere unusual.
  - **Microsoft Office is not required.** PowerPoint COM remains only as an optional legacy
    fallback, tried *after* LibreOffice and never launched when LibreOffice can do the job. Pin it
    off entirely with `VISIONX_PPTX_CONVERTER=libreoffice`.
  - A **`.pdf` upload needs no converter at all** — the shortest PowerPoint-free path.
- **Nothing is required at presentation time.** Conversion happens at upload; the presentation
  itself runs entirely inside VisionX from rendered images.

`GET /api/health` reports `pptxConverter.ready`, so you can check this before your first upload
rather than discovering it from a deck that produced no slides.

### Platform support

**Windows is the supported and verified target**, and VisionX is built as a Windows application
rather than a cross-platform one that happens to run there.

Since the presentation moved into VisionX's own window (§4), **the table below no longer describes
the default configuration.** In web mode nothing is automated: there are no keystrokes, no mouse
synthesis and no COM, so every command works identically on every platform and the whole column of
macOS/Linux caveats stops applying. What remains Windows-specific is the surrounding environment —
camera and microphone access, and the PowerPoint COM path used *once per upload* to convert a
`.pptx` to PDF.

The table is retained because the PowerPoint controller is still shipped and still selectable
(`VISIONX_PRESENTATION_MODE=powerpoint`), for a presenter who genuinely wants VisionX to drive the
PowerPoint on their own machine.

| Command | On Windows | Keystroke fallback | macOS | Linux |
| --- | --- | --- | --- | --- |
| `NEXT_SLIDE` | `View.Next()` | `Right` | works | X11 only |
| `PREVIOUS_SLIDE` | `View.Previous()` | `Left` | works | X11 only |
| `GO_TO_SLIDE` | `View.GotoSlide(n)` | digits + `Enter` | works | X11 only |
| `FIRST_SLIDE` | `View.GotoSlide(1)` | `Home` | needs `Fn`+`Left` | X11 only |
| `LAST_SLIDE` | `View.GotoSlide(count)` | `End` | needs `Fn`+`Right` | X11 only |
| `START_PRESENTATION` | `F5` | `F5` | **no** — macOS uses `Cmd`+`Shift`+`Return` | X11 only |
| `END_PRESENTATION` | `Esc` | `Esc` | works | X11 only |
| `BLACKOUT` / `WHITEOUT` | `B` / `W` | `B` / `W` | works | X11 only |
| `VIRTUAL_POINTER` | `PointerType = Arrow` + real mouse | `Ctrl`+`L` | **no** — macOS uses `Cmd`+`L` | X11 only |
| `ANNOTATION_MODE` | `PointerType = Pen` | `Ctrl`+`P` **(guarded)** | **no** — macOS uses `Cmd`+`P` | X11 only |
| drawing | mouse button held while moving | same | same | X11 only |
| pointer/pen off | `PointerType = Arrow` | `Ctrl`+`A` | **no** — macOS uses `Cmd`+`A` | X11 only |
| `CLEAR_ANNOTATION` | `View.EraseDrawing()` | `E` **(guarded)** | **no** — macOS uses `Shift`+`E` | X11 only |

**Why the COM path exists.** PowerPoint's slideshow shortcuts are not merely useless outside a
slideshow — one of them is actively dangerous:

```
Ctrl+P   in a slideshow            ->  pen
         on an ordinary PPT window ->  PRINT DIALOG
```

Sending it blind is what put the Print dialog on screen mid-talk. VisionX now asks PowerPoint
whether a slideshow is running before it arms the pen, and there are three answers, each handled
differently:

| Probe result | Meaning | What VisionX does |
| --- | --- | --- |
| `CONFIRMED` | Windows, PowerPoint is presenting | set the pen (COM; keystroke only if COM is unavailable) |
| `DENIED` | Windows, PowerPoint is **not** presenting | **refuse** with a message naming the reason — no `Ctrl+P` |
| `UNKNOWN` | not Windows, or no COM binding | send `Ctrl+P` — the historical behaviour, since there is no evidence of danger |

Distinguishing `DENIED` from `UNKNOWN` is the whole fix: refusing everywhere would break every
non-Windows setup, and allowing everywhere is the bug.

Two more Windows specifics:

- **Per-monitor DPI awareness** is enabled at start-up (`enable_dpi_awareness()`), before PyAutoGUI
  caches the screen size. Every laptop ships scaled to 125–150%, and without this the virtual
  pointer lands at roughly 80% of where the presenter is pointing.
- **A small inter-key pause** (12 ms). PowerPoint's slideshow window silently drops keystrokes
  delivered back-to-back with no gap at all.

`GET /api/health` reports the slideshow probe, so a presenter can check before they start.

Everything *except* the key-press layer is platform-neutral: MediaPipe detection, canonicalization,
both recognizers, the debouncer, the intent gate, speech-to-text, the intent classifier, parameter
extraction, the dispatcher, the API and the frontend all behave identically on all three.

Other platform notes:

| Area | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Sending key presses | works | requires granting **Accessibility** permission to the terminal/app, or PyAutoGUI silently does nothing | X11 works; **Wayland is not supported** by PyAutoGUI |
| Camera capture | `CAP_DSHOW` | default AVFoundation backend (fallback) | V4L2 (fallback) |
| `.pptx` slide previews | needs `comtypes` + installed PowerPoint | not available — uploads and control still work, no thumbnails | not available |
| `.pdf` slide previews | works (PyMuPDF) | works | works |

> **macOS is fine for developing and for running the tests** — the whole suite passes on it, because
> the tests fake the keyboard at the OS boundary. It is driving *real* PowerPoint that is
> Windows-shaped.

---

## 3. Setup

### Backend

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r backend/requirements.txt
python scripts/download_model.py  # one-time: fetches the pretrained MediaPipe hand model (~8 MB)

# One-time: train the voice intent model from the dataset committed in data/voice_intents/.
# Takes a few seconds. Skip it and the voice assistant simply reports that it is unavailable.
# The trained model is loaded once per backend process and cached, so if you retrain it later,
# restart the backend - a running server keeps serving the model it loaded at startup.
python -m voice_assistant.training.train_intent_model

copy backend\.env.example backend\.env
```

Two optional extras:

```bash
pip install -r backend/requirements-voice.txt   # local Whisper speech-to-text (faster-whisper)
pip install -r backend/requirements-ml.txt      # ONNX export/runtime for the personalized model
```

Neither is required. Without the first, voice falls back to typed commands and reports why.
Without the second, the personalized model runs on its (equivalent, verified) NumPy runtime.

Edit `backend/.env`:

```ini
MONGO_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
MONGO_DB_NAME=visionx_db
JWT_SECRET=<a long random string>
FRONTEND_URL=http://localhost:5173
```

> Using MongoDB Atlas: create a free cluster, add a database user, and allow-list your IP under
> *Network Access*. For local development `MONGO_URI=mongodb://127.0.0.1:27017` works unchanged —
> the driver and every query are identical.

Run it:

```bash
cd backend
python app.py            # http://127.0.0.1:5000
```

### Frontend

```bash
cd frontend
npm install
npm run dev              # http://localhost:5173
```

The Vite dev server proxies `/api` to Flask, so the SSE telemetry stream and the MJPEG camera preview
are same-origin. For a production build (`npm run build`), set `VITE_API_URL` to the deployed API URL.

---

## 4. The demo path

1. **Register** → you are signed in and given the default gesture bindings.
2. **Upload** a `.pdf`, `.pptx` or `.ppt`. VisionX reads the real slide count and converts the deck
   once (PDFs need no conversion; PowerPoint files are converted by the installed PowerPoint or by
   LibreOffice). This is the step that makes the deck presentable.
3. Open the presentation → **Start presentation**. A `PresentationHistory` document is created (`READY`).
4. Pick the camera and the confidence gate, then start. The camera opens, MediaPipe begins tracking,
   and **a dedicated presentation window opens** — drag it to your projector or second screen. The
   window you started from stays on your laptop as the control screen: camera preview, status, voice.
5. Gesture. The presentation window shows the slide, the pointer and your ink; the control window
   shows the pose, a confidence pulse, the hold progress and the command that fired.
6. **End session** → the presentation window closes, the engine stops, ink is flushed to
   `Annotations`, and the session is written to history with its duration, slide count and gesture
   breakdown.
7. **History** and **Analytics** aggregate those documents — nothing on those pages is hardcoded.

Two optional detours off that path:

- **Gesture settings → Train my gestures** enrols your hands and trains a personalized model
  (§6). Everything above keeps working identically whether or not you do this.
- **Voice** turns on continuous listening in the session screen (§7): say
  "Vision <command> OK" at any point and it runs, with no interaction with the web app. A voice
  command travels the exact same dispatcher as a gesture, so it lands in the same history and the
  same analytics.

Keyboard fallback during a session: `←` `→` `P` `A` `E` `Esc` go through the exact same dispatcher
(`Esc` is Reset; in the presentation window, where `Esc` closes the window, Reset is `X`).

### The presentation window

The presentation itself is a VisionX page (`frontend/src/pages/Present.jsx`) on `/present`, opened
in its own browser window. It is not the VisionX application: no sidebar, no controls, no camera
preview — the audience sees the deck.

```
   AT UPLOAD (once)                        AT PRESENTATION TIME (no conversion, no Office)
   ────────────────                        ──────────────────────────────────────────────
   .pptx                                   gesture ─┐
     │ LibreOffice --headless                voice ─┼─► CommandDispatcher
     ▼   (PowerPoint COM: optional        keyboard ─┘          │
   PDF        legacy fallback)                                 ▼
     │ PyMuPDF                                   WebPresentationController
     ▼                                              (VisionX's own state)
   PNG per slide  ─────────────────────────────────────────────┤
                                                               ▼
                                                    SSE ─► presentation window
```

**Nothing on the right-hand side involves Microsoft PowerPoint.** No process is launched, no window
is focused, no keystroke or mouse event is synthesised, and no COM interface is opened. A `NEXT_SLIDE`
increments VisionX's own slide number; the window redraws. The only external program VisionX ever
runs is `soffice`, on the left, once per upload.

Going through PDF is what keeps the deck faithful: LibreOffice does the layout with the real fonts,
masters, themes and embedded media, so what the audience sees is the deck rather than a browser
library's approximation of it. Slides are rendered at the window's own pixel width
(`/api/presentations/<id>/render/<n>?w=`), cached on disk per (slide, width), and the neighbours of
the current slide are prefetched while it is on screen — so a Next Slide gesture lands on an image
the browser already has.

**What this removed.** The controller behind the dispatcher is an interface
(`presentation_controller/base.py`), so swapping `PowerPointController` for
`WebPresentationController` changed nothing above it — the engine, the dispatcher, the voice
pipeline and every route are identical. What changed is that the bottom of the stack is no longer
the operating system:

| Problem | Cause | Why it cannot happen now |
| --- | --- | --- |
| Print dialog opening mid-talk | blind `Ctrl+P`, which outside a slideshow means Print | no keystrokes are sent at all |
| the pen refusing to arm | it needed a *running PowerPoint slideshow* to be safe | the pen is a flag on a canvas |
| Clear Annotation doing nothing | the COM eraser refused with no slideshow | one event, one `clearRect` |
| drawing not working | it needed a mouse button held for the whole stroke | a stroke is a list of points |
| the mouse button left held down | a lost release stranded it on the desktop | there is no mouse button |
| commands landing in the wrong window | whichever window had focus received them | nothing has focus to steal |

The PowerPoint controller is still shipped and still tested; set
`VISIONX_PRESENTATION_MODE=powerpoint` to drive a real PowerPoint instead. It is **isolated**, not
merely unused: `PowerPointController` is imported inside the branch that asks for it, so a web-mode
process never loads `presentation_controller.windows` (COM) or `presentation_controller.keyboard`
(PyAutoGUI) at all.

**How the independence is enforced.** `tests/test_no_powerpoint.py` is a regression suite whose only
job is this property, because it is easy to reintroduce by accident — one convenience import at the
top of a module is enough, and the symptom in production is not a test failure but a Print dialog in
front of an audience. It checks four things:

| Check | How |
| --- | --- |
| the import graph is clean | imports the web controller in a **subprocess** and inspects `sys.modules` — this process has already imported the PowerPoint controller for its own tests, so checking here would prove nothing |
| a whole session is clean | builds a dispatcher in a subprocess, runs all twelve commands plus a pointer stream and a stroke, then inspects `sys.modules` — this catches a *lazy* import that only fires on one command |
| the backend is clean | boots the actual Flask app with `VISIONX_PRESENTATION_MODE=web`, serves `/api/health`, inspects `sys.modules` |
| no automation is reachable | poisons every `KeyboardBackend` and `PowerPointComBridge` method so any call fails naming the method, then runs every command — this holds even in a process that legitimately has PyAutoGUI loaded for legacy mode |

Plus: `subprocess.run`/`Popen`/`call` are poisoned for the duration of a presentation, so nothing can
launch an application behind our back.

### Why the pointer is smooth

Pointer movement is a **continuous** signal and slide changes are **discrete** ones, and the two
need opposite handling. Conflating them is what made the old pointer lag:

```
slide change  ->  discrete  ->  debounce, cooldown, sustained release   (§5)
pointer       ->  continuous ->  every frame, coalesced, interpolated
```

Three things were in the way, all of them removed:

1. The pointer travelled inside the `telemetry` event, which is rate-limited to 12 Hz — an 83 ms
   quantisation floor before a position even left the server. It now has **its own channel**
   (`backend/services/event_bus.py`), published at camera frame rate.
2. That channel shared one bounded queue with commands, so a browser falling behind received a
   *backlog of stale positions*. The pointer channel is now a **single-slot mailbox**: publishing
   overwrites what has not been read, so a slow client skips positions rather than lagging behind
   them. Discrete events keep the queue, because losing a slide change is never acceptable.
3. The browser re-rendered on every sample. The presentation window writes the pointer to a
   `transform` and the ink to a canvas inside one `requestAnimationFrame`, **never through React
   state**, and interpolates between samples so the dot moves continuously rather than stepping once
   per network event.


---

## 5. Why gestures do not misfire

A command fires only when **all four** conditions hold:

0. **Temporal smoothing** (`computer_vision/gesture_recognition/stabilizer.py`) — the pose that
   reaches the command mapper is a plurality vote over the last 5 frames, not the latest frame's
   classification. Two poses in the library differ by one bit — `INDEX_UP` (the pen) and
   `INDEX_MIDDLE_UP` (the pointer) — so a middle finger that dips below the extension threshold for
   a frame used to change which command you were giving. It cannot now: one or two stray frames are
   outvoted. When no pose commands a plurality the stabilizer reports `UNKNOWN`, which is the
   neutral state the debouncer already handles.

Then, in `computer_vision/gesture_recognition/debouncer.py`:

1. **Confidence gate** — the pose confidence clears the session threshold.
2. **Temporal persistence** — the same command survives N consecutive frames (default 6).
3. **Sustained neutral state between repeats** — after a command fires, the same command cannot fire
   again until neutrality has been *held* for `release_frames` consecutive frames. The default is the
   **full** persistence requirement — releasing a gesture takes as long as making one — because the
   stabilizer needs a few frames to swing over to "no hand", so N dropped frames already produce
   close to N neutral ones. Half of it left a ~66 ms margin, and a 100 ms MediaPipe dropout mid-hold
   still advanced a second slide. Neutral means no hand, an unrecognised pose, or any pose you have
   left unbound.

A cooldown (default 900 ms) sits on top as a final guard.

> **Why neutrality has to be held, not merely observed.** A single neutral frame used to re-arm the
> repeat. That sounds harmless and is not: a held gesture does not produce a clean run of identical
> frames — MediaPipe loses the hand for a frame, the model emits a runner-up class, the intent gate
> neutralises an ambiguous frame. Any one of those unlocked the repeat, the streak rebuilt in a
> fifth of a second, and the command fired again. Measured on a stream with one dropped frame in
> twelve, a single held gesture produced **30 slide advances in 30 seconds**; with the hold rule it
> produces **one**. `tests/test_gesture_stability.py::test_the_neutral_hold_rule_is_what_stops_the_deck_walking`
> asserts both numbers against the same input, so the regression cannot come back quietly.

With a personalized model there is a fourth: an **intent gate** (`computer_vision/ml/intent_gate.py`)
rejects a frame whose top two classes are within 0.15 probability of each other. A hand the model
calls `INDEX_UP` at 0.51 with `INDEX_MIDDLE_UP` at 0.47 is not a confident anything — it is a hand
mid-transition, and the gate turns it into the neutral state the debouncer already understands. The
gate is rule-based and can never *create* a command, only suppress one. It is inert for the
geometric recognizer, which reports no runner-up.

> **The two recognizers report confidence on different scales.** The geometric one multiplies the
> weakest finger's geometric margin by MediaPipe's detection score — a designed proxy, not a
> probability, and for a thumb pose it legitimately sits at 0.1–0.7. The personalized one reports a
> calibrated class probability and routinely exceeds 0.9 for the same hand. The same numeric gate is
> therefore much stricter for the geometric recognizer. This is why the intent-gate margin, not the
> confidence gate, does the discriminating work once a model is in use. `tests/test_integration.py`
> pins this so it cannot regress silently.

---

## 6. Personalized gesture recognition

Optional, opt-in, per user. Until you train a model, VisionX behaves exactly as it always has — the
geometric recognizer is never removed and is always the fallback.

### What it is

| | |
| --- | --- |
| **Input** | 21 MediaPipe landmarks → **86 canonical features** (never pixels) |
| **Model** | MLP `86 → 64 → 32 → 11`, ReLU, softmax · ~8,000 parameters |
| **Classes** | the 10 poses in `computer_vision/gesture_recognition/poses.py`, plus an explicit `UNKNOWN` (null / other) class — derived from the pose library, never hard-coded |
| **Runtime** | pure NumPy by default (~0.013 ms/frame); ONNX Runtime when installed (~0.010 ms/frame) |
| **Training** | scikit-learn `MLPClassifier`; the validation split picks the regularisation strength |

Inference is ~0.01 ms against a 33 ms frame budget — the cost is not measurable next to MediaPipe.

### Canonicalization (`computer_vision/ml/canonicalization.py`)

One deterministic, versioned transform shared by collection, training and inference:

1. undo the frame's aspect distortion
2. translate the wrist to the origin → **translation invariance**
3. divide by palm length (wrist → middle MCP) → **scale invariance**
4. rotate in-plane so that axis points at +Y → **rotation invariance**
5. flatten to 63, then append 23 derived features (10 pairwise fingertip distances, 5 finger
   extension ratios, 4 inter-finger angles, 3 bounding-box terms, the knuckle span)

All three invariances are asserted to float32 precision in `tests/test_canonicalization.py`. The
original geometric recognizer has no rotation invariance at all, which is one reason a tilted hand
confuses it and does not confuse the model. `FEATURE_VERSION` is recorded in every dataset and
model; a model trained on one version is **refused**, not silently mis-fed, on another.

### Enrolment

Gesture settings → *Train my gestures*. The camera runs in **enrolment mode**: hands are tracked and
frames collected, but nothing is dispatched, so nothing can reach PowerPoint while you train.

For each of the 11 classes you record ~3 short takes of ~60 frames, moving your hand closer and
further from the camera and rotating it slightly between takes. The wizard prompts for that
variation explicitly.

The 11th class matters most. It is recorded from **natural non-command hand movement** — you talking
with your hands. Without it the model has no way to stay quiet during a real talk, and training
refuses to run.

Every frame passes a quality gate before it enters the dataset (`computer_vision/ml/dataset.py`):
MediaPipe detection score, brightness, hand bounding-box area (too far / too close), 21 valid
landmarks, and a duplicate check against the previous accepted frame. Rejections are counted and the
reason is shown live.

**The camera loop never waits on I/O.** Capture appends to memory; writing the recording to disk and
MongoDB happens on a worker thread, and training runs on another — `POST /personalization/train`
returns immediately and progress arrives over the existing SSE channel.

### Dataset format

Versioned, JSONL, one object per line, **one file per recording**:

```
data/gesture/v1/samples/user_<id>/rec_20260829T101500_a1b2.jsonl
data/gesture/v1/manifest.json
```

```jsonc
{"schemaVersion": 1, "sampleId": "rec_…#0", "recordingId": "rec_…", "subjectId": "user:66f0…",
 "label": "PINKY_UP", "featureVersion": "gesture-canonical-v1", "features": [/* 86 floats */],
 "landmarks": [[x, y, z] /* × 21 */], "aspect": 1.3333, "detectionScore": 0.97,
 "brightness": 118.4, "handBoxArea": 0.081, "handedness": "Right", "capturedAt": "…"}
```

> **Splitting is by recording, never by frame.** Frames within one recording are near-duplicates of
> each other; splitting them individually would put almost-identical frames on both sides of the
> train/test boundary and report an accuracy the model has not earned. `split_by_recording()` is
> stratified by label over *recordings*, and `assert_no_leakage()` fails loudly if one ever appears
> in two splits.

Collected landmark data is per-user and biometric-adjacent, so `data/gesture/` and
`computer_vision/models/users/` are both git-ignored and never committed.

### Where models live

```
computer_vision/models/users/<user_id>/gesture_model.npz             portable weights (source of truth)
                                       gesture_model.onnx            verified export, optional
                                       gesture_model.metadata.json   version, classes, dataset, metrics
```

The ONNX graph is built by hand from `Sub, Div, Gemm, Relu, Softmax, ArgMax` rather than through a
converter, and the export is **rejected** unless it matches the NumPy runtime to 1e-4 on random
input. A corrupt or version-mismatched model is logged once and treated exactly like a missing one.

---

## 7. Voice assistant

Optional, opt-in, per user. Off by default; turning it off changes nothing about gestures.

```
microphone ─► MediaRecorder (continuous, cut on silence) ─► POST /api/voice/stream
           ─► Whisper (local, pretrained, loaded at boot) ─► transcript
           ─► wake-word machine ("Vision" … "OK")  ─► a command, or nothing at all
           ─► intent classifier (VisionX-trained) ─► intent + probability
           ─► parameter extraction (rule-based)   ─► slideNumber / count
           ─► confidence band ─► CommandIntent ─► the existing CommandDispatcher
```

### Latency: where the seconds were

The pipeline above is unchanged — the same Whisper, the same wake machine, the same trained intent
model (§8 was explicit that it must be reused, and it is). What changed is *when* each stage runs.

**1. The recorder waited on a clock, not on the presenter.** Audio was cut into fixed 3-second
segments, so a command was not even uploaded until the window happened to close:

```
presenter says "…OK"
     │
     │   up to 3 s   ← waiting for a timer, with the audio already captured
     ▼
upload ─► Whisper ─► intent ─► dispatch
```

Segments now end **when the presenter stops talking**: 350 ms of silence closes the recorder and the
audio goes up (`frontend/src/hooks/useContinuousVoice.js`). "OK" becomes a full stop the machine can
hear. Speech that keeps going is still cut at a 2.5 s ceiling, so a command completed early in a long
sentence is not held hostage by the rest of it. Silence is never uploaded at all — an empty Whisper
pass costs as much as a real one and is where Whisper invents text.

That one change removes the largest term, and — as importantly — removes its *variance*: the same
command used to take 0.3 s or 3.3 s depending on where in the window it landed.

**2. The models loaded on first use.** Both are process-wide singletons, which is right, but "first
use" means the presenter's first command, in front of an audience — several seconds for that one,
and fast for every one after it. `_prewarm_voice` (`backend/app.py`) loads both at boot on a daemon
thread and calls Whisper's `warm_up()`, which existed and was never called: the first *inference* is
slower than the rest because the runtime builds its graph on it. Set `VOICE_PREWARM=0` to opt out on
a machine that will never use voice.

**3. Ordinary speech does no work.** Almost every segment of a talk is not a command. It is matched
against the wake vocabulary — a compiled regex — and dropped: nothing is classified, nothing is
dispatched, nothing is written to MongoDB.

Whisper's own settings were already tuned for this workload and are unchanged: `beam_size=1` (a
three-word command needs no beam search), `vad_filter=True`, and `condition_on_previous_text=False`
so each utterance is independent.

The voice layer contains **no PowerPoint logic**. It cannot: the only way it can affect a slideshow
is by handing a `CommandIntent` to the same dispatcher the gesture engine uses.

### Continuous listening — "Vision … OK"

The presenter turns the microphone on once, at the start of the talk, and never touches the web app
again:

```
[Listening] ──"Vision"──► [Command mode] ──"go to next slide"──► "OK" ──► NEXT_SLIDE
     ▲                                                                        │
     └────────────────────────────────────────────────────────────────────────┘
```

`voice_assistant/wake/wake_word.py` is a **pure-text state machine**: transcripts in, decisions out,
no audio and no model. It decides *when* there is something to classify; the trained model still
decides what it means, with the same confidence bands as before. Nothing about the trained pipeline
changed — continuous listening was built around it.

- Both boundaries may arrive in one breath (`"Vision go to next slide OK"`) or across several
  recorder segments (`"Vision"` / `"go to next slide"` / `"OK"`). Where the recorder's timer happens
  to fall does not change what a command means.
- Ordinary speech — including `"next slide please"` and `"ok, so the last slide showed…"` — never
  reaches the intent model at all.
- The wake word must be a whole word, and must be **addressed** rather than merely used. Two guards,
  because getting this wrong is how a talk drives its own deck:
  - No ordinary English word is a wake word, however close it sounds. *Envision* and *provision* were
    accepted at first, and "we need to **provision** more servers and then move to the next slide,
    okay" then executed `NEXT_SLIDE` at 0.85 confidence. The confidence gate cannot help there — the
    captured words genuinely are a command.
  - A wake word directly after a determiner or possessive is part of a sentence, not a summons, so
    "our **vision** going forward…" and "the **vision** is simple…" are ignored. Genuine
    mis-transcriptions (*visions*, *vision x*, *visionx*) still arm it.
- A captured command is capped at 10 words. Every command VisionX can run fits in six, and a run-on
  capture is far more likely to be ordinary speech that followed a stray wake word.
- A capture that never ends times out after 12 s, so an accidental wake word cannot swallow the rest
  of the talk.
- **Continuous listening is not continuous recording.** Each segment is transcribed in memory and
  discarded; silent segments are never uploaded at all.

Push-to-talk (`POST /api/voice/utterance`) still exists and is unchanged — it is what the Voice
Assistant settings screen uses to test a phrase.

### Speech-to-text — pretrained, not trained here

VisionX does not train a speech recogniser. `voice_assistant/speech/base.py` defines the seam:

| Backend | Notes |
| --- | --- |
| `FasterWhisperRecognizer` | **default** — Whisper via CTranslate2. No PyTorch, ~5× faster on CPU. |
| `OpenAIWhisperRecognizer` | reference implementation; needs torch (~2 GB) and ffmpeg on PATH |
| `NullSpeechRecognizer` | neither installed — fails with install instructions, never a stack trace |

Audio never leaves the machine, which is the natural arrangement here: the backend already runs on
the presenter's own computer, because it is that computer's keyboard it drives. Segments are
transcribed in memory and discarded; a segment below the silence threshold is never uploaded.

### Intent classifier — trained by VisionX

| | |
| --- | --- |
| **Features** | word 1–2 gram TF-IDF **+** character 3–5 gram (`char_wb`) TF-IDF, sublinear tf |
| **Model** | multinomial logistic regression (`lbfgs`), C selected on the validation split |
| **Classes** | 15 — 14 command intents plus an explicit `NO_COMMAND` |
| **Dataset** | 912 hand-authored utterances, `data/voice_intents/v1/utterances.jsonl` |

Character n-grams absorb the small differences Whisper produces between runs; the model is small
enough to train in ~4 seconds and to run in under a millisecond per utterance.

Intents are not the same as commands, because speech distinguishes what a toggle cannot:
`ENABLE_ANNOTATION` and `DISABLE_ANNOTATION` both resolve to `ANNOTATION_MODE` with an explicit
`state`. Saying "turn on the pen" twice must leave the pen on.

### The dataset

Hand-authored in `voice_assistant/data/utterances.py` and serialised by
`python -m voice_assistant.training.build_intent_dataset`. Every line was written by hand rather
than produced by filling one template per intent, because a classifier trained on templates learns
the template.

| Intent | Utterances | | Intent | Utterances |
| --- | ---: | --- | --- | ---: |
| `NEXT_SLIDE` | 57 | | `WHITEOUT` | 51 |
| `PREVIOUS_SLIDE` | 55 | | `ENABLE_POINTER` | 50 |
| `GO_TO_SLIDE` | 58 | | `DISABLE_POINTER` | 50 |
| `FIRST_SLIDE` | 51 | | `ENABLE_ANNOTATION` | 52 |
| `LAST_SLIDE` | 51 | | `DISABLE_ANNOTATION` | 51 |
| `START_PRESENTATION` | 52 | | `CLEAR_ANNOTATION` | 53 |
| `END_PRESENTATION` | 51 | | **`NO_COMMAND`** | **179** |
| `BLACKOUT` | 51 | | **total** | **912** |

`NO_COMMAND` is the largest class on purpose, and most of it is *hard* negatives — ordinary
presenter sentences that contain "slide", "next", "back", "point", "clear", "black", "first" and
numbers in a non-command sense:

> "as you can see on this slide revenue grew by twelve percent" · "let me point out three things
> here" · "the next quarter looks strong for us" · "we started this project back in march" ·
> "to be clear this is a projection"

Without those the model fires a command every time a presenter says "on the next slide you can
see", which would make voice control unusable in a real talk.

Each record also carries the parameters the extractor is expected to produce, so the dataset doubles
as the regression suite for parameter extraction:

```jsonc
{"text": "go to slide seven", "intent": "GO_TO_SLIDE", "parameters": {"slideNumber": 7},
 "source": "authored", "datasetVersion": "v1", "featureVersion": "voice-text-v1"}
```

### Parameter extraction — separate, and rule-based

Classification decides *what*; extraction decides *which slide* or *how many*
(`voice_assistant/intent/parameters.py`). Keeping them apart means a classifier change cannot
silently break number handling, and a number bug cannot be mistaken for a misclassification.

It handles digits and number words ("seven", "twenty three", "one hundred and five"), ordinals
("the seventh slide"), and distinguishes a slide *reference* from a step *count* by the words around
it. "go to slide 4" misrouted to `NEXT_SLIDE` yields **no count**, rather than jumping four slides.

### The safety gate

A probability is not a promise, so there are three bands:

| Band | Default | What happens |
| --- | --- | --- |
| `EXECUTE` | p ≥ 0.75 | run it |
| `CONFIRM` | p ≥ 0.50 | show *"I heard: 'Go to slide 17' · Go to slide 17 · 61%"* and wait for a tap |
| `REJECT` | otherwise | do nothing, say nothing |

Both thresholds are per-user and adjustable. `POST /voice/confirm` **re-interprets the transcript**
rather than trusting a client-supplied command — the browser can ask VisionX to run what it heard,
not an arbitrary command of its choosing. An out-of-range slide number is rejected, never clamped:
silently going somewhere the presenter did not ask for is worse than doing nothing.

---

## 8. Live updates

The browser opens **one Server-Sent Events connection** per window
(`GET /api/engine/stream`) — the control screen and the presentation window each have their own.
Two kinds of traffic share it, with deliberately different delivery guarantees:

| | Rate | Delivery | Why |
| --- | --- | --- | --- |
| telemetry, commands, state, ink | ~12/s (rate-limited in the engine) | **queued**, oldest dropped only when a client stalls | losing a slide change is never acceptable |
| pointer positions | camera frame rate | **coalesced** — a single slot, newest wins | a stale fingertip position is worse than none |

Pointer events deliberately bypass the telemetry limiter: 12 Hz is visible as lag on something that
has to follow a hand, while a discrete command at 30 Hz would just be noise. A browser that falls
behind therefore *skips* positions rather than replaying old ones — see §4, "Why the pointer is
smooth".

The camera thumbnail is a separate MJPEG stream (`GET /api/engine/preview`) at 15 fps. All of them
accept the JWT as a query parameter because `EventSource` and `<img>` cannot send headers.

Swapping SSE for WebSockets later touches `backend/services/event_bus.py`, the stream route, and the
two client hooks (`useEngineStream.js`, `usePresentationChannel.js`).

---

## 9. Project structure

```
VisionX/
├── backend/
│   ├── app.py                  Flask entry point, blueprint registration, CORS
│   ├── config/                 settings.py (env) · database.py (Mongo + indexes)
│   ├── models/schema.py        canonical collection + index definitions
│   ├── middleware/             auth.py (JWT) · error_handler.py
│   ├── routes/                 auth · user · presentation · gesture · session · annotation · analytics · engine
│   ├── controllers/            cross-service orchestration (session start/stop)
│   ├── services/               business logic + all database access
│   ├── utils/                  responses · errors · security · validators · files · serializers
│   └── tests/test_api_flow.py  end-to-end API test
├── computer_vision/
│   ├── camera/                 capture, reconnect, permission/disconnect handling
│   ├── preprocessing/          resize · mirror · BGR→RGB · brightness metering
│   ├── hand_detection/         MediaPipe wrapper (Tasks API, legacy fallback)
│   ├── gesture_recognition/    pose library · geometric recognizer · debouncer
│   │                           · recognizer_factory.py (geometric vs personalized)
│   ├── ml/                     VISIONX-TRAINED gesture model
│   │   ├── canonicalization.py landmarks → 86 versioned features
│   │   ├── dataset.py          JSONL format · quality gate · split-by-recording
│   │   ├── synthetic.py        procedural hands, for pipeline validation only
│   │   ├── collector.py        in-memory enrolment capture (camera loop safe)
│   │   ├── mlp.py              NumPy + ONNX runtimes · hand-built ONNX export
│   │   ├── registry.py         per-user model storage and cache
│   │   ├── intent_gate.py      rule-based top-2 margin rejection
│   │   ├── personalized_recognizer.py   same seam, learned decision
│   │   └── training/           train · evaluate · export · synthesize (CLIs)
│   ├── command_mapping/        pose → command using the user's preferences
│   └── engine.py               the camera-loop thread
├── voice_assistant/
│   ├── speech/                 SpeechRecognizer ABC · Whisper backends · factory
│   ├── intent/                 intents · normalize · parameters · classifier
│   │                           · interpreter (transcript → decision)
│   ├── data/utterances.py      the hand-authored dataset source
│   └── training/               build dataset · train · evaluate (CLIs)
├── multimodal/
│   ├── command.py              CommandIntent - the shape both modalities emit
│   ├── context.py              shared live pointer/slide state (Feature D hook)
│   └── reporting.py            one evaluation report format for both models
├── data/
│   ├── gesture/v1/             collected landmark recordings (git-ignored)
│   └── voice_intents/v1/       the committed intent dataset (text)
├── tests/                      pytest: canonicalization · models · integration
├── presentation_controller/
│   ├── base.py                 abstract PresentationController
│   ├── powerpoint.py           the shipped implementation
│   ├── keyboard.py             the only module that imports PyAutoGUI
│   ├── pointer.py · annotation.py
│   └── dispatcher.py           command → controller
├── frontend/src/               components · pages · layouts · services · hooks · context · utils
├── scripts/download_model.py
└── docs/API.md
```

**Layering rule:** Recognition → Mapping → Dispatch → Control are four separate layers. Nothing in
`computer_vision/` or `voice_assistant/` imports PyAutoGUI; the engine only emits command *names*
through a callback, and the voice layer only emits `CommandIntent` objects.

**One dispatcher rule:** gesture, voice, the control bar and the keyboard fallback all resolve to a
`CommandIntent` and go through `EngineService.execute_intent()` → `CommandDispatcher`. There is
exactly one place in the codebase where a VisionX command becomes a PowerPoint key press. Adding a
modality means producing a `CommandIntent`, and nothing else.

---

## 10. Database

MongoDB collections (see `backend/models/schema.py`):

| Collection             | Purpose                                       |
| ---------------------- | --------------------------------------------- |
| `users`                | name, email, bcrypt hash, profilePhoto, createdAt |
| `presentations`        | userId, title, fileName, storedName, filePath, fileType, totalSlides, thumbnails, uploadedAt |
| `gesture_preferences`  | one per user — the six pose bindings          |
| `presentation_history` | one per session — status, times, duration, slidesNavigated, annotationsMade, commandsFired, gestureCounts |
| `annotations`          | presentationId, sessionId, slideNumber, annotationData, createdAt |
| `personalization`      | one per user — multimodal opt-ins, consent, thresholds, model pointer |
| `gesture_recordings`   | one per enrolment recording — label, frames, quality, path (**metadata only**; the landmarks live in the versioned dataset on disk) |
| `voice_commands`       | one per interpreted utterance — intent, command, parameters, confidence, band, executed, outcome (**never audio**) |

Relationships: User 1—N Presentations · User 1—1 GesturePreferences · User 1—N History ·
Presentation 1—N History · Presentation 1—N Annotations · User 1—1 Personalization ·
User 1—N GestureRecordings · User 1—N VoiceCommands · Session 1—N VoiceCommands.

`personalization` is deliberately a separate collection from `gesture_preferences`: "delete my
personalization data" must never take a user's pose bindings with it.

---

## 11. Security

- bcrypt password hashing, JWT bearer auth, every `/api/*` route except `/auth/register|login` and
  `/api/health` behind the auth middleware.
- **The user id always comes from the token**, never from the request body — every query is scoped by
  it, so one account cannot read another's presentations, sessions or annotations.
- Uploads are validated on extension *and* MIME type, size-capped, stored under a server-generated
  UUID filename, and the resolved path is asserted to stay inside `UPLOAD_DIR`.
- Errors return a `{code, message}` pair; stack traces stay in the server log. Secrets live in `.env`
  (git-ignored); only `.env.example` is committed.

### Privacy and user control over learning data

Personalization is off by default and gated on **explicit, separate consent**.

| | |
| --- | --- |
| **Hand landmarks** | Coordinates, never images. No frame is ever written to disk. Collection requires `gestureLearningConsent`; turning it off stops collection immediately (it does not delete what exists — that is a separate, deliberate action). |
| **Raw audio** | **Never stored.** Transcribed in memory and discarded — with continuous listening as with push-to-talk. Nothing is written to disk, and silent segments are never uploaded at all. |
| **Transcripts** | Command-level telemetry only, and only while `voiceTranscriptRetention` is on. Turn it off and just the intent, confidence and outcome are recorded. |
| **Delete** | *Delete model* · *Delete recordings* · *Delete all learning data* · *Clear voice history* — all in the UI, all available independently. |

Deleting learning data **never** touches presentations, sessions, annotations or pose bindings.
That is why `personalization` is its own collection, and why per-user models live under
`computer_vision/models/users/<id>/` rather than mixed in with anything else.

Nothing personal is committed: `data/gesture/`, `computer_vision/models/users/` and
`voice_assistant/models/` are all git-ignored. The only dataset in source control is the
hand-authored voice intent text, which contains no user data.

---

## 12. Tests

```bash
pytest tests/                       # 138 unit + integration tests, ~4 s, no database needed
cd backend && python tests/test_api_flow.py   # 96 end-to-end API assertions (needs MongoDB)
```

`tests/` needs no MongoDB, Flask, webcam, MediaPipe or PyAutoGUI. Two fakes stand at the OS
boundary and nowhere else: `FakeKeyboard` subclasses the real backend and records key presses
instead of sending them — so a signature change in `KeyboardBackend` breaks the tests loudly — and
`FakeCom` scripts what PowerPoint would have answered, including the three slideshow states.

| File | Covers |
| --- | --- |
| `test_canonicalization.py` | translation / scale / rotation invariance, the exact canonical frame, aspect handling, malformed input |
| `test_gesture_model.py` | class list derivation, split-by-recording (and leakage assertion), the quality gate, the collector, artifact round-trip, corrupt- and stale-version model refusal, inference, graceful degradation, the intent gate, every fallback path |
| `test_voice_intent.py` | normalisation, number parsing, slide-vs-count disambiguation, intent classification, `NO_COMMAND` on ordinary speech, threshold bands, out-of-range rejection, the speech-recognizer interface |
| `test_integration.py` | the numbered scenarios below, plus regressions: the bindable commands, gesture toggling, debouncer semantics, boundary clamping, controller-capability fallback |
| `test_gesture_stability.py` | the repeat bug from every direction (dropped frame, ambiguous frame, unmapped pose), the neutral-hold rule measured against the old behaviour on identical input, the stabilizer's vote, warm-up, tie-breaking and pointer pass-through |
| `test_powerpoint_windows.py` | that the pointer can never emit `Ctrl+P` in **any** machine state, the pen refusal without a slideshow, drawing as a drag, erase through COM and its guarded fallback, pen-lift on every exit path, COM/keystroke navigation, and that the platform layer is inert off Windows |
| `test_wake_word.py` | the wake-word machine exhaustively — ordinary speech, a talk that is *about* vision, segmentation, mis-transcriptions, restarts, timeouts, two commands in one segment, concurrent callers — and that its output actually classifies on the trained model |
| `test_voice_continuous.py` | the service seam: continuous listening reaching the real interpreter and the real dispatcher, per-user state, and voice/gesture sharing one slide counter |
| `test_end_to_end.py` | the fixes.md §6 verification list, one test each, driving `GestureEngine.decide()` — the same method the camera loop calls — with time advanced per frame so the 900 ms cooldown runs at its real value |

`backend/tests/test_api_flow.py` covers the whole API including the new endpoints. Its voice
section uses a **voice-only session**, which needs no camera, so the full
`voice → intent → CommandIntent → dispatcher → controller` path is exercised on a machine with no
webcam.

The gesture-model tests train a real model in-session on synthetic landmarks rather than loading a
stub. It is written to a temp directory and never touches your real models.

---

## 13. Training and evaluation commands

Every command is deterministic for a fixed `--seed`, dataset and scikit-learn version. Run them
from the repository root.

### Personalized gesture model

```bash
# Collect data through the UI: Gesture settings -> Train my gestures.
# Or generate a synthetic dataset to exercise the pipeline without a camera:
python -m computer_vision.ml.training.synthesize_dataset --recordings 6 --frames 60 --verify

# Train (also evaluates and exports ONNX)
python -m computer_vision.ml.training.train_gesture_model --user <user_id>
python -m computer_vision.ml.training.train_gesture_model --subject synthetic:v1 --report reports/gesture.json

# Evaluate an existing model against the held-out split
python -m computer_vision.ml.training.evaluate_gesture_model --user <user_id> --split test

# Re-export ONNX from the portable weights (verified against the NumPy runtime)
python -m computer_vision.ml.training.export_gesture_model --user <user_id>
```

### Voice intent model

```bash
# Serialise the hand-authored utterances into the versioned dataset
python -m voice_assistant.training.build_intent_dataset --overwrite

# Train, evaluate and save
python -m voice_assistant.training.train_intent_model --report reports/voice.json

# Evaluate an existing model
python -m voice_assistant.training.evaluate_intent_model --split test
```

### Running VisionX

```bash
cd backend && python app.py     # http://127.0.0.1:5000
cd frontend && npm run dev      # http://localhost:5173
```

`--dataset-version` on every command; datasets are versioned and **never silently overwritten**.

---

## 14. Model quality

### Voice intent classifier — real measurements

912 hand-authored utterances, 15 classes, stratified 70/15/15 split, seed 42. Measured on the
**held-out test split (137 utterances)**:

| Metric | Value |
| --- | ---: |
| Accuracy | **0.942** |
| Macro F1 | **0.941** |
| Weighted F1 | 0.941 |
| Command-level accuracy (intent **and** parameters) | **0.942** |
| `NO_COMMAND` false-positive rate (argmax) | 0.074 |
| **False command rate at the 0.75 execute gate** | **0.007** |
| False command rate at a 0.90 gate | 0.000 |

Command-level accuracy equals intent accuracy, which means parameter extraction made **no** errors
on the test split — every misclassification was the intent, not the number.

Calibration (is a probability of *p* right about *p* of the time?):

| Confidence | n | mean p | accuracy |
| --- | ---: | ---: | ---: |
| 0.2–0.4 | 2 | 0.269 | 0.500 |
| 0.4–0.6 | 12 | 0.516 | 0.750 |
| 0.6–0.8 | 26 | 0.713 | 0.923 |
| 0.8–1.0 | 97 | 0.923 | 0.979 |

Monotone and slightly under-confident — which is the safe direction for a gate.

**Known weakness.** The residual confusion is `START_PRESENTATION` ↔ `END_PRESENTATION`
("open the slideshow" vs "close the slideshow" differ by one word, and character n-grams do not
help there). At the default 0.75 gate most of those land in the confirmation band rather than
executing, but it is a real limitation and more training data is the fix.

**One caveat, stated plainly.** While reviewing test errors I found and corrected four label
conflicts in my own dataset — "go to slide one" was labelled `FIRST_SLIDE` when it is exactly
`GO_TO_SLIDE(1)` and produces identical behaviour, and similar. Correcting them is legitimate data
cleaning, but it does mean the final test number was chosen after looking at that split once, so
treat 0.942 as mildly optimistic. The genuine errors that remained were left alone.

### Personalized gesture model — pipeline validated, accuracy not benchmarked

The gesture model is trained on landmarks a specific person produces in front of a specific webcam.
That data cannot be committed (it is per-user biometric-adjacent data) and cannot be produced in CI,
so the numbers reachable here come from `computer_vision/ml/synthetic.py`.

**Those numbers are a smoke test, not a benchmark.** On the synthetic dataset the model scores
1.000 accuracy / 1.000 macro F1 on the held-out split, which says only that the pipeline is wired
end to end — the synthetic classes are close to linearly separable by construction. Nothing here
tells you how well VisionX recognises real hands. Reporting it as if it did would be dishonest, and
`evaluate_gesture_model` prints a warning next to any model whose metadata says `synthetic: true`.

What *is* meaningfully measured:

| | |
| --- | --- |
| Generator fidelity | **99.3–99.9%** agreement between the synthetic hands and the shipped geometric recognizer's own labels (10 poses × 200 samples, seeds 7/42/1234) — the generator produces hand-like geometry, not noise. Reproduce with `synthesize_dataset --verify`. |
| Canonicalization | translation / scale / rotation invariance to float32 precision, asserted in tests |
| ONNX ≡ NumPy | max probability drift **1.8 × 10⁻⁷**; export is rejected above 1e-4 |
| scikit-learn ≡ artifact | max probability drift **1.8 × 10⁻⁷**; training aborts above 1e-4 |
| Inference latency | **0.010 ms** (ONNX Runtime) / **0.013 ms** (NumPy) per frame, single-threaded — against a 33 ms frame budget |

To get a real number, enrol on a real camera and run
`python -m computer_vision.ml.training.evaluate_gesture_model --user <id> --split test`. The report
includes per-class recall, the confusion matrix and the false-command-rate sweep, exactly as the
voice model does — the two share `multimodal/reporting.py` so they are judged identically.

### Why "false command rate" is the metric that matters

Wrongly changing a slide mid-sentence is far worse than ignoring an input the presenter can simply
repeat. Both models therefore report two numbers a plain accuracy hides:

- **from null** — a non-command input read as a command
- **wrong command** — a command read as a *different* command, which fires the wrong action

and a sweep of both against the confidence gate, so the default thresholds are chosen from the
held-out data rather than picked by feel.

---

## 15. Troubleshooting

| Symptom | Fix |
| ------- | --- |
| `No camera found at index 0` | Close Zoom/Teams/Camera app, check Windows *Camera privacy* settings, or pick a different camera on the session setup screen. |
| Session starts but no presentation window opens | Your browser blocked the pop-up. Allow pop-ups for VisionX and click the **Presentation window** button in the control bar. |
| The presentation window shows "This slide could not be rendered" | A `.pptx` needs **LibreOffice** on the server to convert it once. Check `pptxConverter.ready` in `GET /api/health`, install LibreOffice (or set `VISIONX_SOFFICE_PATH`), then re-upload the deck. A `.pdf` never needs a converter. Nothing is needed at presentation time. |
| Does presenting require Microsoft PowerPoint? | **No.** Web mode never opens, controls, focuses or requires it — see §4. PowerPoint COM is an optional *upload-time* fallback only, and `VISIONX_PPTX_CONVERTER=libreoffice` disables even that. |
| Slides do not move | Check the control window's status strip: if the command fired there but the presentation window did not follow, the window has lost its stream — it reconnects on its own, and the status line says "Reconnecting…". |
| Gestures never fire | Lower the confidence gate on the setup screen, improve lighting, and keep your whole hand in frame. |
| `Could not reach MongoDB` | Check `MONGO_URI` and that your IP is allow-listed in Atlas → Network Access. |
| No slide previews for a `.pptx` | Conversion needs LibreOffice on the server (PowerPoint COM is only a fallback). PDFs always render. Set `VISIONX_SOFFICE_PATH` if LibreOffice is installed somewhere unusual. |
| The first voice command of a talk is slow, later ones are fast | The models loaded on first use instead of at boot. Check the log for `Voice pipeline warm`; if it is missing, `VOICE_PREWARM` is `0` or no speech backend is installed. |
| The pointer trails your hand | Raise `CV_POINTER_SMOOTHING` towards 1 (it follows more closely at the cost of some jitter). If the whole UI is behind, the machine is dropping camera frames — check `fps` in the status strip. |
| Hand model missing | `python scripts/download_model.py` |
| "The voice intent model is not available" | `python -m voice_assistant.training.train_intent_model` |
| "No speech-to-text backend is installed" | `pip install -r backend/requirements-voice.txt`, then restart the API. The first utterance downloads the Whisper weights (~75 MB for `base.en`). |
| Voice hears you but runs nothing | Check the confidence in the panel. Below your execute threshold it asks for confirmation; below the confirm threshold it stays silent. Both sliders are on the Voice page. |
| Training says "not ready" | It needs ≥2 recordings for ≥3 gestures, including the OTHER/null class. Without the null class the model cannot learn to stay quiet. |
| Personalized model stopped being used | A corrupt or version-mismatched model is logged and skipped, and VisionX falls back to the geometric recognizer. The Gesture settings page shows the reason. Retrain to fix it. |
| Gestures got twitchier after training | The two recognizers use different confidence scales (§5). Raise the intent-gate margin on the Gesture settings page rather than the confidence gate. |

---

## 16. Scope

Deliberately **not** included: Kubernetes, microservices, Redis/Kafka, a custom-trained speech
recogniser (Whisper is pretrained and reimplementing it would be worse in every dimension), and
Google Slides support — `PresentationController` is an abstract base with
`WebPresentationController` (the default) and `PowerPointController` implemented today, and room for
a `GoogleSlidesController` later, but VisionX does not claim support that does not exist.

**Limitations, stated rather than hidden:**

- The personalized gesture model's accuracy on real hands is **unmeasured** here — see §14.
- `START_PRESENTATION` / `END_PRESENTATION` are the voice model's weakest pair.
- Only one session runs at a time: one webcam, one deck.
- The presentation window renders **slide images**, so a deck's animations, transitions, embedded
  video and speaker notes do not play. This is the deliberate cost of not driving PowerPoint: the
  layout, fonts and content are exactly the presenter's own, and every failure mode in §4's table
  is gone, but a build-by-build animation is not reproduced. A deck that depends on animation should
  run in `VISIONX_PRESENTATION_MODE=powerpoint`.
- A `.pptx` still needs **LibreOffice once, at upload**, to convert to PDF. This is the only
  remaining rendering dependency, it is not Microsoft Office, and nothing is needed at presentation
  time. A `.pdf` needs nothing at any point.
- Voice is English-only by default (`VISIONX_WHISPER_MODEL=base.en`); set a multilingual Whisper
  model to change that, but the intent classifier is trained on English utterances.
- **Multimodal fusion is a seam, not a feature.** `multimodal/context.py` publishes the live
  pointer position so a command like "highlight this" could resolve against it, and the gesture
  engine already populates it every frame. No intent consumes it today and the voice dataset
  contains no deictic utterances, because shipping commands the dispatcher cannot execute would be
  worse than leaving the seam empty and saying so.
- Enrolment takes real time: 11 classes × 3 recordings × 60 frames is roughly 10 minutes.

---

## 17. Configuration reference — presentation surface and latency

Environment variables, all optional, all read once at startup from `backend/.env`.

| Variable | Default | What it does |
| --- | --- | --- |
| `VISIONX_PRESENTATION_MODE` | `web` | `web` renders the deck in VisionX's own presentation window. `powerpoint` drives the PowerPoint installed on this machine, as VisionX did before — keystrokes, COM and the slideshow guard. |
| `VISIONX_SLIDE_RENDER_WIDTH` | `1920` | Default pixel width for a rendered slide when the client does not ask for one. |
| `VISIONX_SLIDE_RENDER_MAX_WIDTH` | `2560` | Ceiling. A render is never upscaled past the slide's own resolution either way. |
| `VISIONX_PPTX_CONVERTER` | `auto` | Which backend converts `.pptx` → PDF. `auto` tries **LibreOffice first**, then PowerPoint COM. `libreoffice` forbids PowerPoint outright — use this to enforce the guarantee rather than merely prefer it. `powerpoint` is legacy and needs Microsoft Office. |
| `VISIONX_SOFFICE_PATH` | *(search)* | Where to find LibreOffice. Empty means: `PATH`, then the usual install locations. |
| `CV_POINTER_SMOOTHING` | `0.5` | Fingertip smoothing, 0–1. Higher follows the hand more closely; lower is steadier. Governs the **continuous** pointer stream only — the debounce settings never touch it. |
| `VOICE_PREWARM` | `1` | Load Whisper and the intent model at boot on a background thread, and run Whisper's warm-up pass. Set to `0` on a machine that will never use voice. |

The gesture stability settings (`CV_DEBOUNCE_FRAMES`, `CV_COOLDOWN_MS`, `CV_STABILIZER_WINDOW`,
`CV_RELEASE_FRAMES`) are documented in §5 and are unchanged by the move to the web presentation —
they govern discrete commands, which behave identically on either surface.
