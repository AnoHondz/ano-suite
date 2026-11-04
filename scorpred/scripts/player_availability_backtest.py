"""Does adjusting for who plays improve Premier League probabilities? A walk-forward backtest.

For every Premier League match from 2022-23 to 2025-26:

1. Base probabilities are the published ratings model's walk-forward predictions
   (scripts/soccer_model_research.py) and the margin-free pre-weekend and closing prices.
2. Each player's value comes from their season-to-date Fantasy Premier League totals before
   that match, through the production formula (services.soccer_player_value.player_value).
3. Availability is what happened: a player counts as playing if he started. The adjustment is
   the one the injury panel uses: attack x (played - usual share) added to his team's expected
   goals, defence x (played - usual share) taken from the opponent's.

``lineup`` uses every starter and every regular who did not start. ``absences`` uses only
players who did not play at all, which is closer to an injury list. Both know the lineup, so
they are the most this information can add; a pre-lineup injury list knows less.

The scale on the adjustment is chosen on 2022-23 and reported on 2023-24 to 2025-26.
Writes data/validation/player_availability_backtest.json.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import soccer_model_research as research  # noqa: E402
from runtime_paths import repo_root  # noqa: E402
from services import injury_impact  # noqa: E402
from services import soccer_player_value as value  # noqa: E402
from utils.atomic import atomic_write_json  # noqa: E402

FPL_DIR = repo_root() / "data" / "raw" / "fpl_history"
FPL_URL = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{season}/gws/merged_gw.csv"
REPORT_PATH = repo_root() / "data" / "validation" / "player_availability_backtest.json"
SEASONS = {"2223": "2022-23", "2324": "2023-24", "2425": "2024-25", "2526": "2025-26"}
TUNING_SEASON = "2223"
SCALES = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5)
POSITIONS = {"GK": value.GOALKEEPER, "DEF": value.DEFENDER, "MID": value.MIDFIELDER, "FWD": value.FORWARD}
# FPL short names -> football-data.co.uk names (the rest agree).
CLUBS = {"Man Utd": "Man United", "Spurs": "Tottenham", "Sheffield Utd": "Sheffield United"}
MIN_RATE = 0.05


def load_gameweeks(season: str) -> pd.DataFrame:
    path = FPL_DIR / f"{season}_merged_gw.csv"
    if not path.exists():
        FPL_DIR.mkdir(parents=True, exist_ok=True)
        response = requests.get(FPL_URL.format(season=season), timeout=120)
        response.raise_for_status()
        path.write_bytes(response.content)
    rows = pd.read_csv(path, encoding="latin-1")
    rows = rows[rows["position"].isin(POSITIONS)].copy()  # drops the assistant-manager chip ("AM")
    rows["club"] = rows["team"].map(lambda name: CLUBS.get(name, name))
    return rows.sort_values(["kickoff_time", "fixture"], kind="stable")


def fixture_deltas(rows: pd.DataFrame) -> dict[tuple[str, str], dict]:
    """Per (home, away): goal-rate changes from who played, valued from earlier matches only."""
    totals: dict[int, dict] = {}
    team_matches: dict[str, int] = defaultdict(int)
    deltas = {}
    for _fixture, match in rows.groupby("fixture", sort=False):
        home_club = match.loc[match["was_home"], "club"].iloc[0]
        away_club = match.loc[~match["was_home"], "club"].iloc[0]
        players = list(totals.values())
        replacement = value.replacement_levels(players)
        change = {"lineup": {"home": 0.0, "away": 0.0}, "absences": {"home": 0.0, "away": 0.0}}
        for side, club, other in (("home", home_club, "away"), ("away", away_club, "home")):
            matches = team_matches[club]
            started = set(match.loc[(match["club"] == club) & (match["starts"] > 0), "element"])
            appeared = set(match.loc[(match["club"] == club) & (match["minutes"] > 0), "element"])
            for player in players:
                if player["team"] != club or not player["minutes"] or not matches:
                    continue
                worth = value.player_value(player, players, replacement)
                share = value.played_share(player, matches)
                played = 1.0 if player["element"] in started else 0.0
                for scenario, shift in (("lineup", played - share),
                                        ("absences", -share if player["element"] not in appeared else 0.0)):
                    change[scenario][side] += worth["attack"] * shift
                    change[scenario][other] -= worth["defence"] * shift
        deltas[(home_club, away_club)] = change
        for row in match.itertuples():
            entry = totals.setdefault(row.element, {"element": row.element, "minutes": 0.0, "starts": 0.0,
                                                    "expected_goals": 0.0, "expected_assists": 0.0,
                                                    "expected_goals_conceded": 0.0, "goals_conceded": 0.0})
            entry["team"], entry["element_type"] = row.club, POSITIONS[row.position]
            for field in ("minutes", "starts", "expected_goals", "expected_assists", "expected_goals_conceded", "goals_conceded"):
                entry[field] += float(getattr(row, field) or 0)
        team_matches[home_club] += 1
        team_matches[away_club] += 1
    return deltas


def adjust(base: np.ndarray, delta: dict, scale: float) -> np.ndarray:
    """Move a [home, draw, away] forecast by the goal-rate change, keeping the forecast when there is none."""
    if scale == 0 or (delta["home"] == 0 and delta["away"] == 0):
        return base
    home_rate, away_rate = injury_impact.implied_rates(base[0], base[2])
    before = np.array(injury_impact.poisson_outcomes(home_rate, away_rate))
    after = np.array(injury_impact.poisson_outcomes(max(MIN_RATE, home_rate + scale * delta["home"]),
                                                    max(MIN_RATE, away_rate + scale * delta["away"])))
    moved = base * after / np.clip(before, 1e-9, None)
    return moved / moved.sum()


def run() -> dict:
    research_report = json.loads(research.REPORT_PATH.read_text())
    data = research.load_matches()
    frame, probabilities, _ = research.walk_forward(data, research_report["tuning"]["chosen"], with_rules=False)
    epl = ((frame["league"] == "Premier League") & frame["season"].isin(SEASONS)).to_numpy()
    frame = frame[epl]
    base = {name: probabilities[name][epl] for name in ("fitted", "market_open", "market_close")}
    deltas = {}
    for code, season in SEASONS.items():
        for (home, away), change in fixture_deltas(load_gameweeks(season)).items():
            deltas[(code, home, away)] = change
    keys = list(zip(frame["season"], frame["HomeTeam"], frame["AwayTeam"]))
    matched = np.array([key in deltas for key in keys])
    frame, keys = frame[matched], [k for k, m in zip(keys, matched) if m]
    base = {name: values[matched] for name, values in base.items()}
    target = frame["target"].to_numpy()
    seasons = frame["season"].to_numpy()
    blocks = frame["day"].to_numpy() // 7
    tuning = seasons == TUNING_SEASON
    test = ~tuning
    rng = np.random.default_rng(research.SEED)

    def adjusted(name, scenario, scale):
        return np.array([adjust(p, deltas[key][scenario], scale) for p, key in zip(base[name], keys)])

    def log_loss(p, mask):
        return research.scores(p[mask], target[mask])["log_loss"]

    tuning_grid, chosen = {}, {}
    for scenario in ("lineup", "absences"):
        tuning_grid[scenario] = {str(scale): log_loss(adjusted("fitted", scenario, scale), tuning) for scale in SCALES}
        chosen[scenario] = float(min(tuning_grid[scenario], key=tuning_grid[scenario].get))
        print("tuned", scenario, tuning_grid[scenario], flush=True)

    results = {"ratings_model": research.scores(base["fitted"][test], target[test]),
               "market_open": research.scores(base["market_open"][test], target[test]),
               "market_close": research.scores(base["market_close"][test], target[test])}
    gaps = {}
    for scenario, scale in chosen.items():
        for name, label in (("fitted", "ratings_model"), ("market_open", "market_open")):
            probs = adjusted(name, scenario, scale)
            results[f"{label}+{scenario}"] = research.scores(probs[test], target[test])
            gaps[f"{label}+{scenario} vs {label}"] = research.paired_gap(probs[test], base[name][test], target[test], blocks[test], rng)
            gaps[f"{label}+{scenario} vs market_close"] = research.paired_gap(probs[test], base["market_close"][test],
                                                                               target[test], blocks[test], rng)
    shifts = np.array([abs(deltas[k]["absences"]["home"] - deltas[k]["absences"]["away"]) for k in keys])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matches": {"total": int(len(keys)), "tuning": int(tuning.sum()), "evaluated": int(test.sum()),
                    "unmatched": int((~matched).sum())},
        "tuning": {"season": SEASONS[TUNING_SEASON], "log_loss_by_scale": tuning_grid, "chosen_scale": chosen},
        "results": results,
        "log_loss_gap": gaps,
        "absence_goal_shift": {"median": round(float(np.median(shifts)), 3), "p90": round(float(np.quantile(shifts, 0.9)), 3)},
        "method": __doc__.strip(),
    }


def main() -> None:
    report = run()
    atomic_write_json(REPORT_PATH, report)
    print(json.dumps({k: report[k] for k in ("matches", "tuning", "results", "log_loss_gap", "absence_goal_shift")}, indent=1))


if __name__ == "__main__":
    main()
