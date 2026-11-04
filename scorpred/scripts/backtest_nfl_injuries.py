"""Check NFL injury adjustments against results on completed seasons.

For each game from week 4, each team's base margin is its average margin over its last
eight games. The injury adjustment is the sum over the team's valued players of
value x (played this week - share of those eight games played), with "played" taken
from that week's official weekly roster status. Fits, by season:
  margin ~ form difference + adjustment   (adjustment coefficient near 1 = right scale)
  margin ~ market spread + adjustment     (near 0 = the market already prices it)

Usage: python scripts/backtest_nfl_injuries.py 2022 2024
"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import nfl_player_value as pv  # noqa: E402
from services import nflverse_store as store  # noqa: E402

FORM_GAMES = 8
HOME_FIELD = 1.5


def season_rows(season):
    return [row for row in store.records("player_stats", season)
            if row.get("player_id") and row.get("season_type") in {"REG", "POST"} and row.get("position") in pv.POSITIONS]


def adjustment(team, week, current, previous_by_player, replacement, team_games, active):
    by_player = defaultdict(list)
    for row in sorted(current, key=lambda r: r["week"]):
        by_player[row["player_id"]].append(row)
    total, rows_out = 0.0, []
    for player_id, rows in by_player.items():
        if rows[-1].get("team") != team:
            continue
        position = rows[-1]["position"]
        value = pv.player_value([r for r in rows if r.get("team") == team], previous_by_player.get(player_id, []),
                                position, replacement)["value"]
        share = pv.played_share(rows, team_games, position)
        played = 1.0 if active.get((team, player_id)) else 0.0
        total += value * (played - share)
        rows_out.append((player_id, value, share, played))
    return total, rows_out


def backtest(season, value_scale=1.0):
    rows = season_rows(season)
    previous_by_player = defaultdict(list)
    for row in season_rows(season - 1):
        previous_by_player[row["player_id"]].append(row)
    games = sorted((g for g in store.records("schedules", season) if g.get("result") is not None
                    and g.get("game_type") in {"REG", "WC", "DIV", "CON", "SB"}), key=lambda g: g["week"])
    rosters = store.records("weekly_rosters", season)
    active = defaultdict(dict)
    for row in rosters:
        active[row["week"]][(row.get("team"), row.get("gsis_id"))] = row.get("status") == "ACT"
    previous_rows = [row for rows_ in previous_by_player.values() for row in rows_]
    samples = []
    for game in games:
        week = game["week"]
        if week < 4 or not active.get(week):
            continue
        current = [row for row in rows if row["week"] < week]
        replacement = pv.replacement_levels(current + previous_rows)
        features = []
        for team in (game["home_team"], game["away_team"]):
            prior = [g for g in games if g["week"] < week and team in (g["home_team"], g["away_team"])][-FORM_GAMES:]
            margins = [(g["result"] if g["home_team"] == team else -g["result"]) for g in prior]
            form = sum(margins) / len(margins) if margins else 0.0
            adj, _ = adjustment(team, week, current, previous_by_player, replacement, {g["game_id"] for g in prior},
                                active[week])
            features.append((form, adj * value_scale))
        (form_h, adj_h), (form_a, adj_a) = features
        samples.append((game["result"], form_h - form_a, adj_h - adj_a, game.get("spread_line")))
    return np.array([s for s in samples if s[3] is not None], dtype=float)


def fit(target, columns):
    design = np.column_stack([np.ones(len(target))] + columns)
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = target - design @ coefficients
    covariance = np.linalg.inv(design.T @ design) * residual.var(ddof=design.shape[1])
    return coefficients, np.sqrt(np.diag(covariance)), np.abs(residual).mean()


def report(season, data):
    margin, form, adj, spread = data.T
    print(f"\n== {season}: {len(margin)} games; adjustment sd {adj.std():.2f} points")
    (_, b_form), _, mae_form = fit(margin, [form])
    coefficients, errors, mae_both = fit(margin, [form, adj])
    print(f"form only: MAE {mae_form:.2f}  |  form + injuries: MAE {mae_both:.2f}; "
          f"injury coefficient {coefficients[2]:.2f} ± {errors[2]:.2f}")
    _, _, mae_market = fit(margin, [spread])
    coefficients, errors, mae_market_adj = fit(margin, [spread, adj])
    print(f"market only: MAE {mae_market:.2f}  |  market + injuries: MAE {mae_market_adj:.2f}; "
          f"injury coefficient {coefficients[2]:.2f} ± {errors[2]:.2f}")


if __name__ == "__main__":
    for year in [int(arg) for arg in sys.argv[1:]] or [2022, 2024]:
        report(year, backtest(year))
