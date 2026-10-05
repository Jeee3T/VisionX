"""End-to-end API test covering the full demo path.

    cd backend && python tests/test_api_flow.py

Runs against the database configured in backend/.env and cleans up after itself.
Camera-dependent steps degrade gracefully when no webcam is present.
"""

import io
import sys
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR.parent))

from app import app  # noqa: E402
from config.database import (  # noqa: E402
    annotations, gesture_preferences, gesture_recordings, personalization,
    presentation_history, presentations, users, voice_commands,
)
from bson import ObjectId  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"  PASS  {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL  {label} {detail}")


def make_pdf(pages: int = 3) -> bytes:
    import fitz

    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text((72, 144), f"VisionX test slide {index + 1}", fontsize=28)
    data = doc.tobytes()
    doc.close()
    return data


def run() -> int:
    client = app.test_client()
    email = f"test_{uuid.uuid4().hex[:8]}@visionx.test"

    try:
        print("\n1. Health")
        response = client.get("/api/health")
        check("health returns ok", response.status_code == 200 and response.json["data"]["status"] == "ok")
        check("database connected", response.json["data"]["database"] == "connected")

        print("\n2. Auth")
        response = client.post("/api/auth/register", json={"name": "Test User", "email": email, "password": "supersecret1"})
        check("register succeeds", response.status_code == 201, response.get_data(as_text=True))
        token = response.json["data"]["token"]
        check("password never returned", "password" not in response.json["data"]["user"])
        auth = {"Authorization": f"Bearer {token}"}

        response = client.post("/api/auth/register", json={"name": "Dup", "email": email, "password": "supersecret1"})
        check("duplicate email rejected", response.status_code == 409)

        response = client.post("/api/auth/login", json={"email": email, "password": "wrongpassword"})
        check("bad password rejected", response.status_code == 401)

        response = client.post("/api/auth/login", json={"email": email, "password": "supersecret1"})
        check("login succeeds", response.status_code == 200)
        auth = {"Authorization": f"Bearer {response.json['data']['token']}"}

        response = client.get("/api/presentations")
        check("unauthenticated request blocked", response.status_code == 401)

        response = client.get("/api/auth/me", headers=auth)
        check("me returns the user", response.json["data"]["user"]["email"] == email)

        print("\n3. Gesture preferences")
        response = client.get("/api/gestures/preferences", headers=auth)
        prefs = response.json["data"]["preferences"]
        check("defaults created on registration", prefs["nextSlideGesture"] == "PINKY_UP")
        check("pose catalogue served", len(response.json["data"]["poses"]) >= 8)

        response = client.put("/api/gestures/preferences", headers=auth, json={**prefs, "nextSlideGesture": "THUMB_UP"})
        check("duplicate pose rejected", response.status_code == 422, response.get_data(as_text=True))

        remapped = {**prefs, "nextSlideGesture": "FOUR_FINGERS_UP"}
        response = client.put("/api/gestures/preferences", headers=auth, json=remapped)
        check("remap saved", response.status_code == 200 and
              response.json["data"]["preferences"]["nextSlideGesture"] == "FOUR_FINGERS_UP")

        print("\n4. Presentations")
        response = client.post(
            "/api/presentations",
            headers=auth,
            data={"title": "Quarterly Review", "file": (io.BytesIO(make_pdf(3)), "deck.pdf")},
            content_type="multipart/form-data",
        )
        check("upload succeeds", response.status_code == 201, response.get_data(as_text=True))
        presentation = response.json["data"]["presentation"]
        presentation_id = presentation["id"]
        check("slide count read from the file", presentation["totalSlides"] == 3, str(presentation.get("totalSlides")))
        check("thumbnails rendered", len(presentation.get("thumbnails") or []) == 3)
        check("server-generated filename", presentation["storedName"] != presentation["fileName"])

        response = client.post(
            "/api/presentations",
            headers=auth,
            data={"title": "Bad", "file": (io.BytesIO(b"nope"), "malware.exe")},
            content_type="multipart/form-data",
        )
        check("unsupported file type rejected", response.status_code == 422)

        response = client.get(f"/api/presentations/{presentation_id}/slides/1", headers=auth)
        check("slide preview served", response.status_code == 200 and response.mimetype == "image/png")
        thumbnail_bytes = len(response.data)

        # The presentation window's own render path: a full-resolution slide,
        # which is what the audience sees. Distinct from the preview above -
        # putting a 1.6x thumbnail on a projector is what made a deck look blurry.
        response = client.get(
            f"/api/presentations/{presentation_id}/render/1?w=1920", headers=auth)
        check("presentation-resolution slide rendered",
              response.status_code == 200 and response.mimetype == "image/png",
              # Never decoded as text: a successful response here is a PNG.
              f"status={response.status_code} type={response.mimetype}")
        check("the render is larger than the library thumbnail",
              len(response.data) > thumbnail_bytes, f"{len(response.data)} vs {thumbnail_bytes}")

        response = client.get(
            f"/api/presentations/{presentation_id}/render/99", headers=auth)
        check("a slide past the end of the deck is refused", response.status_code == 404)

        response = client.get("/api/presentations", headers=auth)
        check("library lists the upload", response.json["data"]["count"] == 1)

        # Cross-user isolation
        other = client.post("/api/auth/register", json={
            "name": "Other", "email": f"other_{uuid.uuid4().hex[:8]}@visionx.test", "password": "supersecret1"})
        other_auth = {"Authorization": f"Bearer {other.json['data']['token']}"}
        response = client.get(f"/api/presentations/{presentation_id}", headers=other_auth)
        check("another user cannot read the presentation", response.status_code == 404)
        response = client.get(f"/api/presentations/{presentation_id}/render/1", headers=other_auth)
        check("another user cannot render this deck's slides", response.status_code == 404)

        print("\n5. Session lifecycle")
        response = client.post("/api/sessions", headers=auth, json={"presentationId": presentation_id})
        check("session created READY", response.status_code == 201 and
              response.json["data"]["session"]["status"] == "READY")
        session_id = response.json["data"]["session"]["id"]

        response = client.post("/api/engine/start", headers=auth, json={"sessionId": session_id})
        camera_available = response.status_code == 200
        if camera_available:
            check("engine started with a camera", response.json["data"]["engine"]["state"] == "RUNNING")
            status = client.get("/api/engine/status", headers=auth).json["data"]
            check("status reports the live session", status["running"] is True)
            command = client.post("/api/engine/command", headers=auth, json={"command": "NEXT_SLIDE"})
            check("manual command dispatched", command.status_code == 200)
            check("slide advanced", command.json["data"]["result"]["slide"] == 2)
        else:
            check("no camera reported cleanly (not a crash)",
                  response.status_code == 409 and response.json["error"]["code"] in
                  ("CAMERA_UNAVAILABLE", "ENGINE_ERROR"),
                  response.get_data(as_text=True))

        print("\n6. Annotations")
        response = client.post("/api/annotations", headers=auth, json={
            "presentationId": presentation_id,
            "slideNumber": 1,
            "sessionId": session_id,
            "annotationData": {"points": [{"x": 0.1, "y": 0.2}, {"x": 0.3, "y": 0.4}], "colour": "#ef4444", "width": 4},
        })
        check("annotation saved", response.status_code == 201, response.get_data(as_text=True))
        annotation_id = response.json["data"]["annotation"]["id"]

        response = client.post("/api/annotations", headers=auth, json={
            "presentationId": presentation_id, "slideNumber": 1, "annotationData": {"points": [{"x": 0.1, "y": 0.2}]}})
        check("single-point stroke rejected", response.status_code == 422)

        response = client.get(f"/api/annotations/{presentation_id}/1", headers=auth)
        check("annotations listed per slide", response.json["data"]["count"] == 1)

        response = client.delete(f"/api/annotations/{annotation_id}", headers=auth)
        check("annotation deleted", response.status_code == 200)

        print("\n7. Complete session + history")
        # The engine's own counters win when it ran; this client summary is the
        # fallback path used when the engine was never started.
        response = client.post(f"/api/sessions/{session_id}/complete", headers=auth, json={
            "slidesNavigated": 5, "annotationsMade": 2, "commandsFired": 7,
            "gestureCounts": {"NEXT_SLIDE": 4, "PREVIOUS_SLIDE": 1, "ANNOTATION_MODE": 2}})
        check("session completed", response.status_code == 200 and
              response.json["data"]["session"]["status"] == "COMPLETED", response.get_data(as_text=True))
        completed = response.json["data"]["session"]
        check("duration recorded", completed["duration"] >= 0)
        expected_commands = int(completed.get("commandsFired", 0))
        check("commands recorded on the session", expected_commands > 0, str(expected_commands))
        if camera_available:
            check("engine counters are authoritative", expected_commands == 1, str(expected_commands))
        else:
            check("client summary used when no engine ran", expected_commands == 7, str(expected_commands))

        response = client.get("/api/sessions", headers=auth)
        check("history lists the session", response.json["data"]["count"] == 1)

        print("\n8. Analytics reflect real data")
        response = client.get("/api/analytics/dashboard", headers=auth)
        stats = response.json["data"]["stats"]
        check("dashboard counts presentations", stats["presentations"] == 1)
        check("dashboard counts sessions", stats["sessions"] == 1)
        check("dashboard gesture count matches stored history",
              stats["gesturesUsed"] == expected_commands, str(stats["gesturesUsed"]))
        check("sessions-over-time has 14 buckets", len(response.json["data"]["sessionsOverTime"]) == 14)

        response = client.get("/api/analytics/gestures", headers=auth)
        check("gesture analytics aggregated",
              response.json["data"]["total"] == expected_commands, str(response.json["data"]["total"]))

        response = client.get("/api/analytics/presentations", headers=auth)
        check("per-presentation analytics", len(response.json["data"]["presentations"]) == 1)

        print("\n9. Personalized gesture recognition")
        response = client.get("/api/personalization/", headers=auth)
        check("personalization defaults created", response.status_code == 200)
        data = response.json["data"]
        check("personalization is off until the user opts in",
              data["settings"]["gesturePersonalizationEnabled"] is False)
        check("gesture learning consent is off by default",
              data["settings"]["gestureLearningConsent"] is False)
        check("no personalized model yet", data["gesture"]["model"]["available"] is False)
        check("class list derived from the pose library",
              len(data["gesture"]["classes"]) == 11 and
              data["gesture"]["classes"][-1]["isNull"] is True)

        # Collection is refused until consent is given - Feature E.
        response = client.post("/api/personalization/enrollment/camera/start", headers=auth, json={})
        check("enrolment refused without consent",
              response.status_code == 403 and response.json["error"]["code"] == "CONSENT_REQUIRED",
              response.get_data(as_text=True))

        response = client.put("/api/personalization/", headers=auth,
                              json={"gestureLearningConsent": True})
        check("consent can be given", response.json["data"]["settings"]["gestureLearningConsent"] is True)

        response = client.get("/api/personalization/enrollment", headers=auth)
        plan = response.json["data"]
        check("enrolment plan lists every class", len(plan["steps"]) == 11)
        check("enrolment plan starts empty", plan["totalRecordingsCollected"] == 0)
        check("training blocked with no recordings", plan["readyToTrain"] is False)

        response = client.post("/api/personalization/train", headers=auth, json={})
        check("training with no data fails cleanly, not with a crash",
              response.status_code in (200, 409, 422), response.get_data(as_text=True))

        response = client.delete("/api/personalization/", headers=auth)
        check("personalization data can be deleted", response.status_code == 200)

        print("\n10. Voice assistant")
        response = client.get("/api/voice/status", headers=auth)
        check("voice status reachable", response.status_code == 200)
        voice = response.json["data"]
        check("voice is off until the user opts in", voice["enabled"] is False)
        check("voice status explains what is missing", isinstance(voice["blockers"], list))

        response = client.post("/api/voice/interpret", headers=auth, json={"text": "next slide"})
        check("voice refused while disabled",
              response.status_code == 403 and response.json["error"]["code"] == "VOICE_DISABLED")

        client.put("/api/personalization/", headers=auth, json={"voiceEnabled": True})
        response = client.get("/api/voice/commands", headers=auth)
        check("voice command catalogue lists 15 intents",
              len(response.json["data"]["intents"]) == 15, response.get_data(as_text=True))

        response = client.post("/api/voice/interpret", headers=auth,
                               json={"text": "go to slide 2", "execute": False})
        if response.status_code == 200:
            decision = response.json["data"]
            check("voice classifies a real command", decision["command"] == "GO_TO_SLIDE")
            check("voice extracts the slide number", decision["parameters"]["slideNumber"] == 2)
            check("interpret with execute=false runs nothing", decision["executed"] is False)

            response = client.post("/api/voice/interpret", headers=auth,
                                   json={"text": "today we will discuss our results", "execute": True})
            decision = response.json["data"]
            check("ordinary speech is not a command", decision["intent"] == "NO_COMMAND")
            check("ordinary speech executes nothing", decision["executed"] is False)

            response = client.get("/api/voice/history", headers=auth)
            check("voice telemetry recorded", len(response.json["data"]["commands"]) >= 2)
            check("raw audio never stored",
                  all("audio" not in entry for entry in response.json["data"]["commands"]))

            response = client.delete("/api/voice/history", headers=auth)
            check("voice history can be deleted", response.json["data"]["deleted"] >= 2)
        else:
            check("voice intent model missing is reported cleanly (not a crash)",
                  response.status_code == 503 and
                  response.json["error"]["code"] == "VOICE_UNAVAILABLE",
                  response.get_data(as_text=True))

        # A voice-only session needs no camera, so the full
        #   voice -> intent -> CommandIntent -> dispatcher -> controller
        # path can be exercised on a machine with no webcam. Whether the key press
        # is actually delivered depends on PyAutoGUI reaching a desktop; the test
        # asserts the command was dispatched and the session state moved, which is
        # the part VisionX owns.
        response = client.post("/api/sessions", headers=auth, json={"presentationId": presentation_id})
        voice_session_id = response.json["data"]["session"]["id"]
        response = client.post("/api/engine/start-voice", headers=auth,
                               json={"sessionId": voice_session_id})
        voice_session_started = response.status_code == 200
        check("voice-only session starts without a camera", voice_session_started,
              response.get_data(as_text=True))

        if voice_session_started:
            check("voice-only session reports no camera",
                  response.json["data"]["engine"]["cameraActive"] is False)

            # The architectural change, asserted over HTTP: a session drives the
            # VisionX presentation window, not the PowerPoint on this machine.
            engine_state = response.json["data"]["engine"]
            check("the session drives the web presentation surface",
                  engine_state["controller"]["controller"] == "web",
                  str(engine_state.get("controller")))
            check("no OS automation is involved",
                  engine_state["controller"].get("automation") == "none",
                  str(engine_state.get("controller")))
            check("every command is available on the web surface",
                  len(engine_state["controller"]["capabilities"]) == 13,
                  str(engine_state["controller"]["capabilities"]))

            response = client.post("/api/voice/interpret", headers=auth,
                                   json={"text": "go to slide 2", "sessionId": voice_session_id})
            decision = response.json["data"]
            check("voice command dispatched through the existing dispatcher",
                  decision["executed"] is True, response.get_data(as_text=True))
            check("voice command resolved GO_TO_SLIDE 2",
                  decision["command"] == "GO_TO_SLIDE" and decision["parameters"]["slideNumber"] == 2)
            check("dispatcher moved to the requested slide",
                  decision["result"]["currentSlide"] == 2, str(decision["result"]))
            check("dispatch records the voice source", decision["result"]["source"] == "voice")

            response = client.post("/api/voice/interpret", headers=auth,
                                   json={"text": "next slide", "sessionId": voice_session_id})
            check("voice NEXT_SLIDE advances from the current slide",
                  response.json["data"]["result"]["currentSlide"] == 3,
                  response.get_data(as_text=True))

            # A slide beyond the deck must be refused, not clamped.
            response = client.post("/api/voice/interpret", headers=auth,
                                   json={"text": "go to slide 99", "sessionId": voice_session_id})
            decision = response.json["data"]
            check("out-of-range slide refused, not clamped",
                  decision["executed"] is False and decision["reason"] == "invalid_parameters")

            # Ordinary speech during a live session must change nothing.
            response = client.post("/api/voice/interpret", headers=auth,
                                   json={"text": "as you can see on this slide revenue grew",
                                         "sessionId": voice_session_id})
            check("ordinary speech mid-session changes nothing",
                  response.json["data"]["executed"] is False)
            status = client.get("/api/engine/status", headers=auth).json["data"]
            check("slide unchanged after ordinary speech", status["currentSlide"] == 3,
                  str(status.get("currentSlide")))

            # --- continuous listening: "Vision <command> OK" -----------------
            # Same session, same dispatcher. The wake-word machine only decides
            # *when* there is a command; everything after it is unchanged.
            client.post("/api/voice/wake/reset", headers=auth)

            response = client.post("/api/voice/stream/text", headers=auth,
                                   json={"text": "as you can see revenue grew twelve percent",
                                         "sessionId": voice_session_id})
            segment = response.json["data"]
            check("continuous listening ignores ordinary speech",
                  segment["executed"] is False and segment["wake"]["action"] == "IDLE",
                  response.get_data(as_text=True))

            response = client.post("/api/voice/stream/text", headers=auth,
                                   json={"text": "next slide please",
                                         "sessionId": voice_session_id})
            check("a command phrase without the wake word does nothing",
                  response.json["data"]["executed"] is False)

            status = client.get("/api/engine/status", headers=auth).json["data"]
            check("slide unchanged by ordinary speech in continuous mode",
                  status["currentSlide"] == 3, str(status.get("currentSlide")))

            response = client.post("/api/voice/stream/text", headers=auth,
                                   json={"text": "vision go to slide 2 ok",
                                         "sessionId": voice_session_id})
            segment = response.json["data"]
            check("'Vision <command> OK' executes immediately",
                  segment["executed"] is True, response.get_data(as_text=True))
            check("the captured command is what the model classified",
                  segment["wake"]["command"] == "go to slide 2" and
                  segment["command"] == "GO_TO_SLIDE", str(segment.get("wake")))
            check("continuous command moved the deck",
                  segment["result"]["currentSlide"] == 2, str(segment.get("result")))

            response = client.get("/api/voice/wake", headers=auth)
            check("listening resumes after a command",
                  response.json["data"]["state"] == "LISTENING",
                  response.get_data(as_text=True))

            # The same command split across recorder segments must behave identically.
            client.post("/api/voice/stream/text", headers=auth,
                        json={"text": "vision", "sessionId": voice_session_id})
            response = client.get("/api/voice/wake", headers=auth)
            check("the wake word arms command mode",
                  response.json["data"]["state"] == "CAPTURING")

            client.post("/api/voice/stream/text", headers=auth,
                        json={"text": "go to slide 1", "sessionId": voice_session_id})
            response = client.post("/api/voice/stream/text", headers=auth,
                                   json={"text": "ok", "sessionId": voice_session_id})
            segment = response.json["data"]
            check("a command split across segments executes on the terminator",
                  segment["executed"] is True and segment["result"]["currentSlide"] == 1,
                  response.get_data(as_text=True))

            # Put the deck back where the assertions below expect it.
            client.post("/api/engine/command", headers=auth,
                        json={"command": "GO_TO_SLIDE", "parameters": {"slideNumber": 3}})

            # The control bar and voice share one dispatcher and one set of counters.
            response = client.post("/api/engine/command", headers=auth,
                                   json={"command": "GO_TO_SLIDE", "parameters": {"slideNumber": 2}})
            check("manual command accepts parameters too",
                  response.status_code == 200 and
                  response.json["data"]["result"]["currentSlide"] == 2,
                  response.get_data(as_text=True))

            response = client.post(f"/api/sessions/{voice_session_id}/complete", headers=auth, json={})
            summary = response.json["data"]["summary"]
            check("voice commands counted in the session summary",
                  summary["commandsFired"] >= 3, str(summary))
            check("voice commands appear in the gesture breakdown",
                  "GO_TO_SLIDE" in summary["gestureCounts"], str(summary["gestureCounts"]))

        response = client.get("/api/engine/commands", headers=auth)
        commands = response.json["data"]["commands"]
        check("command catalogue exposes 13 commands", len(commands) == 13)
        check("exactly six commands are pose-bindable",
              sum(1 for row in commands if row["bindable"]) == 6)

        print("\n11. Profile")
        response = client.put("/api/users/me", headers=auth, json={"name": "Renamed User"})
        check("profile updated", response.json["data"]["user"]["name"] == "Renamed User")

        response = client.put("/api/users/me/password", headers=auth,
                              json={"currentPassword": "wrong", "newPassword": "anotherpassword1"})
        check("wrong current password rejected", response.status_code == 401)

        response = client.put("/api/users/me/password", headers=auth,
                              json={"currentPassword": "supersecret1", "newPassword": "anotherpassword1"})
        check("password changed", response.status_code == 200)
        response = client.post("/api/auth/login", json={"email": email, "password": "anotherpassword1"})
        check("login with the new password", response.status_code == 200)

        print("\n12. Cleanup path")
        response = client.delete(f"/api/presentations/{presentation_id}", headers=auth)
        check("presentation deleted", response.status_code == 200)
        response = client.get("/api/presentations", headers=auth)
        check("library empty after delete", response.json["data"]["count"] == 0)

    finally:
        # Remove every document this run created.
        for email_pattern in ("test_", "other_"):
            for user in users().find({"email": {"$regex": f"^{email_pattern}"}}):
                uid = user["_id"]
                presentations().delete_many({"userId": uid})
                gesture_preferences().delete_many({"userId": uid})
                presentation_history().delete_many({"userId": uid})
                annotations().delete_many({"userId": uid})
                personalization().delete_many({"userId": uid})
                gesture_recordings().delete_many({"userId": uid})
                voice_commands().delete_many({"userId": uid})
                users().delete_one({"_id": ObjectId(uid)})

    print(f"\n{'=' * 60}")
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for label in FAILED:
            print(f"    - {label}")
    print(f"{'=' * 60}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(run())


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
