"""PBKDF2 hashes, short-lived JWT cookies and DB-backed role checks."""

import hmac
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
import jwt
from flask import current_app, request, g, jsonify, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User

HASH_METHOD = "pbkdf2:sha256:600000"


def hash_password(password):
    return generate_password_hash(password, method=HASH_METHOD, salt_length=16)


def verify_password(stored, password):
    return check_password_hash(stored, password)


def issue_session(response, user):
    csrf = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "ver": user.token_version,
            "csrf": csrf,
            "iat": now,
            "exp": now + timedelta(hours=2),
            "iss": "truehire",
            "aud": "truehire-web",
        },
        current_app.config["SECRET_KEY"],
        algorithm="HS256",
    )
    flags = dict(
        secure=current_app.config["COOKIE_SECURE"],
        samesite="Strict",
        max_age=7200,
        path="/",
    )
    response.set_cookie("access_token", token, httponly=True, **flags)
    response.set_cookie("csrf_token", csrf, httponly=False, **flags)
    return response


def load_user():
    authorization = request.headers.get("Authorization", "")
    bearer = authorization.startswith("Bearer ")
    token = authorization[7:] if bearer else request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            current_app.config["SECRET_KEY"],
            algorithms=["HS256"],
            audience="truehire-web",
            issuer="truehire",
            options={"require": ["exp", "iat", "sub", "ver", "csrf"]},
        )
        user = db.session.get(User, int(payload["sub"]))
        if not user or user.token_version != payload["ver"]:
            return None
        if not bearer and request.method not in ("GET", "HEAD", "OPTIONS"):
            provided = request.headers.get("X-CSRF-Token", "")
            if not hmac.compare_digest(provided, payload["csrf"]):
                return None
        return user
    except (jwt.PyJWTError, ValueError, TypeError):
        return None


def require_auth(admin=False, page=False):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = load_user()
            if not user:
                return (
                    redirect("/login")
                    if page
                    else (
                        jsonify(error="Authentication required or invalid CSRF token."),
                        401,
                    )
                )
            if admin and user.role != "admin":
                return (jsonify(error="Administrator access required."), 403)
            g.user = user
            return fn(*args, **kwargs)

        return wrapped

    return decorator
