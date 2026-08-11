"""MongoDB Atlas connection + collection accessors."""

import logging

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from config.settings import settings
from models.schema import (
    ANNOTATIONS,
    GESTURE_PREFERENCES,
    PRESENTATION_HISTORY,
    PRESENTATIONS,
    SCHEMA,
    USERS,
)

logger = logging.getLogger(__name__)

_client: MongoClient | None = None
_db = None


def connect() -> None:
    """Open the Mongo connection and create indexes. Called once at boot."""
    global _client, _db
    if _db is not None:
        return

    _client = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=8000)
    _db = _client[settings.MONGO_DB_NAME]

    # Fail fast and loudly if Atlas is unreachable — every feature depends on it.
    _client.admin.command("ping")
    logger.info("Connected to MongoDB database '%s'", settings.MONGO_DB_NAME)
    _create_indexes()


def _create_indexes() -> None:
    """Create every index declared in models.schema."""
    for collection_name, definition in SCHEMA.items():
        for index in definition.get("indexes", []):
            try:
                _db[collection_name].create_index(index["keys"], unique=index.get("unique", False))
            except PyMongoError as exc:  # pragma: no cover - best effort
                logger.warning("Index on %s skipped: %s", collection_name, exc)


def get_db():
    if _db is None:
        connect()
    return _db


def is_connected() -> bool:
    if _client is None:
        return False
    try:
        _client.admin.command("ping")
        return True
    except PyMongoError:
        return False


# --- Collection shortcuts ----------------------------------------------------
def users():
    return get_db()[USERS]


def presentations():
    return get_db()[PRESENTATIONS]


def gesture_preferences():
    return get_db()[GESTURE_PREFERENCES]


def presentation_history():
    return get_db()[PRESENTATION_HISTORY]


def annotations():
    return get_db()[ANNOTATIONS]
