"""Shared pregame features and portable, verified scoring-model inference.

The scoring panel is an experimental forecast, separate from DecisionEngine.
Training and calibration run offline; web requests only evaluate JSON coefficients.
"""
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from runtime_paths import cache_dir, repo_root

REFERENCES = {
    "soccer": [{"title": "Dixon & Coles: football score modeling", "url": "https://doi.org/10.1111/1467-9876.00065"}],
    "nba": [{"title": "Sill: regularization and future-game testing", "url": "https://www.sloansportsconference.com/research-papers/improved-nba-adjusted-using-regularization-and-out-of-sample-testing"},
            {"title": "Hoerl & Kennard: ridge regression", "url": "https://doi.org/10.1080/00401706.1970.10488634"}],
    "nfl": [{"title": "nflfastR: predictive EPA analysis", "url": "https://nflfastr.com/articles/beginners_guide"},
            {"title": "Hoerl & Kennard: ridge regression", "url": "https://doi.org/10.1080/00401706.1970.10488634"}],
}
UNCERTAINTY_REFERENCE = {"title": "Angelopoulos & Bates: conformal prediction", "url": "https://arxiv.org/abs/2107.07511"}
ROLLING_FEATURES = [f"{side}_{name}" for side in ("home", "away") for name in ("scored", "conceded", "win_rate", "scored_sd", "samples")] + ["home_venue"]
FORM_WINDOW = 6
MIN_FORM_GAMES = 3
AVAILABILITY_LAG_HOURS = 24
SOCCER_FORM_POLICY = {"season": "same", "window": FORM_WINDOW,
                      "minimum_games": MIN_FORM_GAMES, "availability_lag_hours": AVAILABILITY_LAG_HOURS}


def finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def clean_form(form, sport):
    """Keep complete score pairs, preserving legitimate zero-goal games."""
    result = []
    for row in form or []:
        if not isinstance(row, dict):
            continue
        own = finite(row.get("gf") if sport == "soccer" else row.get("our_pts", row.get("gf")))
        against = finite(row.get("ga") if sport == "soccer" else row.get("their_pts", row.get("ga")))
        if own is None or against is None or min(own, against) < 0:
            continue
        if sport != "soccer" and own + against <= 0:
            continue
        result.append({**row, "gf": own, "ga": against, "our_pts": own, "their_pts": against})
    return result


def _aware_time(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _form_cutoff(before=None, now=None):
    clock = _aware_time(now) if now is not None else datetime.now(UTC)
    target = _aware_time(before) if before is not None else clock
    if clock is None or target is None:
        raise ValueError("An explicit timezone is required for scoring feature cutoffs.")
    return min(clock, target) - timedelta(hours=AVAILABILITY_LAG_HOURS)


def _season(value):
    # API-Football uses season start years; reject ambiguous/missing metadata.
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if len(text) == 4 and text.isdigit():
        return int(text)
    if len(text) in (7, 9) and text[:4].isdigit() and text[4] in "/-":
        return int(text[:4])
    return None


def prior_games(games, sport, *, before=None, now=None, season=None):
    """Select dated inputs using the same strict 24-hour lag as training.

    Passing a season also excludes missing or conflicting season metadata.
    """
    cutoff = _form_cutoff(before, now)
    available = []
    for game in games or []:
        if not isinstance(game, dict):
            continue
        raw = (game.get("fixture") or {}).get("date") if sport == "soccer" else (game.get("date") or {}).get("start")
        kickoff = _aware_time(raw)
        game_season = (game.get("league") or {}).get("season") if sport == "soccer" else game.get("season")
        if season is not None and (_season(season) is None or _season(game_season) != _season(season)):
            continue
        if kickoff is not None and kickoff < cutoff:
            available.append(game)
    return available


def select_prior_form(rows, *, season, before=None, now=None, sport="soccer"):
    """Shared training/live selection of at most six same-season score pairs.

    Rows are team-perspective records with kickoff, season and gf/ga. Missing
    dates/seasons are excluded, rather than borrowing previous-season results.
    """
    cutoff = _form_cutoff(before, now)
    target_season = _season(season)
    if target_season is None:
        return []
    eligible = []
    for row in clean_form(rows, sport):
        kickoff = _aware_time(row.get("kickoff"))
        if kickoff is not None and kickoff < cutoff and _season(row.get("season")) == target_season:
            eligible.append((kickoff, row))
    eligible.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in eligible[:FORM_WINDOW]]


def soccer_scoring_form(games, team_id, *, season, before=None, now=None):
    """Normalize completed API fixtures and apply the offline scoring policy."""
    rows = []
    for game in games or []:
        if not isinstance(game, dict):
            continue
        fixture, teams, goals = game.get("fixture") or {}, game.get("teams") or {}, game.get("goals") or {}
        if (fixture.get("status") or {}).get("short") not in {"FT", "AET", "PEN"}:
            continue
        home, away = teams.get("home") or {}, teams.get("away") or {}
        if str(team_id) not in {str(home.get("id")), str(away.get("id"))}:
            continue
        is_home = str(home.get("id")) == str(team_id)
        opponent = away if is_home else home
        gf, ga = (goals.get("home"), goals.get("away")) if is_home else (goals.get("away"), goals.get("home"))
        own, against = finite(gf), finite(ga)
        if own is None or against is None:
            continue
        rows.append({"kickoff": fixture.get("date"), "date": str(fixture.get("date") or "")[:10],
                     "season": (game.get("league") or {}).get("season"), "fixture_id": fixture.get("id"),
                     "gf": own, "ga": against, "result": "W" if own > against else "L" if own < against else "D",
                     "home": is_home, "is_home": is_home, "opponent": opponent.get("name"),
                     "opponent_id": opponent.get("id"), "opponent_logo": opponent.get("logo")})
    return select_prior_form(rows, season=season, before=before, now=now, sport="soccer")


def rolling_features(form_a, form_b, *, is_home=True, sport="soccer"):
    output = {}
    for side, form in (("home", form_a), ("away", form_b)):
        rows = clean_form(form, sport)[:FORM_WINDOW]
        if len(rows) < MIN_FORM_GAMES:
            raise ValueError("At least three completed games with scores per team are required.")
        scored = [row["gf"] for row in rows]
        mean = sum(scored) / len(rows)
        values = {"scored": mean, "conceded": sum(row["ga"] for row in rows) / len(rows),
                  "win_rate": sum(1 if row["gf"] > row["ga"] else .5 if row["gf"] == row["ga"] else 0 for row in rows) / len(rows),
                  "scored_sd": math.sqrt(sum((value - mean) ** 2 for value in scored) / (len(rows) - 1)), "samples": len(rows)}
        output.update({f"{side}_{name}": value for name, value in values.items()})
    return {**output, "home_venue": int(is_home)}


def schema(names):
    return hashlib.sha256(json.dumps(names).encode()).hexdigest()


def artifact_paths(sport):
    return cache_dir("score_models", f"{sport}.json"), repo_root() / "data" / "validation" / f"scoring_{sport}.json"


def project(sport, features, *, before=None, now=None):
    """Return a verified research forecast or an explicit unavailability reason."""
    now = now or datetime.now(UTC)
    destination, report_path = artifact_paths(sport)
    try:
        payload = Path(destination).read_bytes()
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        if hashlib.sha256(payload).hexdigest() != report["artifact_sha256"]:
            raise ValueError("Model integrity check failed")
        model = json.loads(payload)
        names = model["feature_names"]
        if model["version"] != 1 or model["sport"] != sport or model["schema"] != schema(names):
            raise ValueError("Incompatible scoring model")
        if sport == "soccer" and model.get("form_policy", SOCCER_FORM_POLICY) != SOCCER_FORM_POLICY:
            raise ValueError("Incompatible soccer feature availability policy")
        expected = ROLLING_FEATURES
        if sport == "nfl":
            from services.nfl_features import FEATURE_NAMES
            expected = FEATURE_NAMES
        if names != expected or set(names) - set(features):
            raise ValueError("Incompatible feature schema")
        if any(len(model[key]) != len(names) for key in ("fill", "center", "scale")) or len(model["targets"]) != 2:
            raise ValueError("Invalid scoring model dimensions")
        if not all(finite(value) is not None for key in ("fill", "center", "scale", "radius", "sigma") for value in model[key]):
            raise ValueError("Non-finite scoring model parameters")
        if len(model["radius"]) != 2 or len(model["sigma"]) != 2 or min(model["scale"] + model["sigma"] + [model["sigma_total"]]) <= 0:
            raise ValueError("Invalid scoring uncertainty parameters")
        cutoff = datetime.fromisoformat(before) if before else now
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        generated = datetime.fromisoformat(model["generated_at"])
        if not timedelta(0) <= now - generated < timedelta(days=30):
            raise ValueError("Scoring model needs an offline refresh")
        if datetime.fromisoformat(model["calibration_end"]) >= cutoff:
            raise ValueError("Model calibration is later than this game's cutoff")
        values, missing = [], []
        for i, name in enumerate(names):
            value = finite(features[name])
            if value is None:
                missing.append(name)
            value = model["fill"][i] if value is None else value
            values.append((value - model["center"][i]) / model["scale"][i])
        estimates = []
        for target in model["targets"]:
            prediction = target["intercept"] + sum(a * b for a, b in zip(target["coef"], values, strict=True))
            estimates.append(math.exp(prediction) if model["link"] == "log" else max(0, prediction))
        if not all(math.isfinite(v) and 0 <= v <= (15 if sport == "soccer" else 300) for v in estimates):
            raise ValueError("Invalid scoring projection")
        intervals = {side: {"low": math.ceil(max(0, estimates[i] - model["radius"][i])),
                            "high": math.floor(estimates[i] + model["radius"][i])} for i, side in enumerate(("home", "away"))}
        return {"available": True, "model": model["method"], "projected_score": {"home": estimates[0], "away": estimates[1], "total": sum(estimates)},
                "feature_coverage": 1 - len(missing) / len(names), "imputed_features": missing,
                "intervals": intervals, "nominal_coverage": .8, "sigma_home": model["sigma"][0], "sigma_away": model["sigma"][1],
                "sigma_total": model["sigma_total"], "rho": model.get("rho", 0),
                "validation": {"status": "retrospective_holdout", "test_games": report["windows"]["test"]["count"],
                               "points_mae": report["metrics"]["points_mae"], "baseline_mae": report["metrics"]["baseline_mae"],
                               "interval_coverage": report["metrics"]["interval_coverage"], "test_end": report["windows"]["test"]["end"]},
                "references": REFERENCES[sport] + [UNCERTAINTY_REFERENCE],
                "notice": "Experimental scoring forecast; retrospective evaluation only. The 80% ranges target each team's score separately; future coverage can change with team and season shifts. No injury, lineup, weather or market-price adjustment. Scoring forecasts do not change the match decision above."}
    except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError):
        return {"available": False, "reason": "The research scoring model is unavailable, outdated or incompatible. Run the documented offline training job."}
