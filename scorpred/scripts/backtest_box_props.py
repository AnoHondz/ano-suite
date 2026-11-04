"""Walk-forward check of NBA and soccer player props (services.prop_model) on a full season.

Each game gets exactly what the live endpoint sees: both teams' box scores from this
season and last, before tip-off. Players who did not play are void. Reports each
market's chosen-side calibration and Brier score against the player's own record, then
grades each game's five picks per market and fits the pick shrink on the season's first
half, checking it on the second.

Usage: python scripts/backtest_box_props.py nba 2026
       python scripts/backtest_box_props.py soccer 2025 eng.1
       python scripts/backtest_box_props.py nba 2026 --early   (only the first three weeks, normally skipped)
"""
import sys
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SCORPRED_PROPS_TRAINING"] = "1"  # Published future calibration cannot enter a historical replay.

from services import espn_box_scores as box  # noqa: E402
from services import prop_model  # noqa: E402
from services.prediction_lifecycle import parse_kickoff  # noqa: E402

WARMUP_DAYS = 21


def teams(sport, league):
    url = box.BASE.format(path=f"{box.path(sport, league)}/teams")
    payload = requests.get(url, timeout=20).json()
    return [t["team"]["id"] for t in payload["sports"][0]["leagues"][0]["teams"]]


def season_games(sport, league, season):
    events = {}
    for team_id in teams(sport, league):
        for event in box.team_events(sport, team_id, season, league):
            events[event["id"]] = event
    with ThreadPoolExecutor(max_workers=8) as pool:
        games = list(pool.map(lambda e: box.box_score(sport, e["id"], league), events.values()))
    return [g for g in games if g]


def backtest(sport, season, league=None, early=False):
    config = __import__(f"services.{'nba' if sport == 'nba' else 'soccer'}_props", fromlist=["CONFIG"]).CONFIG
    current, previous = season_games(sport, league, season), season_games(sport, league, season - 1)
    history = sorted(current + previous, key=lambda g: g["date"])
    current.sort(key=lambda g: g["date"])
    start = parse_kickoff(current[0]["date"])
    markets, picks = defaultdict(list), []
    by_team = defaultdict(list)
    for game in history:
        for team_id in game["teams"]:
            by_team[team_id].append(game)
    for index, game in enumerate(current):
        kickoff = parse_kickoff(game["date"])
        if ((kickoff - start).days < WARMUP_DAYS) != early:
            continue
        home, away = (t for t, side in sorted(game["teams"].items(), key=lambda item: not item[1]["home"]))
        prior = {g["id"]: g for team in (home, away) for g in by_team[team] if parse_kickoff(g["date"]) < kickoff}
        result = prop_model.game_props(config, home, away, list(prior.values()))
        actual = {row["id"]: row for team in game["teams"].values() for row in team["players"]}
        for player in result["players"]:
            row = actual.get(player["id"])
            if row is None:
                continue
            for market in player["markets"]:
                observed = prop_model.value(row, config["markets"][market["market"]][0])
                hit = (observed > market["line"]) == (market["side"] == "over")
                markets[market["market"]].append((market["probability"], market["record"], float(hit)))
        for pick in result["picks"]:
            row = next((actual.get(p["id"]) for p in result["players"] if p["name"] == pick["name"] and p["team"] == pick["team"]), None)
            if row is None:
                continue
            observed = prop_model.value(row, config["markets"][pick["market"]][0])
            picks.append((index < len(current) / 2, pick["market"], pick["probability"],
                          float((observed > pick["line"]) == (pick["side"] == "over")), pick["side"]))
    return markets, picks


def report(markets, picks):
    print(f'{"market":<16}{"n":>7}{"model":>8}{"hit":>8}{"brier":>8}{"record":>8}')
    for key, rows in markets.items():
        n = len(rows)
        print(f"{key:<16}{n:>7}{sum(r[0] for r in rows) / n:>8.3f}{sum(r[2] for r in rows) / n:>8.3f}"
              f"{sum((r[0] - r[2]) ** 2 for r in rows) / n:>8.4f}{sum((r[1] - r[2]) ** 2 for r in rows) / n:>8.4f}")
    for label, half in (("first half", True), ("second half", False)):
        rows = [p for p in picks if p[0] == half]
        if not rows:
            continue
        raw, hit = sum(p[2] for p in rows) / len(rows), sum(p[3] for p in rows) / len(rows)
        print(f"picks {label}: n {len(rows)}, raw {raw:.3f}, hit {hit:.3f}, shrink that fits {(hit - 0.5) / (raw - 0.5):.2f}")
        by_market = defaultdict(list)
        for p in rows:
            by_market[p[1]].append(p)
        for key, subset in sorted(by_market.items()):
            print(f"   {key:<16} n {len(subset):>4}  raw {sum(p[2] for p in subset) / len(subset):.3f}  "
                  f"hit {sum(p[3] for p in subset) / len(subset):.3f}")
        for side in ("over", "under"):
            subset = [p for p in rows if p[4] == side]
            if subset:
                print(f"   side {side:<11} n {len(subset):>4}  raw {sum(p[2] for p in subset) / len(subset):.3f}  "
                      f"hit {sum(p[3] for p in subset) / len(subset):.3f}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--early"]
    sport, season = args[0], int(args[1])
    report(*backtest(sport, season, args[2] if len(args) > 2 else None, early="--early" in sys.argv))
