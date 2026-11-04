"""Detailed NBA game projection built on ``nba_pregame_model``: scores, quarters and player box lines.

The score distribution treats the final margin and total as independent normals around the
pregame projection, which makes the two team scores positively correlated the way real games
are (shared pace). Margin uses the model's own win-probability sigma, so the scoreline agrees
with the decision card; the total and per-team spreads are the residuals measured walk-forward:

    2024-25 and 2025-26 (Nov-Jun, ~1,170 games each): per-team residual SD 11.8/12.1,
    total SD 18.6/19.2, home/away residual correlation 0.25. The 80% ranges below covered
    79.9%/78.9% of team scores and 80.5%/80.1% of totals in those seasons.

Player box lines rescale each available player's blended per-game line (this season and last)
to tonight's minutes and the team's projected score. They are descriptive: player-level
projections have not been backtested because the project has no historical box scores.
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

from services import nba_pregame_model as pregame

VERSION = "nba-game-projection-v1"
TEAM_SIGMA = 12.0
TOTAL_SIGMA = 19.0
# Q1-Q4 share of regulation points and overtime rate, 2,146 games in 2024-25/2025-26.
QUARTER_SHARES = (0.2535, 0.2507, 0.2546, 0.2412)
OVERTIME_RATE = 0.047
OVERTIME_POINTS = 22.0
TEAM_MINUTES = 240.0
MAX_MINUTES = 40.0
MAX_MINUTE_BUMP = 8.0
INTERVAL_Z = 1.2816  # 80% two-sided
POSITION_ORDER = {"PG": 1.0, "G": 1.5, "SG": 2.0, "GF": 2.5, "SF": 3.0, "F": 3.5, "PF": 4.0, "FC": 4.5, "C": 5.0}
_NORMAL = NormalDist()


def _phi(x: float) -> float:
    return math.exp(-0.5 * x * x)


def overtime_probability(margin: float, sigma: float) -> float:
    """League OT rate scaled by how close the projection is; 0.93 is that factor's league average."""
    return min(0.2, OVERTIME_RATE / 0.93 * _phi(margin / sigma))


def score_grid(margin: float, total: float, sigma_margin: float, top: int = 15) -> list[dict[str, Any]]:
    """Most likely exact final scores; ties cannot end an NBA game, so they are dropped and renormalized."""
    home_mean, away_mean = (total + margin) / 2, (total - margin) / 2
    span = int(3.5 * TEAM_SIGMA)
    cells, mass = [], 0.0
    for home in range(max(60, round(home_mean) - span), round(home_mean) + span + 1):
        for away in range(max(60, round(away_mean) - span), round(away_mean) + span + 1):
            if home == away:
                continue
            weight = _phi((home - away - margin) / sigma_margin) * _phi((home + away - total) / TOTAL_SIGMA)
            mass += weight
            cells.append((weight, home, away))
    cells.sort(reverse=True)
    return [{"home": home, "away": away, "score": f"{home}-{away}", "prob": round(100 * weight / mass, 2),
             "result": "H" if home > away else "A"} for weight, home, away in cells[:top]]


def total_lines(total: float, market_total: float | None) -> list[float]:
    centre = market_total if market_total else math.floor(total) + 0.5
    return sorted({round(centre + step, 1) for step in (-10, -5, -2.5, 0, 2.5, 5, 10)})


def over_under(total: float, lines: list[float]) -> list[dict[str, Any]]:
    rows = []
    for line in lines:
        integer = float(line).is_integer()
        under = _NORMAL.cdf(((line - 0.5 if integer else line) - total) / TOTAL_SIGMA)
        upper = _NORMAL.cdf(((line + 0.5) - total) / TOTAL_SIGMA) if integer else under
        rows.append({"line": line, "over_prob": round(100 * (1 - upper), 1), "under_prob": round(100 * under, 1),
                     "push_prob": round(100 * (upper - under), 1), "lean": "OVER" if 1 - upper > under else "UNDER"})
    return rows


def quarters(home_score: float, away_score: float, overtime: float) -> list[dict[str, Any]]:
    """Expected points by quarter; overtime's expected points come off regulation first."""
    total = home_score + away_score
    regulation = total - overtime * OVERTIME_POINTS
    home_share = home_score / total if total else 0.5
    rows = [{"period": f"Q{index + 1}", "home": round(regulation * share * home_share, 1),
             "away": round(regulation * share * (1 - home_share), 1)} for index, share in enumerate(QUARTER_SHARES)]
    rows.append({"period": "1st half", "home": round(rows[0]["home"] + rows[1]["home"], 1),
                 "away": round(rows[0]["away"] + rows[1]["away"], 1)})
    return rows


def _minutes(players: list[dict]) -> float:
    """Fill 240 minutes like a depth chart: a crowded rotation loses minutes from the end of the bench,
    a short-handed one spreads the gap across everyone, capped near each player's usual role."""
    for player in players:
        player["proj_min"] = player["minutes"] * player["availability"]
    excess = sum(p["proj_min"] for p in players) - TEAM_MINUTES
    for player in sorted(players, key=lambda p: p["proj_min"]):
        if excess <= 0:
            break
        cut = min(player["proj_min"], excess)
        player["proj_min"] -= cut
        excess -= cut
    for _ in range(6):
        open_players = [p for p in players if p["proj_min"] < min(MAX_MINUTES, p["minutes"] + MAX_MINUTE_BUMP) - 1e-9]
        gap = TEAM_MINUTES - sum(p["proj_min"] for p in players)
        base = sum(p["proj_min"] for p in open_players)
        if abs(gap) < 0.5 or base <= 0:
            break
        factor = 1 + gap / base
        for player in open_players:
            player["proj_min"] = max(0.0, min(player["proj_min"] * factor, MAX_MINUTES, player["minutes"] + MAX_MINUTE_BUMP))
    return max(0.0, TEAM_MINUTES - sum(p["proj_min"] for p in players))


def box_lines(lineup: dict | None, team_score: float) -> dict[str, Any] | None:
    """Tonight's projected box line for each available roster player, reconciled to ``team_score``."""
    if not lineup:
        return None
    players = [dict(row) for row in lineup["players"] if row["on_roster"] and row["minutes"] > 0]
    playing = [row for row in players if row["availability"] >= 0.5]
    unused = _minutes(playing)
    raw_points = sum(row["pts"] / row["minutes"] * row["proj_min"] for row in playing)
    # Opponent defense, pace and shared shots (several players who were the first option elsewhere)
    # show up as the gap between player rates and the team projection; scaling closes it.
    factor = max(0.6, min(1.4, team_score * (1 - unused / TEAM_MINUTES) / raw_points)) if raw_points else 1.0
    lines = []
    for row in playing:
        if row["proj_min"] < 1:
            continue
        share = row["proj_min"] / row["minutes"]
        scoring = share * factor
        lines.append({"id": row["id"], "name": row["name"], "position": row.get("position") or "",
                      "status": row["status"], "availability": row["availability"], "value": row["value"],
                      "minutes": round(row["proj_min"], 1), "pts": round(row["pts"] * scoring, 1),
                      "reb": round(row["reb"] * share, 1), "ast": round(row["ast"] * scoring, 1),
                      "fg3m": round(row.get("fg3m", 0.0) * scoring, 1), "stl": round(row.get("stl", 0.0) * share, 1),
                      "blk": round(row.get("blk", 0.0) * share, 1)})
    lines.sort(key=lambda row: row["minutes"], reverse=True)
    out = [{"id": row["id"], "name": row["name"], "status": row["status"], "pts": round(row["pts"], 1)}
           for row in players if row["availability"] < 0.5 and row["value"] >= pregame.NOTE_VALUE]
    return {"players": lines, "unavailable": out, "scoring_factor": round(factor, 3),
            "unassigned_minutes": round(unused, 1)}


def _slot(position: str) -> float:
    return POSITION_ORDER.get(position.upper().replace("-", ""), 3.0)


def matchups(home: dict | None, away: dict | None) -> dict[str, Any] | None:
    """Starters (top five projected minutes) paired by position, PG side to C side, plus the benches."""
    if not home or not away or len(home["players"]) < 5 or len(away["players"]) < 5:
        return None
    sides = []
    for team in (home, away):
        starters = sorted(team["players"][:5], key=lambda row: (_slot(row["position"]), -row["minutes"]))
        bench = team["players"][5:]
        sides.append((starters, bench))
    pairs = []
    for home_player, away_player in zip(sides[0][0], sides[1][0]):
        gap = home_player["value"] - away_player["value"]
        pairs.append({"position": home_player["position"] or away_player["position"], "home": home_player, "away": away_player,
                      "edge": "home" if gap >= 0.5 else "away" if gap <= -0.5 else "even", "value_gap": round(gap, 2)})
    bench = {side: {"players": len(rows), "pts": round(sum(row["pts"] for row in rows), 1),
                    "minutes": round(sum(row["minutes"] for row in rows), 1), "value": round(sum(row["value"] for row in rows), 2)}
             for side, (_, rows) in zip(("home", "away"), sides)}
    return {"starters": pairs, "bench": bench}


def build(projection: dict, market: dict | None = None, home_name: str = "Home", away_name: str = "Away") -> dict[str, Any]:
    """Scoreline result for the NBA scoring widget, consistent with the pregame decision."""
    margin, total = projection["margin"], projection["total"]
    sigma = projection["sigma"]
    home_score, away_score = projection["home_score"], projection["away_score"]
    market_total = (market or {}).get("total")
    ou = over_under(total, total_lines(total, market_total))
    primary = min(ou, key=lambda row: abs(row["line"] - (market_total or total)))
    grid = score_grid(margin, total, sigma)
    overtime = overtime_probability(margin, sigma)
    spread = (market or {}).get("home_spread")
    home_lines = box_lines((projection.get("availability") or {}).get("home"), home_score)
    away_lines = box_lines((projection.get("availability") or {}).get("away"), away_score)
    p_home = projection["home_probability"]
    factors = {"current_season": projection["min_games"] >= pregame.CONSIDER["games"],
               "previous_season": min(projection["home"]["previous_games"], projection["away"]["previous_games"]) >= 20,
               "rosters": home_lines is not None and away_lines is not None,
               "market": market_total is not None}
    coverage = sum(factors.values()) / len(factors)
    return {
        "data_completeness": {"score": coverage, "tier": "high" if coverage >= 0.75 else "medium" if coverage >= 0.5 else "low",
                              "factors": factors},
        "sport": "nba", "team_a": home_name, "team_b": away_name, "model": VERSION,
        "projected_score": {"home": round(home_score, 1), "away": round(away_score, 1), "total": round(total, 1)},
        "intervals": {side: {"low": math.floor(score - INTERVAL_Z * TEAM_SIGMA), "high": math.ceil(score + INTERVAL_Z * TEAM_SIGMA)}
                      for side, score in (("home", home_score), ("away", away_score))},
        "total_interval": {"low": math.floor(total - INTERVAL_Z * TOTAL_SIGMA), "high": math.ceil(total + INTERVAL_Z * TOTAL_SIGMA)},
        "proj_margin": round(margin, 1),
        "spread_favorite": home_name if margin > 0 else away_name if margin < 0 else "Even",
        "spread": None if spread is None else {"home_line": spread, "home_cover_prob": round(100 * _NORMAL.cdf((margin + spread) / sigma), 1)},
        "win_probabilities": {"home": round(100 * p_home, 1), "away": round(100 * (1 - p_home), 1)},
        "overtime_prob": round(100 * overtime, 1),
        "quarters": quarters(home_score, away_score, overtime),
        "most_likely_range": grid[0], "top_scorelines": grid,
        "over_under": ou, "primary_ou_line": primary, "sigma_total": TOTAL_SIGMA,
        "players": {"home": home_lines, "away": away_lines},
        "matchups": matchups(home_lines, away_lines),
        "inputs": {"current_games": [projection["home"]["games"], projection["away"]["games"]],
                   "previous_games": [projection["home"]["previous_games"], projection["away"]["previous_games"]],
                   "lineup_adjustment": round(projection.get("availability_adjustment", 0.0), 2)},
        "validation": {"window": "2025-11-11..2026-06-14", "games": 1168, "team_points_mae": 9.66, "total_mae": 15.31,
                       "ridge_team_points_mae": 9.85, "ridge_total_mae": 15.49},
        "notice": ("Scores, quarters and over/unders come from the backtested pregame ratings. Player box lines and "
                   "matchups are descriptive: they rescale each player's averages to tonight's minutes and have not "
                   "been backtested."),
    }
