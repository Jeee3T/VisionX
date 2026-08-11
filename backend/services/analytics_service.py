"""Analytics aggregated from real session history - nothing here is hardcoded."""

from datetime import datetime, timedelta, timezone

from bson import ObjectId

from computer_vision.command_mapping.gesture_mapper import COMMAND_LABELS
from config.database import annotations, presentation_history, presentations
from utils.serializers import serialize_many


def _oid(user_id: str) -> ObjectId:
    return ObjectId(user_id)


def _gesture_totals(user_id: str) -> list[dict]:
    pipeline = [
        {"$match": {"userId": _oid(user_id)}},
        {"$project": {"counts": {"$objectToArray": {"$ifNull": ["$gestureCounts", {}]}}}},
        {"$unwind": "$counts"},
        {"$group": {"_id": "$counts.k", "count": {"$sum": "$counts.v"}}},
        {"$sort": {"count": -1}},
    ]
    rows = list(presentation_history().aggregate(pipeline))
    return [
        {
            "command": row["_id"],
            "label": COMMAND_LABELS.get(row["_id"], row["_id"]),
            "count": int(row["count"] or 0),
        }
        for row in rows
    ]


def dashboard(user_id: str) -> dict:
    oid = _oid(user_id)

    totals = list(presentation_history().aggregate([
        {"$match": {"userId": oid}},
        {"$group": {
            "_id": None,
            "sessions": {"$sum": 1},
            "duration": {"$sum": {"$ifNull": ["$duration", 0]}},
            "slides": {"$sum": {"$ifNull": ["$slidesNavigated", 0]}},
            "annotations": {"$sum": {"$ifNull": ["$annotationsMade", 0]}},
            "commands": {"$sum": {"$ifNull": ["$commandsFired", 0]}},
        }},
    ]))
    aggregate = totals[0] if totals else {}

    gestures = _gesture_totals(user_id)
    gestures_used = sum(g["count"] for g in gestures) or int(aggregate.get("commands", 0))

    since = datetime.now(timezone.utc) - timedelta(days=13)
    daily = list(presentation_history().aggregate([
        {"$match": {"userId": oid, "startTime": {"$gte": since}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$startTime"}},
            "sessions": {"$sum": 1},
            "minutes": {"$sum": {"$divide": [{"$ifNull": ["$duration", 0]}, 60]}},
        }},
        {"$sort": {"_id": 1}},
    ]))
    by_day = {row["_id"]: row for row in daily}

    series = []
    for offset in range(13, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")
        row = by_day.get(day, {})
        series.append({
            "date": day,
            "label": day[5:],
            "sessions": int(row.get("sessions", 0)),
            "minutes": round(float(row.get("minutes", 0)), 1),
        })

    recent = serialize_many(
        presentations().find({"userId": oid}).sort("uploadedAt", -1).limit(5),
        drop=("filePath",),
    )
    recent_sessions = serialize_many(
        presentation_history().find({"userId": oid}).sort("startTime", -1).limit(5)
    )

    return {
        "stats": {
            "presentations": presentations().count_documents({"userId": oid}),
            "sessions": int(aggregate.get("sessions", 0)),
            "gesturesUsed": int(gestures_used),
            "annotations": annotations().count_documents({"userId": oid}),
            "slidesNavigated": int(aggregate.get("slides", 0)),
            "totalMinutes": round(float(aggregate.get("duration", 0)) / 60, 1),
        },
        "sessionsOverTime": series,
        "gestureBreakdown": gestures,
        "recentPresentations": recent,
        "recentSessions": recent_sessions,
    }


def presentation_analytics(user_id: str) -> dict:
    oid = _oid(user_id)
    rows = list(presentation_history().aggregate([
        {"$match": {"userId": oid, "presentationId": {"$ne": None}}},
        {"$group": {
            "_id": "$presentationId",
            "title": {"$last": "$presentationTitle"},
            "sessions": {"$sum": 1},
            "minutes": {"$sum": {"$divide": [{"$ifNull": ["$duration", 0]}, 60]}},
            "slides": {"$sum": {"$ifNull": ["$slidesNavigated", 0]}},
            "annotations": {"$sum": {"$ifNull": ["$annotationsMade", 0]}},
            "lastUsed": {"$max": "$startTime"},
        }},
        {"$sort": {"sessions": -1}},
        {"$limit": 20},
    ]))

    return {
        "presentations": [
            {
                "presentationId": str(row["_id"]),
                "title": row.get("title") or "Untitled",
                "sessions": int(row["sessions"]),
                "minutes": round(float(row["minutes"]), 1),
                "slidesNavigated": int(row["slides"]),
                "annotations": int(row["annotations"]),
                "lastUsed": row["lastUsed"].isoformat() if row.get("lastUsed") else None,
            }
            for row in rows
        ]
    }


def gesture_analytics(user_id: str) -> dict:
    gestures = _gesture_totals(user_id)
    total = sum(g["count"] for g in gestures)
    for gesture in gestures:
        gesture["share"] = round(gesture["count"] / total * 100, 1) if total else 0.0

    sessions = list(
        presentation_history()
        .find({"userId": _oid(user_id)}, {"startTime": 1, "commandsFired": 1, "duration": 1})
        .sort("startTime", -1)
        .limit(20)
    )
    timeline = [
        {
            "date": (s["startTime"].isoformat() if s.get("startTime") else None),
            "commands": int(s.get("commandsFired", 0)),
            "minutes": round(float(s.get("duration", 0)) / 60, 1),
        }
        for s in reversed(sessions)
    ]

    return {"gestures": gestures, "total": total, "timeline": timeline}
