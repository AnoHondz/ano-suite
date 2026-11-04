"""Does the attack-vs-defence goal model beat the old pooled record for team goals? Nested chronological check.

Replays every cached soccer box score in date order. For each team in each game (both teams with 8+ earlier games)
it predicts the team scoring over 0.5 / 1.5 / 2.5 goals:
  pooled       the old method: own goals and the opponent's goals conceded pooled as one sample (smoothed)
  model k / h  services.tiered_parlays.expected_goals with GOAL_PRIOR_GAMES = k and HOME_EDGE = h, Poisson
Settings are chosen on the selection period's Brier score and reported once on the untouched final period.
Run: python scripts/validate_goal_model.py
"""
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services import chrono_validation as cv  # noqa: E402
from services import tiered_parlays as tp  # noqa: E402

LINES = (0.5, 1.5, 2.5)


def load():
    games = {}
    for path in (ROOT / "cache" / "box_scores").glob("soccer-*.json"):
        try:
            game = json.loads(path.read_text(encoding="utf-8"))
            if len(game["teams"]) == 2 and game.get("date"):
                game["ts"] = datetime.fromisoformat(game["date"].replace("Z", "+00:00")).timestamp()
                games[game["id"]] = game  # the same match can be cached under two competitions
        except (OSError, ValueError, KeyError):
            continue
    return sorted(games.values(), key=lambda g: g["ts"])


def replay(games, settings):
    """[(time, Brier loss)] for every team-line prediction; settings None = the old pooled method."""
    history, out = defaultdict(list), []
    for game in games:
        ids = list(game["teams"])
        goals = {t: sum(p.get("goals") or 0 for p in game["teams"][t]["players"]) for t in ids}
        for team in ids:
            other = ids[1] if team == ids[0] else ids[0]
            mine, theirs = history[team][-15:], history[other][-15:]
            if len(mine) < 8 or len(theirs) < 8:
                continue
            if settings is not None:
                prior_games, home_edge = settings
                tp.GOAL_PRIOR_GAMES, tp.HOME_EDGE = prior_games, home_edge
                lam = tp.expected_goals([s for s, _ in mine], [c for _, c in theirs], bool(game["teams"][team].get("home")))
            for line in LINES:
                if settings is None:
                    pooled = [s for s, _ in mine] + [c for _, c in theirs]
                    p = (sum(1 for x in pooled if x > line) + 1) / (len(pooled) + 2)
                else:
                    p = 1 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(math.floor(line) + 1))
                out.append((game["ts"], (p - (goals[team] > line)) ** 2))
        for team in ids:
            other = ids[1] if team == ids[0] else ids[0]
            history[team].append((goals[team], goals[other]))
    return out


def main():
    games = load()
    candidates = {"pooled (old)": None}
    candidates.update({f"model k={k} home={h}": (k, h) for k in (0, 5, 12, 20, 30, 45) for h in (1.0, 1.08, 1.15)})
    result = cv.choose_then_test(candidates, lambda settings: replay(games, settings))
    when = lambda ts: datetime.fromtimestamp(ts).date()
    print(f"{len(games)} matches; selection from {when(result['selection_starts'])}, untouched test from {when(result['test_starts'])}")
    for name in candidates:
        (sel, n_sel), (tst, n_tst) = result["selection"][name], result["test"][name]
        print(f"{name:24} selection Brier {sel:.4f} (n={n_sel})   test {tst:.4f} (n={n_tst})")
    print(f"chosen on selection: {result['chosen']}; its untouched test Brier: {result['result'][0]:.4f}")


if __name__ == "__main__":
    main()
