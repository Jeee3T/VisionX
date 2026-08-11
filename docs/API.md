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
(`/presentations/:id/slides/:n`, `/presentations/:id/file`) also accept `?token=<jwt>` because
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
| GET | `/presentations/:id/slides/:n` | — | `image/png` |
| GET | `/presentations/:id/file` | — | the original file |

Accepted uploads: `.pdf`, `.pptx`, `.ppt`, max 50 MB (configurable via `MAX_UPLOAD_MB`).

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
| POST | `/annotations` | `{presentationId, slideNumber, sessionId?, annotationData:{points[], colour, width}}` | `{annotation}` (201) |
| GET | `/annotations/:presentationId/:slideNumber` | — | `{annotations, count}` |
| GET | `/annotations/presentation/:presentationId` | — | `{annotations, count}` |
| DELETE | `/annotations/:annotationId` | — | `{}` |
| DELETE | `/annotations/:presentationId/:slideNumber` | — | `{removed}` |

A stroke needs at least 2 and at most 5000 points, each `{x, y}` normalised to 0–1.

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
| POST | `/engine/start` | `{sessionId, options:{cameraIndex?, confidenceThreshold?, debounceFrames?, cooldownMs?, mirror?, startSlide?}}` | `{engine, session, presentation}` |
| POST | `/engine/stop` | — | `{summary}` |
| GET | `/engine/status` | — | engine snapshot + dispatcher state |
| POST | `/engine/command` | `{command}` | `{result}` — manual dispatch, same path as a gesture |
| POST | `/engine/slide` | `{slide}` | dispatcher state |
| GET | `/engine/cameras` | — | `{cameras: [0, 1, …]}` |
| GET | `/engine/stream` | — | `text/event-stream` |
| GET | `/engine/preview` | — | `multipart/x-mixed-replace` MJPEG |

Only one engine runs at a time (one webcam, one desktop). Starting a second returns
`ENGINE_ERROR` (409); a missing camera returns `CAMERA_UNAVAILABLE` (409) with a human-readable
message — never a stack trace.

### SSE event types

```jsonc
{"type": "connected"}

{"type": "telemetry", "gesture": "PINKY_UP", "confidence": 0.93, "status": "HOLDING",
 "progress": 0.5, "command": "NEXT_SLIDE", "executed": false, "mode": "IDLE",
 "handDetected": true, "pointer": {"x": 0.51, "y": 0.42}, "lowLight": false,
 "idleSeconds": 0.0, "fps": 24.8, "timestamp": 1786475523.2}

{"type": "command", "source": "gesture", "command": "NEXT_SLIDE", "slide": 2,
 "delivered": true, "pointerActive": false, "annotationActive": false,
 "currentSlide": 2, "totalSlides": 18, "slidesNavigated": 1}

{"type": "state",  "state": "RUNNING", "mode": "IDLE", "fps": 25.1, "bindings": {…}}
{"type": "error",  "code": "CAMERA_UNAVAILABLE", "message": "…"}
{"type": "annotations_saved",   "count": 3}
{"type": "annotations_cleared", "slide": 2}
```

`status` values: `IDLE`, `LOW_CONFIDENCE`, `UNMAPPED`, `HOLDING`, `WAIT_NEUTRAL`, `COOLDOWN`,
`EXECUTED`.

## Health

`GET /health` → `{status, database, engine, uploadDir}`. No authentication required.
