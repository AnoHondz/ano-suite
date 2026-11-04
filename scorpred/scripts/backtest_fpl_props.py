"""Walk-forward check of Premier League player props on this season's completed rounds.

Each round is projected from earlier rounds (plus last season's totals) only. Reports each
market's predicted against actual rate and Brier score against the player's own record,
then grades each fixture's five best picks. Players who did not play are void.

Usage: python scripts/backtest_fpl_props.py
"""
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import injury_reports, fpl_props as sp  # noqa: E402


def load():
    payload, _ = injury_reports.fpl()
    players = [p for p in payload["elements"] if sp._float(p.get("minutes")) > 0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        summaries = list(pool.map(lambda p: sp._summary(p["id"]), players))
    return payload, list(zip(players, summaries))


def team_season_before(rows_by_player, round_):
    teams = defaultdict(lambda: {"xg": 0.0, "xgc": 0.0, "fixtures": set()})
    for player, history in rows_by_player:
        for row in history:
            if row["round"] >= round_:
                continue
            team = teams[player["team"]]
            team["xg"] += sp._float(row.get("expected_goals"))
            team["fixtures"].add(row["fixture"])
            if player["element_type"] == sp.GOALKEEPER:
                team["xgc"] += sp._float(row.get("expected_goals_conceded"))
    return {team: {"xg": t["xg"], "xgc": t["xgc"], "matches": max(1, len(t["fixtures"]))} for team, t in teams.items()}


def outcome(market, row):
    field = {"goal": "goals_scored", "assist": "assists", "saves": "saves", "tackles": "tackles"}[market["market"]]
    value = sp._float(row.get(field))
    if market["side"] == "yes":
        return float(value >= 1)
    return float((value > market["line"]) == (market["side"] == "over"))


def backtest(first_round=3):
    payload, data = load()
    rows_by_player = [(player, sorted(summary.get("history") or [], key=lambda r: r["round"])) for player, summary in data]
    pasts = {player["id"]: (summary.get("history_past") or [None])[-1] for player, summary in data}
    last = max(row["round"] for _, history in rows_by_player for row in history)
    graded, picks = defaultdict(list), []
    for round_ in range(first_round, last + 1):
        season = team_season_before(rows_by_player, round_)
        fixtures = defaultdict(list)
        for player, history in rows_by_player:
            prior = [row for row in history if row["round"] < round_]
            for row in history:
                if row["round"] != round_ or player["team"] not in season or row["opponent_team"] not in season:
                    continue
                home, away = (player["team"], row["opponent_team"]) if row["was_home"] else (row["opponent_team"], player["team"])
                home_rate, away_rate = sp.team_rates(season, home, away)
                own, other = (home_rate, away_rate) if row["was_home"] else (away_rate, home_rate)
                team, opponent = season[player["team"]], season[row["opponent_team"]]
                if sp.expected_minutes(prior) < sp.STARTER_MINUTES:
                    continue
                markets = sp.player_markets(player["element_type"], prior, pasts[player["id"]], own,
                                            team["xg"] / team["matches"], other, opponent["xg"] / opponent["matches"])
                if sp._float(row.get("minutes")) <= 0:
                    continue  # void
                for market in markets:
                    graded[market["market"]].append((market["probability"], market["record"], outcome(market, row)))
                fixtures[row["fixture"]].append(({"name": player["web_name"], "team": player["team"], "position": "",
                                                  "markets": markets}, row))
        for entries in fixtures.values():
            rows = {entry["name"]: row for entry, row in entries}
            for pick in sp.best_picks([entry for entry, _ in entries]):
                picks.append((pick["market"], pick["probability"], pick["chance"], outcome(pick, rows[pick["name"]])))
    return graded, picks


def report(graded, picks):
    print(f'{"market":<10}{"n":>6}{"pred":>8}{"actual":>8}{"brier":>8}{"record":>8}')
    for key, rows in graded.items():
        n = len(rows)
        brier = sum((p - y) ** 2 for p, _, y in rows) / n
        record = sum((r - y) ** 2 for _, r, y in rows) / n
        print(f"{key:<10}{n:>6}{sum(p for p, _, _ in rows) / n:>8.3f}{sum(y for *_, y in rows) / n:>8.3f}{brier:>8.4f}{record:>8.4f}")
    n = len(picks)
    print(f"five best picks per fixture: {n}, raw {sum(p[1] for p in picks) / n:.3f}, shown {sum(p[2] for p in picks) / n:.3f}, "
          f"hit {sum(p[3] for p in picks) / n:.3f}")
    by_market = defaultdict(list)
    for pick in picks:
        by_market[pick[0]].append(pick)
    for key, rows in by_market.items():
        print(f"  {key}: n {len(rows)}, shown {sum(r[2] for r in rows) / len(rows):.3f}, hit {sum(r[3] for r in rows) / len(rows):.3f}")


if __name__ == "__main__":
    report(*backtest())
