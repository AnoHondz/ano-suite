"""Isolated early-season research using existing scoring rules; never a decision input."""
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
import os
import statistics
from pathlib import Path

from runtime_paths import cache_dir
from services.scoreline_engine import _calibrate_nba_projections
from services import scoreline_engine

VERSION = "nba-early-season-v1"
TRANSITION_GAMES = 6
MIN_PREVIOUS = 20
MIN_CALIBRATION = 50
DIRECTORY = cache_dir("nba_early_season", "v1")


def enabled():
    return os.getenv("NBA_EARLY_SEASON_RESEARCH_ENABLED", "0") == "1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def method_fingerprint():
    return hashlib.sha256(Path(__file__).read_bytes() + Path(scoreline_engine.__file__).read_bytes()).hexdigest()


def blend(previous, current, *, prior_only=False):
    """Six-observation transition; no fake game rows or feature substitutions."""
    rows = current["recent_results"][:TRANSITION_GAMES]
    weight = 0 if prior_only else min(len(rows) / TRANSITION_GAMES, 1)
    if weight < 1 and previous["games"] < MIN_PREVIOUS:
        raise ValueError("Insufficient observed previous-season history for this research method.")
    values = {}
    for name, field in (("ppg", "points_for"), ("opp_ppg", "points_against")):
        prior = previous[field]
        recent = round(statistics.mean(row[field] for row in rows), 1) if rows else None
        if weight < 1 and (prior is None or not math.isfinite(prior) or prior <= 0):
            raise ValueError("Missing previous-season scoring observations.")
        if weight and (recent is None or not math.isfinite(recent) or recent <= 0):
            raise ValueError("Invalid current-season scoring observations.")
        values[name] = (1 - weight) * (prior or 0) + weight * (recent or 0)
    return {"stats": values, "current_weight": weight, "current_games": len(rows), "previous_games": previous["games"]}


def estimate(home, away, *, neutral, prior_only=False):
    if not isinstance(neutral, bool):
        raise ValueError("Verified venue neutrality metadata is required.")
    inputs = [blend(team["previous"], team["current"], prior_only=prior_only) for team in (home, away)]
    # Existing formula; no injury, odds, opponent-strength or duplicated form adjustments.
    scores = list(_calibrate_nba_projections(inputs[0]["stats"], inputs[1]["stats"], [], [], None, None, True))
    if neutral:
        # Compute the same weighted points without the existing four-point home swing.
        scores = [inputs[0]["stats"]["ppg"] * .55 + inputs[1]["stats"]["opp_ppg"] * .45,
                  inputs[1]["stats"]["ppg"] * .55 + inputs[0]["stats"]["opp_ppg"] * .45]
        scores = [max(85., value) for value in scores]
    return {"home": scores[0], "away": scores[1], "inputs": inputs}


def calibration(rows, method):
    if len(rows) < MIN_CALIBRATION:
        return None
    errors = [[row["actual"][side] - row[method][side] for side in ("home", "away")] for row in rows]
    margins = [error[0] - error[1] for error in errors]
    sigma = statistics.stdev(margins)
    if sigma <= 0:
        return None
    radius = []
    for side in (0, 1):
        values = sorted(abs(error[side]) for error in errors)
        radius.append(values[min(len(values) - 1, math.ceil((len(values) + 1) * .8) - 1)])
    return {"count": len(rows), "end": max(row["kickoff"] for row in rows),
            "margin_bias": statistics.mean(margins), "margin_sigma": sigma, "radius": radius}


def probability(scores, calibrated):
    z = (scores["home"] - scores["away"] + calibrated["margin_bias"]) / calibrated["margin_sigma"]
    return min(1 - 1e-6, max(1e-6, .5 * (1 + math.erf(z / math.sqrt(2)))))


def metrics(rows, method):
    if not rows:
        return {"games": 0}
    probabilities = [row["probabilities"][method] for row in rows]
    targets = [int(row["actual"]["home"] > row["actual"]["away"]) for row in rows]
    bins = []
    for index in range(5):
        selected = [(p, y) for p, y in zip(probabilities, targets, strict=True) if min(4, int(p * 5)) == index]
        if selected:
            bins.append({"lower": index / 5, "upper": (index + 1) / 5, "games": len(selected),
                         "predicted": statistics.mean(p for p, _ in selected), "observed": statistics.mean(y for _, y in selected)})
    result = {"games": len(rows), "log_loss": statistics.mean(-math.log(p if y else 1 - p) for p, y in zip(probabilities, targets, strict=True)),
              "brier": statistics.mean((p - y) ** 2 for p, y in zip(probabilities, targets, strict=True)),
              "accuracy": statistics.mean(int((p >= .5) == bool(y)) for p, y in zip(probabilities, targets, strict=True)),
              "calibration_bins": bins, "ece_5_bins": sum(b["games"] * abs(b["predicted"] - b["observed"]) for b in bins) / len(rows)}
    if method != "home_prior":
        result["points_mae"] = statistics.mean(abs(row[method][side] - row["actual"][side]) for row in rows for side in ("home", "away"))
        result["interval_coverage"] = {side: statistics.mean(abs(row[method][side] - row["actual"][side]) <= row["calibration"][method]["radius"][i] for row in rows) for i, side in enumerate(("home", "away"))}
    return result


def paired_intervals(rows, baseline, *, draws=2000):
    """Paired seven-day calendar-block resampling, preserving games within blocks."""
    import numpy as np
    blocks = {}
    for row in rows:
        day = datetime.fromisoformat(row["kickoff"]).date().toordinal() // 7
        blocks.setdefault(day, []).append(row)
    if len(blocks) < 2:
        return {}
    # Aggregate paired loss differences to avoid copying outcomes for every draw.
    values = []
    for group in blocks.values():
        differences = []
        for row in group:
            y = int(row["actual"]["home"] > row["actual"]["away"])
            p, b = (row["probabilities"][name] for name in ("blend", baseline))
            differences.append([-math.log(p if y else 1-p) + math.log(b if y else 1-b), (p-y)**2-(b-y)**2,
                                int((p >= .5) == bool(y)) - int((b >= .5) == bool(y))])
        values.append([len(group), *np.asarray(differences).sum(axis=0)])
    array = np.asarray(values)
    samples = np.random.default_rng(20261022).integers(0, len(array), size=(draws, len(array)))
    sums = array[samples].sum(axis=1)
    deltas = sums[:, 1:] / sums[:, :1]
    mean = array[:, 1:].sum(axis=0) / array[:, 0].sum()
    return {name: {"delta": float(mean[i]), "low": float(np.quantile(deltas[:, i], .025)), "high": float(np.quantile(deltas[:, i], .975)),
                   "blocks": len(blocks), "draws": draws} for i, name in enumerate(("log_loss", "brier", "accuracy"))}


def load_report():
    try:
        manifest = json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
        payload = (DIRECTORY / "report.json").read_bytes()
        if hashlib.sha256(payload).hexdigest() != manifest["report_sha256"]:
            raise ValueError("Report integrity mismatch")
        report = json.loads(payload)
        if report["version"] != VERSION:
            raise ValueError("Report version mismatch")
        return {"available": True, "research_enabled": enabled(), "production_enabled": False, "report": report}
    except (OSError, ValueError, KeyError):
        return {"available": False, "research_enabled": enabled(), "production_enabled": False, "reason": "Run the offline early-season evaluation first."}


def load_artifact():
    manifest = json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
    payload = (DIRECTORY / "artifact.json").read_bytes()
    if hashlib.sha256(payload).hexdigest() != manifest["artifact_sha256"]:
        raise ValueError("Early-season artifact integrity mismatch")
    artifact = json.loads(payload)
    if artifact["version"] != VERSION or artifact["method_sha256"] != method_fingerprint():
        raise ValueError("Early-season method version changed; rerun the offline study.")
    return artifact


def forecast(context, artifact, *, now=None):
    now = now or datetime.now(UTC)
    target = datetime.fromisoformat(context["game"]["kickoff"])
    cutoff = datetime.fromisoformat(context["statistics_cutoff"])
    if target <= now or context["game"]["status"].get("state") != "pre" or context["game"].get("season_type") != 2:
        raise ValueError("Early-season research is limited to scheduled regular-season games before tipoff.")
    if cutoff > min(now, target) - timedelta(hours=24):
        raise ValueError("Research inputs violate the 24-hour availability lag.")
    age = now - datetime.fromisoformat(artifact["generated_at"])
    if not timedelta(0) <= age < timedelta(days=30):
        raise ValueError("Early-season research artifact needs an offline refresh.")
    calibrated = artifact["calibration"]
    if calibrated["count"] < MIN_CALIBRATION or datetime.fromisoformat(calibrated["end"]) >= cutoff:
        raise ValueError("Calibration must use sufficient strictly earlier games.")
    teams = [context["teams"][side] for side in ("home", "away")]
    if min(team["current"]["games"] for team in teams) >= TRANSITION_GAMES:
        raise ValueError("This research preview is limited to early-season samples.")
    for team in teams:
        source = team["current_source"]
        if source.get("state") != "fresh" or not datetime.fromisoformat(source["observed_at"]) <= now < datetime.fromisoformat(source["expires_at"]):
            raise ValueError("Fresh source receipts are required for research previews.")
    scores = estimate(*teams, neutral=context["game"].get("neutral_site"))
    p = probability(scores, calibrated)
    return {"version": VERSION, "role": "research_only", "production_eligible": False, "game_id": context["game"]["id"],
            "generated_at": now.isoformat(), "statistics_cutoff": cutoff.isoformat(), "projected_score": scores,
            "home_probability": p, "away_probability": 1-p, "inputs_sha256": digest(teams),
            "intervals": {side: {"low": max(0, scores[side] - calibrated["radius"][i]), "high": scores[side] + calibrated["radius"][i]} for i, side in enumerate(("home", "away"))},
            "notice": "Experimental prior-season blend; retrospective validation only. No roster/injury adjustment or proven prospective calibration. Never a production pick."}
