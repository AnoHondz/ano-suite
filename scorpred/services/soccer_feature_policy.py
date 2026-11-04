"""Opponent-strength definitions shared by classifier training and inference.

An opponent snapshot describes that opponent *before the historical meeting*.
It is never the current opponent's form or the focal team's own points rate.
"""

import math
from datetime import UTC, datetime


def _number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def opponent_average_ppg(recent, window=5):
    """Mean of known pre-match opponent snapshots; incomplete means missing.

    Input order is most recent first. A partial average would change the
    training definition, so any missing snapshot leaves this feature missing.
    """
    rows = list(recent)[:window]
    values = [_number(row.get("opp_ppg")) for row in rows]
    if not values or any(value is None or not 0 <= value <= 3 for value in values):
        return None
    return round(sum(values) / len(values), 4)


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    # Historical classifier CSVs have dates only. Interpret these as day
    # boundaries and require strictly earlier days when no kickoff is known.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _identity(value):
    return str(value).strip().casefold() if value is not None else ""


def _team_keys(value):
    if isinstance(value, dict):
        return {_identity(value.get(key)) for key in ("id", "name")} - {""}
    return {_identity(value)} - {""}


def _normalize_match(row):
    """Accept API fixtures, scoring research rows, or historical classifier CSVs."""
    if not isinstance(row, dict):
        return None
    if isinstance(row.get("fixture"), dict):
        fixture, teams, goals = row["fixture"], row.get("teams") or {}, row.get("goals") or {}
        if (fixture.get("status") or {}).get("short") not in {"FT", "AET", "PEN"}:
            return None
        raw_time = fixture.get("date")
        kickoff = _time(raw_time)
        home, away = _team_keys(teams.get("home")), _team_keys(teams.get("away"))
        hs, aws = _number(goals.get("home")), _number(goals.get("away"))
    else:
        raw_time = row.get("kickoff") or row.get("date")
        kickoff = _time(raw_time)
        home, away = _team_keys(row.get("home_team")), _team_keys(row.get("away_team"))
        hs = _number(row.get("home_score", row.get("goals_scored")))
        aws = _number(row.get("away_score", row.get("goals_conceded")))
    if kickoff is None or not home or not away or hs is None or aws is None or min(hs, aws) < 0:
        return None
    return {"kickoff": kickoff, "date_only": len(str(raw_time)) == 10,
            "home": home, "away": away, "home_score": hs, "away_score": aws}


def enrich_opponent_snapshots(form, historical_matches, *, team=None):
    """Fill missing snapshots from supplied history without using later results.

    No source fetch occurs here. If the caller has only the two teams' recent
    form, most past opponents will have no independent history and stay missing.
    Classifier training spans seasons, so enrichment does too; the separate
    scoring model's same-season/24-hour policy is deliberately not substituted.
    """
    games = [game for row in historical_matches or [] if (game := _normalize_match(row)) is not None]
    games.sort(key=lambda game: game["kickoff"])
    own_keys = _team_keys(team)
    enriched = []
    for row in form or []:
        updated = dict(row)
        existing = _number(row.get("opp_ppg"))
        if existing is not None and 0 <= existing <= 3:
            enriched.append(updated)
            continue
        updated["opp_ppg"] = None
        kickoff = _time(row.get("kickoff") or row.get("date") or row.get("match_date"))
        opponent_keys = _team_keys(row.get("opponent_id")) | _team_keys(row.get("opponent") or row.get("opp"))
        if kickoff is None:
            enriched.append(updated)
            continue
        # A matched historical meeting can supply both the ID and name aliases.
        for game in games:
            if game["kickoff"].date() != kickoff.date():
                continue
            for side, other in (("home", "away"), ("away", "home")):
                if (own_keys & game[side] and (not opponent_keys or opponent_keys & game[other])):
                    opponent_keys |= game[other]
        prior = []
        seen = set()
        for game in games:
            if game["kickoff"] >= kickoff:
                break
            if game["date_only"] and game["kickoff"].date() >= kickoff.date():
                continue  # A date-only meeting must not look available at midnight.
            if not opponent_keys & (game["home"] | game["away"]):
                continue
            is_home = bool(opponent_keys & game["home"])
            key = (game["kickoff"], tuple(sorted(game["home"])), tuple(sorted(game["away"])))
            if key in seen:
                continue
            seen.add(key)
            gf, ga = (game["home_score"], game["away_score"]) if is_home else (game["away_score"], game["home_score"])
            prior.append(3 if gf > ga else 1 if gf == ga else 0)
        if prior:
            updated["opp_ppg"] = round(sum(prior[-5:]) / len(prior[-5:]), 4)
        enriched.append(updated)
    return enriched
