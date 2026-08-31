# VisionX REST API

Base URL: `http://127.0.0.1:5000/api` (the Vite dev server proxies `/api` to it).

## Response envelope

Every endpoint returns the same shape.

```jsonc
// success
{ "success": true, "data": { }, "message": "..." }

// error
{ "success": false, "error": { "code": "VALIDATION_ERROR", "message": "..." } }
```

Common error codes: `VALIDATION_ERROR` (422), `UNAUTHORIZED` / `TOKEN_EXPIRED` / `INVALID_TOKEN` (401),
`FORBIDDEN` (403), `NOT_FOUND` (404), `CONFLICT` (409), `ENGINE_ERROR` / `CAMERA_UNAVAILABLE` (409),
`FILE_TOO_LARGE` (413), `DATABASE_ERROR` (503), `INTERNAL_ERROR` (500).

## Authentication

All routes except `POST /auth/register`, `POST /auth/login` and `GET /health` require:

```
Authorization: Bearer <jwt>
```

The streaming endpoints (`/engine/stream`, `/engine/preview`) and the file endpoints
(`/presentations/:id/slides/:n`, `/presentations/:id/render/:n`, `/presentations/:id/file`) also
accept `?token=<jwt>` because
`EventSource` and `<img>` cannot set headers.

**The authenticated user id is always taken from the token.** A `userId` in a request body is ignored.

---

## Auth

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| POST | `/auth/register` | `{name, email, password}` | `{token, user}` (201) |
| POST | `/auth/login` | `{email, password}` | `{token, user}` |
| GET | `/auth/me` | — | `{user}` |
| POST | `/auth/logout` | — | `{}` |

Password rules: 8–128 characters. Emails are unique and lower-cased.

## Users

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/users/me` | — | `{user}` |
| PUT | `/users/me` | `{name?, profilePhoto?}` | `{user}` |
| PUT | `/users/me/password` | `{currentPassword, newPassword}` | `{}` |

## Presentations

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| POST | `/presentations` | multipart: `file`, `title` | `{presentation}` (201) |
| GET | `/presentations` | `?search=` | `{presentations, count}` |
| GET | `/presentations/:id` | — | `{presentation}` incl. `recentSessions`, `annotationCount`, `fileExists` |
| PUT | `/presentations/:id` | `{title?, totalSlides?}` | `{presentation}` |
| DELETE | `/presentations/:id` | — | `{}` |
| GET | `/presentations/:id/slides/:n` | — | `image/png` — the 1.6x library thumbnail |
| GET | `/presentations/:id/render/:n` | `?w=` | `image/png` — a full-resolution slide |
| GET | `/presentations/:id/file` | — | the original file |

Accepted uploads: `.pdf`, `.pptx`, `.ppt`, max 50 MB (configurable via `MAX_UPLOAD_MB`).

A `.pptx` is converted to PDF **once, at upload, by headless LibreOffice** — Microsoft PowerPoint is
not required at any point, and is never involved at presentation time. `GET /health` reports
`pptxConverter.ready` so this can be checked before the first upload.

`/render/:n` is what the presentation window displays and `/slides/:n` is what the library shows;
they are deliberately different images. `w` is the requested pixel width (default
`VISIONX_SLIDE_RENDER_WIDTH`, 1920; capped at `VISIONX_SLIDE_RENDER_MAX_WIDTH`, 2560) and a render is
never upscaled past the slide's own resolution. Results are cached on disk per `(slide, width)` and
served with a 24-hour `max-age`, so the presentation window can prefetch the slides on either side of
the current one. `404` means the slide is outside the deck, or the deck could not be converted —
a `.pptx` needs LibreOffice on the server, once, at upload.

## Gestures

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/gestures/preferences` | — | `{preferences, poses, commands, defaults}` |
| PUT | `/gestures/preferences` | the five `*Gesture` fields | `{preferences, ...}` |

Validation: every command needs a pose, and no pose may drive two commands. Saving while a session is
running re-binds that session immediately.

## Sessions

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| POST | `/sessions` | `{presentationId?}` | `{session}` (201, status `READY`) |
| GET | `/sessions` | `?limit=&status=` | `{sessions, count}` |
| GET | `/sessions/:id` | — | `{session}` |
| PUT | `/sessions/:id` | `{status?, slidesNavigated?, annotationsMade?}` | `{session}` |
| POST | `/sessions/:id/complete` | optional client summary | `{session, summary}` |

`complete` stops the engine if it is running this session, flushes pending ink, and writes
`endTime`, `duration`, `slidesNavigated`, `annotationsMade`, `commandsFired` and `gestureCounts`.
Engine counters win whenever the engine actually ran; the client summary is the fallback.

## Annotations

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| POST | `/annotations` | `{presentationId, slideNumber, sessionId?, annotationData:{points[], colour, width, space?}}` | `{annotation}` (201) |
| GET | `/annotations/:presentationId/:slideNumber` | — | `{annotations, count}` |
| GET | `/annotations/presentation/:presentationId` | — | `{annotations, count}` |
| DELETE | `/annotations/:annotationId` | — | `{}` |
| DELETE | `/annotations/:presentationId/:slideNumber` | — | `{removed}` |

A stroke needs at least 2 and at most 5000 points, each `{x, y}` normalised to 0–1.

### Annotation coordinate spaces

`annotationData.space` records which coordinate system a stroke's points are in.
Strokes arrive from two places that do **not** agree, and conflating them puts ink
somewhere other than where it was drawn:

| `space` | Points are normalised over | Drawn |
| --- | --- | --- |
| `camera` *(default)* | the **camera frame** — the fingertip, from the gesture engine | stretched by the reach margin (0.15) onto the slide |
| `slide` | the **slide itself** — a mouse or touch stroke drawn on the canvas | as-is |

The presenter cannot comfortably reach the edges of the camera frame, so that
region is inset and stretched back over the slide when rendered. A `slide`-space
stroke that goes through the same stretch is displaced by up to 15% of the slide,
and further the closer to an edge it was drawn.

Omitting `space` means `camera`, because every stroke written before this field
existed came from the gesture engine. An unrecognised value is rejected (422)
rather than coerced — a typo must not silently pick a coordinate system.

## Analytics

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/analytics/dashboard` | `{stats, sessionsOverTime[14], gestureBreakdown, recentPresentations, recentSessions}` |
| GET | `/analytics/presentations` | `{presentations[]}` — sessions, minutes, slides, annotations, lastUsed |
| GET | `/analytics/gestures` | `{gestures[], total, timeline[]}` |

All values are Mongo aggregations over `presentation_history` / `annotations`.

## Engine

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| POST | `/engine/start` | `{sessionId, options:{cameraIndex?, confidenceThreshold?, debounceFrames?, cooldownMs?, mirror?, startSlide?, personalizationEnabled?, intentMargin?}}` | `{engine, session, presentation}` |
| POST | `/engine/start-voice` | `{sessionId, options?}` | `{engine, session, presentation}` — binds a session with **no camera** |
| POST | `/engine/stop` | — | `{summary}` |
| GET | `/engine/status` | — | engine snapshot + dispatcher state |
| POST | `/engine/command` | `{command, parameters?}` | `{result}` — manual dispatch, same path as a gesture |
| GET | `/engine/commands` | — | `{commands: [{command, label, bindable, parameters}]}` |
| POST | `/engine/slide` | `{slide}` | dispatcher state |
| GET | `/engine/cameras` | — | `{cameras: [0, 1, …]}` |
| GET | `/engine/stream` | — | `text/event-stream` |
| GET | `/engine/preview` | — | `multipart/x-mixed-replace` MJPEG |

Only one engine runs at a time (one webcam, one desktop). Starting a second returns
`ENGINE_ERROR` (409); a missing camera returns `CAMERA_UNAVAILABLE` (409) with a human-readable
message — never a stack trace.

`personalizationEnabled` defaults to whether the user has both opted in *and* has a trained
model; it never needs to be sent explicitly.

`/engine/start-voice` exists so that losing the webcam does not also lose voice control: it
creates the same session record and the same `CommandDispatcher`, just without a camera thread.

### Commands

Twelve commands exist. Five are bindable to a hand pose (unchanged); the other seven need a
parameter or are awkward to hold a pose for, and are reachable from voice, the control bar and
the keyboard fallback.

The `PowerPoint mode` column applies **only** when a session runs with
`VISIONX_PRESENTATION_MODE=powerpoint`. In the default web mode there is no keystroke, no mouse
event and no COM call: the command changes VisionX's own presentation state, the window is told, and
every one of the twelve is delivered. `tests/test_no_powerpoint.py` enforces this.

| Command | Bindable | Parameters | PowerPoint mode |
| --- | --- | --- | --- |
| `NEXT_SLIDE` | yes | `count?` (1–20) | Right Arrow |
| `PREVIOUS_SLIDE` | yes | `count?` (1–20) | Left Arrow |
| `VIRTUAL_POINTER` | yes | `state?` (bool; omitted = toggle) | `Ctrl+L` / `Ctrl+A` |
| `ANNOTATION_MODE` | yes | `state?` (bool; omitted = toggle) | `Ctrl+P` / `Ctrl+A` |
| `CLEAR_ANNOTATION` | yes | — | `E` |
| `GO_TO_SLIDE` | no | `slideNumber` (required) | digits + Enter |
| `FIRST_SLIDE` | no | — | Home |
| `LAST_SLIDE` | no | — | End |
| `START_PRESENTATION` | no | — | F5 |
| `END_PRESENTATION` | no | — | Esc |
| `BLACKOUT` | no | — | `B` |
| `WHITEOUT` | no | — | `W` |

A gesture supplies no parameters, so gesture behaviour is bit-for-bit what it always was.
`state` exists because voice distinguishes what a toggle cannot: saying "turn on the pen" twice
must leave the pen on. An out-of-range `slideNumber` is **rejected**, never clamped.

### SSE event types

```jsonc
{"type": "connected"}

{"type": "telemetry", "gesture": "PINKY_UP", "confidence": 0.93, "status": "HOLDING",
 "progress": 0.5, "command": "NEXT_SLIDE", "executed": false, "mode": "IDLE",
 "handDetected": true, "pointer": {"x": 0.51, "y": 0.42}, "lowLight": false,
 "idleSeconds": 0.0, "fps": 24.8, "timestamp": 1786475523.2,
 "source": "personalized", "modelVersion": "gm_2026…", "margin": 0.81, "gateReason": null}

{"type": "command", "source": "gesture", "command": "NEXT_SLIDE", "slide": 2,
 "parameters": {}, "delivered": true, "pointerActive": false, "annotationActive": false,
 "blankScreen": null, "currentSlide": 2, "totalSlides": 18, "slidesNavigated": 1}
// `source` is one of: gesture | voice | manual | keyboard

{"type": "voice", "transcript": "go to slide seven", "intent": "GO_TO_SLIDE",
 "command": "GO_TO_SLIDE", "parameters": {"slideNumber": 7}, "probability": 0.97,
 "band": "EXECUTE", "executed": true, "reason": "ok", "modelVersion": "vi_2026…"}

{"type": "enrollment", "label": "PINKY_UP", "accepted": 42, "targetFrames": 60,
 "progress": 0.7, "complete": false, "lastRejection": ""}

{"type": "training", "status": "RUNNING", "message": "…", "modelVersion": null}

{"type": "state",  "state": "RUNNING", "mode": "IDLE", "fps": 25.1, "bindings": {…},
 "recognizer": {"source": "personalized", "modelVersion": "gm_2026…", "runtime": "onnxruntime"},
 "intentGate": {"enabled": true, "minMargin": 0.15}, "gatedFrames": 3}
{"type": "error",  "code": "CAMERA_UNAVAILABLE", "message": "…"}
{"type": "annotations_saved",   "count": 3}
{"type": "annotations_cleared", "slide": 2}

// --- the presentation window (web mode) ---------------------------------
// The pointer channel. Published at camera frame rate, NOT rate-limited with
// telemetry, and coalescing: a client that falls behind receives the newest
// position rather than a backlog of stale ones. `x`/`y` are camera-normalised
// (0..1) - the reach margin is applied by whoever draws, which is also what the
// dispatcher persists, so live ink and replayed ink land in the same place.
{"type": "pointer", "x": 0.51, "y": 0.42, "drawing": true, "t": 1786475523.2}

// Stroke boundaries. BEGIN carries the point the pen went down on, because the
// pointer is moved into position before the pen is pressed.
{"type": "ink", "action": "BEGIN", "x": 0.30, "y": 0.40}
{"type": "ink", "action": "END"}
{"type": "ink", "action": "CLEAR"}

{"type": "mode", "pointerActive": true, "annotationActive": false}
{"type": "presentation", "action": "NEXT"}   // NEXT|PREVIOUS|GOTO|FIRST|LAST|
                                             // BLACKOUT|WHITEOUT|START|END
```

`status` values: `IDLE`, `LOW_CONFIDENCE`, `UNMAPPED`, `HOLDING`, `WAIT_NEUTRAL`, `COOLDOWN`,
`EXECUTED`.

---

## Personalization

Per-user settings, gesture enrolment, background training and data deletion.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/personalization/` | — | `{settings, gesture:{model, dataset, classes}, voice, storage}` |
| PUT | `/personalization/` | any of the settings below | the same payload |
| GET | `/personalization/enrollment` | — | `{steps[], progress, readyToTrain, …}` |
| POST | `/personalization/enrollment/camera/start` | `{options?}` | `{engine, plan}` |
| POST | `/personalization/enrollment/camera/stop` | — | `{stopped}` |
| POST | `/personalization/enrollment/recording/start` | `{label, frames?}` | capture state |
| GET | `/personalization/enrollment/recording` | — | `{capture, plan}` |
| POST | `/personalization/enrollment/recording/finish` | — | `{recordingId, frames, plan}` |
| POST | `/personalization/enrollment/recording/cancel` | — | `{cancelled}` |
| POST | `/personalization/train` | `{seed?}` | training state — **returns immediately** |
| GET | `/personalization/train/status` | — | `{status, message, model}` |
| DELETE | `/personalization/model` | — | deletes the trained model only |
| DELETE | `/personalization/recordings` | — | deletes collected landmark recordings only |
| DELETE | `/personalization/` | — | deletes both |

Settings (`PUT /personalization/`):

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `gestureLearningConsent` | bool | `false` | must be true before **any** landmark data is recorded |
| `gesturePersonalizationEnabled` | bool | `false` | use the trained model in sessions |
| `gestureIntentMargin` | 0.0–0.6 | `0.15` | minimum top-1 − top-2 probability gap |
| `voiceEnabled` | bool | `false` | enables every `/voice/*` endpoint |
| `voiceExecuteThreshold` | 0.4–0.99 | `0.75` | run a voice command automatically at/above this |
| `voiceConfirmThreshold` | 0.1–0.95 | `0.50` | ask for confirmation at/above this; ignore below |
| `voiceTranscriptRetention` | bool | `true` | store the recognised text with each command |

Turning `gestureLearningConsent` off also turns personalization off, and enrolment endpoints
return `CONSENT_REQUIRED` (403). `voiceConfirmThreshold` may not exceed `voiceExecuteThreshold`.

Training runs on a worker thread — `POST /personalization/train` returns immediately, progress
arrives as `{"type": "training"}` SSE events, and `GET /personalization/train/status` polls it.

## Voice

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/voice/status` | — | `{enabled, ready, canInterpretText, blockers[], intentModel, speechBackends, thresholds, continuous}` |
| GET | `/voice/commands` | — | `{intents: [{intent, label, command, examples[]}]}` |
| POST | `/voice/utterance` | `multipart/form-data`: `audio`, `execute?`, `sessionId?` | decision (below) — push-to-talk |
| POST | `/voice/stream` | `multipart/form-data`: `audio`, `execute?`, `sessionId?` | segment result (below) — **continuous listening** |
| POST | `/voice/stream/text` | `{text, execute?, sessionId?}` | segment result — the typed/test equivalent |
| GET | `/voice/wake` | — | `{state, buffered, wakeWords[], terminators[], captureTimeout, armedFor}` |
| POST | `/voice/wake/reset` | — | same, back to `LISTENING` |
| POST | `/voice/interpret` | `{text, execute?, sessionId?}` | decision (below) |
| POST | `/voice/confirm` | `{text, sessionId?}` | decision, executed |
| GET | `/voice/history` | `?limit&sessionId` | `{commands[]}` |
| DELETE | `/voice/history` | — | `{deleted}` |

### Continuous listening — `POST /voice/stream`

The browser records continuously and posts a segment each time the presenter stops speaking
(silence-endpointed, capped at 2.5 s). Each is transcribed and offered to
that user's wake-word machine; **most segments do nothing at all**. The trained intent model only
sees text that the presenter framed as a command with `"Vision" … "OK"`.

A segment result always carries `wake`, and carries a full decision only when a command completed:

```jsonc
{
  "wake": {
    "action": "EXECUTE",     // IDLE | ARMED | CAPTURING | EXECUTE | TIMEOUT
    "state": "LISTENING",    // LISTENING | CAPTURING  (state AFTER this segment)
    "command": "go to next slide",   // the text between the wake word and "OK"
    "heard": "vision go to next slide ok",
    "buffered": "go to next slide",
    "matchedWake": "vision", "matchedTerminator": "ok",
    "shouldExecute": true,
    // Every command completed in this segment, in the order spoken. One
    // recording can span a sentence boundary ("...slide two OK. Vision, next
    // slide, OK"), so this is occasionally longer than one - and every entry is
    // run, not just the last.
    "commands": ["go to next slide"]
  },
  "executed": true,
  "command": "NEXT_SLIDE",
  // ... plus every field of a decision, when one was produced.
  // "decisions" carries all of them when a segment completed more than one.
}
```

| `action` | Meaning |
| --- | --- |
| `IDLE` | ordinary speech; nothing was addressed to VisionX |
| `ARMED` | the wake word was heard; now collecting a command |
| `CAPTURING` | more command words collected, no terminator yet |
| `EXECUTE` | the terminator was heard — `command` was interpreted and, if it cleared the gate, run |
| `TIMEOUT` | the command never ended (12 s, or absurdly long); abandoned, back to listening |

The machine is stateful **per user** and lives on the server, so a page reload does not lose a
half-spoken command. `POST /voice/wake/reset` abandons one deliberately.

A decision:

```jsonc
{
  "transcript": "go to slide seven",
  "intent": "GO_TO_SLIDE", "intentLabel": "Go to a slide",
  "command": "GO_TO_SLIDE", "commandLabel": "Go to slide",
  "parameters": {"slideNumber": 7},
  "probability": 0.97,
  "band": "EXECUTE",           // EXECUTE | CONFIRM | REJECT
  "reason": "ok",              // ok | not_a_command | low_confidence | invalid_parameters | empty_transcript
  "message": "Go to slide 7",
  "executed": true,
  "requiresConfirmation": false,
  "result": { /* the dispatcher record + dispatcher state */ },
  "distribution": {"GO_TO_SLIDE": 0.97, "NEXT_SLIDE": 0.01},
  "modelVersion": "vi_20260829T092202",
  "speech": {"backend": "faster-whisper", "model": "base.en", "processingSeconds": 0.4}
}
```

`POST /voice/confirm` re-interprets the transcript rather than trusting a client-supplied
command: the browser can ask VisionX to run **what it heard**, not an arbitrary command.

`VOICE_DISABLED` (403) when the user has voice off; `VOICE_UNAVAILABLE` (503) when the intent
model has not been trained or no speech-to-text backend is installed. Gesture control is
unaffected in every one of those cases.

**Audio is never stored.** It is transcribed in memory and discarded. The transcript is stored
only while `voiceTranscriptRetention` is on.

## Health

`GET /health` — no authentication required.

```jsonc
{
  "status": "ok",
  "database": "connected",
  "engine": "STOPPED",
  "uploadDir": "…/backend/uploads",
  "voiceIntentModel": true,
  "speechBackends": {"faster-whisper": true, "openai-whisper": false},

  // Which surface a new session will drive.
  "presentationMode": "web",

  // Can this machine turn a .pptx into slides? The web presentation mode needs
  // no Microsoft Office at presentation time, but it does need a converter at
  // upload time - and finding that out from an upload that silently produced no
  // slides is the worst possible moment.
  "pptxConverter": {
    "policy": "auto",                    // VISIONX_PPTX_CONVERTER
    "libreOffice": "/usr/bin/soffice",   // null when not installed
    "powerPointFallback": false,         // Windows + policy allows it
    "ready": true,                       // a .pptx can be converted
    "pdfNeedsNoConverter": true          // a .pdf never needs any of this
  },

  // In web mode this is NOT_USED and no COM bridge is constructed - the health
  // check must not attach to PowerPoint to answer a question about VisionX.
  // Only in VISIONX_PRESENTATION_MODE=powerpoint does it probe for a slideshow
  // and return CONFIRMED / DENIED / UNKNOWN.
  "powerpoint": {"slideshow": "NOT_USED",
                 "reason": "VisionX is rendering the presentation itself."}
}
```
