"""Shared parsing and normalization helpers."""

from __future__ import annotations

from datetime import date, datetime
import re
import unicodedata
from typing import Any, Iterable

_DEFAULT_TEAM_NAME_IGNORED = {"fc", "cf", "sc", "afc", "club"}


def safe_float(value: Any, default: float = 0.0) -> float:
    """Safely coerce a value to float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int | None = None) -> int | None:
    """Safely coerce a value to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_text(value: Any) -> str:
    """ASCII-normalize text for fuzzy matching."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_team_name(value: Any, ignored_tokens: Iterable[str] | None = None) -> str:
    """Normalize team names while dropping common suffix tokens."""
    ignored = {token.lower() for token in (ignored_tokens or _DEFAULT_TEAM_NAME_IGNORED)}
    tokens = [token for token in normalize_text(value).split() if token not in ignored]
    return " ".join(tokens)


def normalize_date(value: Any) -> str | None:
    """Return a YYYY-MM-DD string for common date-like inputs."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return None

    iso_text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_text).date().isoformat()
    except ValueError:
        pass

    head = text[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", head):
        return head
    return None
