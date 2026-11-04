"""NBA pregame model: venue-neutral, shrunk point differential priced against the market.

Each team's rating is its recency-weighted, blowout-capped scoring margin with home
court removed, shrunk toward league average with ``PRIOR_GAMES`` pseudo-games. The
projected home margin is the rating difference plus home court, back-to-back fatigue
and a lineup adjustment; win probability treats the final margin as normal around it.
Regular-season and playoff games before tip-off from this season and the last are used,
last season's at a discount, so ratings exist from opening night.

The lineup adjustment compares who is expected to play tonight (ESPN roster injury
statuses) with who played in the rated games, valuing each player from their per-game
box line this season and last. Head-to-head meetings are reported but not weighted.

A recommendation requires a price: without a live moneyline or spread the output is
a fair line to compare against, never a bet.
"""
from __future__ import annotations

from datetime import datetime
import math
from statistics import NormalDist
from typing import Any

from services.prediction_lifecycle import CALENDAR_ZONE, parse_kickoff

VERSION = "nba-pregame-margin-v2"
# Walk-forward fit on 2022-23/2023-24, checked on 2024-25/2025-26 (2,535 games):
# log loss 0.603 vs 0.669 for the previous form-points engine.
# v2 adds last season at PREVIOUS_SEASON_WEIGHT (picked on 2023-24). On 2024-25/2025-26 (2,636 games,
# playoffs included) log loss 0.6051 vs 0.6055 current-season only; 0.632 vs 0.672 before game 3.
HOME_COURT = 2.0
GAME_SIGMA = 11.5
PRIOR_GAMES = 8.0
HALF_LIFE_GAMES = 60.0
# Points scored/allowed follow recent games faster than margin does. On the trained ridge scoring
# model's test window (2025-11-11..2026-06-14, 1,168 games) a 10-game half-life gives 9.66 points
# MAE per team and 15.31 on totals (ridge 9.85/15.49); 2024-25 agrees (9.40 vs 9.46 at 60 games).
SCORING_HALF_LIFE_GAMES = 10.0
SCORING_PRIOR_GAMES = 8.0
MARGIN_CAP = 25.0
BACK_TO_BACK = 1.5
LEAGUE_POINTS = 114.0
PREVIOUS_SEASONS = 1
PREVIOUS_SEASON_WEIGHT = 0.25
# Player value: margin points per Game Score above a replacement-level rate per minute. Team margin
# on summed player Game Score (60 team-seasons, 2024-25/2025-26) gives 0.66 per point (r 0.75) with
# the league average at 0.39 per minute; the slope is shrunk because good teams inflate box scores.
# Not backtested against historical injury reports, which this project does not have.
POINTS_PER_GMSC = 0.5
REPLACEMENT_GMSC_PER_MINUTE = 0.28
PLAYER_PREVIOUS_WEIGHT = 0.5
PLAYER_PRIOR_GAMES = 5.0
PLAYER_VALUE_CAP = 8.0
ROTATION_MINUTES = 10.0
AVAILABILITY_CAP = 12.0
STATUS_AVAILABILITY = {"out": 0.0, "suspen": 0.0, "doubtful": 0.25, "questionable": 0.5, "day-to-day": 0.6,
                       "probable": 0.9, "left team": 0.0}
NOTE_VALUE = 1.0
BOX_KEYS = ("min", "pts", "reb", "ast", "fg3m", "stl", "blk", "tov")
MIN_GAMES = 3
BET_CURRENT_GAMES = 8
# Before BET_CURRENT_GAMES, a gap this large rests on last season and an unvalidated lineup estimate.
EARLY_MAX_EDGE = 0.10
CONSIDER = {"games": 8, "edge": 0.02}
BET = {"games": 15, "edge": 0.04}
NO_MARKET_CONSIDER = {"games": 15, "probability": 0.65}
_NORMAL = NormalDist()


def _points(game: dict, side: str) -> int | None:
    value = ((game.get("scores") or {}).get(side) or {}).get("points")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _team_id(game: dict, side: str) -> str:
    return str(((game.get("teams") or {}).get(side) or {}).get("id") or "")


def _completed(game: dict) -> bool:
    status = game.get("status") or {}
    return status.get("state") == "post" or status.get("completed") is True


def season_of(moment: datetime) -> int:
    """ESPN season id (its ending year): October 2026 belongs to 2027."""
    day = moment.astimezone(CALENDAR_ZONE).date()
    return day.year + 1 if day.month >= 8 else day.year


def team_games(games: list[dict], team_id: str, before: datetime) -> list[dict]:
    """Completed non-preseason games for ``team_id`` from this season and the last one, before ``before``, newest first."""
    team_id = str(team_id)
    first_season = season_of(before) - PREVIOUS_SEASONS
    rows, seen = [], set()
    for game in games or []:
        if game.get("season_type") == 1 or not _completed(game):
            continue
        kickoff = parse_kickoff((game.get("date") or {}).get("start"))
        if kickoff is None or kickoff >= before or game.get("id") in seen or season_of(kickoff) < first_season:
            continue
        home_id, away_id = _team_id(game, "home"), _team_id(game, "visitors")
        if team_id not in {home_id, away_id}:
            continue
        home_pts, away_pts = _points(game, "home"), _points(game, "visitors")
        if home_pts is None or away_pts is None or home_pts == away_pts:
            continue
        seen.add(game.get("id"))
        is_home = team_id == home_id
        rows.append({"kickoff": kickoff, "season": season_of(kickoff), "home": is_home, "neutral": game.get("neutral_site") is True,
                     "scored": home_pts if is_home else away_pts, "allowed": away_pts if is_home else home_pts})
    rows.sort(key=lambda row: row["kickoff"], reverse=True)
    return rows


def team_rating(rows: list[dict], season: int | None = None) -> dict[str, Any]:
    """Shrunk venue-neutral margin; rows from before ``season`` count ``PREVIOUS_SEASON_WEIGHT`` as much."""
    weight = margin = 0.0
    season_weight = {"current": 0.0, "previous": 0.0}
    current = [row for row in rows if season is None or row.get("season", season) >= season]
    for index, row in enumerate(rows):
        previous = season is not None and row.get("season", season) < season
        w = 0.5 ** (index / HALF_LIFE_GAMES) * (PREVIOUS_SEASON_WEIGHT if previous else 1.0)
        venue = 0.0 if row["neutral"] else (HOME_COURT if row["home"] else -HOME_COURT)
        margin += w * (max(-MARGIN_CAP, min(MARGIN_CAP, row["scored"] - row["allowed"])) - venue)
        weight += w
        season_weight["previous" if previous else "current"] += w
    shrink = weight + PRIOR_GAMES
    return {
        "games": len(current),
        "previous_games": len(rows) - len(current),
        "sample": weight,
        "season_weight": season_weight,
        "shrink": weight / shrink,
        "rating": margin / shrink,
        "uncertainty": GAME_SIGMA / math.sqrt(shrink),
        "raw_margin": (sum(row["scored"] - row["allowed"] for row in current) / len(current)) if current else None,
        "record": f'{sum(row["scored"] > row["allowed"] for row in current)}-{sum(row["scored"] < row["allowed"] for row in current)}',
        "last_game": rows[0]["kickoff"] if rows else None,
    }


def _back_to_back(last_game: datetime | None, tipoff: datetime) -> bool:
    if last_game is None:
        return False
    return (tipoff.astimezone(CALENDAR_ZONE).date() - last_game.astimezone(CALENDAR_ZONE).date()).days == 1


def scoring_profile(rows: list[dict], season: int | None = None) -> dict[str, float]:
    """Recency-weighted, venue-neutral points scored and allowed (weights sum in ``weight``)."""
    weight = scored = allowed = 0.0
    for index, row in enumerate(rows):
        previous = season is not None and row.get("season", season) < season
        w = 0.5 ** (index / SCORING_HALF_LIFE_GAMES) * (PREVIOUS_SEASON_WEIGHT if previous else 1.0)
        venue = 0.0 if row["neutral"] else (HOME_COURT if row["home"] else -HOME_COURT)
        scored += w * (row["scored"] - venue / 2)
        allowed += w * (row["allowed"] + venue / 2)
        weight += w
    return {"weight": weight, "scored": scored, "allowed": allowed}


def _league_points(*profiles: dict) -> float:
    """Scoring level of the two teams' recent games: every one is a sample of the league's environment."""
    weight = sum(profile["weight"] for profile in profiles)
    points = sum(profile["scored"] + profile["allowed"] for profile in profiles)
    return points / (2 * weight) if weight >= 10 else LEAGUE_POINTS


def _attack(profile: dict, league: float) -> tuple[float, float]:
    shrink = profile["weight"] + SCORING_PRIOR_GAMES
    return (profile["scored"] - profile["weight"] * league) / shrink, (profile["allowed"] - profile["weight"] * league) / shrink


def _ratings(home_games: list[dict], away_games: list[dict], home_id: str, away_id: str,
             tip: datetime, neutral: bool) -> dict[str, Any]:
    season = season_of(tip)
    home_rows, away_rows = team_games(home_games, home_id, tip), team_games(away_games, away_id, tip)
    home, away = team_rating(home_rows, season), team_rating(away_rows, season)
    profiles = scoring_profile(home_rows, season), scoring_profile(away_rows, season)
    league = _league_points(*profiles)
    (home["offense"], home["defense"]), (away["offense"], away["defense"]) = (_attack(profile, league) for profile in profiles)
    home["back_to_back"], away["back_to_back"] = _back_to_back(home["last_game"], tip), _back_to_back(away["last_game"], tip)
    rest = BACK_TO_BACK * (away["back_to_back"] - home["back_to_back"])
    court = 0.0 if neutral else HOME_COURT
    return {"season": season, "league": league, "home": home, "away": away, "home_court": court, "rest_adjustment": rest,
            "margin": home["rating"] - away["rating"] + court + rest}


def _meetings(games: list[dict], home_id: str, away_id: str, tip: datetime) -> list[tuple[dict, datetime]]:
    first_season, seen, rows = season_of(tip) - PREVIOUS_SEASONS, set(), []
    for game in games or []:
        kickoff = parse_kickoff((game.get("date") or {}).get("start"))
        if (game.get("season_type") == 1 or not _completed(game) or kickoff is None or kickoff >= tip
                or season_of(kickoff) < first_season or game.get("id") in seen
                or {_team_id(game, "home"), _team_id(game, "visitors")} != {home_id, away_id}):
            continue
        home_pts, away_pts = _points(game, "home"), _points(game, "visitors")
        if home_pts is None or away_pts is None or home_pts == away_pts:
            continue
        seen.add(game.get("id"))
        rows.append((game, kickoff))
    rows.sort(key=lambda row: row[1], reverse=True)
    return rows


def head_to_head(home_games: list[dict], home_id: str, away_id: str, tip: datetime) -> dict[str, Any]:
    """Meetings this season and last, from the home team's side. Context only: shrunk residuals over the
    ratings made 2023-24 walk-forward log loss worse (0.6122 -> 0.6127 at 32 pseudo-games), so they add nothing."""
    margins = [(_points(game, "home") - _points(game, "visitors")) * (1 if _team_id(game, "home") == str(home_id) else -1)
               for game, _ in _meetings(home_games, str(home_id), str(away_id), tip)]
    wins = sum(margin > 0 for margin in margins)
    return {"meetings": len(margins), "record": f"{wins}-{len(margins) - wins}",
            "average_margin": sum(margins) / len(margins) if margins else None}


def _number_or_zero(value: Any) -> float:
    number = _number(value)
    return number if number is not None else 0.0


def game_score(line: dict) -> float:
    """Hollinger Game Score from per-game averages; rebounds use the league-average offensive share."""
    get = lambda key: _number_or_zero(line.get(key))
    return (get("pts") + 0.4 * get("fgm") - 0.7 * get("fga") - 0.4 * (get("fta") - get("ftm")) + 0.4 * get("reb")
            + get("stl") + 0.7 * get("ast") + 0.7 * get("blk") - 0.4 * get("pf") - get("tov"))


def status_availability(status: Any) -> float:
    """Chance a player with this ESPN injury status plays; no status means available."""
    text = str(status or "").strip().lower()
    return next((value for key, value in STATUS_AVAILABILITY.items() if key in text), 1.0) if text else 1.0


def player_pool(team_id: str, roster: list[dict], lines_by_season: dict[int, list[dict]]) -> list[dict]:
    """Roster players plus anyone who played for ``team_id`` in the given seasons and has since left.

    ``roster`` rows carry ``id``, ``name`` and an ESPN injury ``status``; season lines carry per-game
    averages and ``team_ids``, every team the player appeared for that season.
    """
    team_id = str(team_id)
    pool: dict[str, dict] = {}
    for player in roster or []:
        player_id = str(player.get("id") or "")
        if player_id:
            pool[player_id] = {"id": player_id, "name": player.get("name") or "", "on_roster": True,
                               "position": player.get("position") or "",
                               "status": player.get("status") or "", "availability": status_availability(player.get("status")),
                               "seasons": {}}
    for season, lines in (lines_by_season or {}).items():
        for line in lines or []:
            player_id = str(line.get("id") or "")
            teams = [str(team) for team in line.get("team_ids") or []]
            if not player_id or (player_id not in pool and team_id not in teams):
                continue
            entry = pool.setdefault(player_id, {"id": player_id, "name": line.get("name") or "", "on_roster": False,
                                                "position": line.get("position") or "",
                                                "status": "Left team", "availability": 0.0, "seasons": {}})
            entry["name"] = entry["name"] or line.get("name") or ""
            # ESPN combines a traded player's season, so split their games evenly across their teams.
            entry["seasons"][int(season)] = {**line, "team_share": 1.0 / len(teams) if team_id in teams else 0.0}
    return list(pool.values())


def player_value(player: dict, season: int) -> dict[str, Any]:
    """Blend this season and last into per-game minutes, box line and points of margin above replacement."""
    weight, totals = 0.0, {}
    for year, line in (player.get("seasons") or {}).items():
        if year < season - PREVIOUS_SEASONS:
            continue
        w = _number_or_zero(line.get("gp")) * (1.0 if year >= season else PLAYER_PREVIOUS_WEIGHT)
        if w <= 0:
            continue
        weight += w
        for key in BOX_KEYS:
            totals[key] = totals.get(key, 0.0) + w * _number_or_zero(line.get(key))
        totals["gmsc"] = totals.get("gmsc", 0.0) + w * game_score(line)
    if weight <= 0:
        return {"minutes": 0.0, "value": 0.0, "sample": 0.0, **{key: 0.0 for key in BOX_KEYS if key != "min"}}
    line = {key: total / weight for key, total in totals.items()}
    raw = POINTS_PER_GMSC * (line["gmsc"] - REPLACEMENT_GMSC_PER_MINUTE * line["min"])
    value = max(-1.0, min(PLAYER_VALUE_CAP, raw * weight / (weight + PLAYER_PRIOR_GAMES)))
    return {"minutes": line["min"], "value": value, "sample": weight, **{key: line[key] for key in BOX_KEYS if key != "min"}}


def availability(pool: list[dict] | None, rating: dict, season: int) -> dict[str, Any] | None:
    """Points of margin from who plays tonight versus who played in the games behind ``rating``.

    Each rotation player moves the team by their value times (chance they play now - share of the rated
    games they played): an injured regular costs most of their value, a newcomer or a returning starter
    adds theirs, and a departed player's part of the old results is taken back out.
    """
    if pool is None:
        return None
    weights = rating.get("season_weight") or {}
    total_weight = sum(weights.values())
    counts = {season: ("current", rating.get("games") or 0), season - 1: ("previous", rating.get("previous_games") or 0)}
    adjustment, players = 0.0, []
    for player in pool:
        value = player_value(player, season)
        if value["minutes"] < ROTATION_MINUTES:
            continue
        played = 0.0
        for year, (key, count) in counts.items():
            line = (player.get("seasons") or {}).get(year)
            if line and count and total_weight:
                share = min(1.0, _number_or_zero(line.get("gp")) * line.get("team_share", 0.0) / count)
                played += weights.get(key, 0.0) / total_weight * share
        delta = value["value"] * (player["availability"] - played)
        adjustment += delta
        players.append({"id": player["id"], "name": player["name"], "on_roster": player["on_roster"],
                        "position": player.get("position") or "",
                        "status": player["status"], "availability": player["availability"], "played_share": played,
                        "delta": delta, **value})
    players.sort(key=lambda row: row["value"], reverse=True)
    return {"adjustment": max(-AVAILABILITY_CAP, min(AVAILABILITY_CAP, adjustment)), "players": players}


def project(home_games: list[dict], away_games: list[dict], home_id: str, away_id: str,
            tipoff: Any, *, neutral: bool = False, home_players: list[dict] | None = None,
            away_players: list[dict] | None = None) -> dict[str, Any]:
    """Project one game from each team's own schedule, last season included at a discount.

    ``home_players``/``away_players`` are ``player_pool`` outputs; without them no availability
    adjustment is made. Raises ValueError without a valid tip-off.
    """
    tip = parse_kickoff(tipoff)
    if tip is None:
        raise ValueError("A timezone-aware tip-off is required.")
    home_id, away_id = str(home_id), str(away_id)
    base = _ratings(home_games, away_games, home_id, away_id, tip, neutral)
    home, away, league, season = base["home"], base["away"], base["league"], base["season"]
    h2h = head_to_head(home_games, home_id, away_id, tip)
    lineups = {"home": availability(home_players, home, season), "away": availability(away_players, away, season)}
    roster = (lineups["home"] or {}).get("adjustment", 0.0) - (lineups["away"] or {}).get("adjustment", 0.0)
    margin = base["margin"] + roster
    total = 2 * league + home["offense"] + home["defense"] + away["offense"] + away["defense"]
    sigma = math.sqrt(GAME_SIGMA ** 2 + home["uncertainty"] ** 2 + away["uncertainty"] ** 2)
    home_probability = _NORMAL.cdf(margin / sigma)
    return {
        "version": VERSION, "season": season,
        "home": home, "away": away,
        "min_games": min(home["games"], away["games"]),
        "min_sample": min(home["sample"], away["sample"]),
        "home_court": base["home_court"], "rest_adjustment": base["rest_adjustment"],
        "head_to_head": h2h, "availability": lineups, "availability_adjustment": roster,
        "margin": margin, "total": total, "sigma": sigma,
        "home_score": (total + margin) / 2, "away_score": (total - margin) / 2,
        "home_probability": home_probability,
    }


def american_to_decimal(value: Any) -> float | None:
    try:
        number = float(str(value).replace("+", "").strip())
    except (TypeError, ValueError):
        return None
    if number >= 100:
        return 1 + number / 100
    if number <= -100:
        return 1 + 100 / -number
    return None


def fair_american(probability: float) -> str:
    probability = max(0.01, min(0.99, probability))
    if probability >= 0.5:
        return f"-{round(100 * probability / (1 - probability))}"
    return f"+{round(100 * (1 - probability) / probability)}"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def market_from_odds(odds: dict | None) -> dict[str, Any] | None:
    """Normalize an ESPN odds block to a no-vig home probability; None when no usable price exists."""
    if not isinstance(odds, dict) or not odds:
        return None
    moneyline = odds.get("moneyline") or {}
    home_side, away_side = odds.get("homeTeamOdds") or {}, odds.get("awayTeamOdds") or {}

    def price(side: str, legacy: dict) -> float | None:
        line = ((moneyline.get(side) or {}).get("close") or (moneyline.get(side) or {}).get("open") or {}).get("odds")
        return american_to_decimal(line if line is not None else legacy.get("moneyLine"))

    home_decimal, away_decimal = price("home", home_side), price("away", away_side)
    spread_block = (((odds.get("pointSpread") or {}).get("home") or {}).get("close") or {})
    home_spread = _number(spread_block.get("line"))
    if home_spread is None and (raw := _number(odds.get("spread"))) is not None:
        if home_side.get("favorite") is True:
            home_spread = -abs(raw)
        elif away_side.get("favorite") is True:
            home_spread = abs(raw)
        else:
            home_spread = raw
    if home_decimal and away_decimal:
        home_raw, away_raw = 1 / home_decimal, 1 / away_decimal
        home_probability, source = home_raw / (home_raw + away_raw), "moneyline"
    elif home_spread is not None:
        home_probability, source = _NORMAL.cdf(-home_spread / GAME_SIGMA), "spread"
    else:
        return None
    return {
        "bookmaker": ((odds.get("provider") or {}).get("displayName") or (odds.get("provider") or {}).get("name") or "Sportsbook"),
        "home_probability": home_probability, "source": source,
        "home_decimal": home_decimal, "away_decimal": away_decimal,
        "home_spread": home_spread, "total": _number(odds.get("overUnder")),
    }


def _spread_text(name: str, margin: float) -> str:
    line = round(abs(margin) * 2) / 2
    return f"{name} {'-' if margin > 0 else '+'}{line:g}" if line else f"{name} pick'em"


def _settling(projection: dict) -> str:
    home, away = projection["home"], projection["away"]
    return (f"Ratings still settling ({min(home['games'], away['games'])} games this season, "
            f"{min(home['previous_games'], away['previous_games'])} last season).")


def _line(player: dict) -> str:
    return f"{player['name']} {player['pts']:.1f}/{player['reb']:.1f}/{player['ast']:.1f}"


def context_notes(projection: dict, home_name: str, away_name: str) -> list[str]:
    """Plain-language roster, lineup and matchup context behind the projection."""
    notes = []
    stars = []
    for side, name in (("home", home_name), ("away", away_name)):
        lineup = (projection.get("availability") or {}).get(side)
        if not lineup:
            continue
        players = lineup["players"]
        missing = [f"{row['name']} ({row['status']}, {row['pts']:.1f} ppg)" for row in players
                   if row["on_roster"] and row["availability"] < 1 and row["value"] >= NOTE_VALUE]
        arrived = [f"{row['name']} ({row['pts']:.1f} ppg)" for row in players
                   if row["on_roster"] and row["availability"] >= 0.5 and row["played_share"] < 0.3 and row["value"] >= NOTE_VALUE]
        departed = [row["name"] for row in players if not row["on_roster"] and row["played_share"] >= 0.3 and row["value"] >= NOTE_VALUE]
        if missing:
            notes.append(f"{name} without {', '.join(missing[:3])}")
        if arrived:
            notes.append(f"{name} adds {', '.join(arrived[:3])} not yet in their results")
        if departed:
            notes.append(f"{name} lost {', '.join(departed[:3])}")
        if abs(lineup["adjustment"]) >= 0.5:
            notes.append(f"{name} lineup {lineup['adjustment']:+.1f} pts vs their rated games")
        top = next((row for row in players if row["on_roster"] and row["availability"] >= 0.5), None)
        if top:
            stars.append(_line(top))
    if len(stars) == 2:
        notes.append(f"Key matchup {stars[0]} vs {stars[1]} (pts/reb/ast)")
    h2h = projection.get("head_to_head") or {}
    if h2h.get("meetings"):
        notes.append(f"{home_name} {h2h['record']} vs {away_name} this season and last (avg {h2h['average_margin']:+.1f})")
    return notes


def decide(projection: dict, market: dict | None, home_name: str, away_name: str) -> dict[str, Any]:
    """Pick a side and an action. Without a price nothing is a bet; with one, only the edge matters."""
    p_home = projection["home_probability"]
    games = projection.get("min_sample", projection["min_games"])
    current = projection["min_games"]
    favorite_home = p_home >= 0.5
    side_home = favorite_home
    edge = None
    action = "SKIP"
    if market:
        home_edge = p_home - market["home_probability"]
        side_home = home_edge >= 0
        edge = abs(home_edge)
        implausible = current < BET_CURRENT_GAMES and edge > EARLY_MAX_EDGE
        if not implausible and games >= BET["games"] and current >= BET_CURRENT_GAMES and edge >= BET["edge"]:
            action = "BET"
        elif not implausible and games >= CONSIDER["games"] and edge >= CONSIDER["edge"]:
            action = "CONSIDER"
        else:
            side_home = favorite_home
            edge = home_edge if favorite_home else -home_edge
    elif games >= NO_MARKET_CONSIDER["games"] and max(p_home, 1 - p_home) >= NO_MARKET_CONSIDER["probability"]:
        action = "CONSIDER"
    side_probability = p_home if side_home else 1 - p_home
    favored, margin = (home_name, projection["margin"]) if projection["margin"] >= 0 else (away_name, -projection["margin"])
    fair = f"Fair line {_spread_text(favored, margin)}, {home_name if side_home else away_name} {fair_american(side_probability)}."
    projected = f"Projected {home_name} {projection['home_score']:.0f}–{projection['away_score']:.0f} {away_name}."
    if market:
        market_side = market["home_probability"] if side_home else 1 - market["home_probability"]
        signed_edge = side_probability - market_side
        price = f"{market['bookmaker']} implies {market_side:.0%} vs model {side_probability:.0%} ({signed_edge:+.1%})."
        if action == "SKIP" and implausible:
            verdict = "Too far from the market this early; the price likely knows something the ratings do not."
        elif action == "SKIP":
            verdict = "No edge at the current price." if games >= CONSIDER["games"] else _settling(projection)
        else:
            verdict = f"Value on {home_name if side_home else away_name}."
        reason = f"{projected} {fair} {price} {verdict}"
    else:
        verdict = "No sportsbook price available; bet only above the fair price." if action == "CONSIDER" else (
            "No sportsbook price available." if games >= NO_MARKET_CONSIDER["games"] else _settling(projection))
        reason = f"{projected} {fair} {verdict}"
    notes = [f"{name} on a back-to-back" for name, team in ((home_name, projection["home"]), (away_name, projection["away"]))
             if team["back_to_back"]]
    notes += context_notes(projection, home_name, away_name)
    if notes:
        reason += f" {'; '.join(notes)}."
    return {"side_home": side_home, "side_probability": side_probability, "action": action,
            "edge": edge, "reason": reason, "fair_line": fair}
