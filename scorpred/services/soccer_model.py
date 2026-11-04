"""Published soccer probabilities: Elo plus Dixon-Coles ratings, combined by a fitted logistic layer.

Ratings are rebuilt from finished league results (ESPN team schedules, this season and the
two before it) and cached for a few hours. The combining layer's coefficients come from
``scripts/soccer_model_research.py``, which fits it on football-data.co.uk results with the
same rating code and reports its walk-forward accuracy against bookmaker prices.

The model is better calibrated than the rules engine it replaces but does not beat the
market (see docs/SOCCER_MODEL_RESEARCH.md), so it never produces a betting recommendation.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any

import numpy as np

from runtime_paths import model_dir
from services import soccer_ratings as sr
from utils.parsing import normalize_team_name

_log = logging.getLogger(__name__)

ARTIFACT_NAME = "soccer_ratings_v1.json"
HISTORY_SEASONS = 3
REFRESH_SECONDS = 6 * 3600
MIN_RESULTS = 60

_lock = threading.Lock()
_build_locks: dict[int, Any] = {}
_ratings: dict[int, dict[str, Any]] = {}
_artifact: dict[str, Any] | None = None


def artifact() -> dict[str, Any] | None:
    global _artifact
    if _artifact is None:
        try:
            _artifact = json.loads((model_dir() / ARTIFACT_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _log.warning("Soccer ratings artifact %s is unavailable", ARTIFACT_NAME)
            return None
    return _artifact


def _season_now(now: datetime) -> int:
    return now.year if now.month >= 7 else now.year - 1


def _fetch_results(api_client, league_id: int, seasons: list[int]) -> tuple[list[dict], dict[int, set[str]]]:
    """Finished league matches for the seasons, and the clubs in each season."""
    clubs: dict[int, set[str]] = {}
    jobs = []
    for season in seasons:
        # These IDs are passed to ESPN schedules. The preferred public team
        # lookup may return football-data IDs, which belong to another namespace.
        if hasattr(api_client, "_espn_league_teams"):
            teams = api_client._espn_league_teams(league_id) or []
        else:
            teams = api_client.get_teams(league_id, season) or []
        clubs[season] = {str((t.get("team") or t).get("name")) for t in teams}
        jobs += [((t.get("team") or t).get("id"), season) for t in teams if (t.get("team") or t).get("id")]
    def schedule(job):
        # One club's schedule failing (ESPN returns persistent 500s for some
        # club-seasons) must not discard the league; its matches still arrive
        # through each opponent's schedule and are deduplicated by fixture id.
        try:
            return api_client._espn_schedule(job[0], league_id, job[1])
        except Exception as exc:
            _log.warning("Skipping schedule team=%s season=%s league=%s: %s", job[0], job[1], league_id, exc)
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        schedules = list(pool.map(schedule, jobs))
    seen: dict[str, dict] = {}
    for fixtures in schedules:
        for fixture in fixtures or []:
            goals = fixture.get("goals") or {}
            status = ((fixture.get("fixture") or {}).get("status") or {}).get("short")
            fixture_id = str((fixture.get("fixture") or {}).get("id") or "")
            if fixture_id and status == "FT" and goals.get("home") is not None and goals.get("away") is not None:
                seen[fixture_id] = fixture
    return list(seen.values()), clubs


def build_ratings(results: list[dict], clubs: dict[int, set[str]], today: date, *, params: dict) -> dict[str, Any] | None:
    """Elo walked through every result, and a Dixon-Coles fit as of today, as in the research replay."""
    rows = []
    for fixture in results:
        kickoff = str((fixture.get("fixture") or {}).get("date") or "")[:10]
        try:
            day = date.fromisoformat(kickoff)
        except ValueError:
            continue
        if day >= today:
            continue
        teams = fixture.get("teams") or {}
        rows.append((day, str((teams.get("home") or {}).get("name")), str((teams.get("away") or {}).get("name")),
                     int(fixture["goals"]["home"]), int(fixture["goals"]["away"])))
    if len(rows) < MIN_RESULTS:
        return None
    rows.sort()
    elo = sr.Elo(k=params["elo_k"], home_advantage=params["elo_home"])
    season_of = lambda day: day.year if day.month >= 7 else day.year - 1  # noqa: E731
    current_season = None
    for day, home, away, home_goals, away_goals in rows:
        if current_season is not None and season_of(day) != current_season:
            elo.new_season()
        current_season = season_of(day)
        elo.update(home, away, home_goals, away_goals)
    this_season = season_of(today)
    if current_season is not None and current_season != this_season:
        elo.new_season()
    previous = clubs.get(this_season - 1) or set()
    prior = {club: sr.NEW_TEAM_PRIOR for club in (clubs.get(this_season) or set()) - previous} if previous else {}
    matches = [sr.Match(day.toordinal(), home, away, hg, ag) for day, home, away, hg, ag in rows]
    dixon_coles = sr.fit(matches, today.toordinal(), half_life_days=params["half_life"], ridge=params["ridge"], prior=prior)
    if dixon_coles is None:
        return None
    return {"elo": elo, "dixon_coles": dixon_coles, "results": len(rows), "built_at": time.time(),
            "clubs": clubs.get(this_season) or set()}


def _league_ratings(api_client, league_id: int) -> dict[str, Any] | None:
    model = artifact()
    if model is None:
        return None
    with _lock:
        cached = _ratings.get(league_id)
        if cached and time.time() - cached["built_at"] < REFRESH_SECONDS:
            return cached
        build_lock = _build_locks.setdefault(league_id, threading.Lock())
    # Dashboard, startup warming and tracking can reach the same cold league
    # concurrently. Share the existing calculation instead of fetching and
    # fitting identical inputs several times; model parameters stay unchanged.
    with build_lock:
        with _lock:
            cached = _ratings.get(league_id)
            if cached and time.time() - cached["built_at"] < REFRESH_SECONDS:
                return cached
        return _refresh_league_ratings(api_client, league_id, model)


def _refresh_league_ratings(api_client, league_id, model):
    now = datetime.now(timezone.utc)
    seasons = [_season_now(now) - offset for offset in range(HISTORY_SEASONS - 1, -1, -1)]
    try:
        results, clubs = _fetch_results(api_client, league_id, seasons)
        built = build_ratings(results, clubs, now.date(), params=model["ratings"])
    except Exception:
        _log.warning("Soccer ratings rebuild failed for league %s", league_id, exc_info=True)
        built = None
    with _lock:
        if built is not None:
            _ratings[league_id] = built
        return built


def _features(elo: sr.Elo, dixon_coles: sr.DixonColes, home: str, away: str) -> np.ndarray:
    lam, mu = dixon_coles.rates(home, away)
    dc = np.clip(dixon_coles.probabilities(home, away), 1e-6, 1)
    return np.array([elo.difference(home, away) / 400, math.log(dc[0] / dc[2]), math.log(dc[1] / (1 - dc[1])),
                     math.log(lam * mu)])


def combine(features: np.ndarray, layer: dict) -> np.ndarray:
    """Multinomial logistic layer stored as plain numbers (scaler, coefficients, intercepts)."""
    scaled = (features - np.array(layer["mean"])) / np.array(layer["scale"])
    logits = np.array(layer["coef"]) @ scaled + np.array(layer["intercept"])
    exp = np.exp(logits - logits.max())
    return exp / exp.sum()


def predict(api_client, league_id: int, home: str, away: str) -> dict[str, Any] | None:
    """Home/draw/away probabilities (0-1) with the inputs behind them; None when ratings are unavailable."""
    model = artifact()
    ratings = _league_ratings(api_client, league_id) if model else None
    if not ratings:
        return None
    elo, dixon_coles = ratings["elo"], ratings["dixon_coles"]
    # Fixtures and history can use different provider spellings. Resolve only
    # unambiguous normalized names that actually have fitted team parameters;
    # unknown teams must never silently receive generic league probabilities.
    def fitted_name(name):
        if name in dixon_coles.attack:
            return name
        matches = [team for team in dixon_coles.attack
                   if normalize_team_name(team) == normalize_team_name(name)]
        return matches[0] if len(matches) == 1 else None

    home, away = fitted_name(home), fitted_name(away)
    if home is None or away is None or home == away:
        return None
    raw = combine(_features(elo, dixon_coles, home, away), model["layer"])
    from services import recalibration
    probabilities = recalibration.adjust("soccer", *map(float, raw))
    lam, mu = dixon_coles.rates(home, away)
    grid = dixon_coles.score_grid(home, away)
    likely = np.unravel_index(int(grid.argmax()), grid.shape)
    return {"home": float(probabilities[0]), "draw": float(probabilities[1]), "away": float(probabilities[2]),
            "expected_goals": {"home": round(lam, 2), "away": round(mu, 2)}, "rho": round(dixon_coles.rho, 4),
            "most_likely_score": {"home": int(likely[0]), "away": int(likely[1]), "probability": round(float(grid.max()), 3)},
            "elo": {"home": round(elo.rating(home)), "away": round(elo.rating(away))},
            "results_used": ratings["results"], "version": model["version"],
            "raw": {"home": float(raw[0]), "draw": float(raw[1]), "away": float(raw[2])},
            "recalibrated": tuple(probabilities) != tuple(map(float, raw))}


DIXON_COLES_REFERENCE = {"title": "Dixon & Coles (1997), Modelling association football scores",
                         "url": "https://doi.org/10.1111/1467-9876.00065"}


def scoreline_projection(fitted: dict | None) -> dict | None:
    """The ratings model's goals forecast in the scoring-research shape the score panel shows."""
    from scipy.stats import poisson
    model = artifact() or {}
    validation = model.get("goals_validation")
    if not fitted or not validation:
        return None
    home, away = fitted["expected_goals"]["home"], fitted["expected_goals"]["away"]
    interval = lambda rate: {"low": float(poisson.ppf(0.1, rate)), "high": float(poisson.ppf(0.9, rate))}  # noqa: E731
    return {
        "available": True, "model": fitted["version"], "rho": fitted["rho"],
        "projected_score": {"home": home, "away": away, "total": round(home + away, 2)},
        "intervals": {"home": interval(home), "away": interval(away)},
        "feature_coverage": 1.0, "validation": validation, "references": [DIXON_COLES_REFERENCE],
        "notice": ("Expected goals from the same ratings model as the win probabilities (Elo and a time-weighted "
                   "Dixon-Coles fit on this league's results), tested week by week on five leagues. "
                   "Whole-goal 80% ranges are coarse, so they held more often than 80% in testing."),
    }


def apply(prediction: dict, fitted: dict | None, home_name: str, away_name: str) -> dict:
    """Replace the rules engine's probabilities and pick with the fitted model's, keeping its narrative fields."""
    if not isinstance(prediction, dict) or not fitted:
        return prediction
    probs = {"a": round(fitted["home"] * 100, 1), "draw": round(fitted["draw"] * 100, 1), "b": round(fitted["away"] * 100, 1)}
    side = max(probs, key=probs.get)
    label = {"a": f"{home_name} Win", "draw": "Draw", "b": f"{away_name} Win"}[side]
    top = probs[side]
    confidence = "High" if top >= 60 else "Medium" if top >= 48 else "Low"
    reasoning = (f"Ratings model: {home_name} {probs['a']:.0f}%, draw {probs['draw']:.0f}%, {away_name} {probs['b']:.0f}%. "
                 f"Expected goals {fitted['expected_goals']['home']:.1f}–{fitted['expected_goals']['away']:.1f}.")
    best_pick = dict(prediction.get("best_pick") or {})
    best_pick.update(prediction=label, team={"a": "A", "b": "B", "draw": "draw"}[side], tracking_team=label,
                     confidence=confidence, reasoning=reasoning)
    import scorpred_engine as se
    home, draw, away = probs["a"], probs["draw"], probs["b"]
    goals, elo = fitted["expected_goals"], fitted["elo"]
    rating_gap = elo["home"] - elo["away"]
    goal_gap = goals["home"] - goals["away"]
    # Explanations come from the same ratings as the numbers, replacing the rules engine's form comparison.
    prediction.update(
        key_edges=[{"team": "A" if rating_gap >= 0 else "B", "team_name": home_name if rating_gap >= 0 else away_name,
                    "category": "Team rating (Elo)", "margin": round(abs(rating_gap))},
                   {"team": "A" if goal_gap >= 0 else "B", "team_name": home_name if goal_gap >= 0 else away_name,
                    "category": "Expected goals", "margin": round(abs(goal_gap), 2)}],
        matchup_reading=(f"{home_name} are rated {elo['home']} and {away_name} {elo['away']} (home advantage included in the "
                         f"forecast). Expected goals {goals['home']:.1f}–{goals['away']:.1f} from {fitted['results_used']} league results."))
    # The rules engine repeats its probabilities under several names; every copy must agree.
    prediction.update(prob_a=home, prob_b=away, prob_draw=draw, home_pct=home, draw_pct=draw, away_pct=away,
                      winner_label=label, top_lean=se.compute_top_lean(home, draw, away, home_name, away_name),
                      decision_status=se.compute_decision_status(home, draw, away))
    prediction.update(win_probabilities=probs, best_pick=best_pick, confidence=confidence, confidence_pct=round(top),
                      data_quality="Strong" if fitted["results_used"] >= 300 else "Moderate",
                      ratings_model=fitted, model_source=fitted["version"],
                      raw_win_probabilities={side: round(fitted.get("raw", {}).get(key, fitted[key]) * 100, 1)
                                             for side, key in (("a", "home"), ("draw", "draw"), ("b", "away"))},
                      rules_win_probabilities=prediction.get("win_probabilities"))
    return prediction
