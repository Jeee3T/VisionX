# VisionX — what was wrong, and what was changed

A response to [`fixes.md`](fixes.md), section by section.

Every item is described the same way: **the symptom you reported**, **the root cause found in the
code**, **the fix**, and **the test that proves it**. Nothing was disabled, removed or bypassed —
the personalized gesture model and the trained voice model both still run, unchanged, and every
command that worked before still works.

---

## Summary

| # | Reported | Root cause | Status |
| --- | --- | --- | --- |
| 1 | Gestures unstable; holding one repeats the command | Debouncer's neutral latch was re-armed by a **single** neutral frame; nothing smoothed per-frame predictions | Fixed |
| 2 | Virtual Pointer opens the Print dialog | `Ctrl+P` was sent blind; outside a slideshow that **is** Print | Fixed |
| 3 | Annotation on/off, drawing and clear all broken | Three separate bugs: keystroke landed on the wrong window; drawing never held the mouse button; `E` only erases in pen mode | Fixed |
| 4 | Voice needs a button press every time | Push-to-talk by design | Replaced with continuous listening + `"Vision … OK"` |
| 5 | Should be Windows-specific | Generic keystrokes only; no DPI awareness; no PowerPoint integration | Windows-first: COM + guarded keystrokes |
| 6 | End-to-end testing | — | 348 automated tests + 96 API assertions |

**Verification:** 348 automated tests pass, plus 96 end-to-end API assertions; the frontend builds
and lint is clean.

**One caveat, stated plainly:** this work was done on macOS. The Windows COM layer is exercised
through a scripted fake covering all three slideshow states, and it degrades to the previous
keystroke behaviour wherever it cannot connect — but **the COM calls themselves have never touched a
real PowerPoint**. There is a step-by-step list for you at
[What you need to test](#what-you-need-to-test); it takes about fifteen minutes.

---

## 1. Gesture recognition was unstable and commands repeated

### What you saw

> "when I show or hold gestures such as Pinky, Index, etc., the web app keeps changing the displayed
> action or slide number continuously/randomly"

### Root cause

Two independent defects, in two different files.

**(a) One dropped frame unlocked a repeat.**
`computer_vision/gesture_recognition/debouncer.py` required a "neutral" state between two firings of
the same command, so that holding a gesture could not fire it twice. But neutrality *latched
instantly* — a single neutral frame set the flag.

That sounds harmless and is not. A held gesture does **not** produce a clean run of identical frames:

* MediaPipe loses the hand for a frame or two, constantly;
* the personalized MLP occasionally emits a runner-up class;
* the intent gate *deliberately* neutralises ambiguous frames.

Any one of those re-armed the repeat. The streak rebuilt in six frames (a fifth of a second), the
command fired again, and the deck walked forward on its own.

Measured on a stream with one dropped frame in twelve — an ordinary webcam, not a bad one:

```
holding ONE gesture for 30 seconds
   before:  30 slide advances
   after:    1
```

**(b) Nothing smoothed the per-frame prediction.**
The recognizer classifies each frame independently, and whatever it said went straight to the command
mapper *and* straight to the UI. So a single misread frame could change which command you were
giving, and the on-screen action label flickered between poses several times a second — which is the
"displayed action changes randomly" half of the report.

### The fix

**A temporal stabilizer** — new file
[`computer_vision/gesture_recognition/stabilizer.py`](computer_vision/gesture_recognition/stabilizer.py).
The pose that reaches the command mapper is a **plurality vote over the last 5 frames**, not the
latest frame's guess. One or two stray frames are outvoted. When no pose commands a plurality it
reports `UNKNOWN`, which is the neutral state the debouncer already handles — the same contract the
intent gate uses.

It does not replace, bypass or second-guess the model. It consumes the model's predictions and
reports which one the model has actually been making.

**A sustained release rule** — `release_frames` in
[`debouncer.py`](computer_vision/gesture_recognition/debouncer.py). Neutrality now has to be *held*,
exactly like the gesture itself, before the same command may repeat. The default is the **full** hold
requirement — releasing a gesture takes as long as making one — because the stabilizer needs a few
frames to swing over to "no hand", so N dropped frames already produce close to N neutral ones.

> An earlier version of this fix used *half* the hold requirement. Review caught that it left only a
> ~66 ms margin, and a 100 ms MediaPipe dropout mid-hold still advanced a second slide. It is now
> symmetric, with a floor of 3 frames.

**Honest telemetry** — [`engine.py`](computer_vision/engine.py) now publishes the *stabilised* pose
as `gesture` (with the raw one alongside as `rawGesture`), so the UI shows what VisionX actually
believes rather than the noisiest available signal.

### Where the pipeline changed

```
camera → MediaPipe → recognizer → intent gate → stabilizer → mapper → debouncer → dispatcher
                                   per-frame     5-frame               hold ·
                                   ambiguity     vote                  release ·
                                                 [NEW]                 cooldown
```

`GestureEngine.decide()` was extracted so this exact path is directly testable — the camera loop
calls that same method, so the tests drive shipped code rather than a re-implementation of it.

### Proof

`tests/test_gesture_stability.py`, `tests/test_end_to_end.py` — including:

* `test_a_single_dropped_frame_cannot_repeat_a_held_gesture`
* `test_an_ambiguous_frame_cannot_repeat_a_held_gesture`
* `test_holding_a_gesture_for_ten_seconds_fires_exactly_once`
* `test_the_neutral_hold_rule_is_what_stops_the_deck_walking` — runs the old and new rules over
  **identical input** and asserts both numbers (30 vs 1), so the regression cannot come back quietly
* `test_a_realistic_mediapipe_dropout_cannot_repeat_a_held_gesture` — 1–5 frame dropouts, all must
  fire exactly once
* `test_a_deliberate_release_still_lets_the_command_repeat` — the fix must not make the feature
  unusable
* `test_every_bound_pose_held_fires_at_most_once` — not just the poses you named

---

## 2. The Virtual Pointer opened the Print dialog

### What you saw

> "When I try to use the Virtual Pointer, the PowerPoint/Windows Print dialog repeatedly opens."

### Root cause

This is the most important finding in the whole review, and it is a one-liner:

```
Ctrl+P   inside a running slideshow      →  pen
         on an ordinary PowerPoint window →  PRINT DIALOG
```

`PowerPointController.set_annotation(True)` sent `Ctrl+P` **unconditionally**. Nothing checked
whether a slideshow was actually running or which window had focus.

That explains the "repeatedly" too. Combined with defect 1(b) — `INDEX_UP` and `INDEX_MIDDLE_UP`
differ by exactly one bit in the finger signature, so a middle finger dipping below the extension
threshold for two frames turned the pointer into the pen — holding up two fingers produced a stream
of `Ctrl+P` presses into a PowerPoint that read every one of them as Print.

### The fix

**Ask PowerPoint instead of guessing.** New file
[`presentation_controller/windows.py`](presentation_controller/windows.py) attaches to the running
PowerPoint over COM and probes it. There are three answers, and distinguishing them is the whole fix:

| Probe | Meaning | What VisionX does |
| --- | --- | --- |
| `CONFIRMED` | Windows, PowerPoint is presenting | Set the pen — via COM; `Ctrl+P` only if COM is unavailable |
| `DENIED` | Windows, PowerPoint is **not** presenting | **Refuse**, with a message naming the reason. No keystroke. |
| `UNKNOWN` | Not Windows, or no COM binding | Send `Ctrl+P` — the historical behaviour, since we cannot ask and there is no evidence of danger |

Refusing everywhere would have broken every non-Windows setup; allowing everywhere is the bug.

**Belt and braces on the classification side.** The stabilizer from §1 means a stray `INDEX_UP` frame
can no longer reach the pen command at all.

**A hard invariant.** `set_pointer()` is now documented and tested to *never* emit `Ctrl+P` under any
circumstance — any COM state, any starting mode.

### Proof

`tests/test_powerpoint_windows.py`:

* `test_the_pointer_never_sends_ctrl_p_in_any_machine_state` — brute-forces all three COM states ×
  pen-first/not
* `test_the_pen_is_refused_rather_than_printing_when_no_slideshow_is_running`
* `test_repeatedly_arming_the_pen_without_a_slideshow_never_prints` — twenty attempts, zero keystrokes
* `test_ctrl_p_is_still_sent_when_the_slideshow_state_is_unknowable` — the degradation path is
  preserved, not removed

`tests/test_end_to_end.py::test_noisy_two_finger_frames_never_open_print` reproduces your exact
scenario: two fingers held up, 13% of frames misread as one finger, on a machine where `Ctrl+P` means
Print. Zero `Ctrl+P`.

---

## 3. PowerPoint annotation was not working

### What you saw

> "Annotation ON/OFF … Drawing/pen movement … Clear Annotation" — all not working.

Three separate bugs, one per symptom.

### 3a. Annotation ON/OFF

**Root cause.** Same as §2 — the pen was armed by a keystroke that frequently landed on the wrong
window (or opened Print). On top of that, the pen/pointer state was tracked in *two* places:
`PowerPointController` kept `_annotation_active`, and `CommandDispatcher` kept its own
`annotation_active`. A refused command left the dispatcher believing the pen was on while PowerPoint
had never been told — so the next annotation gesture toggled it *off* instead of on, and it looked
like the feature simply did not work.

**Fix.** The pen is set through COM (`View.PointerType = ppSlideShowPointerPen`), no keystroke. And
there is now exactly **one** copy of pointer/pen state, in `PowerPointController`, next to the thing
it describes; the dispatcher mirrors it back after every command, including refused ones.

### 3b. Drawing / pen movement

**Root cause.** PowerPoint's pen draws on a **drag**, not on a move. `stream_pointer` only called
`move_pointer` → `pyautogui.moveTo`. That walks the pen across the slide with no button held and
**leaves no mark at all**. This is why fingertip movement never drew anything.

**Fix.** `pen_down()` / `pen_up()` on the controller and backend, and the correct ordering in
[`dispatcher.py`](presentation_controller/dispatcher.py):

```
move to the first point  →  press the button  →  keep moving
```

Pressing before the first move would instead drag a line in from wherever the cursor happened to be.

The pen is now lifted on **every** exit path — the hand leaving frame, switching to the pointer,
turning annotation off, ending the presentation, stopping the session — so the mouse button is never
stranded down on your desktop.

### 3c. Clear Annotation

**Root cause.** `clear_annotation()` pressed `E`, which only erases while the pen is *already*
selected and the slideshow window has focus. Worse, it then reported the pen as off, while PowerPoint
kept it on — desyncing the two layers again.

**Fix.** `View.EraseDrawing()` over COM, which erases whatever pointer mode the show is in. Clearing
ink is **not** a mode change, so the pen is left exactly as it was and you can carry on drawing on a
now-clean slide. The keystroke fallback is guarded like the pen.

### Proof

`tests/test_powerpoint_windows.py`, `tests/test_end_to_end.py`:

* `test_drawing_holds_the_mouse_button_down` — asserts a `mouseDown` happens, exactly once, and
  **after** the first move
* `test_the_pen_is_lifted_when_the_hand_leaves_the_frame`
* `test_switching_to_the_pointer_lifts_the_pen`
* `test_the_pointer_moves_without_ever_pressing_a_button` — pointing is not drawing
* `test_clear_annotation_erases_the_ink_through_com`
* `test_clearing_ink_is_not_a_mode_change`
* `test_a_dead_keyboard_mid_stroke_does_not_leave_the_button_stuck`

---

## 4. Press-to-talk replaced with continuous listening

### What you asked for

> `[Listening] → "Vision" → [Command mode] → "go to next slide" → "OK" → Execute → [Listening again]`
> …with the trained voice model preserved.

### What was built

**The trained pipeline is untouched.** Faster-Whisper → TF-IDF → logistic regression → confidence
bands → parameter extraction → dispatcher, all exactly as before. Continuous listening was built
*around* it.

**Server side** — new package [`voice_assistant/wake/`](voice_assistant/wake/). The state machine is
deliberately **pure text**: transcripts in, decisions out, no audio, no model, no I/O. It decides
*when* there is something to classify; the trained model still decides what it means. Being pure, it
can be tested exhaustively — which matters, because it is the only thing standing between your
ordinary speech and your slides.

**Client side** — new hook
[`frontend/src/hooks/useContinuousVoice.js`](frontend/src/hooks/useContinuousVoice.js). You turn the
microphone on **once**, at the start of the talk, and never touch the web app again. The recorder
runs continuously and is cut into 3-second segments, each transcribed and offered to the machine.

**New endpoints** — `POST /api/voice/stream`, `POST /api/voice/stream/text`, `GET /api/voice/wake`,
`POST /api/voice/wake/reset`. Push-to-talk (`/api/voice/utterance`) still exists, unchanged; it is
what the Voice settings screen uses to test a phrase.

### Guards against a talk driving its own deck

This is the hard part of the problem, and the first implementation got it wrong. Review found that
these all executed a command:

| Spoken | Was captured as | Model said |
| --- | --- | --- |
| "our **vision** going forward is to move on to the next slide, okay" | "going forward is to move on to the next slide" | `NEXT_SLIDE` 0.997 |
| "we need to **provision** more servers and then move to the next slide, okay" | "more servers and then move to the next slide" | `NEXT_SLIDE` 0.853 |
| "the **vision** is simple, let's go back one slide, okay so…" | "is simple lets go back one slide" | `PREVIOUS_SLIDE` 0.941 |

The confidence gate cannot save you here — the captured words genuinely *are* a command. Three fixes,
all cheap and principled:

1. **No ordinary English word is a wake word.** `envision` and `provision` were removed; genuine
   mis-transcriptions (`visions`, `vision x`, `visionx`) stay, because a wake word that works two
   times in three is worse than none.
2. **The wake word must be *addressed*, not merely used.** One directly after a determiner or
   possessive is part of a sentence — "our vision…", "the vision…" — and is ignored.
3. **A captured command is capped at 10 words.** Every command VisionX can run fits in six; a run-on
   capture is far more likely to be speech that followed a stray wake word.

### Segmentation correctness

A 3-second recorder does not respect sentence boundaries, so the machine handles boundaries in the
order they were **actually spoken**:

* `"Vision go to next slide OK"` — one segment, executes immediately.
* `"Vision"` / `"go to next slide"` / `"OK"` — three segments, executes on the third.
* `"…slide five OK"` + `"Vision"` in one segment — completes the first command **and then** re-arms.
  (An earlier single-pass version handled the wake word first and silently threw the finished
  command away.)
* Two commands in one segment — **both** run, in order.
* A capture that never ends times out after 12 s and returns to listening, so an accidental wake word
  cannot swallow the rest of the talk. The segment that triggers the timeout is still processed, so
  you never have to say a command twice.

### Privacy

Continuous listening is **not** continuous recording. Each segment is transcribed in memory and
discarded; nothing is written to disk; segments below the silence threshold are never uploaded at
all. Transcripts reach MongoDB only if you have transcript retention on — unchanged from before.

### Proof

`tests/test_wake_word.py` (68 tests) and `tests/test_voice_continuous.py` (27 tests), including a
property test that fuzzes 2 000 sessions of word-salad and asserts the machine never hangs, never
raises, never reports a blank command and never exceeds the word cap. Plus 9 new end-to-end API
assertions in `backend/tests/test_api_flow.py` driving the real endpoints.

---

## 5. Windows-specific

VisionX is now built as a Windows application rather than a cross-platform one that happens to run
there.

| Area | What changed |
| --- | --- |
| **PowerPoint control** | COM attachment to the *running* PowerPoint (`GetActiveObject`, never `CreateObject` — launching a hidden second instance and driving *that* looks exactly like "nothing happens") |
| **Navigation** | `View.Next()`, `View.Previous()`, `View.GotoSlide(n)`; keystrokes as fallback |
| **Pen / eraser** | `View.PointerType`, `View.EraseDrawing()` |
| **Slideshow detection** | The three-state probe from §2 |
| **DPI** | Per-monitor DPI awareness at start-up, **before** PyAutoGUI caches the screen size. Every laptop ships scaled to 125–150%; without this the pointer lands at roughly 80% of where you point, and drifts worse toward the edges |
| **Keyboard timing** | A 12 ms inter-key pause — PowerPoint's slideshow window silently drops keystrokes delivered with no gap, which is what made "previous slide" occasionally do nothing |
| **Mouse** | Explicit button control, which is what actually draws |
| **Window handling** | Foreground-window lookup via `ctypes`/user32 (no extra dependency); slideshow window activation before a fallback keystroke |
| **Health** | `GET /api/health` reports the slideshow probe, so you can check before you start |
| **Camera** | `CAP_DSHOW` first (already correct) |
| **Paths / model loading** | Reviewed — already platform-neutral via `pathlib`; no changes needed |

Everything degrades cleanly: on a machine without Windows, without PowerPoint, or without a COM
binding, every COM call returns "unavailable" and the previous keystroke behaviour runs. Nothing in
the platform layer may raise into the camera loop.

`comtypes` is already a Windows dependency (it was there for `.pptx` thumbnails); `pywin32` is
auto-detected as an alternative if present.

---

## 6. End-to-end testing

`tests/test_end_to_end.py` implements your verification list, **one test per line**, driving
`GestureEngine.decide()` — the same method the camera loop calls — with time advanced per frame so
the 900 ms cooldown runs at its real value.

| Your requirement | Test |
| --- | --- |
| Gesture recognition is stable | `test_gesture_recognition_is_stable_under_realistic_noise` |
| Holding a gesture does not repeatedly trigger commands | `test_holding_a_gesture_does_not_repeatedly_trigger_commands` |
| Slide numbers do not randomly/continuously change | `test_a_hand_resting_in_frame_never_moves_a_slide`, `test_an_unsettled_hand_never_moves_a_slide` |
| `INDEX + MIDDLE` controls the Virtual Pointer | `test_index_middle_controls_the_virtual_pointer`, `test_the_pointer_follows_the_fingertip` |
| Virtual Pointer never opens Print | `test_the_virtual_pointer_never_opens_print` (all 3 machine states), `test_noisy_two_finger_frames_never_open_print` |
| `INDEX ONLY` enables/disables annotation | `test_index_only_enables_and_disables_annotation` |
| Fingertip movement draws on slides | `test_fingertip_movement_draws_on_the_slide` |
| Clear Annotation actually clears | `test_clear_annotation_actually_clears`, `test_clearing_does_not_turn_the_pen_off` |
| Voice listens without repeated UI interaction | `test_no_ui_interaction_is_needed_between_commands` |
| `"Vision <command> OK"` executes immediately | `test_the_brief_example_executes_immediately` |
| Voice returns to wake-word listening | `test_listening_resumes_after_every_command` |
| Gesture and voice do not interfere | `test_voice_and_gesture_share_one_consistent_state`, `test_voice_navigation_and_gesture_navigation_agree_on_the_slide` |

### Test inventory

| File | Tests | Covers |
| --- | ---: | --- |
| `tests/test_wake_word.py` | 68 | The voice state machine, exhaustively |
| `tests/test_voice_intent.py` | 67 | Existing — trained intent model (unchanged) |
| `tests/test_powerpoint_windows.py` | 45 | Print dialog, pen, erase, COM, threading |
| `tests/test_gesture_stability.py` | 34 | The repeat bug from every direction; the stabilizer |
| `tests/test_integration.py` | 32 | Existing — both modalities through one dispatcher |
| `tests/test_end_to_end.py` | 31 | Your §6 checklist; concurrency |
| `tests/test_gesture_model.py` | 29 | Existing — personalized model (unchanged) |
| `tests/test_voice_continuous.py` | 27 | Continuous listening at the service seam |
| `tests/test_canonicalization.py` | 15 | Existing — landmark invariance (unchanged) |
| **Total** | **348** | plus `backend/tests/test_api_flow.py` (96 assertions, needs MongoDB) |

Run them:

```bash
python -m pytest tests/ -q                    # 348, no MongoDB/camera/PowerPoint needed
cd backend && python tests/test_api_flow.py   # 96 API assertions (needs MongoDB)
cd frontend && npm run build
```

---

## Bugs found while reviewing this work

The change set was reviewed three times after being written — twice by an independent reviewer, once
by hand. Every round found real defects, including ones introduced by the fixes themselves. All are
fixed, and each has a regression test that fails without the fix.

**23 defects in total**, beyond the six items you reported.

### Round one — 13 defects

The most serious:

* **`set_pointer(False)` left the pen held and the mouse button down.** PowerPoint was then in
  *arrow* mode with the button held — where a drag **advances the slide** — so every attempted stroke
  skipped slides while the UI reported the pen as on. Reachable from voice ("turn the pointer off").
* **A refused Clear deleted your annotations anyway.** The in-memory buffer was dropped *before* the
  controller was asked, so a refusal threw the ink away while leaving it on the slide — and the
  matching MongoDB rows were deleted too.
* **The COM interface pointer was shared across thread apartments.** A proxy obtained on the camera
  thread and used from a Flask thread raises `RPC_E_WRONG_THREAD`, which is caught and reported as
  `UNKNOWN` — the one probe result that lets `Ctrl+P` through. A status poll could re-open the
  Print-dialog path.
* **The release margin was too thin** (see §1).
* **The wake vocabulary was too loose** (see §4).
* **Segments were silently dropped** while an upload was in flight, losing whole commands; and a
  segment captured at shutdown could still execute a command after you stopped listening.

### Round two — 3 more, all concurrency

* **A crash inside the camera loop.** `AnnotationController` is shared between the camera thread
  (adding points at frame rate) and Flask threads (ending and reading strokes to persist). Unguarded,
  `add_point` saw `_active` set on entry and `None` by the time it dereferenced it →
  `AttributeError`. The engine swallows subscriber exceptions, so **drawing just stopped silently,
  with the pen still held down**.
* **Every annotation duplicated in MongoDB.** `_flush_annotations` runs on the camera thread (every
  3 s while drawing) *and* on Flask threads, with no lock. Both read `_saved_strokes`, both sliced
  the same pending strokes, both inserted them across the `insert_many` round trip. Measured: **80
  rows persisted for 20 strokes.** Now 20.
  The fix uses a *dedicated* lock, not the service lock — `stop()` holds that while joining the
  camera thread, so a camera thread blocked on it inside a flush would deadlock the join.
* **The Print-dialog bug, reintroduced by the round-one COM fix.** Making the connection thread-local
  while leaving the probe *cache* shared meant a thread that failed to attach cached `UNKNOWN`, and
  the camera thread then read `UNKNOWN` instead of its own `DENIED`. One rejected COM call on a
  status-polling thread would have put Print back on screen. The cache is now thread-local too, with
  a shared epoch so `invalidate()` still clears every thread.

### Round three — 7 more

* **One dropped MediaPipe frame split every pen stroke.** The engine loop used the *raw* result's
  fingertip, not the stabilised one. The stabilizer deliberately carries the last known fingertip
  through a frame that had none — and that value was being thrown away. So a single lost frame
  mid-stroke made the pointer `None`, took the pointer-release branch, lifted the pen, and stored the
  annotation as two fragments with a gap. The debouncer's own docstring notes that MediaPipe loses
  the hand for a frame constantly, so this happened continually.
  *The end-to-end harness mirrored the same wrong line, which is why no test caught it. It now
  mirrors the fixed loop.*
* **Turning the pointer off broke the pen for the rest of the session.** `_handle_pointer` closed the
  stroke on the *on* path only. But `set_pointer(False)` also leaves pen mode, so the dispatcher
  ended up reporting the pen off while the buffer still had `is_drawing == True` — and
  `stream_pointer` guards `pen_down` behind `if not is_drawing`. Every subsequent stroke moved the
  pen across the slide **without ever pressing the button**: precisely the "annotation does not work"
  symptom, re-created.
* **Every annotation longer than 3 seconds was stored as fragments.** The periodic crash-safety
  flush called `annotations.end()` on a 3-second timer, truncating the stroke the presenter was
  still drawing; `begin()` then restarted the next one at a new point, leaving a gap at each join.
  The periodic flush now persists only *completed* strokes and never touches the live one.
* **The wake machine discarded a completed command on overflow.** When a runaway capture hit the
  10-word cap while scanning toward a wake word, `_consume` returned immediately instead of re-arming
  on that wake word — so `"Vision <20 words> Vision next slide OK"` lost `next slide` entirely.
* **The microphone leaked on unmount and on a double click.** `runningRef` was set *after* the
  `await getUserMedia`. Navigating away while the permission prompt was open let `teardown` run
  against a null stream, and the continuation then opened the mic and started uploading anyway —
  executing commands after the presenter had left the page. Two fast clicks gave two independent
  recorder chains uploading interleaved segments, which corrupts the server's stateful wake machine.
  Now claimed synchronously, with the late stream released if it was cancelled meanwhile.
* **A voice-only session inherited a dead engine.** `start_voice_only` never cleared `self.engine`,
  so after a camera crash it reported the old `ERROR` state instead of `VOICE_ONLY` and counted
  commands against the dead engine — double-counting the previous session in the summary.
* **DPI awareness reported success when it had failed.** `ctypes.windll` returns a failing HRESULT
  rather than raising, so an unchecked `SetProcessDpiAwareness` looked like success: the reliable
  `user32` fallback was never tried and the start-up warning never fired. On a 125%/150% display the
  pointer then lands ~80% of the way to your fingertip — the exact bug the function exists to
  prevent, reported as fixed.

Two hardening changes came out of the same round: `GestureEngine.stop()` drops to IDLE *before*
joining (the join has a 4-second timeout, so the camera thread could otherwise press the button again
after the caller lifted it), and the dispatcher now converts *any* unexpected input-layer exception
into `delivered: false` rather than letting an `OSError` from PyAutoGUI become a browser 500.

### Locking

VisionX now holds several locks across a camera thread and Flask request threads. The ordering was
traced by hand and then stress-tested: five threads through the real engine → dispatcher → controller
→ keyboard/COM wiring. No deadlock, no exceptions, no stranded mouse button, and the dispatcher and
controller agreed at the end. That is a permanent test
(`test_the_camera_thread_and_request_threads_do_not_deadlock`).

Independent review confirmed no lock-order inversion exists: the only nesting is on Flask threads
(`EngineService._lock` → `CommandDispatcher._lock` → `GestureEngine._lock`), and the camera thread
never takes `EngineService._lock` at all, so the reverse order cannot occur.

---

## What has already been verified

Everything below was run on this machine and passes. You do not need to repeat any of it — it is
listed so you know what is already covered, and what is therefore *not* on your list.

| Check | Result |
| --- | --- |
| `pytest tests/ backend/tests/` | **349 passed, 0 failed** |
| Flake check — 8 consecutive runs | 348 passed every run, no variance |
| `backend/tests/test_api_flow.py` (real MongoDB, real Flask) | **96 passed, 0 failed** |
| `npm run build` (frontend) | builds clean |
| `ruff check --select=F,E9` | all checks passed |
| `compileall` over every changed package | clean |
| App boots; `GET /api/health` | 200, database connected, 21 routes registered |
| Every reported problem re-run through the live pipeline | **18 scenarios passed, 0 failed** |

Two lines in that output look like errors and are not:

* `FAILED (No gesture recordings found …)` in the API-flow log is the **enrollment service**
  correctly reporting it has no samples to train on. That file is untouched by this work.
* `"slideshow": "UNKNOWN"` in `/api/health` is correct on macOS — there is no PowerPoint to attach
  to. On Windows it should read `CONFIRMED`, which is item 2 below.

---

## What you need to test

This work was done on **macOS**, so the COM calls could not be executed against a real PowerPoint.
They are exercised against a scripted fake covering all three slideshow states, and every one
degrades to the previous keystroke behaviour when it cannot connect — but the real calls have never
touched a real PowerPoint. **That is the gap, and it is what this list closes.**

Work through it on your Windows machine, in order. Each item says what to do, what should happen,
and what it means if it does not.

### 0. Setup

```powershell
pip install -r backend/requirements.txt     # installs comtypes on Windows
cd backend && python app.py                 # leave running
cd frontend && npm run dev                  # in a second terminal
```

Open your deck in PowerPoint and **start the slideshow (F5)**. Leave it presenting.

### 1. The automated suite runs on Windows too

```powershell
python -m pytest tests/ -q
```

**Expect:** 348 passed. These do not need a camera, a microphone or PowerPoint, so a failure here
means a genuine platform difference worth reporting back.

### 2. COM is attaching — do this before anything else

Open `http://localhost:5000/api/health`.

**Expect:** `"powerpoint": { "slideshow": "CONFIRMED" }` while the slideshow is running.

| What you see | What it means |
| --- | --- |
| `CONFIRMED` | COM is working. Everything below runs on the good path. |
| `DENIED` | COM works, but PowerPoint is not presenting. Press F5 and refresh. |
| `UNKNOWN` | COM is **not** attaching — read `powerpoint.reason`. The pen falls back to guarded keystrokes, so items 5–7 will behave differently. Worth reporting back with the reason string. |

Now press **Esc** to leave the slideshow and refresh health: it should flip to `DENIED`. That single
transition proves the probe is live rather than cached — and `DENIED` is the state that blocks
`Ctrl+P`. Press F5 again before continuing.

### 3. Gestures — the repeat bug

| Do | Expect |
| --- | --- |
| Hold **pinky only** up for a full 20 seconds | The deck advances **exactly one** slide, then stops. The slide number must not creep. |
| Lower your hand, raise pinky again, repeat 5 times | Exactly 5 slides forward — the fix must not make the gesture unresponsive |
| Hold **thumb only** for 10 seconds | Exactly one slide back |
| Talk with your hands for a minute, gesturing naturally but not deliberately | **Zero** slide changes |
| Watch the on-screen action label while holding a gesture | It should stay put, not flicker between poses |

*If a held gesture still walks the deck:* the release rule is not being reached — send me
`GET /api/engine/status` (it reports `releaseFrames`, `stabilizer` and `cooldownMs`).

### 4. Virtual Pointer — the Print dialog

**This is the most important item.** The Print dialog must not appear at any point.

| Do | Expect |
| --- | --- |
| Hold **index + middle** for a second | Pointer mode on; the cursor follows your fingertip |
| Move your hand around the slide | The cursor tracks it. **On a scaled display (125%/150%) check the cursor actually reaches your fingertip and the screen edges** — that is the DPI fix |
| Wave two fingers around for 30 seconds, including sloppy transitions where a finger drops | **No Print dialog. Ever.** No pen either |
| Hold index + middle again | Pointer mode off |

*If Print appears:* that is the headline bug and I need to know immediately — send the
`powerpoint.slideshow` value from health at that moment.

### 5. Annotation — on/off and drawing

| Do | Expect |
| --- | --- |
| Hold **index only** for a second | Pen mode on |
| Move your fingertip across the slide | **A line is actually drawn.** This is the "drawing never worked" fix — moving without drawing means the mouse button is not being held |
| Draw one continuous line for 5+ seconds | **One unbroken line**, not several fragments with gaps. (The 3-second flush used to chop it up) |
| Take your hand out of frame mid-stroke | The line ends cleanly; the cursor does not keep drawing to wherever it drifts |
| Bring your hand back, draw again | A new line starts normally |
| Hold index only again | Pen off |

### 6. Clear Annotation

| Do | Expect |
| --- | --- |
| Draw a few marks, then hold **index + middle + ring** | The ink is **removed from the slide** |
| Immediately draw again, without re-arming the pen | It still draws — clearing erases ink but does **not** turn the pen off |

### 7. The mode-switching bug that broke the pen

This sequence specifically. It is the defect that silently killed annotation for the rest of a
session:

1. Pen on (index only), draw a line.
2. Switch to the pointer (index + middle), then **off** again.
3. Pen on again (index only), draw.

**Expect:** step 3 draws normally. If the pen moves without leaving a mark, that regression is back.

### 8. Continuous voice

| Do | Expect |
| --- | --- |
| Click the mic once at the start | It stays on. You never touch it again. |
| Say **"Vision, go to next slide, OK"** | The slide advances immediately |
| Say it again 30 seconds later, without touching anything | It works again |
| Say "so as you can see on this slide, the revenue grew" | **Nothing happens** |
| Say "next slide please" (no wake word) | **Nothing happens** |
| Say "our vision going forward is to move to the next slide, okay" | **Nothing happens** — this one used to execute at 0.99 confidence |
| Say **"Vision"**, pause, then "go to slide seven", pause, then "OK" | Executes on the "OK" — the words may land in different 3-second recordings |
| Say **"Vision"** then nothing at all | After ~12 s it gives up and returns to listening |
| Talk normally for 2–3 minutes | **Zero** commands fired |

*Watch for:* an amber "Speech-to-text is running behind" message. If it appears, Whisper cannot keep
up on your machine — set `VISIONX_WHISPER_MODEL=tiny.en` and restart.

### 9. Both modalities together

| Do | Expect |
| --- | --- |
| Advance by gesture, then by voice, then by gesture | The slide counter stays correct throughout |
| Turn the pen on by gesture, then say "Vision, turn off the pen, OK" | Pen goes off; the next index-only gesture turns it back **on**, not off |
| Say "Vision, turn on the pen, OK" twice | Stays on — voice sets state, it does not toggle |

### 10. Clean shutdown

| Do | Expect |
| --- | --- |
| End the session while mid-stroke | The mouse button is released — your desktop is not left in a drag |
| Navigate away from the session page while the mic is on | The microphone light goes out; no further commands execute |

### If something fails

Send me: what you did, what happened, plus `GET /api/health` and `GET /api/engine/status` from the
moment it failed. Those two carry the slideshow probe, the recognizer in use, the stabilizer and
debounce settings, and the pointer/pen state as VisionX believes it — which is normally enough to
locate the fault without guessing.

---

## Files changed

**New (1 561 lines added across the change set):**

| File | Purpose |
| --- | --- |
| `presentation_controller/windows.py` | Windows platform layer: COM bridge, slideshow probe, DPI awareness |
| `computer_vision/gesture_recognition/stabilizer.py` | Temporal plurality vote over recognizer output |
| `voice_assistant/wake/wake_word.py` | The `"Vision … OK"` state machine (pure text) |
| `frontend/src/hooks/useContinuousVoice.js` | Always-on microphone capture |
| `tests/test_gesture_stability.py`, `test_powerpoint_windows.py`, `test_wake_word.py`, `test_voice_continuous.py`, `test_end_to_end.py` | 205 new tests |

**Modified:**

`presentation_controller/{powerpoint,dispatcher,keyboard,annotation,base}.py` ·
`computer_vision/engine.py` · `computer_vision/gesture_recognition/debouncer.py` ·
`backend/services/{engine_service,voice_service}.py` · `backend/routes/voice_routes.py` ·
`backend/{app.py,config/settings.py,requirements.txt}` ·
`frontend/src/components/session/VoicePanel.jsx` · `frontend/src/services/endpoints.js` ·
`frontend/src/pages/VoiceAssistant.jsx` · `README.md` · `docs/API.md` ·
`tests/{conftest,test_integration}.py` · `backend/tests/test_api_flow.py`

**Not changed, deliberately:** the personalized gesture MLP, the trained voice intent model, their
training pipelines, and the five gesture→command bindings. Nothing was disabled or bypassed to make
a symptom go away.
