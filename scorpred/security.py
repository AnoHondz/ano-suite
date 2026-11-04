"""Application security helpers for secrets, CSRF, and chat rate limiting."""

from __future__ import annotations

from collections import defaultdict, deque
import hmac
import secrets
import time

from flask import current_app, jsonify, request, session

CSRF_SESSION_KEY = "_csrf_token"
_CHAT_RATE_STATE: dict[str, deque[float]] = defaultdict(deque)


def configure_security(app, secret_key: str | None = None) -> None:
    """Configure runtime security defaults for the Flask app."""
    resolved_secret = (secret_key or "").strip() or secrets.token_hex(32)
    app.config["SECRET_KEY"] = resolved_secret
    app.config.setdefault("WTF_CSRF_ENABLED", True)
    app.config.setdefault("CHAT_RATE_LIMIT_COUNT", 8)
    app.config.setdefault("CHAT_RATE_LIMIT_WINDOW_SECONDS", 60)

    if not (secret_key or "").strip():
        app.logger.warning(
            "SECRET_KEY not set — using an ephemeral key. "
            "Sessions will NOT persist across restarts or Gunicorn workers. "
            "Set the SECRET_KEY environment variable for production."
        )

    @app.context_processor
    def _inject_csrf_token():
        return {"csrf_token": get_csrf_token}

    @app.before_request
    def _protect_post_requests():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if not current_app.config.get("WTF_CSRF_ENABLED", True):
            return None
        token = (
            request.headers.get("X-CSRF-Token")
            or request.headers.get("X-CSRFToken")
            or request.form.get("csrf_token")
        )
        if request.is_json and not token:
            payload = request.get_json(silent=True)
            token = payload.get("csrf_token") if isinstance(payload, dict) else None
        if validate_csrf_token(token):
            return None
        # Exempt API endpoints for external/client access
        if request.path in {"/api/scoreline/soccer", "/api/scoreline/nba"}:
            return None
        if request.path.startswith("/chat") or request.is_json:
            return jsonify({"error": "Invalid or missing CSRF token"}), 400
        return "Invalid or missing CSRF token", 400


def get_csrf_token() -> str:
    """Return a stable session CSRF token, creating one when needed."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token(token: str | None) -> bool:
    """Check a submitted token against the session token."""
    expected = session.get(CSRF_SESSION_KEY) or get_csrf_token()
    submitted = str(token or "").strip()
    return bool(submitted and hmac.compare_digest(expected, submitted))


def _chat_rate_key() -> str:
    remote_addr = request.remote_addr or "local"
    return remote_addr


def check_chat_rate_limit(limit: int, window_seconds: int) -> int:
    """Return retry-after seconds when over limit, otherwise 0."""
    now = time.monotonic()
    key = _chat_rate_key()
    timestamps = _CHAT_RATE_STATE[key]

    while timestamps and now - timestamps[0] >= window_seconds:
        timestamps.popleft()

    if len(timestamps) >= limit:
        retry_after = max(1, int(window_seconds - (now - timestamps[0])))
        return retry_after

    timestamps.append(now)
    return 0


def reset_chat_rate_limits() -> None:
    """Clear in-memory rate-limit state, primarily for tests."""
    _CHAT_RATE_STATE.clear()


def sanitize_error(exc: Exception) -> str:
    """Return a safe error string that never leaks internal paths or secrets."""
    msg = str(exc)
    # Strip filesystem paths (Windows + Unix)
    import re
    msg = re.sub(r'[A-Za-z]:\\[^\s\'"]+', '<path>', msg)
    msg = re.sub(r'/(?:home|var|tmp|usr|opt|etc|srv)/[^\s\'"]+', '<path>', msg)
    # Truncate to prevent oversized error payloads
    if len(msg) > 200:
        msg = msg[:200] + '…'
    return msg
