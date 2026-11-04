"""NFL player value: points of margin per game above a replacement, from nflverse EPA.

Quarterbacks: (passing + rushing EPA) per play (attempts, sacks and carries) above the
EPA per play of backup quarterbacks, times plays per game. Running backs, receivers and
tight ends: (rushing + receiving EPA) per opportunity (carries and targets) above
non-starters at the position, times opportunities per game, times ``SKILL_CREDIT``
because a receiver shares the EPA of every catch with the passer. Linemen, defenders
and specialists have no per-player EPA, so they are not valued.
"""
from collections import defaultdict

POSITIONS = ("QB", "RB", "WR", "TE")
STARTERS = {"QB": 1, "RB": 1, "WR": 3, "TE": 1}  # per team-season, by plays
PREVIOUS_WEIGHT = 0.5
PRIOR_GAMES = 3.0
# scripts/backtest_nfl_injuries.py, 2022 + 2024 pooled (473 games, actual weekly roster
# status): margin on form plus this adjustment fits 0.61 ± 0.19 per raw point, so values
# are scaled to match. In-sample: one season alone is too noisy (0.26 ± 0.29 in 2022,
# 0.86 ± 0.26 in 2024). Against the market spread it adds nothing significant (0.28 ± 0.19).
SKILL_CREDIT = 0.5
SCALE = 0.6
VALUE_CAP = 12.0
MIN_PLAYS = {"QB": 10, "RB": 3, "WR": 2, "TE": 2}  # a game counts as played above this


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def plays(row):
    if row.get("position") == "QB":
        return _number(row.get("attempts")) + _number(row.get("sacks_suffered")) + _number(row.get("carries"))
    return _number(row.get("carries")) + _number(row.get("targets"))


def epa(row):
    if row.get("position") == "QB":
        return _number(row.get("passing_epa")) + _number(row.get("rushing_epa"))
    return _number(row.get("rushing_epa")) + _number(row.get("receiving_epa"))


def replacement_levels(rows):
    """EPA per play of each position's non-starters, starters ranked by plays per team-season."""
    totals = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for row in rows:
        if row.get("position") in POSITIONS and row.get("team"):
            entry = totals[(row["team"], row["position"])][row["player_id"]]
            entry[0] += epa(row)
            entry[1] += plays(row)
    backups = defaultdict(lambda: [0.0, 0.0])
    for (_team, position), players in totals.items():
        ranked = sorted(players.values(), key=lambda entry: -entry[1])
        for value, volume in ranked[STARTERS[position]:]:
            backups[position][0] += value
            backups[position][1] += volume
    return {position: (value / volume if volume else 0.0) for position, (value, volume) in backups.items()}


def player_value(rows, previous_rows, position, replacement):
    """Points of margin per game this player adds over a replacement, shrunk for small samples."""
    weight = total_epa = total_plays = 0.0
    for sample, w in ((rows, 1.0), (previous_rows, PREVIOUS_WEIGHT)):
        for row in sample:
            if plays(row) > 0:
                weight += w
                total_epa += w * epa(row)
                total_plays += w * plays(row)
    if not weight or not total_plays:
        return {"value": 0.0, "plays_per_game": 0.0, "epa_per_play": 0.0, "games": 0}
    per_play = total_epa / total_plays
    per_game = total_plays / weight
    raw = per_game * (per_play - replacement.get(position, 0.0)) * (1.0 if position == "QB" else SKILL_CREDIT)
    value = max(-2.0, min(VALUE_CAP, SCALE * raw * weight / (weight + PRIOR_GAMES)))
    return {"value": value, "plays_per_game": per_game, "epa_per_play": per_play, "games": len(rows)}


def played_share(rows, team_games, position):
    """Share of the team's recent games (the base form window) the player took part in."""
    if not team_games:
        return 0.0
    played = {row["game_id"] for row in rows if row.get("game_id") in team_games and plays(row) >= MIN_PLAYS[position]}
    return len(played) / len(team_games)
