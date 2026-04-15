import secrets
from functools import wraps

from flask import jsonify, request


TOKENS: dict[str, dict] = {}
USER_LOOKUP: dict[str, dict] = {}


def seed_auth_state(users: list[dict]):
    USER_LOOKUP.clear()
    for user in users:
        USER_LOOKUP[user["username"]] = user


def authenticate(username: str, password: str):
    user = USER_LOOKUP.get(username)
    if not user or user["password"] != password:
        return None

    token = secrets.token_urlsafe(24)
    public_user = {
        "id": user["id"],
        "username": user["username"],
        "displayName": user["display_name"],
        "role": user["role"],
        "department": user["department"],
    }
    TOKENS[token] = public_user
    return {"token": token, "user": public_user}


def require_auth(roles: set[str] | None = None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return jsonify({"error": "Missing bearer token"}), 401

            token = header.split(" ", 1)[1]
            current_user = TOKENS.get(token)
            if not current_user:
                return jsonify({"error": "Invalid or expired token"}), 401

            if roles and current_user["role"] not in roles:
                return jsonify({"error": "Forbidden for this role"}), 403

            return fn(current_user, *args, **kwargs)

        return wrapper

    return decorator
