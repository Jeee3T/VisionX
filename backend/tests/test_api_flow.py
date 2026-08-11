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
from config.database import annotations, gesture_preferences, presentation_history, presentations, users  # noqa: E402
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
    user_id = None

    try:
        print("\n1. Health")
        response = client.get("/api/health")
        check("health returns ok", response.status_code == 200 and response.json["data"]["status"] == "ok")
        check("database connected", response.json["data"]["database"] == "connected")

        print("\n2. Auth")
        response = client.post("/api/auth/register", json={"name": "Test User", "email": email, "password": "supersecret1"})
        check("register succeeds", response.status_code == 201, response.get_data(as_text=True))
        token = response.json["data"]["token"]
        user_id = response.json["data"]["user"]["id"]
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

        response = client.get("/api/presentations", headers=auth)
        check("library lists the upload", response.json["data"]["count"] == 1)

        # Cross-user isolation
        other = client.post("/api/auth/register", json={
            "name": "Other", "email": f"other_{uuid.uuid4().hex[:8]}@visionx.test", "password": "supersecret1"})
        other_auth = {"Authorization": f"Bearer {other.json['data']['token']}"}
        response = client.get(f"/api/presentations/{presentation_id}", headers=other_auth)
        check("another user cannot read the presentation", response.status_code == 404)

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

        print("\n9. Profile")
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

        print("\n10. Cleanup path")
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
