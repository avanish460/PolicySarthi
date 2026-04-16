import os
import secrets
import time
from functools import wraps

from flask import jsonify, request
from werkzeug.security import check_password_hash


TOKENS: dict[str, dict] = {}
USER_LOOKUP: dict[str, dict] = {}
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", "43200"))


def seed_auth_state(users: list[dict]):
    USER_LOOKUP.clear()
    for user in users:
        USER_LOOKUP[user["username"]] = user


def authenticate(username: str, password: str):
    user = USER_LOOKUP.get(username)
    if not user:
        return None

    stored_password = user["password"]
    password_ok = False
    try:
        password_ok = check_password_hash(stored_password, password)
    except Exception:
        password_ok = False
    # Backward compatibility for existing plaintext records.
    if not password_ok:
        password_ok = stored_password == password
    if not password_ok:
        return None

    token = secrets.token_urlsafe(24)
    public_user = {
        "id": user["id"],
        "username": user["username"],
        "displayName": user["display_name"],
        "role": user["role"],
        "department": user["department"],
    }
    TOKENS[token] = {"user": public_user, "issued_at": time.time()}
    return {"token": token, "user": public_user}


def require_auth(roles: set[str] | None = None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return jsonify({"error": "Missing bearer token"}), 401

            token = header.split(" ", 1)[1]
            token_payload = TOKENS.get(token)
            if not token_payload:
                return jsonify({"error": "Invalid or expired token"}), 401
            if time.time() - token_payload["issued_at"] > TOKEN_TTL_SECONDS:
                TOKENS.pop(token, None)
                return jsonify({"error": "Invalid or expired token"}), 401
            current_user = token_payload["user"]

            if roles and current_user["role"] not in roles:
                return jsonify({"error": "Forbidden for this role"}), 403

            return fn(current_user, *args, **kwargs)

        return wrapper

    return decorator
