"""Compare the production soccer model with bookmaker prices on its chronological test window.

Odds come from football-data.co.uk (top five European leagues). The model's test-window
probabilities are reproduced from the published artifact and must match the recorded accuracy.
Writes data/validation/market_evaluation.json.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from production_model import chronological_splits, feature_matrix  # noqa: E402
from runtime_paths import clean_soccer_model_path, historical_dataset_path, repo_root  # noqa: E402
from train_model import build_clean_features  # noqa: E402
from utils.atomic import atomic_write_json  # noqa: E402

SOURCE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
LEAGUES = {"E0": "Premier League", "SP1": "La Liga", "I1": "Serie A", "D1": "Bundesliga", "F1": "Ligue 1"}
ODDS_DIR = repo_root() / "data" / "raw" / "football_data_odds"
REPORT_PATH = repo_root() / "data" / "validation" / "market_evaluation.json"
OUTCOMES = ("H", "D", "A")
VALUE_EDGES = (0.0, 0.05, 0.10)
BOOTSTRAP_DRAWS = 2000
SEED = 20261022

# Dataset (ESPN) name -> football-data.co.uk name, after _normalize on both sides.
ALIASES = {
    "manchester united": "man united", "manchester city": "man city", "wolverhampton wanderers": "wolves",
    "newcastle united": "newcastle", "nottingham forest": "nottm forest", "brighton hove albion": "brighton",
    "tottenham hotspur": "tottenham", "west ham united": "west ham", "leeds united": "leeds",
    "leicester city": "leicester", "ipswich town": "ipswich", "afc bournemouth": "bournemouth",
    "internazionale": "inter", "ac milan": "milan", "as roma": "roma", "hellas verona": "verona",
    "atltico madrid": "ath madrid", "athletic club": "ath bilbao", "real betis": "betis", "celta vigo": "celta",
    "rayo vallecano": "vallecano", "real sociedad": "sociedad", "espanyol": "espanol", "alavs": "alaves",
    "real oviedo": "oviedo", "tsg hoffenheim": "hoffenheim", "borussia dortmund": "dortmund",
    "vfl wolfsburg": "wolfsburg", "fc cologne": "fc koln", "bayer leverkusen": "leverkusen",
    "sc freiburg": "freiburg", "eintracht frankfurt": "ein frankfurt", "1 fc heidenheim 1846": "heidenheim",
    "fc augsburg": "augsburg", "vfb stuttgart": "stuttgart", "1 fc union berlin": "union berlin",
    "borussia mnchengladbach": "mgladbach", "st pauli": "st pauli", "hamburg sv": "hamburg",
    "fc st pauli": "st pauli", "vfl bochum": "bochum", "holstein kiel": "holstein kiel",
    "stade rennais": "rennes", "as monaco": "monaco", "aj auxerre": "auxerre", "le havre ac": "le havre",
    "paris saintgermain": "paris sg", "saintetienne": "st etienne", "as saintetienne": "st etienne",
    "mallorca": "mallorca", "deportivo alavs": "alaves", "girona": "girona", "leganes": "leganes",
    "legans": "leganes", "real valladolid": "valladolid", "las palmas": "las palmas",
}


def _normalize(name: str) -> str:
    # Drop every non-ASCII character first so accented and mis-encoded spellings agree.
    ascii_only = "".join(ch for ch in str(name) if ord(ch) < 128)
    text = re.sub(r"[^a-z0-9 ]+", "", unicodedata.normalize("NFKD", ascii_only).lower())
    return re.sub(r"\s+", " ", text).strip()


def _canonical(name: str) -> str:
    key = _normalize(name)
    return ALIASES.get(key, key)


def _similarity(left: str, right: str) -> float:
    a, b = _canonical(left), _normalize(right)
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _season_codes(start: date, end: date) -> list[str]:
    codes = []
    for year in range(start.year - 1, end.year + 1):
        season_start, season_end = date(year, 7, 1), date(year + 1, 6, 30)
        if season_start <= end and season_end >= start:
            codes.append(f"{year % 100:02d}{(year + 1) % 100:02d}")
    return codes


def load_odds(start: date, end: date, *, download: bool = True) -> tuple[pd.DataFrame, list[dict]]:
    ODDS_DIR.mkdir(parents=True, exist_ok=True)
    frames, sources = [], []
    for season in _season_codes(start, end):
        for league, league_name in LEAGUES.items():
            path = ODDS_DIR / f"{season}_{league}.csv"
            url = SOURCE_URL.format(season=season, league=league)
            if not path.exists() or path.stat().st_size == 0:
                if not download:
                    raise FileNotFoundError(path)
                response = requests.get(url, timeout=60, allow_redirects=True)
                response.raise_for_status()
                path.write_bytes(response.content)
            raw = path.read_bytes()
            frame = pd.read_csv(io.BytesIO(raw), encoding="latin-1")
            frame["league"] = league_name
            frame["parsed_date"] = pd.to_datetime(frame["Date"], dayfirst=True, errors="coerce").dt.date
            frames.append(frame)
            sources.append({"league": league_name, "season": season, "url": url,
                            "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(frame)})
    odds = pd.concat(frames, ignore_index=True)
    return odds.dropna(subset=["parsed_date", "HomeTeam", "AwayTeam", "FTR"]), sources


def match_rows(test_rows: list[dict], odds: pd.DataFrame) -> tuple[list[tuple[int, int]], dict]:
    by_date = defaultdict(list)
    for index, row in odds.iterrows():
        by_date[row["parsed_date"]].append(index)
    pairs, unmatched, result_mismatch = [], [], 0
    used = set()
    for position, row in enumerate(test_rows):
        day = date.fromisoformat(row["date"][:10])
        candidates = [i for offset in (0, -1, 1) for i in by_date.get(day + timedelta(days=offset), []) if i not in used]
        scored = sorted(((min(_similarity(row["home_team"], odds.at[i, "HomeTeam"]),
                                  _similarity(row["away_team"], odds.at[i, "AwayTeam"])), i) for i in candidates), reverse=True)
        if not scored or scored[0][0] < 0.6:
            unmatched.append(f'{row["date"]} {row["home_team"]} v {row["away_team"]}')
            continue
        index = scored[0][1]
        if OUTCOMES[int(row["target"])] != odds.at[index, "FTR"]:
            result_mismatch += 1
            unmatched.append(f'{row["date"]} {row["home_team"]} v {row["away_team"]} (result disagrees)')
            continue
        used.add(index)
        pairs.append((position, index))
    return pairs, {"test_rows": len(test_rows), "matched": len(pairs), "unmatched": len(unmatched),
                   "result_disagreements": result_mismatch, "unmatched_examples": unmatched[:25]}


def _devig(prices: np.ndarray) -> np.ndarray:
    implied = 1.0 / prices
    return implied / implied.sum(axis=1, keepdims=True)


def _scores(probabilities: np.ndarray, target: np.ndarray) -> dict:
    clipped = np.clip(probabilities, 1e-12, 1.0)
    return {"accuracy": float((probabilities.argmax(axis=1) == target).mean()),
            "log_loss": float(-np.log(clipped[np.arange(len(target)), target]).mean()),
            "brier_score": float(np.square(probabilities - np.eye(3)[target]).mean())}


def _block_bootstrap(blocks: np.ndarray, values: np.ndarray, weights: np.ndarray, rng) -> tuple[float, float]:
    """Weekly-block resampling of a weighted mean (profit per stake, loss per match)."""
    unique = np.unique(blocks)
    sums = np.array([[values[blocks == b].sum(), weights[blocks == b].sum()] for b in unique])
    picks = rng.integers(0, len(unique), size=(BOOTSTRAP_DRAWS, len(unique)))
    totals = sums[picks].sum(axis=1)
    valid = totals[:, 1] > 0
    ratios = totals[valid, 0] / totals[valid, 1]
    return float(np.quantile(ratios, 0.025)), float(np.quantile(ratios, 0.975))


def _betting(selection: np.ndarray, prices: np.ndarray, target: np.ndarray, blocks: np.ndarray, rng) -> dict:
    """Flat one-unit stakes on the selected outcome where selection >= 0."""
    placed = selection >= 0
    if not placed.any():
        return {"bets": 0}
    rows = np.where(placed)[0]
    chosen = selection[rows]
    won = chosen == target[rows]
    profit = np.where(won, prices[rows, chosen] - 1.0, -1.0)
    low, high = _block_bootstrap(blocks[rows], profit, np.ones(len(rows)), rng)
    return {"bets": int(len(rows)), "hit_rate": float(won.mean()), "average_price": float(prices[rows, chosen].mean()),
            "profit_units": float(profit.sum()), "roi": float(profit.mean()), "roi_95ci": [low, high]}


def evaluate(*, download: bool = True) -> dict:
    rows, _ = build_clean_features(historical_dataset_path())
    _, _, test = chronological_splits(rows)
    bundle = joblib.load(clean_soccer_model_path())
    probabilities_all = bundle["model"].predict_proba(feature_matrix(test))
    target_all = np.array([int(row["target"]) for row in test])
    reproduced = float((probabilities_all.argmax(axis=1) == target_all).mean())
    published = json.loads((repo_root() / "data" / "validation" / "production_model.json").read_text())["metrics"]["accuracy"]
    if abs(reproduced - published) > 1e-6:
        raise RuntimeError(f"Reproduced test accuracy {reproduced:.6f} differs from the published {published:.6f}.")

    start, end = date.fromisoformat(test[0]["date"][:10]), date.fromisoformat(test[-1]["date"][:10])
    odds, sources = load_odds(start, end, download=download)
    pairs, matching = match_rows(test, odds)
    positions = np.array([p for p, _ in pairs])
    odds_rows = odds.loc[[i for _, i in pairs]].reset_index(drop=True)
    model = probabilities_all[positions]
    target = target_all[positions]
    blocks = np.array([date.fromisoformat(test[p]["date"][:10]).toordinal() // 7 for p in positions])

    price_sets = {"pinnacle_closing": ["PSCH", "PSCD", "PSCA"], "average_closing": ["AvgCH", "AvgCD", "AvgCA"],
                  "average_early": ["AvgH", "AvgD", "AvgA"]}
    prices, available = {}, {}
    for name, columns in price_sets.items():
        values = odds_rows[columns].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        prices[name] = values
        available[name] = np.isfinite(values).all(axis=1) & (values > 1).all(axis=1)
    complete = available["average_closing"] & available["average_early"]
    model, target, blocks = model[complete], target[complete], blocks[complete]
    prices = {name: values[complete] for name, values in prices.items()}
    available = {name: mask[complete] for name, mask in available.items()}
    market = _devig(prices["average_closing"])
    rng = np.random.default_rng(SEED)

    per_match_loss = -np.log(np.clip(model[np.arange(len(target)), target], 1e-12, 1)) + np.log(market[np.arange(len(target)), target])
    loss_low, loss_high = _block_bootstrap(blocks, per_match_loss, np.ones(len(target)), rng)

    top_pick = model.argmax(axis=1)
    betting = {}
    for price_name in ("average_early", "average_closing", "pinnacle_closing"):
        mask = available[price_name]
        offered = prices[price_name][mask]
        expected = model[mask] * offered - 1.0
        best = expected.argmax(axis=1)
        strategies = {"model_top_pick": top_pick[mask], "market_favourite": market[mask].argmax(axis=1)}
        for edge in VALUE_EDGES:
            strategies[f"value_edge_over_{int(edge * 100)}pct"] = np.where(expected[np.arange(len(best)), best] > edge, best, -1)
        betting[price_name] = {"matches": int(mask.sum())} | {
            name: _betting(selection, offered, target[mask], blocks[mask], rng) for name, selection in strategies.items()}

    pinnacle = available["pinnacle_closing"]
    pinnacle_comparison = {"matches": int(pinnacle.sum()), "model": _scores(model[pinnacle], target[pinnacle]),
                           "pinnacle": _scores(_devig(prices["pinnacle_closing"][pinnacle]), target[pinnacle])}

    by_league = {}
    leagues = odds_rows["league"].to_numpy()[complete]
    for league in sorted(set(leagues)):
        mask = leagues == league
        by_league[league] = {"matches": int(mask.sum()), "model": _scores(model[mask], target[mask]),
                             "market": _scores(market[mask], target[mask])}

    model_scores, market_scores = _scores(model, target), _scores(market, target)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_artifact": str(clean_soccer_model_path().relative_to(repo_root())),
        "reproduced_test_accuracy": reproduced,
        "test_window": {"start": start.isoformat(), "end": end.isoformat()},
        "matching": matching | {"evaluated_with_complete_prices": int(complete.sum())},
        "market_reference": "Market-average closing prices with the margin removed proportionally",
        "model": model_scores,
        "market": market_scores,
        "pinnacle_subset": pinnacle_comparison,
        "model_minus_market_log_loss": {"mean": float(per_match_loss.mean()), "95ci": [loss_low, loss_high],
                                        "interpretation": "Positive means the model's probabilities are worse than the market's."},
        "model_predicted_draws": int((top_pick == 1).sum()),
        "market_favourite_draws": int((market.argmax(axis=1) == 1).sum()),
        "betting": betting,
        "by_league": by_league,
        "method": {"stake": "Flat one unit per bet, no commission",
                   "value_rule": "Bet the outcome with the highest model expected value when it exceeds the edge",
                   "bootstrap": f"Weekly calendar blocks, {BOOTSTRAP_DRAWS} resamples, seed {SEED}",
                   "price_notes": {"average_early": "Market average collected before the weekend (the price a user could typically take)",
                                   "average_closing": "Market average at kickoff",
                                   "pinnacle_closing": "Sharpest closing price; hardest to beat"}},
        "sources": sources,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Fail instead of downloading missing odds files")
    parser.add_argument("--report-path", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    report = evaluate(download=not args.offline)
    atomic_write_json(args.report_path, report)
    summary = {key: report[key] for key in ("matching", "model", "market", "pinnacle_subset", "model_minus_market_log_loss",
                                            "model_predicted_draws", "market_favourite_draws", "by_league")}
    summary["betting"] = {price: {name: {k: v for k, v in result.items() if k in ("bets", "hit_rate", "roi", "roi_95ci")}
                                  if isinstance(result, dict) else result for name, result in strategies.items()}
                          for price, strategies in report["betting"].items()}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
