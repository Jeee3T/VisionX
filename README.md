# VisionX

**AI-Powered Vision-Based Intelligent Presentation Control System**

Control a presentation with your hands and a standard webcam — no clicker, no sensor glove, no depth
camera. VisionX detects hand landmarks with MediaPipe, classifies them into poses, filters out false
positives, and drives PowerPoint through real key presses.

```
Webcam → OpenCV → MediaPipe → Gesture recognizer → Debouncer → Gesture mapper
       → Command dispatcher → PowerPoint controller (PyAutoGUI) → PowerPoint
                     ↕
         Flask REST API ↔ MongoDB ↔ React frontend (SSE live telemetry)
```

---

## 1. What it does

| Gesture (default pose)      | Command             | Effect                                  |
| --------------------------- | ------------------- | --------------------------------------- |
| Pinky only                  | `NEXT_SLIDE`        | Right Arrow → next slide                |
| Thumb only                  | `PREVIOUS_SLIDE`    | Left Arrow → previous slide             |
| Index + middle              | `VIRTUAL_POINTER`   | Toggles the laser pointer, cursor follows your fingertip |
| Index only                  | `ANNOTATION_MODE`   | Toggles the pen; your fingertip draws   |
| Index + middle + ring       | `CLEAR_ANNOTATION`  | Erases the ink on the current slide     |

Poses are **not hardcoded to commands** — every binding lives in the user's `GesturePreferences`
document and can be reassigned in the UI. A saved remap applies to a running session immediately.

---

## 2. Requirements

- **Python 3.11 – 3.13** (3.13 verified)
- **Node.js 18+** (24 verified)
- **MongoDB** — MongoDB Atlas, or a local `mongod` for development
- A webcam, and Microsoft PowerPoint on the machine that runs the backend (that is the machine whose
  keyboard VisionX drives)

---

## 3. Setup

### Backend

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r backend/requirements.txt
python scripts/download_model.py  # one-time: fetches the pretrained MediaPipe hand model (~8 MB)

copy backend\.env.example backend\.env
```

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

Keyboard fallback during a session: `←` `→` `P` `A` `E` go through the exact same dispatcher.

---

## 5. Why gestures do not misfire

A command fires only when **all three** conditions hold (`computer_vision/gesture_recognition/debouncer.py`):

1. **Confidence gate** — the pose confidence, derived from the least certain finger's geometric margin
   times MediaPipe's detection score, clears the session threshold.
2. **Temporal persistence** — the same command survives N consecutive frames (default 6).
3. **Neutral state between repeats** — after a command fires, the same command cannot fire again until
   a neutral frame occurs: no hand, an unrecognised pose, or any pose you have left unbound. This is
   what stops one flick of the hand from skipping three slides.

A cooldown (default 900 ms) sits on top as a final guard.

---

## 6. Live updates

The browser opens **one Server-Sent Events connection** per session
(`GET /api/engine/stream`). The engine rate-limits telemetry to ~12 events/second regardless of
camera frame rate, so the UI is live without any frame-rate REST polling. The camera thumbnail is a
separate MJPEG stream (`GET /api/engine/preview`) at 15 fps. Both accept the JWT as a query
parameter because `EventSource` and `<img>` cannot send headers.

Swapping SSE for WebSockets later touches exactly two files: `backend/services/event_bus.py` and
`frontend/src/hooks/useEngineStream.js`.

---

## 7. Project structure

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
│   ├── command_mapping/        pose → command using the user's preferences
│   └── engine.py               the camera-loop thread
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
`computer_vision/` imports PyAutoGUI; the engine only emits command *names* through a callback.

---

## 8. Database

MongoDB collections (see `backend/models/schema.py`):

| Collection             | Purpose                                       |
| ---------------------- | --------------------------------------------- |
| `users`                | name, email, bcrypt hash, profilePhoto, createdAt |
| `presentations`        | userId, title, fileName, storedName, filePath, fileType, totalSlides, thumbnails, uploadedAt |
| `gesture_preferences`  | one per user — the five pose bindings         |
| `presentation_history` | one per session — status, times, duration, slidesNavigated, annotationsMade, commandsFired, gestureCounts |
| `annotations`          | presentationId, sessionId, slideNumber, annotationData, createdAt |

Relationships: User 1—N Presentations · User 1—1 GesturePreferences · User 1—N History ·
Presentation 1—N History · Presentation 1—N Annotations.

---

## 9. Security

- bcrypt password hashing, JWT bearer auth, every `/api/*` route except `/auth/register|login` and
  `/api/health` behind the auth middleware.
- **The user id always comes from the token**, never from the request body — every query is scoped by
  it, so one account cannot read another's presentations, sessions or annotations.
- Uploads are validated on extension *and* MIME type, size-capped, stored under a server-generated
  UUID filename, and the resolved path is asserted to stay inside `UPLOAD_DIR`.
- Errors return a `{code, message}` pair; stack traces stay in the server log. Secrets live in `.env`
  (git-ignored); only `.env.example` is committed.

---

## 10. Tests

```bash
cd backend
python tests/test_api_flow.py
```

47 assertions across health, auth, cross-user isolation, gesture preferences, upload/validation,
session lifecycle, live engine start, annotations, history and analytics. It creates and removes its
own data. Camera-dependent assertions adapt when no webcam is present.

---

## 11. Troubleshooting

| Symptom | Fix |
| ------- | --- |
| `No camera found at index 0` | Close Zoom/Teams/Camera app, check Windows *Camera privacy* settings, or pick a different camera on the session setup screen. |
| Session starts but slides do not move | The slideshow window must have focus. VisionX sends real key presses to the foreground window. |
| Gestures never fire | Lower the confidence gate on the setup screen, improve lighting, and keep your whole hand in frame. |
| `Could not reach MongoDB` | Check `MONGO_URI` and that your IP is allow-listed in Atlas → Network Access. |
| No slide previews for a `.pptx` | Preview rendering needs PowerPoint (via `comtypes`) on the server. PDFs always render. Gesture control is unaffected. |
| Hand model missing | `python scripts/download_model.py` |

---

## 12. Scope

Deliberately **not** included: Kubernetes, microservices, Redis/Kafka, custom-trained models
(MediaPipe's pretrained hand model is sufficient), and Google Slides support — `PresentationController`
is an abstract base with `PowerPointController` implemented today and room for a
`GoogleSlidesController` later, but VisionX does not claim support that does not exist.
