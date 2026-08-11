"""Domain errors that map cleanly onto the API error envelope."""


class ApiError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class ValidationError(ApiError):
    status_code = 422
    code = "VALIDATION_ERROR"


class AuthError(ApiError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(ApiError):
    status_code = 403
    code = "FORBIDDEN"


class NotFoundError(ApiError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(ApiError):
    status_code = 409
    code = "CONFLICT"


class EngineError(ApiError):
    status_code = 409
    code = "ENGINE_ERROR"
