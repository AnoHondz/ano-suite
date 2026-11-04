"""Strict request validation for score forecasts; reject non-finite values."""
from collections.abc import Mapping
from datetime import UTC, datetime

from services.scoring_research import finite


class ScorelineInputError(ValueError):
    pass


def validate(params, sport):
    if not isinstance(params, Mapping):
        raise ScorelineInputError("Expected a request object.")
    ids = [str(params.get(key) or params.get(alias) or "").strip() for key, alias in (("team_a_id", "home_id"), ("team_b_id", "away_id"))]
    if any(not value.isdecimal() or int(value) <= 0 for value in ids) or int(ids[0]) == int(ids[1]):
        raise ScorelineInputError("Choose two different teams with valid provider IDs.")
    maximum = 15 if sport == "soccer" else 500
    def line(value):
        number = finite(value)
        if number is None or not 0 < number <= maximum:
            raise ScorelineInputError(f"Totals must be finite numbers above zero and at most {maximum}.")
        return number
    odds_total = line(params["odds_total"]) if params.get("odds_total") not in (None, "") else None
    raw = params.get("ou_lines")
    lines = None
    if raw not in (None, ""):
        raw = raw.split(",") if isinstance(raw, str) else raw
        if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= 20:
            raise ScorelineInputError("Supply between 1 and 20 total lines.")
        lines = sorted({line(value) for value in raw})
    before = params.get("before")
    if before:
        try:
            parsed = datetime.fromisoformat(str(before))
            if parsed.tzinfo is None or parsed <= datetime.now(UTC):
                raise ValueError
        except (ValueError, TypeError):
            raise ScorelineInputError("Scoring predictions require an upcoming kickoff with a timezone.") from None
    return {"team_a_id": ids[0], "team_b_id": ids[1], "odds_total": odds_total, "ou_lines": lines, "before": before}
