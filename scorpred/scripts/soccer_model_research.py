"""Walk-forward comparison of soccer models with bookmaker prices, top five leagues, 2019-20 onward.

Every probability is made from matches played before the day it predicts. Models:

* market_open / market_close  pre-weekend and closing market-average prices, margin removed
* rules        the published rules engine (scorpred_engine), replayed from the same results
* elo          goal-difference Elo, mapped to probabilities by a multinomial logistic fit
* dixon_coles  time-weighted Dixon-Coles goals model, refitted every week
* logistic     regularised logistic regression on Elo, Dixon-Coles and shots-on-target form
* fitted       the same without shots on target; this is the published model
* market_lr    the pre-weekend price recalibrated by logistic regression (the anchor alone)
* anchored     the pre-weekend price plus Dixon-Coles and Elo, by logistic regression

Hyperparameters are chosen on 2019-20 and 2020-21 only. Logistic layers are refitted at the
start of every season on all earlier seasons. 2021-22 to 2025-26 are reported.

Writes data/validation/soccer_model_research.json and, fitted on every season, the published
model's coefficients to data/models/soccer_ratings_v1.json.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scorpred_engine as se  # noqa: E402
from runtime_paths import repo_root  # noqa: E402
from services import soccer_ratings as sr  # noqa: E402
from utils.atomic import atomic_write_json  # noqa: E402

ODDS_DIR = repo_root() / "data" / "raw" / "football_data_odds"
REPORT_PATH = repo_root() / "data" / "validation" / "soccer_model_research.json"
ARTIFACT_PATH = repo_root() / "data" / "models" / "soccer_ratings_v1.json"
ARTIFACT_VERSION = "soccer-ratings-v1"
LEAGUES = {"E0": "Premier League", "SP1": "La Liga", "I1": "Serie A", "D1": "Bundesliga", "F1": "Ligue 1"}
SEASONS = ("1920", "2021", "2122", "2223", "2324", "2425", "2526")
TUNING_SEASONS = ("1920", "2021")
EVALUATION_SEASONS = SEASONS[2:]
OUTCOMES = {"H": 0, "D": 1, "A": 2}
BOOTSTRAP_DRAWS = 2000
SEED = 20261023
EDGES = (0.0, 0.05)
FORM_GAMES = 6

# The engine reads the live track record for display text on every call; probabilities do not use it.
se.summarize_prediction_history = lambda: {"total_tracked": 0, "total_verified": 0, "accuracy": None, "confidence": {}}


def load_matches() -> pd.DataFrame:
    frames = []
    for season in SEASONS:
        for code, league in LEAGUES.items():
            raw = (ODDS_DIR / f"{season}_{code}.csv").read_bytes()
            frame = pd.read_csv(io.BytesIO(raw), encoding="latin-1")
            frame["league"], frame["season"] = league, season
            frames.append(frame)
    data = pd.concat(frames, ignore_index=True)
    data = data.copy()
    data["parsed_date"] = pd.to_datetime(data["Date"], dayfirst=True, errors="coerce")
    data = data.dropna(subset=["parsed_date", "HomeTeam", "AwayTeam", "FTR", "FTHG", "FTAG"])
    data["day"] = data["parsed_date"].map(lambda d: d.date().toordinal())
    data["target"] = data["FTR"].map(OUTCOMES)
    for columns in (["AvgH", "AvgD", "AvgA"], ["AvgCH", "AvgCD", "AvgCA"], ["PSCH", "PSCD", "PSCA"],
                    ["HS", "AS", "HST", "AST", "HC", "AC"]):
        data[columns] = data[columns].apply(pd.to_numeric, errors="coerce")
    data = data.sort_values(["day", "league", "HomeTeam"], kind="stable").reset_index(drop=True)
    return data


def devig(prices: np.ndarray) -> np.ndarray:
    implied = 1.0 / prices
    return implied / implied.sum(axis=1, keepdims=True)


# ── First stage: ratings, rules engine and form, strictly before each match day ─────────────────

def _form_entry(row, home: bool) -> dict:
    gf, ga = (int(row.FTHG), int(row.FTAG)) if home else (int(row.FTAG), int(row.FTHG))
    side, other = ("H", "A") if home else ("A", "H")
    return {"result": "W" if gf > ga else "L" if gf < ga else "D", "gf": gf, "ga": ga, "goals_for": gf,
            "goals_against": ga, "shots": getattr(row, f"{side}S"), "shots_on_target": getattr(row, f"{side}ST"),
            "possession": None, "corners": getattr(row, f"{side}C"),
            "opponent": row.AwayTeam if home else row.HomeTeam, "home": home, "is_home": home,
            "season": row.season, "date": row.parsed_date.date().isoformat(), "score": f"{gf}-{ga}", "cs": ga == 0,
            "_sot_against": getattr(row, f"{other}ST")}


def _standings(table: dict[str, dict]) -> list[dict]:
    ordered = sorted(table.items(), key=lambda item: (-item[1]["points"], -item[1]["gd"], item[0]))
    return [{"team": {"name": team}, "rank": rank, "points": row["points"], "played": row["played"], "win": row["win"]}
            for rank, (team, row) in enumerate(ordered, start=1)]


def _rules_probabilities(history, table, home: str, away: str, season: str) -> np.ndarray:
    form_a = [f for f in reversed(history[home]) if f["season"] == season][:5]
    form_b = [f for f in reversed(history[away]) if f["season"] == season][:5]
    h2h_a = [f for f in reversed(history[home]) if f["opponent"] == away][:5]
    h2h_b = [f for f in reversed(history[away]) if f["opponent"] == home][:5]
    strengths = se.build_opp_strengths_from_standings(_standings(table)) if table else {}
    prediction = se.scorpred_predict(form_a=form_a, form_b=form_b, h2h_form_a=h2h_a, h2h_form_b=h2h_b,
                                     injuries_a=[], injuries_b=[], team_a_is_home=True, team_a_name=home,
                                     team_b_name=away, sport="soccer", opp_strengths=strengths)
    probs = prediction.get("win_probabilities") or {}
    values = np.array([float(probs.get("a") or 0), float(probs.get("draw") or 0), float(probs.get("b") or 0)])
    return values / values.sum() if values.sum() > 0 else np.full(3, 1 / 3)


def _sot_form(history, team: str) -> tuple[float, float]:
    recent = history[team][-FORM_GAMES:]
    if not recent:
        return 4.0, 4.0
    return (float(np.nanmean([f["shots_on_target"] for f in recent])),
            float(np.nanmean([f["_sot_against"] for f in recent])))


def first_stage(data: pd.DataFrame, *, half_life: float, ridge: float, elo_k: float, elo_home: float,
                with_rules: bool = True, seasons: tuple[str, ...] = SEASONS) -> pd.DataFrame:
    """Per-match pre-kickoff features for every league, walking forward one day at a time."""
    out = []
    for league in LEAGUES.values():
        rows = data[(data["league"] == league) & data["season"].isin(seasons)]
        elo = sr.Elo(k=elo_k, home_advantage=elo_home)
        played: list[sr.Match] = []
        history: dict[str, list[dict]] = defaultdict(list)
        table: dict[str, dict] = {}
        season, previous_teams, prior, model, fitted_week = None, set(), {}, None, None
        for day, day_rows in rows.groupby("day", sort=True):
            if day_rows["season"].iloc[0] != season:
                if season is not None:
                    elo.new_season()
                    previous_teams = set(table)
                season = day_rows["season"].iloc[0]
                teams = set(rows.loc[rows["season"] == season, "HomeTeam"])
                prior = {team: sr.NEW_TEAM_PRIOR for team in teams - previous_teams} if previous_teams else {}
                table = {}
                fitted_week = None
            week = day // 7
            if week != fitted_week:
                model = sr.fit(played, day, half_life_days=half_life, ridge=ridge, prior=prior, warm=model)
                fitted_week = week
            for row in day_rows.itertuples():
                home, away = row.HomeTeam, row.AwayTeam
                lam, mu = model.rates(home, away) if model else (1.5, 1.2)
                dc = model.probabilities(home, away) if model else np.array([0.45, 0.27, 0.28])
                sot_h, sot_h_against = _sot_form(history, home)
                sot_a, sot_a_against = _sot_form(history, away)
                record = {"index": row.Index, "elo_diff": elo.difference(home, away), "lam": lam, "mu": mu,
                          "dc_h": dc[0], "dc_d": dc[1], "dc_a": dc[2],
                          "sot_diff": (sot_h - sot_h_against) - (sot_a - sot_a_against)}
                if with_rules:
                    rules = _rules_probabilities(history, table, home, away, season)
                    record |= {"rules_h": rules[0], "rules_d": rules[1], "rules_a": rules[2]}
                out.append(record)
            for row in day_rows.itertuples():
                home, away, hg, ag = row.HomeTeam, row.AwayTeam, int(row.FTHG), int(row.FTAG)
                elo.update(home, away, hg, ag)
                played.append(sr.Match(day, home, away, hg, ag))
                history[home].append(_form_entry(row, True))
                history[away].append(_form_entry(row, False))
                for team, gf, ga in ((home, hg, ag), (away, ag, hg)):
                    entry = table.setdefault(team, {"points": 0, "played": 0, "win": 0, "gd": 0})
                    entry["played"] += 1
                    entry["gd"] += gf - ga
                    entry["win"] += gf > ga
                    entry["points"] += 3 if gf > ga else 1 if gf == ga else 0
    return pd.DataFrame(out).set_index("index").sort_index()


# ── Second stage: logistic layers refitted each season on earlier seasons ────────────────────

def _logit_columns(frame: pd.DataFrame, prefix: str) -> np.ndarray:
    h, d, a = (np.clip(frame[f"{prefix}_{k}"].to_numpy(), 1e-6, 1) for k in "hda")
    return np.column_stack([np.log(h / a), np.log(d / (1 - d))])


SECOND_STAGE = {
    "elo": lambda f: (f[["elo_diff"]].to_numpy() / 400,),
    "logistic": lambda f: (f[["elo_diff"]].to_numpy() / 400, _logit_columns(f, "dc"),
                           np.log(f[["lam"]].to_numpy() * f[["mu"]].to_numpy()), f[["sot_diff"]].to_numpy()),
    # What production publishes (services/soccer_model.py): only inputs the live results feed provides.
    "fitted": lambda f: (f[["elo_diff"]].to_numpy() / 400, _logit_columns(f, "dc"), np.log(f[["lam"]].to_numpy() * f[["mu"]].to_numpy())),
    "market_lr": lambda f: (_logit_columns(f, "open"),),
    "anchored": lambda f: (_logit_columns(f, "open"), _logit_columns(f, "dc"), f[["elo_diff"]].to_numpy() / 400),
}


def second_stage(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    predictions = {name: np.full((len(frame), 3), np.nan) for name in SECOND_STAGE}
    coefficients = {}
    for season in EVALUATION_SEASONS:
        train = frame["season"] < season
        test = frame["season"] == season
        for name, features in SECOND_STAGE.items():
            x_train = np.column_stack(features(frame[train]))
            model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
            model.fit(x_train, frame.loc[train, "target"])
            predictions[name][test.to_numpy()] = model.predict_proba(np.column_stack(features(frame[test])))
            coefficients.setdefault(name, {})[season] = np.round(model[-1].coef_, 3).tolist()
    return predictions, coefficients


# ── Scoring ──────────────────────────────────────────────────────────────────────────────────

def scores(probabilities: np.ndarray, target: np.ndarray) -> dict:
    clipped = np.clip(probabilities, 1e-12, 1.0)
    picks = probabilities.argmax(axis=1)
    return {"matches": int(len(target)), "log_loss": round(float(-np.log(clipped[np.arange(len(target)), target]).mean()), 5),
            "brier": round(float(np.square(probabilities - np.eye(3)[target]).mean()), 5),
            "accuracy": round(float((picks == target).mean()), 4), "draw_picks": int((picks == 1).sum())}


def block_interval(blocks: np.ndarray, values: np.ndarray, rng, weights: np.ndarray | None = None) -> list[float]:
    weights = np.ones(len(values)) if weights is None else weights
    unique, inverse = np.unique(blocks, return_inverse=True)
    sums = np.column_stack([np.bincount(inverse, values * weights), np.bincount(inverse, weights)])
    draws = rng.integers(0, len(unique), size=(BOOTSTRAP_DRAWS, len(unique)))
    totals = sums[draws].sum(axis=1)
    ratios = totals[:, 0] / np.where(totals[:, 1] > 0, totals[:, 1], np.nan)
    return [round(float(np.nanquantile(ratios, 0.025)), 5), round(float(np.nanquantile(ratios, 0.975)), 5)]


def paired_gap(model: np.ndarray, reference: np.ndarray, target: np.ndarray, blocks: np.ndarray, rng) -> dict:
    rows = np.arange(len(target))
    gap = -np.log(np.clip(model[rows, target], 1e-12, 1)) + np.log(np.clip(reference[rows, target], 1e-12, 1))
    return {"mean": round(float(gap.mean()), 5), "95ci": block_interval(blocks, gap, rng)}


def value_bets(model: np.ndarray, open_prices: np.ndarray, open_fair: np.ndarray, close_fair: np.ndarray,
               target: np.ndarray, blocks: np.ndarray, edge: float, rng) -> dict:
    expected = model * open_prices - 1.0
    choice = expected.argmax(axis=1)
    rows = np.where(expected[np.arange(len(choice)), choice] > edge)[0]
    if not len(rows):
        return {"bets": 0}
    chosen = choice[rows]
    won = chosen == target[rows]
    profit = np.where(won, open_prices[rows, chosen] - 1.0, -1.0)
    clv = close_fair[rows, chosen] / open_fair[rows, chosen] - 1.0
    return {"bets": int(len(rows)), "roi": round(float(profit.mean()), 4),
            "roi_95ci": block_interval(blocks[rows], profit, rng),
            "closing_line_value": round(float(clv.mean()), 4), "closing_line_value_95ci": block_interval(blocks[rows], clv, rng),
            "beat_close_share": round(float((clv > 0).mean()), 4)}


def top_pick_bets(model: np.ndarray, open_prices: np.ndarray, target: np.ndarray, blocks: np.ndarray, rng) -> dict:
    """Flat stakes on the model's most likely outcome at pre-weekend prices."""
    chosen = model.argmax(axis=1)
    profit = np.where(chosen == target, open_prices[np.arange(len(chosen)), chosen] - 1.0, -1.0)
    return {"bets": int(len(chosen)), "roi": round(float(profit.mean()), 4), "roi_95ci": block_interval(blocks, profit, rng)}


def goals_validation(frame: pd.DataFrame, evaluation: np.ndarray) -> dict:
    """Dixon-Coles expected goals against final scores: error per team, a baseline, 80% range coverage."""
    from scipy.stats import poisson
    test = frame[evaluation]
    lam, mu = test["lam"].to_numpy(), test["mu"].to_numpy()
    home, away = test["FTHG"].to_numpy(dtype=float), test["FTAG"].to_numpy(dtype=float)
    earlier = frame[~evaluation]
    baseline = np.concatenate([np.abs(home - earlier["FTHG"].mean()), np.abs(away - earlier["FTAG"].mean())]).mean()

    def coverage(rate, goals):
        return float(((poisson.ppf(0.1, rate) <= goals) & (goals <= poisson.ppf(0.9, rate))).mean())
    return {"status": "walk_forward", "test_games": int(len(test)),
            "points_mae": round(float(np.concatenate([np.abs(lam - home), np.abs(mu - away)]).mean()), 4),
            "baseline_mae": round(float(baseline), 4),
            "total_mae": round(float(np.abs(lam + mu - home - away).mean()), 4),
            "interval_coverage": {"home": round(coverage(lam, home), 4), "away": round(coverage(mu, away), 4)},
            "test_end": pd.Timestamp.fromordinal(int(test["day"].max())).date().isoformat()}


def tune(data: pd.DataFrame) -> dict:
    """Pick Dixon-Coles decay/ridge and Elo K/home on 2020-21 (2019-20 is warm-up), by log loss."""
    target = data.loc[data["season"] == "2021", "target"].to_numpy()
    results = []
    for half_life in (180.0, 365.0, 730.0):
        for ridge in (2.0, 6.0):
            frame = first_stage(data, half_life=half_life, ridge=ridge, elo_k=20, elo_home=60, with_rules=False,
                                seasons=TUNING_SEASONS)
            dc = frame.loc[(data.loc[frame.index, "season"] == "2021").to_numpy(), ["dc_h", "dc_d", "dc_a"]].to_numpy()
            results.append({"half_life": half_life, "ridge": ridge, "log_loss": scores(dc, target)["log_loss"]})
            print("tune dixon-coles", results[-1], flush=True)
    best = min(results, key=lambda r: r["log_loss"])
    elo_results = []
    for k in (15.0, 25.0, 35.0):
        frame = first_stage(data, half_life=best["half_life"], ridge=best["ridge"], elo_k=k, elo_home=60,
                            with_rules=False, seasons=TUNING_SEASONS)
        mask = (data.loc[frame.index, "season"] == "2021").to_numpy()
        x = frame[["elo_diff"]].to_numpy() / 400
        y = data.loc[frame.index, "target"].to_numpy()
        early = data.loc[frame.index, "season"].to_numpy() == "1920"
        model = LogisticRegression(max_iter=1000).fit(x[early], y[early])
        elo_results.append({"k": k, "log_loss": scores(model.predict_proba(x[mask]), y[mask])["log_loss"]})
        print("tune elo", elo_results[-1], flush=True)
    best_k = min(elo_results, key=lambda r: r["log_loss"])["k"]
    return {"dixon_coles": results, "elo": elo_results,
            "chosen": {"half_life": best["half_life"], "ridge": best["ridge"], "elo_k": best_k, "elo_home": 60.0}}


def walk_forward(data: pd.DataFrame, chosen: dict, *, with_rules: bool = True) -> tuple[pd.DataFrame, dict, dict]:
    """Every priced match with each model's pre-kickoff probabilities (rows aligned with the frame)."""
    frame = first_stage(data, half_life=chosen["half_life"], ridge=chosen["ridge"], elo_k=chosen["elo_k"],
                        elo_home=chosen["elo_home"], with_rules=with_rules)
    frame = frame.join(data[["league", "season", "day", "target", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "AvgH", "AvgD", "AvgA",
                             "AvgCH", "AvgCD", "AvgCA", "PSCH", "PSCD", "PSCA"]])
    open_prices = frame[["AvgH", "AvgD", "AvgA"]].to_numpy(dtype=float)
    close_prices = frame[["AvgCH", "AvgCD", "AvgCA"]].to_numpy(dtype=float)
    priced = np.isfinite(open_prices).all(axis=1) & np.isfinite(close_prices).all(axis=1)         & (open_prices > 1).all(axis=1) & (close_prices > 1).all(axis=1)
    frame = frame[priced].copy()
    open_fair, close_fair = devig(open_prices[priced]), devig(close_prices[priced])
    frame[["open_h", "open_d", "open_a"]] = open_fair
    second, coefficients = second_stage(frame)
    probabilities = {"market_open": open_fair, "market_close": close_fair}
    if with_rules:
        probabilities["rules"] = frame[["rules_h", "rules_d", "rules_a"]].to_numpy()
    probabilities = probabilities | {"dixon_coles": frame[["dc_h", "dc_d", "dc_a"]].to_numpy()} | second
    return frame, probabilities, coefficients


def evaluate(*, tuning: dict | None = None) -> dict:
    started = time.time()
    data = load_matches()
    tuning = tuning or tune(data)
    chosen = tuning["chosen"]
    frame, probabilities, coefficients = walk_forward(data, chosen)
    open_prices = frame[["AvgH", "AvgD", "AvgA"]].to_numpy(dtype=float)
    open_fair, close_fair = probabilities["market_open"], probabilities["market_close"]
    evaluation = frame["season"].isin(EVALUATION_SEASONS).to_numpy()
    target = frame["target"].to_numpy()[evaluation]
    blocks = frame["day"].to_numpy()[evaluation] // 7
    leagues = frame["league"].to_numpy()[evaluation]
    seasons = frame["season"].to_numpy()[evaluation]
    probabilities = {name: values[evaluation] for name, values in probabilities.items()}
    rng = np.random.default_rng(SEED)

    overall = {name: scores(values, target) for name, values in probabilities.items()}
    gaps = {name: {"vs_market_open": paired_gap(values, probabilities["market_open"], target, blocks, rng),
                   "vs_market_close": paired_gap(values, probabilities["market_close"], target, blocks, rng)}
            for name, values in probabilities.items() if not name.startswith("market")}
    gaps["dixon_coles"]["vs_rules"] = paired_gap(probabilities["dixon_coles"], probabilities["rules"], target, blocks, rng)
    by_season = {season: {name: scores(values[seasons == season], target[seasons == season])
                          for name, values in probabilities.items()} for season in EVALUATION_SEASONS}
    by_league = {league: {name: scores(values[leagues == league], target[leagues == league])
                          for name, values in probabilities.items()} for league in LEAGUES.values()}
    betting = {name: {f"edge_over_{int(edge * 100)}pct": value_bets(probabilities[name], open_prices[evaluation],
                                                                    open_fair[evaluation], close_fair[evaluation],
                                                                    target, blocks, edge, rng) for edge in EDGES}
               for name in ("rules", "dixon_coles", "logistic", "fitted", "anchored")}
    for name in ("rules", "fitted"):
        betting[name]["top_pick"] = top_pick_bets(probabilities[name], open_prices[evaluation], target, blocks, rng)
    model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    model.fit(np.column_stack(SECOND_STAGE["fitted"](frame)), frame["target"])
    published = {"version": ARTIFACT_VERSION, "trained_on": {"seasons": list(SEASONS), "matches": int(len(frame))},
                 "features": ["elo_diff/400", "log(dc_home/dc_away)", "logit(dc_draw)", "log(lambda*mu)"],
                 "classes": ["home", "draw", "away"], "ratings": chosen,
                 "layer": {"mean": model[0].mean_.tolist(), "scale": model[0].scale_.tolist(),
                           "coef": model[-1].coef_.tolist(), "intercept": model[-1].intercept_.tolist()},
                 "walk_forward": overall["fitted"] | {"vs_market_open": gaps["fitted"]["vs_market_open"]},
                 "goals_validation": goals_validation(frame, evaluation)}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": round(time.time() - started),
        "data": {"source": "football-data.co.uk results and market-average prices", "leagues": list(LEAGUES.values()),
                 "seasons": list(SEASONS), "evaluated_seasons": list(EVALUATION_SEASONS),
                 "evaluated_matches": int(evaluation.sum())},
        "tuning": tuning,
        "overall": overall,
        "log_loss_gap": gaps,
        "by_season": by_season,
        "by_league": by_league,
        "betting_at_pre_weekend_prices": betting,
        "second_stage_coefficients": coefficients,
        "published_artifact": published,
        "method": {
            "walk_forward": "Ratings use only matches before the predicted day; Dixon-Coles is refitted weekly.",
            "logistic_layers": "Refitted at each season start on every earlier season (2020-21 is the first training season).",
            "log_loss_gap": "Model minus reference per match, mean; negative means the model is better. Weekly-block bootstrap 95% interval.",
            "betting": "Flat one-unit stakes at pre-weekend market-average prices on the outcome with the highest expected value above the edge.",
            "closing_line_value": "Closing margin-free probability of the backed outcome over its pre-weekend probability, minus one.",
            "rules_replay": "scorpred_engine.scorpred_predict with same-season last-5 form, last-5 head-to-head, a table built from results to date, and no injuries (as the dashboard runs it).",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report-path", type=Path, default=REPORT_PATH)
    parser.add_argument("--reuse-tuning", action="store_true", help="Reuse the tuning block of an existing report")
    args = parser.parse_args()
    tuning = json.loads(args.report_path.read_text())["tuning"] if args.reuse_tuning and args.report_path.exists() else None
    report = evaluate(tuning=tuning)
    atomic_write_json(args.report_path, report)
    atomic_write_json(ARTIFACT_PATH, report["published_artifact"])
    print(json.dumps({"overall": report["overall"], "log_loss_gap": report["log_loss_gap"],
                      "betting": report["betting_at_pre_weekend_prices"]}, indent=1))


if __name__ == "__main__":
    main()
