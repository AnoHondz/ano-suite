"""Walk-forward check of NFL player prop projections on completed seasons.

Each week is projected from earlier weeks only, for the players the live
endpoint would select. Reports, per market: projection MAE against a plain
season-average baseline, mean bias (projected minus actual), calibration of the over price at the model line, and
coverage of the 20th-80th percentile range (ideal 60%).

Usage: python scripts/backtest_nfl_props.py 2024 2025
"""
import sys
import os
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SCORPRED_PROPS_TRAINING"] = "1"

from services import nfl_props  # noqa: E402


def over_price(market, line):
    ladder = market["ladder"]
    index = int(line - 0.5) - ladder["start"]
    if index < 0:
        return 1.0
    return ladder["over"][index] if index < len(ladder["over"]) else 0.0


def backtest(season, first_week=4):
    rows = nfl_props._rows(season)
    previous = nfl_props._rows(season - 1)
    previous_by_player = defaultdict(list)
    for row in sorted(previous, key=lambda r: r["week"]):
        previous_by_player[row["player_id"]].append(row)
    results = defaultdict(lambda: defaultdict(list))
    for week in range(first_week, max(row["week"] for row in rows) + 1):
        current = [row for row in rows if row["week"] < week]
        actual = {row["player_id"]: row for row in rows if row["week"] == week}
        if not actual:
            continue
        context = nfl_props.Context(current, current + previous)
        by_player = defaultdict(list)
        for row in sorted(current, key=lambda r: r["week"]):
            by_player[row["player_id"]].append(row)
        matchups = {(row["team"], row["opponent_team"]) for row in actual.values() if row.get("opponent_team")}
        for team, opponent in matchups:
            for player_id, latest in nfl_props._select(team, current):
                if player_id not in actual:
                    continue  # void prop: the player did not record a statistic
                for market in nfl_props.player_markets(latest["position"], by_player[player_id],
                                                       previous_by_player[player_id], opponent, context):
                    key = market["market"]
                    observed = nfl_props._value(actual[player_id], nfl_props.MARKETS[key][1])
                    if observed is None:
                        continue
                    bucket = results[key]
                    bucket["error"].append(abs(market["projection"] - observed))
                    bucket["bias"].append(market["projection"] - observed)
                    history = market["recent"] and [v for v in (nfl_props._value(r, nfl_props.MARKETS[key][1])
                                                                for r in by_player[player_id]) if v is not None]
                    bucket["baseline"].append(abs(sum(history) / len(history) - observed))
                    if key == "anytime_td":
                        bucket["predicted"].append(market["probability"])
                        bucket["hit"].append(float(observed >= 1))
                    else:
                        bucket["predicted"].append(over_price(market, market["model_line"]))
                        bucket["hit"].append(float(observed > market["model_line"]))
                        low, high = market["range"]
                        bucket["covered"].append(float(low <= observed <= high))
    return results


def pick_backtest(season, first_week=4):
    """Grade each game's five best picks (season-median lines) against what happened.

    Void props (the player recorded no statistic) are left out, as a book refunds them.
    """
    rows = nfl_props._rows(season)
    previous = nfl_props._rows(season - 1)
    previous_by_player = defaultdict(list)
    for row in sorted(previous, key=lambda r: r["week"]):
        previous_by_player[row["player_id"]].append(row)
    graded = []
    for week in range(first_week, max(row["week"] for row in rows) + 1):
        current = [row for row in rows if row["week"] < week]
        actual = {row["player_id"]: row for row in rows if row["week"] == week}
        context = nfl_props.Context(current, current + previous)
        by_player = defaultdict(list)
        for row in sorted(current, key=lambda r: r["week"]):
            by_player[row["player_id"]].append(row)
        games = defaultdict(set)
        for row in actual.values():
            if row.get("opponent_team"):
                games[row["game_id"]].add((row["team"], row["opponent_team"]))
        for sides in games.values():
            teams = []
            for team, opponent in sides:
                players = []
                for player_id, latest in nfl_props._select(team, current):
                    if player_id in actual:
                        markets = nfl_props.player_markets(latest["position"], by_player[player_id],
                                                           previous_by_player[player_id], opponent, context)
                        players.append({"player_id": player_id, "name": "", "position": latest["position"],
                                        "injury": None, "markets": markets})
                teams.append({"team": team, "players": players})
            for rank, pick in enumerate(nfl_props.best_picks(teams), 1):
                observed = nfl_props._value(actual[pick["player_id"]], nfl_props.MARKETS[pick["market"]][1])
                if observed is None:
                    continue
                hit = observed >= 1 if pick["side"] == "yes" else (observed > pick["line"]) == (pick["side"] == "over")
                graded.append((rank, pick["market"], pick["side"], pick["probability"], float(hit), pick["record"]))
    return graded


def pick_report(graded):
    n = len(graded)
    predicted, hit = sum(g[3] for g in graded) / n, sum(g[4] for g in graded) / n
    record = sum(g[5] for g in graded) / n
    brier = lambda i: sum((g[i] - g[4]) ** 2 for g in graded) / n
    print(f"five best picks per game: {n} graded, model {predicted:.3f}, season record {record:.3f}, hit {hit:.3f}; "
          f"Brier model {brier(3):.4f} vs record {brier(5):.4f}")
    for rank in range(1, 6):
        subset = [g for g in graded if g[0] == rank]
        if subset:
            print(f"  rank {rank}: n {len(subset)}, predicted {sum(g[3] for g in subset) / len(subset):.3f}, "
                  f"hit {sum(g[4] for g in subset) / len(subset):.3f}")
    sides = defaultdict(list)
    for g in graded:
        sides[g[2]].append(g)
    for side, subset in sides.items():
        print(f"  {side}: n {len(subset)}, predicted {sum(g[3] for g in subset) / len(subset):.3f}, "
              f"hit {sum(g[4] for g in subset) / len(subset):.3f}")


def report(results):
    print(f'{"market":<18}{"n":>6}{"MAE":>8}{"base":>8}{"pred":>7}{"actual":>8}{"brier":>7}{"20-80":>7}{"bias":>8}')
    for key, bucket in results.items():
        n = len(bucket["error"])
        mae, base = sum(bucket["error"]) / n, sum(bucket["baseline"]) / n
        predicted, hit = sum(bucket["predicted"]) / n, sum(bucket["hit"]) / n
        brier = sum((p - h) ** 2 for p, h in zip(bucket["predicted"], bucket["hit"], strict=True)) / n
        covered = sum(bucket["covered"]) / n if bucket["covered"] else float("nan")
        bias = sum(bucket["bias"]) / n
        print(f"{key:<18}{n:>6}{mae:>8.2f}{base:>8.2f}{predicted:>7.3f}{hit:>8.3f}{brier:>7.3f}{covered:>7.3f}{bias:>8.2f}")


if __name__ == "__main__":
    for season in [int(arg) for arg in sys.argv[1:]] or [2025]:
        print(f"\n== {season}")
        report(backtest(season))
        pick_report(pick_backtest(season))
