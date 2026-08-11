"""Single response envelope used by every endpoint."""

from flask import jsonify


def success(data=None, message: str = "", status: int = 200):
    return jsonify({"success": True, "data": data if data is not None else {}, "message": message}), status


def failure(code: str, message: str, status: int = 400):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status
