#!/usr/bin/env python3
"""ScorPred Weekly Retrain Pipeline.

Runs once a week (e.g. Sunday 03:00 UTC) to refresh ML models and all
downstream artifacts from the latest committed historical data.

Steps (in order):
  1. prepare_dataset   -- merge raw CSVs into training-ready CSV
  2. train_model       -- retrain calibrated RF on clean features + ELO
  3. walk_forward      -- 5-fold backtest (LR / RF / XGB / LGBM / Ensemble)
  4. generate_report   -- regenerate Strategy Lab model-comparison report
  5. daily_refresh     -- learn from mistakes + optimize policy + report refresh

Usage:
    python weekly_retrain.py [--dry-run] [--skip-dataset] [--skip-train]
                             [--skip-backtest] [--folds N]

Artifacts written:
    data/processed/soccer_training_data_clean.csv
    data/processed/soccer_elo_state.json
    data/models/soccer_random_forest_clean.pkl
    data/backtests/walk_forward_report.json
    cache/ml/model_comparison.json
    cache/ml/prediction_policy.json
    data/analysis/mistake_report.json
    data/analysis/policy_adjustments.json
    data/logs/weekly_retrain.jsonl
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from runtime_paths import (
    clean_soccer_dataset_path,
    clean_soccer_model_path,
    data_dir,
    ensure_runtime_dirs,
    historical_dataset_path,
    walk_forward_report_path,
)

_LOG_PATH = _REPO / "data" / "logs" / "weekly_retrain.jsonl"


# -- helpers -------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append_log(record: dict) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _banner(text: str) -> None:
    print(f"\n{'-' * 60}")
    print(f"  {text}")
    print(f"{'-' * 60}")


def _print_step(r: dict) -> None:
    status = r.get("status", "?")
    elapsed = r.get("elapsed_s")
    suffix = f"  ({elapsed:.1f}s)" if elapsed is not None else ""
    print(f"  -> {status}{suffix}")
    for k, v in (r.get("details") or {}).items():
        if k == "traceback":
            print(f"     {v}")
        else:
            print(f"     {k}: {v}")


# -- pipeline steps ------------------------------------------------------------

def step_prepare_dataset(dry_run: bool = False) -> dict:
    """Merge raw CSVs in data/raw/ into data/processed/matches.csv.

    prepare_dataset.py is a standalone script (no importable function),
    so it is run as a subprocess. Skipped if data/raw/ is empty or missing.
    """
    result: dict = {"step": "prepare_dataset", "status": "ok", "details": {}}
    start = time.monotonic()

    raw_dir = data_dir() / "raw"
    if not raw_dir.exists() or not any(raw_dir.glob("*.csv")):
        result["status"] = "skipped"
        result["details"]["reason"] = "data/raw/ is empty or missing -- skipping dataset prep"
        result["elapsed_s"] = round(time.monotonic() - start, 2)
        return result

    if dry_run:
        result["details"]["dry_run"] = True
        result["elapsed_s"] = round(time.monotonic() - start, 2)
        return result

    try:
        proc = subprocess.run(
            [sys.executable, str(_REPO / "prepare_dataset.py")],
            capture_output=True,
            text=True,
            cwd=str(_REPO),
        )
        result["details"]["stdout"] = proc.stdout.strip()
        if proc.returncode != 0:
            result["status"] = "error"
            result["details"]["stderr"] = proc.stderr.strip()
            result["details"]["returncode"] = proc.returncode
    except Exception:
        result["status"] = "error"
        result["details"]["traceback"] = traceback.format_exc()

    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


def step_train_model(dry_run: bool = False, random_state: int = 42) -> dict:
    """Retrain the calibrated RF on clean pre-match features.

    Requires data/historical_matches.csv to exist (the canonical training set).
    Skipped when the historical CSV is not present.
    """
    result: dict = {"step": "train_model", "status": "ok", "details": {}}
    start = time.monotonic()

    hist = historical_dataset_path()
    if not hist.exists():
        result["status"] = "skipped"
        result["details"]["reason"] = "data/historical_matches.csv not found"
        result["elapsed_s"] = round(time.monotonic() - start, 2)
        return result

    if dry_run:
        result["details"]["dry_run"] = True
        result["elapsed_s"] = round(time.monotonic() - start, 2)
        return result

    try:
        from production_model import train_production_model

        info = train_production_model(random_state=random_state)
        result["details"]["total_rows"] = info.get("total_rows")
        result["details"]["train_size"] = info.get("train_size")
        result["details"]["cal_size"] = info.get("cal_size")
        result["details"]["test_size"] = info.get("test_size")
        result["details"]["accuracy"] = info.get("accuracy")
        result["details"]["brier_score"] = info.get("brier_score")
        result["details"]["model_path"] = info.get("model_path")
    except Exception:
        result["status"] = "error"
        result["details"]["traceback"] = traceback.format_exc()

    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


def step_walk_forward_backtest(
    dry_run: bool = False,
    n_folds: int = 5,
    random_state: int = 42,
) -> dict:
    """Run expanding-window walk-forward backtest across 5 model families.

    Trains LR / RF / XGBoost / LightGBM / Stacking ensemble per fold.
    Requires data/historical_matches.csv.
    """
    result: dict = {"step": "walk_forward_backtest", "status": "ok", "details": {}}
    start = time.monotonic()

    hist = historical_dataset_path()
    if not hist.exists():
        result["status"] = "skipped"
        result["details"]["reason"] = "data/historical_matches.csv not found"
        result["elapsed_s"] = round(time.monotonic() - start, 2)
        return result

    if dry_run:
        result["details"]["dry_run"] = True
        result["elapsed_s"] = round(time.monotonic() - start, 2)
        return result

    try:
        from walk_forward_backtest import run_walk_forward

        report = run_walk_forward(
            n_folds=n_folds,
            random_state=random_state,
        )
        windows = report.get("windows") or {}
        recent = (windows.get("last_3_years") or {}) if isinstance(windows, dict) else {}
        headline = recent if recent.get("available") else (windows.get("all_history") or {})
        agg = headline.get("aggregate", {}) if isinstance(headline, dict) else {}
        comb = agg.get("combined", {})
        result["details"]["n_folds"] = agg.get("n_folds")
        result["details"]["total_test_matches"] = agg.get("total_test_matches")
        result["details"]["combined_accuracy"] = comb.get("mean_combined_accuracy")
        result["details"]["trend"] = agg.get("trend")
        selector_profile = report.get("selector") or {}
        result["details"]["selector_default"] = selector_profile.get("default_source")
        result["details"]["report_path"] = str(walk_forward_report_path())
    except Exception:
        result["status"] = "error"
        result["details"]["traceback"] = traceback.format_exc()

    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


def step_generate_ml_report(dry_run: bool = False) -> dict:
    """Regenerate the Strategy Lab model-comparison report from current artifacts."""
    result: dict = {"step": "generate_ml_report", "status": "ok", "details": {}}
    start = time.monotonic()

    from runtime_paths import ensemble_soccer_model_path, ml_report_path

    dataset = clean_soccer_dataset_path()
    model_path = (
        ensemble_soccer_model_path()
        if ensemble_soccer_model_path().exists()
        else clean_soccer_model_path()
    )

    if not dataset.exists():
        result["status"] = "skipped"
        result["details"]["reason"] = "processed dataset not found"
        result["elapsed_s"] = round(time.monotonic() - start, 2)
        return result

    if not model_path.exists():
        result["status"] = "skipped"
        result["details"]["reason"] = "trained model not found"
        result["elapsed_s"] = round(time.monotonic() - start, 2)
        return result

    if dry_run:
        result["details"]["dry_run"] = True
        result["elapsed_s"] = round(time.monotonic() - start, 2)
        return result

    try:
        import generate_ml_report as gen

        out = gen.generate_clean_soccer_report(
            dataset_path=dataset,
            model_path=model_path,
            output=ml_report_path(),
        )
        result["details"]["report_path"] = str(out)
    except Exception:
        result["status"] = "error"
        result["details"]["traceback"] = traceback.format_exc()

    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


# -- orchestrator --------------------------------------------------------------

def run_weekly_retrain(
    dry_run: bool = False,
    skip_dataset: bool = False,
    skip_train: bool = False,
    skip_backtest: bool = False,
    n_folds: int = 5,
    random_state: int = 42,
    skip_policy_optimize: bool = False,
) -> dict:
    """Run the full weekly retrain pipeline and return a summary dict."""
    ensure_runtime_dirs()
    run_start = time.monotonic()
    timestamp = _utcnow()
    steps: list[dict] = []

    print(f"\n{'=' * 60}")
    print(f"  ScorPred  .  Weekly Retrain  .  {timestamp}")
    if dry_run:
        print("  [DRY RUN -- no artifacts will be written]")
    print(f"{'=' * 60}")

    # Step 1 -- Prepare dataset
    if not skip_dataset:
        _banner("[1/5] Prepare dataset")
        r = step_prepare_dataset(dry_run=dry_run)
        steps.append(r)
        _print_step(r)
    else:
        steps.append({"step": "prepare_dataset", "status": "skipped", "details": {"reason": "--skip-dataset flag"}})

    # Step 2 -- Retrain model
    if not skip_train:
        _banner("[2/5] Train calibrated Random Forest")
        r = step_train_model(dry_run=dry_run, random_state=random_state)
        steps.append(r)
        _print_step(r)
    else:
        steps.append({"step": "train_model", "status": "skipped", "details": {"reason": "--skip-train flag"}})

    # Step 3 -- Walk-forward backtest
    if not skip_backtest:
        _banner("[3/5] Walk-forward backtest")
        r = step_walk_forward_backtest(dry_run=dry_run, n_folds=n_folds, random_state=random_state)
        steps.append(r)
        _print_step(r)
    else:
        steps.append({"step": "walk_forward_backtest", "status": "skipped", "details": {"reason": "--skip-backtest flag"}})

    # Step 4 -- Regenerate ML comparison report
    _banner("[4/5] Generate ML comparison report")
    r = step_generate_ml_report(dry_run=dry_run)
    steps.append(r)
    _print_step(r)

    # Step 5 -- Run daily refresh (learning + policy + report)
    _banner("[5/5] Daily refresh (learn . optimize . report)")
    try:
        from daily_refresh import run_daily_refresh
        daily_summary = run_daily_refresh(dry_run=dry_run, skip_policy=skip_policy_optimize)
        for s in daily_summary.get("steps", []):
            steps.append(s)
    except Exception:
        steps.append({
            "step": "daily_refresh",
            "status": "error",
            "details": {"traceback": traceback.format_exc()},
        })

    errors = [s for s in steps if s.get("status") == "error"]
    total_elapsed = round(time.monotonic() - run_start, 2)

    summary = {
        "run_at": timestamp,
        "pipeline": "weekly_retrain",
        "dry_run": dry_run,
        "total_elapsed_s": total_elapsed,
        "steps": steps,
        "error_count": len(errors),
    }

    print(f"\n{'=' * 60}")
    status_line = f"  Weekly retrain done in {total_elapsed:.1f}s"
    if errors:
        status_line += f"  .  {len(errors)} error(s)"
    print(status_line)
    print(f"{'=' * 60}\n")

    if not dry_run:
        try:
            _append_log(summary)
        except Exception:
            pass

    return summary


# -- CLI -----------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ScorPred weekly retrain pipeline")
    p.add_argument("--dry-run", action="store_true", help="Run without writing any artifacts")
    p.add_argument("--skip-dataset", action="store_true", help="Skip dataset preparation step")
    p.add_argument("--skip-train", action="store_true", help="Skip model retraining step")
    p.add_argument("--skip-backtest", action="store_true", help="Skip walk-forward backtest step")
    p.add_argument("--folds", type=int, default=5, help="Number of walk-forward folds (default: 5)")
    p.add_argument("--random-state", type=int, default=42, help="Random seed")
    p.add_argument("--skip-policy-optimize", action="store_true", help="Skip policy optimisation step in daily refresh")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    summary = run_weekly_retrain(
        dry_run=args.dry_run,
        skip_dataset=args.skip_dataset,
        skip_train=args.skip_train,
        skip_backtest=args.skip_backtest,
        n_folds=args.folds,
        random_state=args.random_state,
        skip_policy_optimize=args.skip_policy_optimize,
    )
    return 1 if summary["error_count"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
