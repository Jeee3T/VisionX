"""Convert BSON documents into JSON-safe dictionaries."""

from datetime import datetime, date

from bson import ObjectId


def _value(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return [_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _value(v) for k, v in value.items()}
    return value


def serialize(doc: dict | None, drop: tuple[str, ...] = ()) -> dict | None:
    if doc is None:
        return None
    out = {k: _value(v) for k, v in doc.items() if k not in drop}
    if "_id" in out:
        out["id"] = out["_id"]
    return out


def serialize_many(docs, drop: tuple[str, ...] = ()) -> list[dict]:
    return [serialize(d, drop) for d in docs]
