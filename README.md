# VisionX

**AI-Powered Multimodal Intelligent Presentation Control System**

Control a presentation with your hands or your voice, using a standard webcam and microphone — no
clicker, no sensor glove, no depth camera. VisionX detects hand landmarks with MediaPipe, classifies
them (either geometrically or with a model trained on *your* hands), transcribes speech locally with
Whisper, classifies what you said with a VisionX-trained intent model, and drives PowerPoint through
real key presses.

Both modalities converge on **one** command pipeline. Neither can reach PowerPoint any other way.

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
                                                                                          PowerPointController
                                                                                         COM ─┬─ PyAutoGUI
                                                                                              └─ slideshow guard
                     ↕
         Flask REST API ↔ MongoDB ↔ React frontend (SSE live telemetry)
```

**Three kinds of component, never conflated:**

| | What it is | Where |
| --- | --- | --- |
| **Pretrained, third-party** | MediaPipe hand landmarker · Whisper speech-to-text | `computer_vision/hand_detection/` · `voice_assistant/speech/` |
| **Trained by VisionX** | personalized gesture MLP · voice intent classifier | `computer_vision/ml/` · `voice_assistant/intent/` |
| **Rule-based** | geometric recognizer · stabilizer · debouncer · intent gate · wake word · parameter extraction · dispatcher · slideshow guard | `computer_vision/gesture_recognition/` · `computer_vision/ml/intent_gate.py` · `voice_assistant/wake/` · `voice_assistant/intent/parameters.py` · `presentation_controller/` |

---

## 1. What it does

| Gesture (default pose)      | Command             | Effect                                  |
| --------------------------- | ------------------- | --------------------------------------- |
| Pinky only                  | `NEXT_SLIDE`        | Right Arrow → next slide                |
| Thumb only                  | `PREVIOUS_SLIDE`    | Left Arrow → previous slide             |
| Index + middle              | `VIRTUAL_POINTER`   | Toggles the pointer; the cursor follows your fingertip. **Never** sends `Ctrl+P` |
| Index only                  | `ANNOTATION_MODE`   | Toggles the pen; your fingertip draws (a real drag, not just a move) |
| Index + middle + ring       | `CLEAR_ANNOTATION`  | Erases the ink on the current slide, leaving the pen as it was |

Poses are **not hardcoded to commands** — every binding lives in the user's `GesturePreferences`
document and can be reassigned in the UI. A saved remap applies to a running session immediately.

Seven more commands exist that a pose cannot express, because they take a parameter or are awkward
to hold a hand still for. Voice, the on-screen control bar and the keyboard fallback can all issue
them, and every one is a real PowerPoint shortcut:

| Command | Parameters | PowerPoint |
| --- | --- | --- |
| `GO_TO_SLIDE` | `slideNumber` | type the digits, then Enter |
| `FIRST_SLIDE` / `LAST_SLIDE` | — | Home / End |
| `START_PRESENTATION` / `END_PRESENTATION` | — | F5 / Esc |
| `BLACKOUT` / `WHITEOUT` | — | `B` / `W` |

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
- A webcam, and Microsoft PowerPoint on the machine that runs the backend (that is the machine whose
  keyboard VisionX drives)

### Platform support

**Windows is the supported and verified target**, and VisionX is built as a Windows application
rather than a cross-platform one that happens to run there. On Windows it does not merely send
keystrokes at PowerPoint — it **talks to PowerPoint** through COM
(`presentation_controller/windows.py`), and falls back to keystrokes only where it must.

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
2. **Upload** a `.pdf`, `.pptx` or `.ppt`. VisionX reads the real slide count and renders previews
   (PDFs directly; PowerPoint files need PowerPoint installed on the server to render previews —
   gesture control works either way).
3. Open the presentation → **Start session**. A `PresentationHistory` document is created (`READY`).
4. Pick the camera and the confidence gate, then start. The camera opens, MediaPipe begins tracking,
   and the session screen goes live.
5. **Open your slideshow in PowerPoint (F5)** — VisionX sends real key presses to whatever window has
   focus.
6. Gesture. The status strip shows the pose, a confidence pulse, the hold progress and the command
   that fired. Pointer and ink render on the slide canvas.
7. **End session** → the engine stops, ink is flushed to `Annotations`, and the session is written to
   history with its duration, slide count and gesture breakdown.
8. **History** and **Analytics** aggregate those documents — nothing on those pages is hardcoded.

Two optional detours off that path:

- **Gesture settings → Train my gestures** enrols your hands and trains a personalized model
  (§6). Everything above keeps working identically whether or not you do this.
- **Voice** turns on continuous listening in the session screen (§7): say
  "Vision <command> OK" at any point and it runs, with no interaction with the web app. A voice
  command travels the exact same dispatcher as a gesture, so it lands in the same history and the
  same analytics.

Keyboard fallback during a session: `←` `→` `P` `A` `E` go through the exact same dispatcher.

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
microphone ─► MediaRecorder (continuous, 3 s segments) ─► POST /api/voice/stream
           ─► Whisper (local, pretrained)   ─► transcript
           ─► wake-word machine ("Vision" … "OK")  ─► a command, or nothing at all
           ─► intent classifier (VisionX-trained) ─► intent + probability
           ─► parameter extraction (rule-based)   ─► slideNumber / count
           ─► confidence band ─► CommandIntent ─► the existing CommandDispatcher
```

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

The browser opens **one Server-Sent Events connection** per session
(`GET /api/engine/stream`). The engine rate-limits telemetry to ~12 events/second regardless of
camera frame rate, so the UI is live without any frame-rate REST polling. The camera thumbnail is a
separate MJPEG stream (`GET /api/engine/preview`) at 15 fps. Both accept the JWT as a query
parameter because `EventSource` and `<img>` cannot send headers.

Swapping SSE for WebSockets later touches exactly two files: `backend/services/event_bus.py` and
`frontend/src/hooks/useEngineStream.js`.

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
| `gesture_preferences`  | one per user — the five pose bindings         |
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
| `test_integration.py` | the numbered scenarios below, plus regressions: the five bindable commands, gesture toggling, debouncer semantics, boundary clamping, controller-capability fallback |
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
| Session starts but slides do not move | The slideshow window must have focus. VisionX sends real key presses to the foreground window. |
| Gestures never fire | Lower the confidence gate on the setup screen, improve lighting, and keep your whole hand in frame. |
| `Could not reach MongoDB` | Check `MONGO_URI` and that your IP is allow-listed in Atlas → Network Access. |
| No slide previews for a `.pptx` | Preview rendering needs PowerPoint (via `comtypes`) on the server. PDFs always render. Gesture control is unaffected. |
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
Google Slides support — `PresentationController` is an abstract base with `PowerPointController`
implemented today and room for a `GoogleSlidesController` later, but VisionX does not claim support
that does not exist.

**Limitations, stated rather than hidden:**

- The personalized gesture model's accuracy on real hands is **unmeasured** here — see §14.
- `START_PRESENTATION` / `END_PRESENTATION` are the voice model's weakest pair.
- Only one session runs at a time: one webcam, one desktop.
- Voice is English-only by default (`VISIONX_WHISPER_MODEL=base.en`); set a multilingual Whisper
  model to change that, but the intent classifier is trained on English utterances.
- **Multimodal fusion is a seam, not a feature.** `multimodal/context.py` publishes the live
  pointer position so a command like "highlight this" could resolve against it, and the gesture
  engine already populates it every frame. No intent consumes it today and the voice dataset
  contains no deictic utterances, because shipping commands the dispatcher cannot execute would be
  worse than leaving the seam empty and saying so.
- Enrolment takes real time: 11 classes × 3 recordings × 60 frames is roughly 10 minutes.
