"""
model_tracker.py — Lightweight prediction tracking and performance metrics.

Stores predictions in JSON format for easy inspection and analysis.
Provides functions to save, update, and summarize model performance.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
import uuid
import re
from runtime_paths import prediction_tracking_path
from utils.parsing import normalize_date
from utils.atomic import atomic_write_json, transaction

logger = logging.getLogger(__name__)

_TRACKING_FILE = str(prediction_tracking_path())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_datetime(value: Any) -> datetime | None:
    """Parse a kickoff timestamp; date-only or naive values are not precise enough to freeze picks."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def verified_pregame(prediction: dict) -> bool:
    """True only when the pick was first recorded before a known, precise kickoff."""
    kickoff = _aware_datetime(prediction.get("kickoff_at"))
    created = _aware_datetime(prediction.get("created_at"))
    return kickoff is not None and created is not None and created < kickoff


@transaction(lambda: _TRACKING_FILE)
def _ensure_tracking_file() -> None:
    """Create tracking file if it doesn't exist."""
    if os.path.exists(_TRACKING_FILE):
        # Migrate existing predictions to add status field
        _migrate_predictions()
        return
    folder = os.path.dirname(_TRACKING_FILE)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    atomic_write_json(_TRACKING_FILE, {"predictions": []})


@transaction(lambda: _TRACKING_FILE)
def _migrate_predictions() -> None:
    """Migrate existing predictions to add status field if missing."""
    try:
        with open(_TRACKING_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Prediction history is corrupt; restore a backup before writing.") from exc

        predictions = data.get("predictions", [])
        if not isinstance(data, dict) or not isinstance(data.get("predictions"), list):
            raise RuntimeError("Prediction history has an invalid schema")
        migrated = False

        for pred in predictions:
            if "status" not in pred:
                # Set status based on whether result is known
                if pred.get("is_correct") is not None:
                    pred["status"] = "completed"
                else:
                    pred["status"] = "pending"
                migrated = True

            if "prediction_market" not in pred:
                pred["prediction_market"] = "winner"
                migrated = True

            if "totals_pick" not in pred:
                legacy_pick, legacy_line, legacy_market = _legacy_totals_defaults(pred)
                pred["totals_pick"] = legacy_pick
                pred["totals_line"] = legacy_line
                pred["totals_market"] = legacy_market
                if legacy_pick is not None:
                    pred["totals_pick_source"] = "legacy_implicit_over"
                migrated = True

            if pred.get("totals_pick") and "totals_line" not in pred:
                pred["totals_line"] = _default_totals_line(pred.get("sport", ""))
                migrated = True

            # A line of 0 came from a parsing fallback; the real line is in the market name.
            if pred.get("totals_pick") and pred.get("totals_line") is not None and not (pred.get("totals_line") or 0) > 0:
                pred["totals_line"] = parse_totals_line(None, pred.get("totals_market")) or _default_totals_line(pred.get("sport", ""))
                pred["totals_line_repaired"] = True
                migrated = True

            if pred.get("totals_pick") and "totals_market" not in pred:
                pred["totals_market"] = _default_totals_market(pred.get("sport", ""))
                migrated = True

            if "fixture_id" not in pred:
                pred["fixture_id"] = None
                migrated = True

            original_pick = pred.get("predicted_winner")
            normalized_pick = _winner_choice_code(
                original_pick,
                pred.get("team_a", ""),
                pred.get("team_b", ""),
            )
            if normalized_pick and pred.get("predicted_winner") != normalized_pick:
                pred["predicted_winner"] = normalized_pick
                migrated = True

            display_pick = _prediction_label_from_value(
                pred.get("predicted_pick_label") or original_pick,
                pred.get("team_a", ""),
                pred.get("team_b", ""),
            )
            if pred.get("predicted_pick_label") != display_pick:
                pred["predicted_pick_label"] = display_pick
                migrated = True

            actual_result = pred.get("actual_result")
            normalized_actual = _winner_choice_code(
                actual_result,
                pred.get("team_a", ""),
                pred.get("team_b", ""),
            )
            if actual_result and normalized_actual and actual_result != normalized_actual:
                pred["actual_result"] = normalized_actual
                migrated = True

            if pred.get("status") == "completed" or pred.get("actual_result"):
                outcome = _compute_prediction_outcome(pred)
                # The tracked pick is the winner; the totals leg is graded separately (overall_result).
                if pred.get("is_correct") != outcome["winner_hit"]:
                    pred["is_correct"] = outcome["winner_hit"]
                    migrated = True
                if pred.get("actual_winner") != outcome["actual_winner"]:
                    pred["actual_winner"] = outcome["actual_winner"]
                    migrated = True
                if pred.get("winner_result") != outcome["winner_result"]:
                    pred["winner_result"] = outcome["winner_result"]
                    migrated = True
                if pred.get("totals_result") != outcome["totals_result"]:
                    pred["totals_result"] = outcome["totals_result"]
                    migrated = True
                if pred.get("overall_result") != outcome["overall_result"]:
                    pred["overall_result"] = outcome["overall_result"]
                    migrated = True

        if migrated:
            atomic_write_json(_TRACKING_FILE, {"predictions": predictions})
    except Exception:
        logger.warning("Prediction migration failed", exc_info=True)
        raise


@transaction(lambda: _TRACKING_FILE)
def _load_predictions() -> list[dict]:
    """Load all tracked predictions."""
    _ensure_tracking_file()
    try:
        with open(_TRACKING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        predictions = data.get("predictions", []) if isinstance(data, dict) else []
        changed = False
        for pred in predictions:
            if pred.get("status") == "completed":
                changed = _apply_grading(pred) or changed
        if changed:
            atomic_write_json(_TRACKING_FILE, {"predictions": predictions})
        return predictions
    except Exception:
        logger.error("Unable to read prediction history", exc_info=True)
        raise


@transaction(lambda: _TRACKING_FILE)
def _save_predictions(predictions: list[dict]) -> None:
    """Save predictions to file."""
    try:
        _ensure_tracking_file()
        atomic_write_json(_TRACKING_FILE, {"predictions": predictions})
    except Exception:
        logger.error("Failed to save predictions to %s", _TRACKING_FILE, exc_info=True)
        raise


def _get_game_key(
    sport: str,
    date: str,
    team_a: str,
    team_b: str,
    league_id: int | None = None,
) -> str:
    """Generate a unique key for a game to prevent duplicates.

    Strict dedupe requires sport, date, home_team, and away_team to match exactly.
    """
    parts = [sport.lower().strip()]
    if sport.lower().strip() == "soccer" and league_id is not None:
        parts.append(str(league_id))
    parts.extend([str(date)[:10], team_a.lower().strip(), team_b.lower().strip()])
    return "|".join(parts)


def _normalized_text(value: str | None) -> str:
    """Return a lowercase, alphanumeric-only token stream for fuzzy comparisons."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def parse_totals_line(value: Any, market: str | None = None) -> float | None:
    """A positive totals line from an explicit value or the number in a market name; None otherwise."""
    for candidate in (value, *re.findall(r"\d+(?:\.\d+)?", str(market or ""))[-1:]):
        try:
            line = float(candidate)
        except (TypeError, ValueError):
            continue
        if line > 0:
            return line
    return None


def _default_totals_line(sport: str) -> float:
    return 2.5 if str(sport or "").lower() == "soccer" else 220.5


def _default_totals_market(sport: str) -> str:
    return "goals_over_under" if str(sport or "").lower() == "soccer" else "points_over_under"


def _format_totals_line(line: float | int | None) -> str:
    if line is None:
        return ""
    try:
        value = float(line)
    except (TypeError, ValueError):
        return str(line)
    if value.is_integer():
        return str(int(value))
    return str(round(value, 1))


def _normalize_totals_pick(value: str | None) -> str | None:
    text = _normalized_text(value)
    if not text:
        return None
    if "over" in text:
        return "Over"
    if "under" in text:
        return "Under"
    return None


def _totals_pick_display(pick: str | None, line: float | int | None) -> str | None:
    if not pick or line is None:
        return None
    return f"{pick} {_format_totals_line(line)}"


def _legacy_totals_defaults(prediction: dict) -> tuple[str | None, float | None, str | None]:
    sport = str(prediction.get("sport") or "").lower()
    if sport == "soccer":
        return "Over", 2.5, "goals_over_under"
    return None, None, None


def _winner_choice_code(value: str | None, team_a: str = "", team_b: str = "") -> str:
    """Normalize raw winner labels into canonical tracking codes A/B/draw."""
    text = _normalized_text(value)
    if not text:
        return ""

    if text in {"a", "team a", "home", "home win"}:
        return "A"
    if text in {"b", "team b", "away", "away win"}:
        return "B"
    if text in {"draw", "tie", "x"} or "draw" in text or "tie" in text:
        return "draw"

    team_a_norm = _normalized_text(team_a)
    team_b_norm = _normalized_text(team_b)
    if team_a_norm and (text == team_a_norm or text == f"{team_a_norm} win" or text.startswith(f"{team_a_norm} ")):
        return "A"
    if team_b_norm and (text == team_b_norm or text == f"{team_b_norm} win" or text.startswith(f"{team_b_norm} ")):
        return "B"
    return ""


def _prediction_label_from_value(value: str | None, team_a: str = "", team_b: str = "") -> str:
    """Convert a raw stored pick into a clean human-readable winner label."""
    choice = _winner_choice_code(value, team_a, team_b)
    if choice == "A":
        return team_a or "Team A"
    if choice == "B":
        return team_b or "Team B"
    if choice == "draw":
        return "Draw"

    text = str(value or "").strip()
    if text.lower().endswith(" win"):
        text = text[:-4].strip()
    return text or "Unknown"


def _compute_prediction_outcome(prediction: dict) -> dict:
    """Compute winner/totals display values for a completed prediction.

    Winner and totals legs are evaluated separately, and the tracked result is a
    parlay-style win only when every tracked leg hits.
    """
    sport = (prediction.get("sport") or "").lower()
    final_score = prediction.get("final_score") or {}
    actual_result = _winner_choice_code(
        prediction.get("actual_result"),
        prediction.get("team_a", ""),
        prediction.get("team_b", ""),
    )
    predicted_winner = _winner_choice_code(
        prediction.get("predicted_winner"),
        prediction.get("team_a", ""),
        prediction.get("team_b", ""),
    )

    winner_hit = bool(actual_result and predicted_winner and actual_result == predicted_winner)
    actual_winner = prediction.get("actual_winner") or "Unknown"
    if actual_result == "A":
        actual_winner = prediction.get("team_a", "Unknown")
    elif actual_result == "B":
        actual_winner = prediction.get("team_b", "Unknown")
    elif actual_result == "draw":
        actual_winner = "Draw"

    totals_pick = _normalize_totals_pick(prediction.get("totals_pick"))
    total_line = prediction.get("totals_line")
    if total_line is None and totals_pick:
        total_line = _default_totals_line(sport)
    elif total_line is not None:
        try:
            total_line = float(total_line)
        except (TypeError, ValueError):
            total_line = _default_totals_line(sport) if totals_pick else None

    totals_hit = None
    totals_result = None
    ou_display = None
    total_scored = None
    actual_total_side = None
    totals_pick_display = _totals_pick_display(totals_pick, total_line)
    totals_required = bool(totals_pick_display)

    if isinstance(final_score, dict) and "a" in final_score and "b" in final_score:
        total_scored = final_score.get("a", 0) + final_score.get("b", 0)
        if total_line is not None:
            actual_total_side = "Over" if total_scored > total_line else "Under"
            totals_result = actual_total_side
            if totals_required:
                totals_hit = actual_total_side == totals_pick
                ou_display = f"Totals Leg: {totals_pick_display} — {'Hit' if totals_hit else 'Miss'}"

    game_win = winner_hit and (totals_hit is True if totals_required else True)
    overall_result = "Win" if game_win else "Loss"
    winner_result = "Hit" if winner_hit else "Miss"

    return {
        "winner_hit": winner_hit,
        "actual_winner": actual_winner,
        "winner_result": winner_result,
        "totals_result": totals_result,
        "totals_hit": totals_hit,
        "totals_pick": totals_pick,
        "totals_pick_display": totals_pick_display,
        "totals_line": total_line,
        "totals_required": totals_required,
        "actual_total_side": actual_total_side,
        "ou_display": ou_display,
        "game_win": game_win,
        "overall_result": overall_result,
        "total_scored": total_scored,
    }


def _display_predicted_winner(prediction: dict) -> str:
    """Return a human-readable predicted winner label for a stored prediction."""
    if prediction.get("predicted_pick_label"):
        return str(prediction.get("predicted_pick_label") or "Unknown")
    return _prediction_label_from_value(
        prediction.get("predicted_winner"),
        prediction.get("team_a", ""),
        prediction.get("team_b", ""),
    )


def _enhance_prediction_record(prediction: dict) -> dict:
    """Return a copy of a prediction with derived display fields."""
    pred = dict(prediction or {})
    sport = str(pred.get("sport") or "").lower()
    final_score = pred.get("final_score", {})

    pred["prediction_market"] = str(pred.get("prediction_market") or "winner").lower()
    pred["predicted_winner_display"] = _display_predicted_winner(pred)
    pred["predicted_winner_code"] = _winner_choice_code(
        pred.get("predicted_winner"),
        pred.get("team_a", ""),
        pred.get("team_b", ""),
    )

    if isinstance(final_score, dict) and "a" in final_score and "b" in final_score:
        total_value = final_score.get("a", 0) + final_score.get("b", 0)
        pred["total_scored"] = total_value
        pred["total_label"] = (
            f"Total Goals: {total_value}" if sport == "soccer" else f"Total Points: {total_value}"
        )
        pred["final_score_display"] = f"{final_score.get('a', 0)}-{final_score.get('b', 0)}"
    else:
        pred["final_score_display"] = "Unknown"

    outcome = _compute_prediction_outcome(pred)
    pred["actual_winner"] = outcome["actual_winner"]
    pred["winner_hit"] = outcome["winner_hit"]
    pred["winner_display"] = f"Winner Pick: {'Hit' if outcome['winner_hit'] else 'Miss'}"
    pred["ou_result"] = outcome["totals_result"]
    pred["ou_hit"] = outcome["totals_hit"]
    pred["totals_pick"] = outcome["totals_pick"]
    pred["totals_pick_display"] = outcome["totals_pick_display"]
    pred["totals_line"] = outcome["totals_line"]
    pred["totals_required"] = outcome["totals_required"]
    pred["actual_total_side"] = outcome["actual_total_side"]
    pred["ou_display"] = outcome["ou_display"]
    pred["game_win"] = outcome["game_win"]
    pred["overall_game_result"] = outcome["overall_result"]

    return pred


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _canonical_outcome(value: Any, team_a: str, team_b: str) -> str:
    text = _normalize_text(value)
    if not text:
        return ""

    draw_aliases = {
        "draw",
        "d",
        "x",
        "tie",
        "tied",
        "stalemate",
        "home/away draw",
    }
    if text in draw_aliases:
        return "draw"

    if text in {"avoid", "skip", "pass", "no bet", "nobet"}:
        return "avoid"

    if text in {"a", "home", "home win", "homewin", "1", "team a", "teama"}:
        return "A"
    if text in {"b", "away", "away win", "awaywin", "2", "team b", "teamb"}:
        return "B"

    norm_a = _normalize_text(team_a)
    norm_b = _normalize_text(team_b)
    if norm_a and (text == norm_a or f"{norm_a} win" in text):
        return "A"
    if norm_b and (text == norm_b or f"{norm_b} win" in text):
        return "B"

    return ""


def _canonical_total_pick(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    if text in {"over", "o", "ov", "over 2.5", "over 220.5"}:
        return "Over"
    if text in {"under", "u", "un", "under 2.5", "under 220.5"}:
        return "Under"
    return ""


def _actual_result_from_score(final_score: dict | None) -> str:
    if not isinstance(final_score, dict):
        return ""
    a_score = final_score.get("a")
    b_score = final_score.get("b")
    if a_score is None or b_score is None:
        return ""
    try:
        a_val = int(a_score)
        b_val = int(b_score)
    except (TypeError, ValueError):
        return ""
    if a_val > b_val:
        return "A"
    if b_val > a_val:
        return "B"
    return "draw"


def _totals_line_for_sport(sport: str) -> float:
    return 220.5 if sport == "nba" else 2.5


def _apply_grading(pred: dict[str, Any]) -> bool:
    """Recompute winner/totals/overall grading for one prediction record."""
    changed = False
    _grade_score(pred)
    sport = str(pred.get("sport") or "").lower()
    team_a = str(pred.get("team_a") or "")
    team_b = str(pred.get("team_b") or "")
    final_score = pred.get("final_score") if isinstance(pred.get("final_score"), dict) else None

    predicted_outcome = _canonical_outcome(pred.get("predicted_winner"), team_a, team_b)
    actual_outcome = _canonical_outcome(pred.get("actual_result"), team_a, team_b)
    if not actual_outcome:
        actual_outcome = _actual_result_from_score(final_score)

    winner_hit: bool | None
    if predicted_outcome and actual_outcome:
        winner_hit = predicted_outcome == actual_outcome
    else:
        winner_hit = None

    total_scored: int | None = None
    if final_score:
        a_score = final_score.get("a")
        b_score = final_score.get("b")
        if isinstance(a_score, (int, float)) and isinstance(b_score, (int, float)):
            total_scored = int(a_score) + int(b_score)

    ou_line = _totals_line_for_sport(sport)
    actual_total_side = ""
    if total_scored is not None:
        if total_scored > ou_line:
            actual_total_side = "Over"
        elif total_scored < ou_line:
            actual_total_side = "Under"
        else:
            actual_total_side = "Push"

    predicted_total_side = _canonical_total_pick(
        pred.get("predicted_total_pick")
        or pred.get("predicted_ou")
        or pred.get("ou_pick")
    )

    totals_hit: bool | None
    if predicted_total_side and actual_total_side in {"Over", "Under"}:
        totals_hit = predicted_total_side == actual_total_side
    else:
        totals_hit = None

    if winner_hit is True:
        if totals_hit is False:
            overall_result = "Partial"
        else:
            overall_result = "Win"
    elif winner_hit is False:
        if totals_hit is True:
            overall_result = "Partial"
        else:
            overall_result = "Loss"
    else:
        overall_result = "Pending"

    winner_display = "Winner Pick: Pending"
    if winner_hit is True:
        winner_display = "Winner Pick: Hit"
    elif winner_hit is False:
        winner_display = "Winner Pick: Miss"

    totals_display = "Totals Pick: Not tracked"
    if predicted_total_side:
        if actual_total_side == "Push":
            totals_display = f"Totals Pick ({predicted_total_side} {ou_line}): Push"
        elif totals_hit is True:
            totals_display = f"Totals Pick ({predicted_total_side} {ou_line}): Hit"
        elif totals_hit is False:
            totals_display = f"Totals Pick ({predicted_total_side} {ou_line}): Miss"
        else:
            totals_display = "Totals Pick: Pending"

    if final_score and isinstance(final_score, dict):
        final_score_display = f"{final_score.get('a', 0)}-{final_score.get('b', 0)}"
    else:
        final_score_display = "Unknown"

    if actual_outcome == "A":
        actual_winner = team_a or "Unknown"
    elif actual_outcome == "B":
        actual_winner = team_b or "Unknown"
    elif actual_outcome == "draw":
        actual_winner = "Draw"
    else:
        actual_winner = "Unknown"

    updates: dict[str, Any] = {
        "predicted_winner_normalized": predicted_outcome,
        "actual_result_normalized": actual_outcome,
        "winner_hit": winner_hit,
        "is_correct": winner_hit,
        "winner_display": winner_display,
        "actual_winner": actual_winner,
        "total_scored": total_scored,
        "total_label": f"Total {'Points' if sport == 'nba' else 'Goals'}: {total_scored}" if total_scored is not None else "",
        "ou_line": ou_line,
        "ou_result": actual_total_side,
        "totals_hit": totals_hit,
        "ou_hit": totals_hit,
        "ou_display": totals_display,
        "final_score_display": final_score_display,
        "game_win": bool(winner_hit) if winner_hit is not None else False,
        "overall_game_result": overall_result,
    }

    for key, value in updates.items():
        if pred.get(key) != value:
            pred[key] = value
            changed = True

    return changed


def market_snapshot(odds: dict | None, bookmaker: str | None = None) -> dict | None:
    """Decimal prices keyed a/b/draw plus their margin-free probabilities; None unless every side is priced."""
    if not isinstance(odds, dict):
        return None
    prices: dict[str, float] = {}
    for side in ("a", "b", "draw"):
        try:
            price = float(odds.get(side)) if odds.get(side) is not None else None
        except (TypeError, ValueError):
            return None
        if price is not None:
            if not price > 1.0:
                return None
            prices[side] = price
    if "a" not in prices or "b" not in prices:
        return None
    overround = sum(1.0 / price for price in prices.values())
    return {
        "odds": prices,
        "fair": {side: round((1.0 / price) / overround, 6) for side, price in prices.items()},
        "bookmaker": bookmaker or odds.get("bookmaker"),
        "captured_at": _isoformat_z(_utc_now()),
    }


def _score_forecast(predicted: dict | None) -> dict | None:
    """Validated expected score {"a", "b"} with an optional most likely exact score."""
    if not isinstance(predicted, dict):
        return None
    try:
        forecast = {"a": round(float(predicted["a"]), 2), "b": round(float(predicted["b"]), 2)}
    except (KeyError, TypeError, ValueError):
        return None
    if not all(0 <= value < 500 for value in forecast.values()):
        return None
    likely = predicted.get("likely")
    if isinstance(likely, dict) and isinstance(likely.get("a"), int) and isinstance(likely.get("b"), int):
        forecast["likely"] = {"a": likely["a"], "b": likely["b"]}
    return forecast


def _grade_score(pred: dict[str, Any]) -> None:
    """Compare the predicted score with the final score: per-team and total error, exact-score hit."""
    forecast, final = pred.get("predicted_score"), pred.get("final_score")
    if not isinstance(forecast, dict) or not isinstance(final, dict):
        return
    try:
        actual_a, actual_b = int(final["a"]), int(final["b"])
    except (KeyError, TypeError, ValueError):
        return
    grade = {"error_a": round(abs(forecast["a"] - actual_a), 2), "error_b": round(abs(forecast["b"] - actual_b), 2),
             "error_total": round(abs(forecast["a"] + forecast["b"] - actual_a - actual_b), 2)}
    likely = forecast.get("likely")
    if likely:
        grade["exact_hit"] = likely["a"] == actual_a and likely["b"] == actual_b
    pred["score_grade"] = grade


def _engine_fingerprint(sport: str) -> dict[str, Any] | None:
    try:
        from services import engine_fingerprint
        return engine_fingerprint.for_sport(sport)
    except Exception:
        logger.warning("Engine fingerprint unavailable for %s", sport, exc_info=True)
        return None


FIRST_PUBLISHED_FIELDS = ("predicted_winner", "predicted_pick_label", "prob_a", "prob_b", "prob_draw", "confidence",
                          "prediction_market", "totals_pick", "totals_line", "market_at_pick", "engine", "created_at")


@transaction(lambda: _TRACKING_FILE)
def save_prediction(
    sport: str,
    team_a: str,
    team_b: str,
    predicted_winner: str,  # "A" | "B" | "draw"
    win_probs: dict[str, float],  # {"a": x, "b": y, "draw": z}
    confidence: str,  # "High" | "Medium" | "Low"
    game_date: str | None = None,
    team_a_id: str | int | None = None,
    team_b_id: str | int | None = None,
    league_id: int | None = None,
    league_name: str | None = None,
    prediction_notes: str | None = None,
    model_factors: dict[str, Any] | None = None,
    fixture_id: str | int | None = None,
    prediction_market: str = "winner",
    totals_pick: str | None = None,
    totals_line: float | int | None = None,
    totals_market: str | None = None,
    season: int | None = None,
    model_probability: float | None = None,   # model's top class probability (0-1)
    implied_probability: float | None = None,  # market implied probability (reserved)
    form_a_length: int | None = None,          # home team's form list length (for recency_bias detection)
    elo_diff: float | None = None,             # home_elo - away_elo (for popular_team_overrating)
    kickoff: str | datetime | None = None,     # scheduled start; picks are frozen from this moment
    market_odds: dict | None = None,           # decimal prices {"a", "b", "draw"?, "bookmaker"?} seen at pick time
    predicted_score: dict | None = None,       # {"a", "b"} expected goals/points, optional "likely": {"a", "b"}
    raw_probs: dict | None = None,             # model probabilities before recalibration (what learning fits)
) -> str:
    """
    Save or update a prediction in the tracking file.
    
    Returns the prediction ID for later updates.
    Checks for duplicates based on sport, date, and teams.
    If a duplicate exists, returns the existing ID without saving.
    If no concrete game date is available, the prediction is not tracked.
    """
    predictions = _load_predictions()
    normalized_pick = _winner_choice_code(predicted_winner, team_a, team_b) or str(predicted_winner or "").strip()
    predicted_pick_label = _prediction_label_from_value(predicted_winner, team_a, team_b)
    normalized_market = str(prediction_market or "winner").strip().lower() or "winner"
    fixture_id_value = str(fixture_id).strip() if fixture_id is not None and str(fixture_id).strip() else None
    normalized_totals_pick = _normalize_totals_pick(totals_pick)
    totals_line_value = parse_totals_line(totals_line, totals_market)
    if normalized_totals_pick and totals_line_value is None:
        totals_line_value = _default_totals_line(sport)
    totals_market_value = str(totals_market or _default_totals_market(sport)).strip().lower() if normalized_totals_pick else None
    
    game_date_normalized = normalize_date(game_date)
    if not game_date_normalized:
        return ""
    game_key = _get_game_key(sport, game_date_normalized, team_a, team_b, league_id)
    kickoff_at = _aware_datetime(kickoff)
    started = kickoff_at is not None and kickoff_at <= _utc_now()
    engine = _engine_fingerprint(sport)
    market_at_pick = market_snapshot(market_odds)
    score_forecast = _score_forecast(predicted_score)
    raw = {k: round(float(raw_probs.get(k) or 0), 2) for k in ("a", "draw", "b")} if isinstance(raw_probs, dict) else None

    # Check for existing prediction with same game key
    for existing in predictions:
        existing_key = _get_game_key(
            existing.get("sport", ""),
            existing.get("date", ""),
            existing.get("team_a", ""),
            existing.get("team_b", ""),
            existing.get("league_id"),
        )
        if existing_key == game_key:
            stored_kickoff = _aware_datetime(existing.get("kickoff_at"))
            frozen = started or (stored_kickoff is not None and stored_kickoff <= _utc_now())
            if existing.get("status") != "completed" and not frozen:
                # The first published version is kept exactly as published and graded on its own (audit, 2026-11-04);
                # the fields below then follow the latest pregame revision.
                existing.setdefault("first_published", {key: existing.get(key) for key in FIRST_PUBLISHED_FIELDS})
                if (existing.get("predicted_winner"), existing.get("totals_pick")) != (normalized_pick, normalized_totals_pick):
                    existing["revisions"] = int(existing.get("revisions") or 0) + 1
                existing["predicted_winner"] = normalized_pick
                existing["predicted_pick_label"] = predicted_pick_label
                existing["prob_a"] = round(win_probs.get("a", 0), 1)
                existing["prob_b"] = round(win_probs.get("b", 0), 1)
                existing["prob_draw"] = round(win_probs.get("draw", 0), 1)
                existing["confidence"] = confidence
                existing["prediction_market"] = normalized_market
                existing["totals_pick"] = normalized_totals_pick
                existing["totals_line"] = totals_line_value
                existing["totals_market"] = totals_market_value
                existing["status"] = "pending"
                existing["actual_result"] = None
                existing["is_correct"] = None
                existing["final_score"] = None
                existing["actual_winner"] = None
                existing["winner_result"] = None
                existing["totals_result"] = None
                existing["overall_result"] = None
                existing["league_id"] = league_id
                existing["league_name"] = league_name
                existing["fixture_id"] = fixture_id_value
                existing["prediction_notes"] = prediction_notes
                existing["model_factors"] = model_factors if isinstance(model_factors, dict) else {}
                if kickoff_at is not None:
                    existing["kickoff_at"] = _isoformat_z(kickoff_at)
                existing["engine"] = engine
                if market_at_pick is not None:
                    existing["market_at_pick"] = market_at_pick
                if score_forecast is not None:
                    existing["predicted_score"] = score_forecast
                if raw is not None:
                    existing["raw_probs"] = raw
                existing["updated_at"] = _utc_now().isoformat().replace("+00:00", "Z")
                _save_predictions(predictions)
            return existing.get("id", "")

    if started:
        return ""

    pred_id = str(uuid.uuid4())[:8]
    now = _utc_now().isoformat().replace("+00:00", "Z")
    
    prediction = {
        "id": pred_id,
        "sport": sport,
        "date": game_date_normalized,
        "game_date": game_date_normalized,
        "team_a": team_a,
        "team_b": team_b,
        "predicted_winner": normalized_pick,
        "predicted_pick_label": predicted_pick_label,
        "prob_a": round(win_probs.get("a", 0), 1),
        "prob_b": round(win_probs.get("b", 0), 1),
        "prob_draw": round(win_probs.get("draw", 0), 1),
        "confidence": confidence,
        "prediction_market": normalized_market,
        "totals_pick": normalized_totals_pick,
        "totals_line": totals_line_value,
        "totals_market": totals_market_value,
        "league_id": league_id,
        "league_name": league_name,
        "fixture_id": fixture_id_value,
        "prediction_notes": prediction_notes,
        "model_factors": model_factors if isinstance(model_factors, dict) else {},
        "status": "pending",
        "actual_result": None,
        "is_correct": None,
        "final_score": None,
        "actual_winner": None,
        "winner_result": None,
        "totals_result": None,
        "overall_result": None,
        "created_at": now,
        "updated_at": now,
    }
    if kickoff_at is not None:
        prediction["kickoff_at"] = _isoformat_z(kickoff_at)
    prediction["engine"] = engine
    if market_at_pick is not None:
        prediction["market_at_pick"] = market_at_pick
    if score_forecast is not None:
        prediction["predicted_score"] = score_forecast
    if raw is not None:
        prediction["raw_probs"] = raw
    if team_a_id is not None:
        prediction["team_a_id"] = str(team_a_id)
    if team_b_id is not None:
        prediction["team_b_id"] = str(team_b_id)
    if league_id is not None:
        prediction["league_id"] = int(league_id)
    if season is not None:
        prediction["season"] = int(season)
    if model_probability is not None:
        prediction["model_probability"] = round(float(model_probability), 6)
    prediction["implied_probability"] = implied_probability  # None by default (reserved for odds)
    if form_a_length is not None:
        prediction["form_a_length"] = int(form_a_length)
    if elo_diff is not None:
        prediction["elo_diff"] = round(float(elo_diff), 2)

    predictions.append(prediction)
    _save_predictions(predictions)
    
    return pred_id


@transaction(lambda: _TRACKING_FILE)
def clean_duplicate_predictions() -> int:
    """
    Remove duplicate predictions, keeping the most recent one for each game.
    
    Returns the number of duplicates removed.
    """
    predictions = _load_predictions()
    seen_keys = {}
    to_keep = []
    removed = 0
    
    # Sort by updated_at descending to keep the latest
    predictions.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    
    for pred in predictions:
        game_key = _get_game_key(
            pred.get("sport", ""),
            pred.get("date", ""),
            pred.get("team_a", ""),
            pred.get("team_b", ""),
            pred.get("league_id"),
        )
        if game_key not in seen_keys:
            seen_keys[game_key] = True
            to_keep.append(pred)
        else:
            removed += 1
    
    if removed > 0:
        _save_predictions(to_keep)
    
    return removed


@transaction(lambda: _TRACKING_FILE)
def record_closing_market(pred_id: str, market_odds: dict | None, *, bookmaker: str | None = None) -> bool:
    """Store the latest pre-kickoff price as the closing line; refused once the game has started."""
    snapshot = market_snapshot(market_odds, bookmaker)
    if snapshot is None:
        return False
    predictions = _load_predictions()
    for pred in predictions:
        if pred.get("id") != pred_id:
            continue
        kickoff_at = _aware_datetime(pred.get("kickoff_at"))
        if kickoff_at is None or kickoff_at <= _utc_now() or pred.get("status") == "completed":
            return False
        pred["closing_market"] = snapshot
        _save_predictions(predictions)
        return True
    return False


@transaction(lambda: _TRACKING_FILE)
def update_prediction_result(
    pred_id: str,
    actual_result: str,
    final_score: dict | None = None,
    fixture_id: str | int | None = None,
) -> bool:
    """
    Update a prediction with the actual game result and final score.
    
    Args:
        pred_id: The prediction ID returned from save_prediction()
        actual_result: "A" | "B" | "draw"
        final_score: Optional dict with {"a": int, "b": int} for the final score
    
    Returns True if updated, False if prediction not found.
    """
    predictions = _load_predictions()
    
    for pred in predictions:
        if pred.get("id") == pred_id:
            pred["actual_result"] = actual_result
            pred["status"] = "completed"
            pred["final_score"] = final_score
            _apply_grading(pred)
            first = pred.get("first_published")
            if isinstance(first, dict) and first.get("predicted_winner"):
                first["is_correct"] = str(first["predicted_winner"]).lower() == str(actual_result).lower()
            pred["updated_at"] = _utc_now().isoformat().replace("+00:00", "Z")
            _save_predictions(predictions)
            return True
    
    return False


def get_summary_metrics(exclude_seeded: bool = True) -> dict[str, Any]:
    """
    Compute summary metrics across tracked winner predictions.

    Completed predictions are graded from the persisted winner outcome stored in
    `is_correct`, which is the source of truth for dashboard accuracy.

    Args:
        exclude_seeded: When True (default), exclude predictions with is_seeded=True.

    Returns:
        {
            "total_predictions": int,
            "finalized_predictions": int,
            "wins": int,
            "losses": int,
            "overall_accuracy": float,  # 0-100%
            "by_confidence": {...},
            "by_sport": {...},
            "calibration": {...},
            "seeded_count": int,         # number of seeded predictions excluded
            "recent_predictions": list   # Last 10
        }
    """
    all_predictions = _load_predictions()
    seeded_count = sum(1 for p in all_predictions if p.get("is_seeded"))
    predictions = [p for p in all_predictions if not (exclude_seeded and p.get("is_seeded"))]
    
    if not predictions:
        return {
            "total_predictions": 0,
            "finalized_predictions": 0,
            "wins": 0,
            "losses": 0,
            "overall_accuracy": None,
            "by_confidence": {},
            "by_sport": {},
            "by_league": {},
            "recent_predictions": [],
        }

    # Filter to only finalized predictions with an actual graded outcome.
    finalized = [
        p for p in predictions
        if p.get("status") == "completed" and p.get("is_correct") is not None
    ]

    if not finalized:
        return {
            "total_predictions": len(predictions),
            "finalized_predictions": 0,
            "wins": 0,
            "losses": 0,
            "overall_accuracy": None,
            "by_confidence": {},
            "by_sport": {},
            "by_league": {},
            "recent_predictions": sorted(predictions, key=lambda p: p.get("created_at", ""), reverse=True)[:10],
        }
    
    def is_game_win(pred: dict[str, Any]) -> bool:
        if pred.get("is_correct") is not None:
            return bool(pred.get("is_correct"))
        if pred.get("winner_hit") is not None:
            return bool(pred.get("winner_hit"))
        if pred.get("game_win") is not None:
            return bool(pred.get("game_win"))
        return False
    
    wins = sum(1 for p in finalized if is_game_win(p))
    losses = len(finalized) - wins
    overall_accuracy = (wins / len(finalized)) * 100 if finalized else 0
    
    # By confidence level
    by_confidence = {}
    for conf_level in ("High", "Medium", "Low"):
        conf_preds = [p for p in finalized if p.get("confidence") == conf_level]
        if conf_preds:
            conf_wins = sum(1 for p in conf_preds if is_game_win(p))
            conf_losses = len(conf_preds) - conf_wins
            by_confidence[conf_level] = {
                "accuracy": round((conf_wins / len(conf_preds)) * 100, 1) if conf_preds else 0,
                "count": len(conf_preds),
                "wins": conf_wins,
                "losses": conf_losses,
            }
    
    # By sport
    by_sport = {}
    for sport in ("soccer", "nba"):
        sport_preds = [p for p in finalized if p.get("sport") == sport]
        if sport_preds:
            sport_wins = sum(1 for p in sport_preds if is_game_win(p))
            sport_losses = len(sport_preds) - sport_wins
            by_sport[sport] = {
                "accuracy": round((sport_wins / len(sport_preds)) * 100, 1) if sport_preds else 0,
                "count": len(sport_preds),
                "wins": sport_wins,
                "losses": sport_losses,
            }

    by_league = {}
    soccer_preds = [p for p in finalized if p.get("sport") == "soccer"]
    league_names = sorted({(p.get("league_name") or "Unspecified League") for p in soccer_preds})
    for league_name in league_names:
        league_preds = [p for p in soccer_preds if (p.get("league_name") or "Unspecified League") == league_name]
        if not league_preds:
            continue
        league_wins = sum(1 for p in league_preds if is_game_win(p))
        league_losses = len(league_preds) - league_wins
        by_league[league_name] = {
            "accuracy": round((league_wins / len(league_preds)) * 100, 1) if league_preds else 0,
            "count": len(league_preds),
            "wins": league_wins,
            "losses": league_losses,
        }
    
    # Calibration: per confidence tier, compare avg model_probability vs actual hit rate
    calibration: dict[str, dict[str, Any]] = {}
    for conf_level in ("High", "Medium", "Low"):
        tier_preds = [p for p in finalized if p.get("confidence") == conf_level]
        if not tier_preds:
            continue
        probs = [float(p["model_probability"]) for p in tier_preds if p.get("model_probability") is not None]
        tier_wins = sum(1 for p in tier_preds if is_game_win(p))
        actual_hr  = round(tier_wins / len(tier_preds), 4)
        avg_model  = round(sum(probs) / len(probs), 4) if probs else None
        gap = round(actual_hr - avg_model, 4) if avg_model is not None else None
        if gap is not None:
            if gap < -0.05:
                label = "Overconfident"
            elif gap > 0.05:
                label = "Underconfident"
            else:
                label = "Well-calibrated"
        else:
            label = "Collecting probability tracking"
        calibration[conf_level] = {
            "avg_model_probability": avg_model,
            "actual_hit_rate":       actual_hr,
            "calibration_gap":       gap,
            "label":                 label,
            "count":                 len(tier_preds),
        }

    return {
        "total_predictions": len(predictions),
        "finalized_predictions": len(finalized),
        "wins": wins,
        "losses": losses,
        "overall_accuracy": round(overall_accuracy, 1),
        "by_confidence": by_confidence,
        "by_sport": by_sport,
        "by_league": by_league,
        "calibration": calibration,
        "seeded_count": seeded_count,
        "first_published": _first_published_record(finalized),
        "recent_predictions": sorted(predictions, key=lambda p: p.get("created_at", ""), reverse=True)[:10],
    }


def _first_published_record(finalized: list[dict[str, Any]]) -> dict[str, Any]:
    """Accuracy of picks as first published, beside the final pregame revision, for picks that were revised."""
    revised = [p for p in finalized if isinstance(p.get("first_published"), dict) and p["first_published"].get("is_correct") is not None]
    first = sum(1 for p in revised if p["first_published"]["is_correct"])
    final = sum(1 for p in revised if p.get("is_correct"))
    return {"revised_picks": len(revised), "first_version_correct": first, "final_version_correct": final}


def get_prediction_by_id(pred_id: str) -> dict | None:
    """Fetch a prediction by its stable tracking ID with derived display fields."""
    lookup = str(pred_id or "").strip()
    if not lookup:
        return None
    for pred in _load_predictions():
        if str(pred.get("id") or "") == lookup:
            return _enhance_prediction_record(pred)
    return None


# Alias used by app.py prediction_result_detail route
get_prediction = get_prediction_by_id


def get_recent_predictions(limit: int = 20) -> list[dict]:
    """Get the most recent predictions (finalized or not)."""
    predictions = _load_predictions()
    return sorted(predictions, key=lambda p: p.get("created_at", ""), reverse=True)[:limit]


def get_pending_predictions(limit: int = 20) -> list[dict]:
    """Get the most recent pending predictions."""
    predictions = _load_predictions()
    pending = [p for p in predictions if p.get("status") != "completed"]
    return sorted(pending, key=lambda p: p.get("created_at", ""), reverse=True)[:limit]


def get_completed_predictions(limit: int = 50) -> list[dict]:
    """
    Get completed predictions with detailed result information.
    
    Returns predictions with status="completed" including:
    - Final score
    - Total scored (goals/points)
    - Over/Under result
    - Winner pick result
    """
    predictions = _load_predictions()
    completed = [p for p in predictions if p.get("status") == "completed"]
    
    # Sort by most recent first
    completed = sorted(completed, key=lambda p: p.get("updated_at", ""), reverse=True)[:limit]
    
    # Enhance with calculated fields
    for pred in completed:
        _apply_grading(pred)
    
    return completed


def _prediction_timestamp(pred: dict[str, Any]) -> datetime:
    """Best-effort timestamp parser for stable chronological ordering."""
    for key in ("updated_at", "created_at", "game_date", "date"):
        raw = str(pred.get(key) or "").strip()
        if not raw:
            continue
        try:
            if raw.endswith("Z"):
                raw = raw.replace("Z", "+00:00")
            return datetime.fromisoformat(raw)
        except Exception:
            continue
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _is_win(pred: dict[str, Any]) -> bool:
    value = pred.get("is_correct")
    if value is not None:
        return bool(value)
    return bool(pred.get("winner_hit"))


def _predicted_outcome(pred: dict[str, Any]) -> str:
    outcome = _canonical_outcome(
        pred.get("predicted_winner_normalized") or pred.get("predicted_winner"),
        pred.get("team_a") or "",
        pred.get("team_b") or "",
    )
    if outcome:
        return outcome
    return ""


def _confidence_percent(pred: dict[str, Any]) -> float:
    probs = [pred.get("prob_a"), pred.get("prob_b"), pred.get("prob_draw")]
    numeric_probs = []
    for value in probs:
        if isinstance(value, (int, float)):
            numeric_probs.append(float(value))
    if numeric_probs:
        top_prob = max(numeric_probs)
        if top_prob <= 1.0:
            top_prob *= 100.0
        return max(0.0, min(100.0, round(top_prob, 1)))

    tier = str(pred.get("confidence") or "").lower()
    mapped = {"high": 85.0, "medium": 70.0, "low": 55.0}
    return mapped.get(tier, 50.0)


def _confidence_bucket(conf_pct: float) -> str:
    if conf_pct >= 80.0:
        return "80-100"
    if conf_pct >= 60.0:
        return "60-80"
    if conf_pct >= 40.0:
        return "40-60"
    return "<40"


def _finalized_predictions(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finalized = [
        pred for pred in predictions
        if pred.get("status") == "completed" and pred.get("is_correct") is not None
    ]
    finalized.sort(key=_prediction_timestamp)
    return finalized


def _build_rolling_accuracy_series(finalized: list[dict[str, Any]], rolling_window: int) -> list[dict[str, Any]]:
    window_size = max(1, int(rolling_window))
    rolling_points: list[dict[str, Any]] = []
    recent_binary: list[int] = []

    for idx, pred in enumerate(finalized, start=1):
        is_win = _is_win(pred)
        recent_binary.append(1 if is_win else 0)
        window = recent_binary[max(0, len(recent_binary) - window_size) :]
        rolling_acc = round((sum(window) / len(window)) * 100.0, 1) if window else 0.0

        label = pred.get("date") or pred.get("game_date") or pred.get("updated_at") or ""
        rolling_points.append(
            {
                "match_index": idx,
                "label": str(label)[:10] if label else f"#{idx}",
                "rolling_accuracy": rolling_acc,
                "is_win": is_win,
            }
        )

    return rolling_points


def _build_daily_rolling_accuracy_series(finalized: list[dict[str, Any]], rolling_window: int) -> list[dict[str, Any]]:
    window_size = max(1, int(rolling_window))
    daily_buckets: dict[str, dict[str, int]] = {}
    for pred in finalized:
        day = normalize_date(pred.get("date") or pred.get("game_date")) or "unknown"
        bucket = daily_buckets.setdefault(day, {"wins": 0, "count": 0})
        bucket["count"] += 1
        bucket["wins"] += 1 if _is_win(pred) else 0

    daily_points: list[dict[str, Any]] = []
    rolling_daily: list[float] = []
    for idx, day in enumerate(sorted(daily_buckets.keys()), start=1):
        bucket = daily_buckets[day]
        day_acc = round((bucket["wins"] / bucket["count"]) * 100.0, 1) if bucket["count"] else 0.0
        rolling_daily.append(day_acc)
        day_window = rolling_daily[max(0, len(rolling_daily) - window_size) :]
        daily_points.append(
            {
                "day_index": idx,
                "label": day,
                "daily_accuracy": day_acc,
                "rolling_accuracy": round(sum(day_window) / len(day_window), 1) if day_window else 0.0,
                "matches": bucket["count"],
            }
        )

    return daily_points


def _build_cumulative_performance_series(finalized: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cumulative_points: list[dict[str, Any]] = []
    cumulative_value = 0

    for idx, pred in enumerate(finalized, start=1):
        is_win = _is_win(pred)
        outcome = _predicted_outcome(pred)
        delta = 0 if outcome == "avoid" else (1 if is_win else -1)
        cumulative_value += delta

        label = pred.get("date") or pred.get("game_date") or pred.get("updated_at") or ""
        cumulative_points.append(
            {
                "match_index": idx,
                "label": str(label)[:10] if label else f"#{idx}",
                "cumulative_points": cumulative_value,
                "delta": delta,
            }
        )

    return cumulative_points


def _build_confidence_bucket_stats(finalized: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calibration_counts = {
        "80-100": {"count": 0, "wins": 0},
        "60-80": {"count": 0, "wins": 0},
        "40-60": {"count": 0, "wins": 0},
        "<40": {"count": 0, "wins": 0},
    }
    for pred in finalized:
        conf_pct = _confidence_percent(pred)
        bucket_key = _confidence_bucket(conf_pct)
        calibration_counts[bucket_key]["count"] += 1
        calibration_counts[bucket_key]["wins"] += 1 if _is_win(pred) else 0

    confidence_calibration = []
    for bucket_key in ("80-100", "60-80", "40-60", "<40"):
        bucket = calibration_counts[bucket_key]
        hit_rate = round((bucket["wins"] / bucket["count"]) * 100.0, 1) if bucket["count"] else 0.0
        confidence_calibration.append(
            {
                "bucket": bucket_key,
                "sample_size": bucket["count"],
                "avg_confidence": {
                    "80-100": 90.0,
                    "60-80": 70.0,
                    "40-60": 50.0,
                    "<40": 30.0,
                }[bucket_key],
                "actual_hit_rate": hit_rate,
            }
        )

    return confidence_calibration


def _build_outcome_breakdown(finalized: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outcome_breakdown = []
    for key, label in (("A", "Home"), ("B", "Away"), ("draw", "Draw")):
        subset = [pred for pred in finalized if _predicted_outcome(pred) == key]
        wins = sum(1 for pred in subset if _is_win(pred))
        count = len(subset)
        outcome_breakdown.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "wins": wins,
                "losses": count - wins,
                "accuracy": round((wins / count) * 100.0, 1) if count else None,
            }
        )
    return outcome_breakdown


def _build_recent_form(finalized: list[dict[str, Any]]) -> dict[str, dict[str, int | float | None]]:
    recent_10 = finalized[-10:]
    recent_20 = finalized[-20:]
    return {
        "last_10": {
            "count": len(recent_10),
            "accuracy": round((sum(1 for pred in recent_10 if _is_win(pred)) / len(recent_10)) * 100.0, 1)
            if recent_10 else None,
        },
        "last_20": {
            "count": len(recent_20),
            "accuracy": round((sum(1 for pred in recent_20 if _is_win(pred)) / len(recent_20)) * 100.0, 1)
            if recent_20 else None,
        },
    }


def _recommendation_label(pred: dict[str, Any]) -> str:
    existing = str(pred.get("play_type") or pred.get("recommendation") or "").strip()
    if existing:
        return existing

    outcome = _predicted_outcome(pred)
    if outcome == "A":
        return f"{pred.get('team_a') or 'Team A'} ML"
    if outcome == "B":
        return f"{pred.get('team_b') or 'Team B'} ML"
    if outcome == "draw":
        return "Draw"
    if outcome == "avoid":
        return "Avoid"
    return "--"


def _strategy_sample_status(source: str, sample_size: int | None) -> str:
    count = max(int(sample_size or 0), 0)
    if count == 0:
        return "Collecting data"
    if source == "offline":
        if count < 80:
            return "Limited evaluation sample"
        if count < 250:
            return "Building evaluation sample"
        return "Established evaluation sample"
    if source == "live":
        if count < 8:
            return "Early live sample"
        if count < 25:
            return "Limited live sample"
        return "Established live sample"
    if count < 25:
        return "Experimental sample"
    return "Experimental"


def _strategy_rank_key(row: dict[str, Any]) -> tuple[float, float, int]:
    accuracy = row.get("accuracy")
    if not isinstance(accuracy, (int, float)):
        return (-1.0, -1.0, 0)

    source = str(row.get("source") or "")
    sample_size = max(int(row.get("sample_size") or 0), 0)
    sample_status = str(row.get("sample_status") or "")

    if source == "offline":
        reliability = 3.0 if sample_size >= 80 else 2.5
    elif source == "live":
        reliability = 2.0 if sample_size >= 25 else 1.25 if sample_size >= 8 else 0.75
    else:
        reliability = 0.5 if sample_size >= 25 else 0.0

    if "Early" in sample_status:
        reliability -= 0.5

    return (reliability, float(accuracy), sample_size)


def _build_failure_rows(finalized: list[dict[str, Any]], failure_limit: int) -> list[dict[str, Any]]:
    failures = [pred for pred in finalized if not _is_win(pred)]
    failures.sort(key=_prediction_timestamp, reverse=True)
    failure_rows = []
    for pred in failures[: max(1, failure_limit)]:
        failure_rows.append(
            {
                "date": normalize_date(pred.get("date") or pred.get("game_date")) or (str(pred.get("updated_at") or "")[:10]),
                "sport": str(pred.get("sport") or "").upper(),
                "matchup": f"{pred.get('team_a', 'Team A')} vs {pred.get('team_b', 'Team B')}",
                "predicted_outcome": pred.get("predicted_winner") or _predicted_outcome(pred) or "--",
                "actual_result": pred.get("actual_winner") or pred.get("actual_result") or pred.get("actual_result_normalized") or "--",
                "confidence": pred.get("confidence") or "Low",
                "confidence_pct": _confidence_percent(pred),
                "recommendation": _recommendation_label(pred),
                "notes": pred.get("reasoning") or pred.get("winner_display") or "",
            }
        )
    return failure_rows


def _build_pass_rows(finalized: list[dict[str, Any]], pass_limit: int) -> list[dict[str, Any]]:
    passes = [pred for pred in finalized if _is_win(pred)]
    passes.sort(key=_prediction_timestamp, reverse=True)
    pass_rows = []
    for pred in passes[: max(1, pass_limit)]:
        pass_rows.append(
            {
                "date": normalize_date(pred.get("date") or pred.get("game_date")) or (str(pred.get("updated_at") or "")[:10]),
                "sport": str(pred.get("sport") or "").upper(),
                "matchup": f"{pred.get('team_a', 'Team A')} vs {pred.get('team_b', 'Team B')}",
                "predicted_outcome": pred.get("predicted_winner") or _predicted_outcome(pred) or "--",
                "actual_result": pred.get("actual_winner") or pred.get("actual_result") or pred.get("actual_result_normalized") or "--",
                "confidence": pred.get("confidence") or "Low",
                "confidence_pct": _confidence_percent(pred),
                "recommendation": _recommendation_label(pred),
                "notes": pred.get("reasoning") or pred.get("winner_display") or "",
            }
        )
    return pass_rows


def _build_strategy_comparison(
    metrics: dict[str, Any],
    finalized: list[dict[str, Any]],
    all_predictions: list[dict[str, Any]],
    strategy_reference: dict[str, Any],
) -> list[dict[str, Any]]:
    tracker_accuracy = metrics.get("overall_accuracy")
    edge_filtered_subset = [
        pred for pred in finalized
        if _predicted_outcome(pred) != "avoid" and _confidence_percent(pred) >= 60.0
    ]
    edge_filtered_accuracy = (
        round((sum(1 for pred in edge_filtered_subset if _is_win(pred)) / len(edge_filtered_subset)) * 100.0, 1)
        if edge_filtered_subset else None
    )
    avoid_aware_score = (
        round(
            ((sum(1 for pred in all_predictions if _predicted_outcome(pred) == "avoid") + metrics.get("wins", 0))
             / max(1, len(all_predictions))) * 100.0,
            1,
        )
        if all_predictions else None
    )

    ml_accuracy = strategy_reference.get("ml_accuracy")
    combined_accuracy = strategy_reference.get("combined_accuracy")

    rows = [
        {
            "strategy": "Live tracked production picks",
            "accuracy": tracker_accuracy,
            "sample_size": metrics.get("finalized_predictions", 0),
            "source": "live",
            "note": "Real predictions graded after matches finish.",
        },
        {
            "strategy": "Combined Signal",
            "accuracy": combined_accuracy,
            "sample_size": strategy_reference.get("evaluation_matches"),
            "source": "offline",
            "note": "Primary offline evaluation for the production decision layer.",
        },
        {
            "strategy": "ML (Stacking)",
            "accuracy": ml_accuracy,
            "sample_size": strategy_reference.get("evaluation_matches"),
            "source": "offline",
            "note": "Offline evaluation for the standalone model stack.",
        },
        {
            "strategy": "High-confidence live subset",
            "accuracy": edge_filtered_accuracy,
            "sample_size": len(edge_filtered_subset),
            "source": "experimental",
            "note": "Smaller live slice using only higher-confidence picks.",
        },
        {
            "strategy": "Avoid-aware live policy",
            "accuracy": avoid_aware_score,
            "sample_size": len(all_predictions),
            "source": "experimental",
            "note": "Experimental lens that credits avoided matches as skipped risk.",
        },
    ]
    for row in rows:
        row["sample_status"] = _strategy_sample_status(row.get("source", ""), row.get("sample_size"))
    return rows


def get_evaluation_dashboard(
    rolling_window: int = 10,
    failure_limit: int = 12,
    pass_limit: int = 12,
    strategy_reference: dict[str, Any] | None = None,
    exclude_seeded: bool = True,
) -> dict[str, Any]:
    """Build a full evaluation payload for the model performance dashboard."""
    raw_predictions = _load_predictions()
    seeded_count = sum(1 for p in raw_predictions if p.get("is_seeded"))
    all_predictions = [p for p in raw_predictions if not (exclude_seeded and p.get("is_seeded"))]
    metrics = get_summary_metrics(exclude_seeded=exclude_seeded)
    finalized = _finalized_predictions(all_predictions)

    avoids_skipped = sum(1 for pred in all_predictions if _predicted_outcome(pred) == "avoid")

    rolling_points = _build_rolling_accuracy_series(finalized, rolling_window)
    daily_points = _build_daily_rolling_accuracy_series(finalized, rolling_window)
    cumulative_points = _build_cumulative_performance_series(finalized)
    confidence_calibration = _build_confidence_bucket_stats(finalized)
    outcome_breakdown = _build_outcome_breakdown(finalized)
    recent_form = _build_recent_form(finalized)
    failure_rows = _build_failure_rows(finalized, failure_limit)
    pass_rows = _build_pass_rows(finalized, pass_limit)

    strategy_reference = strategy_reference or {}
    strategy_comparison = _build_strategy_comparison(
        metrics,
        finalized,
        all_predictions,
        strategy_reference,
    )
    tracker_accuracy = metrics.get("overall_accuracy")

    valid_strategies = [item for item in strategy_comparison if isinstance(item.get("accuracy"), (int, float))]
    best_strategy = max(valid_strategies, key=_strategy_rank_key)["strategy"] if valid_strategies else "Awaiting sample"

    roi_fields = ["profit", "roi", "points_won"]
    roi_values = [
        float(pred.get(field))
        for pred in finalized
        for field in roi_fields
        if isinstance(pred.get(field), (int, float))
    ]
    cumulative_value = cumulative_points[-1]["cumulative_points"] if cumulative_points else 0
    roi_or_points = round(sum(roi_values), 2) if roi_values else cumulative_value

    kpis = {
        "overall_accuracy": tracker_accuracy,
        "rolling_win_rate": rolling_points[-1]["rolling_accuracy"] if rolling_points else None,
        "total_tracked_predictions": metrics.get("total_predictions", 0),
        "finalized_predictions": metrics.get("finalized_predictions", 0),
        "avoids_skipped": avoids_skipped,
        "current_best_strategy": best_strategy,
        "roi_or_points": roi_or_points,
    }

    by_confidence_tier = []
    for tier in ("High", "Medium", "Low"):
        row = (metrics.get("by_confidence") or {}).get(tier)
        if row:
            by_confidence_tier.append(
                {
                    "tier": tier,
                    "accuracy": row.get("accuracy"),
                    "count": row.get("count", 0),
                    "wins": row.get("wins", 0),
                    "losses": row.get("losses", 0),
                }
            )

    by_sport = []
    for sport_key in ("soccer", "nba"):
        row = (metrics.get("by_sport") or {}).get(sport_key)
        if row:
            by_sport.append(
                {
                    "sport": sport_key,
                    "accuracy": row.get("accuracy"),
                    "count": row.get("count", 0),
                    "wins": row.get("wins", 0),
                    "losses": row.get("losses", 0),
                }
            )

    return {
        "kpis": kpis,
        "rolling_window": max(1, int(rolling_window)),
        "series": {
            "rolling_by_match": rolling_points,
            "rolling_by_day": daily_points,
            "cumulative_points": cumulative_points,
        },
        "confidence_calibration": confidence_calibration,
        "strategy_comparison": strategy_comparison,
        "breakdowns": {
            "by_sport": by_sport,
            "by_confidence_tier": by_confidence_tier,
            "by_predicted_outcome": outcome_breakdown,
            "recent_form": recent_form,
        },
        "failure_rows": failure_rows,
        "pass_rows": pass_rows,
        "seeded_count": seeded_count,
        "exclude_seeded": exclude_seeded,
    }
