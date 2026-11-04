"""Context factors that adjust a player's or team's record for tonight's game.

Each one was kept only after a replay of every cached box score showed it makes predictions more accurate in each
of the last three seasons (2026-11-03; NBA ~97,000 player-stat predictions, soccer 5,481 matches):
  recency      last 5 games count 2x, games 6-10 1.5x, 11-15 1x
  teammate     a star teammate is out tonight: the player's own games without him, at 0.2 strength
  b2b          the team played yesterday: 3% off
  blowout      a 10+ point spread: starters' numbers trimmed (they sit the 4th quarter)
  soccer goals a team's goals judged by its scoring AND the opponent's goals conceded, blended with a Poisson
               model on shots on target (its own and what the opponent allows)
Pace (via combined scores) and stronger strengths for the matchup and teammate factors made predictions worse, so
they are not used. These are small edges; the player's own recent record still does most of the work.
"""
from __future__ import annotations

import math

TEAMMATE_STRENGTH = 0.2
BACK_TO_BACK = 0.97
BLOWOUT_SPREAD = 10
STAR_MINUTES, STAR_POINTS = 28, 17


def recency_weights(n: int) -> list[float]:
    """Weights for games ordered newest first."""
    return [2.0 if i < 5 else 1.5 if i < 10 else 1.0 for i in range(n)]


def weighted_hits(values: list[float], line: float, side: str, factor: float = 1.0) -> tuple[float, int]:
    """(hits, games) at the line, recent games weighted more; hits are rescaled so the total weight equals the games."""
    if not values:
        return 0.0, 0
    weights = recency_weights(len(values))
    hit = sum(w for v, w in zip(values, weights) if (v * factor > line if side == "over" else v * factor < line))
    return hit * len(values) / sum(weights), len(values)


def back_to_back(game_times: list[float], kickoff_ts: float) -> bool:
    earlier = [t for t in game_times if t < kickoff_ts - 3600]
    return bool(earlier) and kickoff_ts - max(earlier) < 1.3 * 86400


def stars(games: list[dict], team_id: str, name_of) -> set[str]:
    """Normalised names of the team's stars over its last 10 games (28+ minutes and 17+ points a game)."""
    minutes, points = {}, {}
    for game in games[-10:]:
        for p in game["teams"].get(str(team_id), {}).get("players", []):
            key = name_of(p["name"])
            minutes.setdefault(key, []).append(p.get("minutes") or 0)
            points.setdefault(key, []).append(p.get("points") or 0)
    return {k for k, m in minutes.items() if len(m) >= 6 and sum(m) / len(m) >= STAR_MINUTES and sum(points[k]) / len(points[k]) >= STAR_POINTS}


def teammate_factor(player: str, stat_of, games: list[dict], team_id: str, stars_out: set[str], name_of) -> tuple[float, str | None]:
    """Factor for a player when star teammates are out: his average in recent games without them vs with them."""
    stars_out = {s for s in stars_out if s != player}
    if not stars_out:
        return 1.0, None
    without, with_them, display = [], [], {}
    for game in games[-30:]:
        players = game["teams"].get(str(team_id), {}).get("players", [])
        display.update({name_of(p["name"]): p["name"] for p in players if name_of(p["name"]) in stars_out})
        row = next((p for p in players if name_of(p["name"]) == player), None)
        value = stat_of(row) if row else None
        if value is None:
            continue
        (with_them if any(name_of(p["name"]) in stars_out for p in players) else without).append(value)
    if len(without) < 3 or len(with_them) < 5 or not sum(with_them):
        return 1.0, None
    ratio = (sum(without) / len(without)) / (sum(with_them) / len(with_them))
    factor = 1 + TEAMMATE_STRENGTH * (ratio - 1)
    names = " and ".join(sorted(display.get(s, s.title()) for s in stars_out))
    return factor, (f"{names} {'are' if len(stars_out) > 1 else 'is'} out tonight; in {len(without)} recent games without "
                    f"{'them' if len(stars_out) > 1 else 'him'}, his average was {abs(ratio - 1):.0%} {'higher' if ratio > 1 else 'lower'}"
                    if abs(ratio - 1) >= .08 else None)


def blowout_factor(spread: float | None, starter: bool) -> float:
    if spread is None or not starter or abs(spread) < BLOWOUT_SPREAD:
        return 1.0
    return max(.85, 1 - .02 * (abs(spread) - 8))


def poisson_over(lam: float, line: float) -> float:
    k = math.floor(line)
    return 1 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k + 1))


def shots_goal_chance(sot_for: float | None, sot_allowed: float | None, conversion: float, line: float, side: str) -> float | None:
    """Chance a team goes over/under `line` goals from shots on target: its own and what the opponent allows."""
    if sot_for is None or sot_allowed is None or conversion <= 0:
        return None
    over = poisson_over(conversion * (sot_for + sot_allowed) / 2, line)
    return over if side == "over" else 1 - over
