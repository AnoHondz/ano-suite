"""Learning from tracked mistakes: a small, validated recalibration of published probabilities.

The ratings behind each sport already learn from every result (soccer refits Elo and
Dixon-Coles every few hours; the NBA ratings use every game played). This layer learns
something different from the picks themselves: whether the published probabilities are
systematically too confident, too timid, or biased toward home sides or draws.

It has two parameters per sport, so it cannot chase individual misses:
  soccer  temperature T and a draw offset:  p' ∝ p^(1/T) · e^(d·[draw])
  nba     temperature T and a home offset:  logit p' = logit(p)/T + h

It is fitted on the raw (pre-recalibration) probabilities of verified pregame picks from the
current engine version, oldest 70% first, then judged on the newest 30%. It switches on only
when it lowers held-out log loss by at least ``MIN_GAIN`` with a bootstrap interval above zero.
Once switched on it is refitted on every pick. Every run is recorded, switched on or not.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime, timezone
from typing import Any

import numpy as np
from scipy.optimize import minimize

import model_tracker as mt
from runtime_paths import persistent_root
from utils.atomic import atomic_write_json

_log = logging.getLogger(__name__)

MIN_PICKS = 200
HOLDOUT_SHARE = 0.3
MIN_GAIN = 0.005
SHRINK = 2.0            # ridge toward the identity map (T=1, offset=0), in picks-equivalent units
BOOTSTRAP_DRAWS = 1000
SPORTS = ("soccer", "nba")
_SIDES = ("a", "draw", "b")

_lock = threading.RLock()
_state: dict[str, Any] | None = None
_state_mtime: float | None = None


def _path():
    return persistent_root() / "recalibration.json"


def _engine_version(sport: str) -> str | None:
    from services import engine_fingerprint
    return engine_fingerprint.for_sport(sport).get("version")


def state() -> dict[str, Any]:
    """Persisted decisions, re-read whenever another process (the nightly job) rewrites them."""
    global _state, _state_mtime
    with _lock:
        try:
            mtime = _path().stat().st_mtime
        except OSError:
            mtime = None
        if _state is None or mtime != _state_mtime:
            try:
                _state = json.loads(_path().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _state = {}
            _state_mtime = mtime
        return _state


def transform(sport: str, probabilities: np.ndarray, params: dict | None) -> np.ndarray:
    """Apply recalibration parameters; identity when ``params`` is None."""
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1)
    if not params:
        return p / p.sum(axis=-1, keepdims=True)
    t, offset = params["temperature"], params["offset"]
    if sport == "nba":
        home = p[..., 0] / (p[..., 0] + p[..., -1])
        logit = np.log(home / (1 - home)) / t + offset
        home = 1 / (1 + np.exp(-logit))
        return np.stack([home, np.zeros_like(home), 1 - home], axis=-1)
    scaled = np.power(p, 1 / t) * np.exp(np.array([0.0, offset, 0.0]))
    return scaled / scaled.sum(axis=-1, keepdims=True)


def _log_loss(sport, probabilities, target, params):
    p = transform(sport, probabilities, params)
    return -np.log(np.clip(p[np.arange(len(target)), target], 1e-12, 1))


def fit(sport: str, probabilities: np.ndarray, target: np.ndarray) -> dict[str, float]:
    def objective(theta):
        params = {"temperature": math.exp(theta[0]), "offset": theta[1]}
        return _log_loss(sport, probabilities, target, params).sum() + SHRINK * float(np.square(theta).sum())
    theta = minimize(objective, np.zeros(2), method="L-BFGS-B", bounds=[(-1.0, 1.0), (-1.0, 1.0)]).x
    return {"temperature": round(math.exp(theta[0]), 4), "offset": round(float(theta[1]), 4)}


def _dataset(sport: str, version: str | None) -> tuple[np.ndarray, np.ndarray]:
    rows = []
    for r in mt._load_predictions():
        if str(r.get("sport", "")).lower() != sport or r.get("status") != "completed" or not mt.verified_pregame(r):
            continue
        if version is None or (r.get("engine") or {}).get("version") != version:
            continue
        raw = r.get("raw_probs") or {k: r.get(f"prob_{k}") for k in _SIDES}
        code = mt._canonical_outcome(r.get("actual_result"), str(r.get("team_a") or ""), str(r.get("team_b") or "")) \
            or mt._actual_result_from_score(r.get("final_score"))
        outcome = {"A": 0, "draw": 1, "B": 2}.get(code)
        try:
            values = [float(raw.get(k) or 0) for k in _SIDES]
        except (TypeError, ValueError, AttributeError):
            continue
        if outcome is None or sum(values) <= 0 or (sport == "nba" and outcome == 1):
            continue
        rows.append((r.get("kickoff_at") or "", values, outcome))
    rows.sort(key=lambda row: row[0])
    if not rows:
        return np.zeros((0, 3)), np.zeros(0, dtype=int)
    probabilities = np.array([values for _k, values, _o in rows], dtype=float)
    return probabilities / probabilities.sum(axis=1, keepdims=True), np.array([o for _k, _v, o in rows])


def learn(sport: str, *, now: datetime | None = None, seed: int = 20261023) -> dict[str, Any]:
    """Refit and decide whether recalibration is switched on for ``sport``; the decision is persisted."""
    now = now or datetime.now(timezone.utc)
    version = _engine_version(sport)
    probabilities, target = _dataset(sport, version)
    record: dict[str, Any] = {"sport": sport, "engine_version": version, "picks": int(len(target)),
                              "checked_at": now.isoformat(), "active": False, "params": None}
    if len(target) < MIN_PICKS:
        record["reason"] = f"Needs {MIN_PICKS} graded pregame picks from {version}; has {len(target)}."
    else:
        split = int(len(target) * (1 - HOLDOUT_SHARE))
        trial = fit(sport, probabilities[:split], target[:split])
        held_p, held_t = probabilities[split:], target[split:]
        gain = _log_loss(sport, held_p, held_t, None) - _log_loss(sport, held_p, held_t, trial)
        rng = np.random.default_rng(seed)
        draws = gain[rng.integers(0, len(gain), size=(BOOTSTRAP_DRAWS, len(gain)))].mean(axis=1)
        low = float(np.quantile(draws, 0.025))
        record.update(trial=trial, holdout_picks=int(len(held_t)), holdout_gain=round(float(gain.mean()), 5),
                      holdout_gain_95ci=[round(low, 5), round(float(np.quantile(draws, 0.975)), 5)])
        if gain.mean() >= MIN_GAIN and low > 0:
            record.update(active=True, params=fit(sport, probabilities, target),
                          reason="Held-out log loss improved; refitted on every pick.")
        else:
            record["reason"] = "No reliable held-out improvement; probabilities are published unchanged."
    with _lock:
        current = dict(state())
        current[sport] = record
        atomic_write_json(_path(), current)
        globals()["_state"] = None  # re-read (and record the new mtime) on next access
    _log.info("recalibration %s active=%s picks=%s", sport, record["active"], record["picks"])
    return record


def learn_all() -> dict[str, Any]:
    return {sport: learn(sport) for sport in SPORTS}


def active_params(sport: str) -> dict | None:
    """Parameters to apply now: only when switched on for the engine version currently running."""
    record = state().get(sport) or {}
    if not record.get("active") or record.get("engine_version") != _engine_version(sport):
        return None
    return record.get("params")


def adjust(sport: str, home: float, draw: float, away: float) -> tuple[float, float, float]:
    """Published (home, draw, away) from raw model probabilities (0-1)."""
    params = active_params(sport)
    if params is None:
        return home, draw, away
    p = transform(sport, np.array([home, draw, away]), params)
    return float(p[0]), float(p[1]), float(p[2])
