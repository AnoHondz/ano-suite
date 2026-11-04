#!/usr/bin/env python3
"""Calibration reliability diagram for ScorPred soccer predictions.

Reads finalized prediction tracking data and prints a reliability table
grouped by confidence bucket, plus an Expected Calibration Error (ECE).

Usage:
    python calibration_check.py [--tracking-path PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from runtime_paths import prediction_tracking_path

MIN_COMPLETED = 30

# Confidence buckets: (label, low_inclusive, high_exclusive)
BUCKETS: list[tuple[str, float, float]] = [
    ("50-60%",  0.50, 0.60),
    ("60-70%",  0.60, 0.70),
    ("70-80%",  0.70, 0.80),
    ("80-90%",  0.80, 0.90),
    ("90-100%", 0.90, 1.01),
]


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def load_completed_predictions(tracking_path: Path) -> list[dict]:
    """Return predictions that have a non-None is_correct field."""
    if not tracking_path.exists():
        return []
    try:
        data = json.loads(tracking_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[warn] Could not parse tracking file: {exc}", file=sys.stderr)
        return []

    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("predictions") or []
    else:
        return []

    completed = [
        r for r in rows
        if isinstance(r, dict) and r.get("is_correct") is not None
    ]
    return completed


def reliability_table(completed: list[dict]) -> list[dict]:
    """Group completed predictions into confidence buckets.

    Each prediction must have:
        confidence_pct  (float, 0–100 scale) OR confidence (float, 0–1 scale)
        is_correct      (bool / 0 / 1)
    """
    total = len(completed)
    bucket_data: list[dict] = []

    for label, lo, hi in BUCKETS:
        members = []
        for pred in completed:
            raw = pred.get("confidence_pct")
            if raw is None:
                # Try 0-1 scale field
                raw = pred.get("confidence")
                if raw is not None:
                    raw = _safe_float(raw, 0.0) * 100.0
            conf = _safe_float(raw, 0.0)
            # Normalise: values stored as 0-1 fraction need * 100
            if 0.0 < conf <= 1.0:
                conf *= 100.0
            if lo * 100 <= conf < hi * 100:
                members.append((conf, bool(pred.get("is_correct"))))

        if not members:
            bucket_data.append({
                "bucket": label,
                "count": 0,
                "fraction": 0.0,
                "avg_confidence": 0.0,
                "actual_accuracy": 0.0,
                "calibration_gap": 0.0,
            })
            continue

        avg_conf = sum(c for c, _ in members) / len(members) / 100.0
        actual_acc = sum(1 for _, ok in members if ok) / len(members)
        bucket_data.append({
            "bucket": label,
            "count": len(members),
            "fraction": round(len(members) / total, 4) if total else 0.0,
            "avg_confidence": round(avg_conf, 4),
            "actual_accuracy": round(actual_acc, 4),
            "calibration_gap": round(avg_conf - actual_acc, 4),
        })

    return bucket_data


def compute_ece(bucket_data: list[dict]) -> float:
    """ECE = sum over buckets of (bucket_fraction * |avg_confidence - actual_accuracy|)."""
    return round(
        sum(
            b["fraction"] * abs(b["avg_confidence"] - b["actual_accuracy"])
            for b in bucket_data
            if b["count"] > 0
        ),
        6,
    )


def print_reliability_diagram(bucket_data: list[dict], ece: float, total: int) -> None:
    print(f"\nCalibration Reliability Diagram  (n={total} completed predictions)\n")
    header = f"{'Bucket':<12}  {'Count':>6}  {'Avg Conf':>9}  {'Actual Acc':>10}  {'Gap':>8}"
    print(header)
    print("-" * len(header))
    for b in bucket_data:
        if b["count"] == 0:
            print(f"  {b['bucket']:<10}  {'0':>6}  {'—':>9}  {'—':>10}  {'—':>8}")
        else:
            print(
                f"  {b['bucket']:<10}  {b['count']:>6}  "
                f"{b['avg_confidence'] * 100:>8.1f}%  "
                f"{b['actual_accuracy'] * 100:>9.1f}%  "
                f"{b['calibration_gap'] * 100:>+7.1f}%"
            )
    print("-" * len(header))
    print(f"\nExpected Calibration Error (ECE): {ece * 100:.3f}%")
    if ece < 0.02:
        print("  [excellent] ECE < 2% — model is well-calibrated.")
    elif ece < 0.05:
        print("  [good]      ECE < 5% — minor calibration drift.")
    else:
        print("  [warn]      ECE >= 5% — consider retraining or recalibration.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print calibration reliability diagram from prediction tracking data."
    )
    parser.add_argument(
        "--tracking-path",
        default=None,
        help="Override path to prediction_tracking.json",
    )
    args = parser.parse_args(argv)

    tracking = Path(args.tracking_path) if args.tracking_path else prediction_tracking_path()

    if not tracking.exists():
        print(
            f"[warn] Tracking file not found: {tracking}\n"
            "Run result_updater.py to populate prediction outcomes.",
            file=sys.stderr,
        )
        return 0

    completed = load_completed_predictions(tracking)

    if len(completed) < MIN_COMPLETED:
        print(
            f"[warn] Only {len(completed)} completed predictions found "
            f"(minimum {MIN_COMPLETED} required for a meaningful reliability diagram).\n"
            "Collect more finalized results before running this check.",
            file=sys.stderr,
        )
        return 0

    bucket_data = reliability_table(completed)
    ece = compute_ece(bucket_data)
    print_reliability_diagram(bucket_data, ece, len(completed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
