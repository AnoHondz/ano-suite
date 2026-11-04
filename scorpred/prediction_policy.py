"""Prediction policy tuning and runtime threshold loading.

This module keeps avoid/play thresholds configurable from historical backtests
while preserving safe defaults when no tuned profile is available.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
from typing import Any

from runtime_paths import prediction_policy_path

_DEFAULT_POLICY = {
    "version": 1,
    "generated_at": None,
    # ── ML blend weights ─────────────────────────────────────────────────────
    # Fraction of the final probability that comes from the ML model (0–1).
    # The remainder comes from the rule engine (scorpred_engine).
    # Selected by optimize_prediction_policy.py from [0.2, 0.3, 0.4, 0.5].
    "soccer_ml_blend_weight": 0.4,
    "nba_ml_blend_weight": 0.3,
    "sports": {
        "soccer": {
            "min_confidence_pct": 53.0,
            "min_top_two_gap_pct": 3.0,
            "lean_min_confidence_pct": 51.0,
            "bet_min_confidence_pct": 70.0,
            "draw_min_top_prob_pct": 37.0,
        },
        "nba": {
            "min_confidence_pct": 53.0,
            "min_top_two_gap_pct": 3.0,
            "lean_min_confidence_pct": 51.0,
            "bet_min_confidence_pct": 70.0,
            "draw_min_top_prob_pct": 37.0,
        },
    },
    "metadata": {
        "source": "defaults",
        "sample_size": 0,
    },
}

_cache: dict[str, Any] = {}
_cache_mtime: dict[str, float] = {}
_cache_lock = threading.Lock()


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def default_policy() -> dict[str, Any]:
    return json.loads(json.dumps(_DEFAULT_POLICY))


def _normalized_sport_config(raw: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, float]:
    payload = raw or {}
    return {
        "min_confidence_pct": _safe_float(payload.get("min_confidence_pct"), float(fallback["min_confidence_pct"])),
        "min_top_two_gap_pct": _safe_float(payload.get("min_top_two_gap_pct"), float(fallback["min_top_two_gap_pct"])),
        "lean_min_confidence_pct": _safe_float(payload.get("lean_min_confidence_pct"), float(fallback["lean_min_confidence_pct"])),
        "bet_min_confidence_pct": _safe_float(payload.get("bet_min_confidence_pct"), float(fallback["bet_min_confidence_pct"])),
        "draw_min_top_prob_pct": _safe_float(payload.get("draw_min_top_prob_pct"), float(fallback["draw_min_top_prob_pct"])),
    }


def normalize_policy(payload: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_policy()
    incoming = payload or {}
    sports = incoming.get("sports") if isinstance(incoming.get("sports"), dict) else {}

    defaults["version"] = int(_safe_float(incoming.get("version"), 1.0))
    defaults["generated_at"] = incoming.get("generated_at")
    defaults["metadata"] = dict(defaults.get("metadata") or {})
    if isinstance(incoming.get("metadata"), dict):
        defaults["metadata"].update(incoming["metadata"])

    defaults["sports"]["soccer"] = _normalized_sport_config(
        sports.get("soccer") if isinstance(sports.get("soccer"), dict) else None,
        defaults["sports"]["soccer"],
    )
    defaults["sports"]["nba"] = _normalized_sport_config(
        sports.get("nba") if isinstance(sports.get("nba"), dict) else None,
        defaults["sports"]["nba"],
    )

    # Blend weights: clamp to [0.0, 1.0]; fall back to defaults when absent or invalid.
    raw_sw = incoming.get("soccer_ml_blend_weight")
    if isinstance(raw_sw, (int, float)) and 0.0 <= float(raw_sw) <= 1.0:
        defaults["soccer_ml_blend_weight"] = round(float(raw_sw), 4)

    raw_nw = incoming.get("nba_ml_blend_weight")
    if isinstance(raw_nw, (int, float)) and 0.0 <= float(raw_nw) <= 1.0:
        defaults["nba_ml_blend_weight"] = round(float(raw_nw), 4)

    return defaults


def load_policy(force_reload: bool = False) -> dict[str, Any]:
    path = prediction_policy_path()
    cache_key = str(path)

    with _cache_lock:
        if not force_reload and cache_key in _cache:
            # Invalidate cache if file has been updated by another process
            try:
                current_mtime = path.stat().st_mtime if path.exists() else -1.0
            except OSError:
                current_mtime = -1.0
            if current_mtime == _cache_mtime.get(cache_key, -1.0):
                return _cache[cache_key]

        if not path.exists():
            policy = default_policy()
            _cache[cache_key] = policy
            _cache_mtime[cache_key] = -1.0
            return policy

        try:
            current_mtime = path.stat().st_mtime
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Policy file must be a JSON object")
        except Exception:
            policy = default_policy()
            _cache[cache_key] = policy
            _cache_mtime[cache_key] = -1.0
            return policy

        policy = normalize_policy(payload)
        _cache[cache_key] = policy
        _cache_mtime[cache_key] = current_mtime
        return policy


def save_policy(policy: dict[str, Any]) -> None:
    path = prediction_policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_policy(policy)
    if not normalized.get("generated_at"):
        normalized["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    with _cache_lock:
        _cache[str(path)] = normalized


def sport_policy(sport: str | None = None) -> dict[str, float]:
    payload = load_policy()
    key = str(sport or "soccer").strip().lower()
    if key not in {"soccer", "nba"}:
        key = "soccer"
    return dict(payload.get("sports", {}).get(key, _DEFAULT_POLICY["sports"]["soccer"]))


# ── Blend-weight helpers (shared by scormastermind + ml_service) ──────────────

_DEFAULT_SOCCER_ML_BLEND = 0.40
_DEFAULT_NBA_ML_BLEND = 0.30


def soccer_ml_blend_weight() -> float:
    """Return the soccer ML blend weight (0.0–1.0).

    Reads ``soccer_ml_blend_weight`` from prediction_policy.json on every call
    so changes take effect without a restart.  Falls back to 0.40.
    """
    policy = load_policy()
    w = policy.get("soccer_ml_blend_weight")
    if isinstance(w, (int, float)) and 0.0 <= float(w) <= 1.0:
        return float(w)
    return _DEFAULT_SOCCER_ML_BLEND


def nba_ml_blend_weight() -> float:
    """Return the NBA ML blend weight (0.0–1.0).

    Same hot-reload behaviour as :func:`soccer_ml_blend_weight`.
    """
    policy = load_policy()
    w = policy.get("nba_ml_blend_weight")
    if isinstance(w, (int, float)) and 0.0 <= float(w) <= 1.0:
        return float(w)
    return _DEFAULT_NBA_ML_BLEND
