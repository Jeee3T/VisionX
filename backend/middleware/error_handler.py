"""Central error handling: clients get a code + message, logs keep the detail."""

import logging

from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from config.settings import settings
from utils.errors import ApiError
from utils.responses import failure

logger = logging.getLogger(__name__)


def register_error_handlers(app):
    @app.errorhandler(ApiError)
    def handle_api_error(exc: ApiError):
        return failure(exc.code, exc.message, exc.status_code)

    @app.errorhandler(RequestEntityTooLarge)
    def handle_too_large(_exc):
        return failure(
            "FILE_TOO_LARGE",
            f"File exceeds the {settings.MAX_UPLOAD_MB} MB upload limit.",
            413,
        )

    @app.errorhandler(DuplicateKeyError)
    def handle_duplicate(_exc):
        return failure("CONFLICT", "That record already exists.", 409)

    @app.errorhandler(PyMongoError)
    def handle_mongo(exc: PyMongoError):
        logger.exception("Database error: %s", exc)
        return failure("DATABASE_ERROR", "The database is currently unavailable.", 503)

    @app.errorhandler(HTTPException)
    def handle_http(exc: HTTPException):
        return failure(
            exc.name.upper().replace(" ", "_"),
            exc.description or exc.name,
            exc.code or 500,
        )

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        # Never leak stack traces to the client.
        logger.exception("Unhandled error: %s", exc)
        return failure("INTERNAL_ERROR", "Something went wrong on our side.", 500)
