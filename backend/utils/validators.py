"""Request payload validation helpers."""

import re

from bson import ObjectId
from bson.errors import InvalidId

from utils.errors import ValidationError

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def require_fields(payload: dict, fields: list[str]) -> None:
    missing = [f for f in fields if not str(payload.get(f, "") or "").strip()]
    if missing:
        raise ValidationError(f"Missing required field(s): {', '.join(missing)}")


def clean_str(payload: dict, field: str, max_len: int = 200) -> str:
    value = str(payload.get(field, "") or "").strip()
    if len(value) > max_len:
        raise ValidationError(f"'{field}' must be at most {max_len} characters.")
    return value


def validate_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValidationError("Please enter a valid email address.")
    return email


def validate_password(password: str) -> str:
    if len(password) < 8:
        raise ValidationError("Password must be at least 8 characters long.")
    if len(password) > 128:
        raise ValidationError("Password must be at most 128 characters long.")
    return password


def to_object_id(value: str, label: str = "id") -> ObjectId:
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        raise ValidationError(f"Invalid {label}.")
