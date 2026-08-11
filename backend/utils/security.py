"""Password hashing and JWT issue/verify helpers."""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from config.settings import settings
from utils.errors import AuthError


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(hours=settings.JWT_EXPIRES_HOURS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> str:
    """Return the user id encoded in the token, or raise AuthError."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise AuthError("Session expired, please log in again.", code="TOKEN_EXPIRED")
    except jwt.InvalidTokenError:
        raise AuthError("Invalid authentication token.", code="INVALID_TOKEN")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthError("Invalid authentication token.", code="INVALID_TOKEN")
    return user_id
