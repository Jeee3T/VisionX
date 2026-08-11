from flask import Blueprint, g, request

from middleware.auth import require_auth
from services import user_service
from utils.responses import success
from utils.validators import clean_str, require_fields, validate_email, validate_password

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.post("/register")
def register():
    payload = request.get_json(silent=True) or {}
    require_fields(payload, ["name", "email", "password"])
    result = user_service.register(
        name=clean_str(payload, "name", 80),
        email=validate_email(payload["email"]),
        password=validate_password(payload["password"]),
    )
    return success(result, "Account created.", 201)


@auth_bp.post("/login")
def login():
    payload = request.get_json(silent=True) or {}
    require_fields(payload, ["email", "password"])
    result = user_service.login(validate_email(payload["email"]), payload["password"])
    return success(result, "Signed in.")


@auth_bp.get("/me")
@require_auth
def me():
    return success({"user": user_service.get_profile(g.user_id)})


@auth_bp.post("/logout")
@require_auth
def logout():
    # JWTs are stateless: the client drops the token. The endpoint exists so the
    # frontend has one place to call and the action can be audited later.
    return success(message="Signed out.")
