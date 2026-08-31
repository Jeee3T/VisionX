"""Canonical MongoDB schema for VisionX.

This module is the single description of every collection: field list (for
documentation and review) and the indexes that `config.database` actually
creates at boot. Changing the schema here changes the database.
"""

from pymongo import ASCENDING, DESCENDING

USERS = "users"
PRESENTATIONS = "presentations"
GESTURE_PREFERENCES = "gesture_preferences"
PRESENTATION_HISTORY = "presentation_history"
ANNOTATIONS = "annotations"
PERSONALIZATION = "personalization"
GESTURE_RECORDINGS = "gesture_recordings"
VOICE_COMMANDS = "voice_commands"

SCHEMA: dict[str, dict] = {
    USERS: {
        "description": "Registered users.",
        "fields": ["_id", "name", "email", "password", "profilePhoto", "createdAt"],
        "indexes": [{"keys": [("email", ASCENDING)], "unique": True}],
    },
    PRESENTATIONS: {
        "description": "Uploaded presentation files owned by a user.",
        "fields": [
            "_id", "userId", "title", "fileName", "storedName", "filePath",
            "fileType", "totalSlides", "thumbnails", "uploadedAt",
        ],
        "indexes": [{"keys": [("userId", ASCENDING), ("uploadedAt", DESCENDING)]}],
    },
    GESTURE_PREFERENCES: {
        "description": "One document per user mapping hand poses to commands.",
        "fields": [
            "_id", "userId", "nextSlideGesture", "previousSlideGesture",
            "pointerGesture", "annotationGesture", "clearGesture", "resetGesture",
        ],
        "indexes": [{"keys": [("userId", ASCENDING)], "unique": True}],
    },
    PRESENTATION_HISTORY: {
        "description": "One document per presentation session (READY/ACTIVE/COMPLETED).",
        "fields": [
            "_id", "userId", "presentationId", "presentationTitle", "status",
            "startTime", "endTime", "duration", "slidesNavigated",
            "annotationsMade", "commandsFired", "gestureCounts",
        ],
        "indexes": [
            {"keys": [("userId", ASCENDING), ("startTime", DESCENDING)]},
            {"keys": [("presentationId", ASCENDING)]},
        ],
    },
    ANNOTATIONS: {
        "description": "Ink strokes drawn on a slide during a session.",
        "fields": [
            "_id", "presentationId", "userId", "sessionId", "slideNumber",
            "annotationData", "createdAt",
        ],
        "indexes": [
            {"keys": [("presentationId", ASCENDING), ("slideNumber", ASCENDING)]},
            {"keys": [("userId", ASCENDING)]},
        ],
    },
    PERSONALIZATION: {
        "description": (
            "One document per user: multimodal opt-ins, thresholds and a pointer to "
            "the personalized gesture model. Separate from gesture_preferences so "
            "deleting personalization never touches a user's pose bindings."
        ),
        "fields": [
            "_id", "userId",
            "gesturePersonalizationEnabled", "gestureLearningConsent",
            "gestureModelVersion", "gestureModelTrainedAt", "gestureIntentMargin",
            "voiceEnabled", "voiceExecuteThreshold", "voiceConfirmThreshold",
            "voiceTranscriptRetention", "updatedAt", "createdAt",
        ],
        "indexes": [{"keys": [("userId", ASCENDING)], "unique": True}],
    },
    GESTURE_RECORDINGS: {
        "description": (
            "Metadata for one enrolment recording. The landmark features themselves "
            "live in the versioned dataset on disk, not in MongoDB; this row records "
            "where they went so they can be listed and deleted."
        ),
        "fields": [
            "_id", "userId", "recordingId", "label", "datasetVersion", "featureVersion",
            "frames", "rejectedFrames", "path", "meanDetectionScore", "meanBrightness",
            "createdAt",
        ],
        "indexes": [
            {"keys": [("userId", ASCENDING), ("createdAt", DESCENDING)]},
            {"keys": [("recordingId", ASCENDING)], "unique": True},
        ],
    },
    VOICE_COMMANDS: {
        "description": (
            "One document per interpreted voice utterance. Command-level telemetry "
            "only - raw audio is never stored, and the transcript is only kept when "
            "the user has left transcript retention on."
        ),
        "fields": [
            "_id", "userId", "sessionId", "transcript", "intent", "command",
            "parameters", "confidence", "band", "executed", "reason", "result",
            "modelVersion", "sttBackend", "latencyMs", "createdAt",
        ],
        "indexes": [
            {"keys": [("userId", ASCENDING), ("createdAt", DESCENDING)]},
            {"keys": [("sessionId", ASCENDING)]},
        ],
    },
}

# Relationships (enforced in the service layer, documented here):
#   User 1 - N Presentations           presentations.userId          -> users._id
#   User 1 - 1 GesturePreferences      gesture_preferences.userId    -> users._id
#   User 1 - N PresentationHistory     presentation_history.userId   -> users._id
#   Presentation 1 - N History         presentation_history.presentationId -> presentations._id
#   Presentation 1 - N Annotations     annotations.presentationId    -> presentations._id
#   User 1 - 1 Personalization         personalization.userId        -> users._id
#   User 1 - N GestureRecordings       gesture_recordings.userId     -> users._id
#   User 1 - N VoiceCommands           voice_commands.userId         -> users._id
#   Session 1 - N VoiceCommands        voice_commands.sessionId      -> presentation_history._id
