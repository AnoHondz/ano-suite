#!/usr/bin/env python3
"""ScorPred Daily Offline Refresh Pipeline.

Runs every day (ideally at 02:00 UTC) without touching the live Flask app.
Steps that require live prediction tracking data are skipped gracefully when
that data is unavailable (e.g. in GitHub Actions CI environments).

Usage:
    python daily_refresh.py [--dry-run] [--skip-results] [--skip-learn]
                            [--skip-policy] [--skip-report]

Artifacts written (when data permits):
    cache/prediction_tracking.json  (result grading)
    data/analysis/mistake_report.json
    data/analysis/policy_adjustments.json
    cache/ml/prediction_policy.json
    cache/ml/model_comparison.json
    data/logs/daily_refresh.jsonl  (run log)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Ensure the repo root is importable regardless of working directory
_REPO = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from runtime_paths import (
    clean_soccer_dataset_path,
    clean_soccer_model_path,
    ensemble_soccer_model_path,
    ensure_runtime_dirs,
    ml_report_path,
    prediction_tracking_path,
)

_LOG_PATH = _REPO / "data" / "logs" / "daily_refresh.jsonl"


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


# -- pipeline steps ------------------------------------------------------------

def step_update_results(dry_run: bool = False) -> dict:
    """Fetch completed game results and grade pending predictions.

    Calls result_updater.update_pending_predictions() so that predictions
    transition from 'pending' to 'completed' with their actual outcomes.
    Must run *before* learn/optimize steps so they work on fresh data.

    Requires cache/prediction_tracking.json with pending predictions.
    Skipped gracefully when the tracking file is absent (CI environments).
    """
    result: dict = {"step": "update_results", "status": "ok", "details": {}}
    start = time.monotonic()

    tracking = prediction_tracking_path()
    if not tracking.exists():
        result["status"] = "skipped"
        result["details"]["reason"] = "prediction_tracking.json not found (expected in CI)"
        return result

    try:
        import result_updater

        if not dry_run:
            stats = result_updater.update_pending_predictions()
            result["details"]["checked"] = stats.get("checked", 0)
            result["details"]["found"] = stats.get("found", 0)
            result["details"]["updated"] = stats.get("updated", 0)
            result["details"]["failed"] = stats.get("failed", 0)
            if stats.get("errors"):
                result["details"]["sample_errors"] = stats["errors"][:5]
        else:
            result["details"]["dry_run"] = True
    except Exception:
        result["status"] = "error"
        result["details"]["traceback"] = traceback.format_exc()

    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


def step_learn_from_mistakes(dry_run: bool = False) -> dict:
    """Learn from graded picks: the validated recalibration of published probabilities.

    services.recalibration switches a correction on only when it improves held-out picks.
    The older mistake report (it adjusts the legacy ScorMastermind pages only) still runs.
    Requires cache/prediction_tracking.json to have graded predictions.
    Skipped gracefully when the tracking file is absent (CI environments).
    """
    result: dict = {"step": "learn_from_mistakes", "status": "ok", "details": {}}
    start = time.monotonic()

    tracking = prediction_tracking_path()
    if not tracking.exists():
        result["status"] = "skipped"
        result["details"]["reason"] = "prediction_tracking.json not found (expected in CI)"
        return result

    try:
        import model_tracker as mt
        import mistake_analysis as ma

        if not dry_run:
            from services import recalibration
            learned = recalibration.learn_all()
            result["details"]["recalibration"] = {sport: {k: record.get(k) for k in ("active", "picks", "reason")}
                                                  for sport, record in learned.items()}

        completed = mt.get_completed_predictions(limit=500)
        if not completed:
            result["status"] = "skipped"
            result["details"]["reason"] = "no_completed_predictions"
            return result

        report = ma.build_mistake_report(completed)
        result["details"]["total_analysed"] = report["total_analysed"]
        result["details"]["accuracy_pct"] = report["accuracy_pct"]
        result["details"]["total_wrong"] = report["total_wrong"]

        if not dry_run:
            ma.save_report(report)
            adj_doc = ma.propose_adjustments(report)
            ma.save_adjustments(adj_doc)
            result["details"]["adjustments_saved"] = True
    except Exception:
        result["status"] = "error"
        result["details"]["traceback"] = traceback.format_exc()

    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


def step_optimize_policy(dry_run: bool = False) -> dict:
    """Grid-search for optimal BET/LEAN/AVOID thresholds from tracked outcomes.

    Requires graded predictions in prediction_tracking.json.
    Skipped when the tracking file is absent.
    """
    result: dict = {"step": "optimize_policy", "status": "ok", "details": {}}
    start = time.monotonic()

    tracking = prediction_tracking_path()
    if not tracking.exists():
        result["status"] = "skipped"
        result["details"]["reason"] = "prediction_tracking.json not found (expected in CI)"
        return result

    try:
        import optimize_prediction_policy as opt
        import prediction_policy as ps

        payload = opt.build_tuned_policy()
        sports = list(payload.get("sports", {}).keys())
        result["details"]["sports"] = sports
        result["details"]["sample_size"] = payload.get("metadata", {}).get("sample_size", 0)

        if not dry_run:
            # Guard: only save when the new policy scores at least as well as the current one.
            new_score = (
                payload
                .get("metadata", {})
                .get("sports", {})
                .get("soccer", {})
                .get("metrics", {})
                .get("score", None)
            )

            current_policy = ps.load_policy()
            current_score = (
                current_policy
                .get("metadata", {})
                .get("sports", {})
                .get("soccer", {})
                .get("metrics", {})
                .get("score", None)
            ) if current_policy else None

            # Save when: (a) no current policy, (b) current has no score, or (c) new >= current
            policy_is_default = current_policy is None or current_score is None
            should_save = policy_is_default or (
                new_score is not None and new_score >= current_score
            )

            if should_save:
                ps.save_policy(payload)
                result["details"]["policy_saved"] = True
                result["details"]["policy_improved"] = True
            else:
                result["details"]["policy_saved"] = False
                result["details"]["policy_improved"] = False
                result["details"]["new_score"] = new_score
                result["details"]["current_score"] = current_score
        else:
            result["details"]["dry_run"] = True
    except Exception:
        result["status"] = "error"
        result["details"]["traceback"] = traceback.format_exc()

    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


def step_generate_ml_report(dry_run: bool = False) -> dict:
    """Regenerate the Strategy Lab model-comparison report.

    Requires the clean processed dataset and a trained model on disk.
    Skipped when either is missing.
    """
    result: dict = {"step": "generate_ml_report", "status": "ok", "details": {}}
    start = time.monotonic()

    dataset = clean_soccer_dataset_path()
    model_path = (
        ensemble_soccer_model_path()
        if ensemble_soccer_model_path().exists()
        else clean_soccer_model_path()
    )

    if not dataset.exists():
        result["status"] = "skipped"
        result["details"]["reason"] = "processed dataset not found"
        return result

    if not model_path.exists():
        result["status"] = "skipped"
        result["details"]["reason"] = "trained model not found -- run weekly_retrain.py first"
        return result

    try:
        import generate_ml_report as gen

        if not dry_run:
            out = gen.generate_clean_soccer_report(
                dataset_path=dataset,
                model_path=model_path,
                output=ml_report_path(),
            )
            result["details"]["report_path"] = str(out)
        else:
            result["details"]["dry_run"] = True
    except Exception:
        result["status"] = "error"
        result["details"]["traceback"] = traceback.format_exc()

    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


# -- orchestrator --------------------------------------------------------------

def run_daily_refresh(
    dry_run: bool = False,
    skip_results: bool = False,
    skip_learn: bool = False,
    skip_policy: bool = False,
    skip_report: bool = False,
) -> dict:
    """Run all daily refresh tasks and return a summary dict."""
    ensure_runtime_dirs()
    run_start = time.monotonic()
    timestamp = _utcnow()
    steps: list[dict] = []

    print(f"\n{'=' * 60}")
    print(f"  ScorPred  |  Daily Refresh  |  {timestamp}")
    if dry_run:
        print("  [DRY RUN -- no artifacts will be written]")
    print(f"{'=' * 60}")

    # Step 1 -- Update results (grade pending predictions)
    label = "[1/4] Update results"
    if not skip_results:
        _banner(label)
        r = step_update_results(dry_run=dry_run)
        steps.append(r)
        _print_step(r)
    else:
        steps.append({"step": "update_results", "status": "skipped", "details": {"reason": "--skip-results flag"}})

    # Step 2 -- Learn from mistakes
    label = "[2/4] Learn from mistakes"
    if not skip_learn:
        _banner(label)
        r = step_learn_from_mistakes(dry_run=dry_run)
        steps.append(r)
        _print_step(r)
    else:
        steps.append({"step": "learn_from_mistakes", "status": "skipped", "details": {"reason": "--skip-learn flag"}})

    # Step 3 -- Optimize prediction policy
    label = "[3/4] Optimize prediction policy"
    if not skip_policy:
        _banner(label)
        r = step_optimize_policy(dry_run=dry_run)
        steps.append(r)
        _print_step(r)
    else:
        steps.append({"step": "optimize_policy", "status": "skipped", "details": {"reason": "--skip-policy flag"}})

    # Step 4 -- Generate ML comparison report
    label = "[4/4] Generate ML report"
    if not skip_report:
        _banner(label)
        r = step_generate_ml_report(dry_run=dry_run)
        steps.append(r)
        _print_step(r)
    else:
        steps.append({"step": "generate_ml_report", "status": "skipped", "details": {"reason": "--skip-report flag"}})

    errors = [s for s in steps if s.get("status") == "error"]
    total_elapsed = round(time.monotonic() - run_start, 2)

    summary = {
        "run_at": timestamp,
        "pipeline": "daily_refresh",
        "dry_run": dry_run,
        "total_elapsed_s": total_elapsed,
        "steps": steps,
        "error_count": len(errors),
    }

    print(f"\n{'=' * 60}")
    status_line = f"  Done in {total_elapsed:.1f}s"
    if errors:
        status_line += f"  |  {len(errors)} error(s)"
    print(status_line)
    print(f"{'=' * 60}\n")

    if not dry_run:
        try:
            _append_log(summary)
        except Exception:
            pass  # Never let logging kill the pipeline

    return summary


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


# -- CLI -----------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ScorPred daily offline refresh pipeline")
    p.add_argument("--dry-run", action="store_true", help="Run without writing any artifacts")
    p.add_argument("--skip-results", action="store_true", help="Skip update_results step")
    p.add_argument("--skip-learn", action="store_true", help="Skip learn_from_mistakes step")
    p.add_argument("--skip-policy", action="store_true", help="Skip optimize_policy step")
    p.add_argument("--skip-report", action="store_true", help="Skip generate_ml_report step")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    summary = run_daily_refresh(
        dry_run=args.dry_run,
        skip_results=args.skip_results,
        skip_learn=args.skip_learn,
        skip_policy=args.skip_policy,
        skip_report=args.skip_report,
    )
    return 1 if summary["error_count"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
