"""User authentication routes and persistence helpers for ScorPred."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from hmac import compare_digest
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for, jsonify, g
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from db_models import User, db
from extensions import limiter

user_auth_bp = Blueprint("user_auth", __name__)

USER_SESSION_KEY = "user_email"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthStorageError(RuntimeError):
    """Raised when durable auth persistence fails."""


def _normalize_email(email: str | None) -> str:
    return str(email or "").strip().lower()


def _is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def _safe_next_url() -> str:
    candidate = (request.form.get("next") or request.args.get("next") or "").strip()
    if not candidate:
        return url_for("index")
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return url_for("index")
    if not candidate.startswith("/"):
        return url_for("index")
    return candidate


def _request_relative_url() -> str:
    if request.query_string:
        return f"{request.path}?{request.query_string.decode('utf-8', errors='ignore')}"
    return request.path


def _login_user(email: str, *, persistent: bool = True) -> None:
    session.clear()
    session[USER_SESSION_KEY] = _normalize_email(email)
    session.permanent = bool(persistent)
    session.modified = True


def _hash_password(password: str) -> str:
    return generate_password_hash(password)


def _legacy_hash_password(password: str, salt: str) -> str:
    hash_ = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}${hash_.hex()}"


def _is_legacy_hash(hashed: str) -> bool:
    # Werkzeug hashes are "method:params$salt$hash"; legacy ones are "salt$hash".
    return ":" not in hashed.split("$", 1)[0]


def _verify_password(password: str, hashed: str) -> bool:
    try:
        if _is_legacy_hash(hashed):
            salt, _hashval = hashed.split("$", 1)
            return compare_digest(_legacy_hash_password(password, salt), hashed)
        return check_password_hash(hashed, password)
    except Exception:
        return False


def _upgrade_legacy_hash(email: str, password: str, hashed: str) -> None:
    if not _is_legacy_hash(hashed):
        return
    try:
        db_user = User.query.filter_by(email=_normalize_email(email)).first()
        if db_user is not None:
            db_user.password_hash = _hash_password(password)
            db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.warning("Password hash upgrade failed.", exc_info=True)


def _serialize_user(user: User, *, include_password_hash: bool = False) -> dict:
    payload = user.to_dict()
    if include_password_hash:
        payload["password_hash"] = user.password_hash
    return payload


def _load_user(email: str | None, *, include_password_hash: bool = False) -> dict | None:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        return None
    user = User.query.filter_by(email=normalized_email).first()
    return _serialize_user(user, include_password_hash=include_password_hash) if user else None


def _save_user(user: dict) -> None:
    normalized_email = _normalize_email(user.get("email"))
    if not normalized_email:
        raise AuthStorageError("Email is required.")

    created_at_raw = user.get("created_at") or datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    created_at = (
        created_at_raw
        if isinstance(created_at_raw, datetime)
        else datetime.fromisoformat(str(created_at_raw).replace("Z", ""))
    )

    try:
        db_user = User.query.filter_by(email=normalized_email).first()
        if not db_user:
            password_hash = str(user.get("password_hash") or "").strip()
            if not password_hash:
                raise AuthStorageError("Unable to save the account right now.")
            db_user = User(
                email=normalized_email,
                password_hash=password_hash,
                created_at=created_at,
                saved_picks=user.get("saved_picks", []),
                history=user.get("history", []),
            )
            db.session.add(db_user)
        else:
            password_hash = str(user.get("password_hash") or "").strip()
            if password_hash:
                db_user.password_hash = password_hash
            db_user.saved_picks = user.get("saved_picks", [])
            db_user.history = user.get("history", [])
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise AuthStorageError("That email is already in use.") from exc
    except Exception as exc:
        db.session.rollback()
        raise AuthStorageError("Unable to save the account right now.") from exc


def current_user() -> dict | None:
    email = _normalize_email(session.get(USER_SESSION_KEY))
    if not email:
        return None
    cached = g.get("_auth_user_cache")
    if cached is not None and cached[0] == email:
        return cached[1]
    try:
        user = _load_user(email)
        g._auth_user_cache = (email, user)
        return user
    except Exception:
        current_app.logger.warning("Failed to load current user session.", exc_info=True)
        session.pop(USER_SESSION_KEY, None)
        return None


def login_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Login required to access this page.", "warning")
            return redirect(url_for("user_auth.login", next=_request_relative_url()))
        return view_func(*args, **kwargs)

    return wrapped


def api_login_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"status": "error", "error": "Authentication required"}), 401
        g.current_user = user
        return view_func(*args, **kwargs)
    return wrapped


def admin_required(view_func):
    from functools import wraps

    @wraps(view_func)
    @api_login_required
    def wrapped(*args, **kwargs):
        if not g.current_user.get("is_admin"):
            return jsonify({"status": "error", "error": "Administrator access required"}), 403
        return view_func(*args, **kwargs)
    return wrapped


@user_auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    next_url = _safe_next_url()
    if request.method == "POST":
        email = _normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))
        user = _load_user(email, include_password_hash=True)
        if user and _verify_password(password, user["password_hash"]):
            _upgrade_legacy_hash(email, password, user["password_hash"])
            _login_user(email, persistent=remember)
            flash("Signed in successfully.", "success")
            return redirect(next_url)
        flash("Invalid email or password.", "danger")
    return render_template("login.html", user=current_user(), next_url=next_url)


@user_auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def signup():
    next_url = _safe_next_url()
    if request.method == "POST":
        email = _normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        if not email or not password:
            flash("Email and password required.", "danger")
            return render_template("signup.html", user=current_user(), next_url=next_url)
        if not _is_valid_email(email):
            flash("Enter a valid email address.", "danger")
            return render_template("signup.html", user=current_user(), next_url=next_url)
        if len(password) < 8:
            flash("Use at least 8 characters for your password.", "danger")
            return render_template("signup.html", user=current_user(), next_url=next_url)
        if _load_user(email):
            flash("That account already exists. Try signing in instead.", "warning")
            return render_template("signup.html", user=current_user(), next_url=next_url)

        user = {
            "email": email,
            "password_hash": _hash_password(password),
            "created_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "saved_picks": [],
            "history": [],
        }
        try:
            _save_user(user)
        except AuthStorageError as exc:
            current_app.logger.error("Signup persistence failed for %s", email, exc_info=True)
            flash(str(exc), "danger")
            return render_template("signup.html", user=current_user(), next_url=next_url)

        _login_user(email, persistent=True)
        flash("Account created successfully.", "success")
        return redirect(next_url)
    return render_template("signup.html", user=current_user(), next_url=next_url)


@user_auth_bp.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("index"))


def save_user_pick(pick: dict) -> None:
    user = current_user()
    if not user:
        return
    user.setdefault("saved_picks", []).append(pick)
    user["history"] = user.get("history", [])
    try:
        _save_user(user)
    except AuthStorageError:
        current_app.logger.warning("Failed to save user pick.", exc_info=True)


def get_user_picks() -> list:
    user = current_user()
    return user.get("saved_picks", []) if user else []


def get_user_history() -> list:
    user = current_user()
    return user.get("history", []) if user else []
