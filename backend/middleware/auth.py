"""JWT auth middleware. The user id ALWAYS comes from the token, never from the body."""

from functools import wraps

from bson import ObjectId
from flask import g, request

from config.database import users
from utils.errors import AuthError
from utils.security import decode_token


def _extract_token() -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    # EventSource (SSE) cannot send custom headers, so the stream endpoint
    # accepts the same JWT as a query parameter.
    token = request.args.get("token", "").strip()
    if token:
        return token
    raise AuthError("Authentication required.")


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user_id = decode_token(_extract_token())
        user = users().find_one({"_id": ObjectId(user_id)})
        if not user:
            raise AuthError("Account no longer exists.")
        g.user_id = user_id
        g.user = user
        return fn(*args, **kwargs)

    return wrapper


def current_user_oid() -> ObjectId:
    return ObjectId(g.user_id)
