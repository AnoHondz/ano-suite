#!/usr/bin/env python3
"""
Offline learning script: analyse past prediction mistakes and generate
bounded policy adjustments for ScorMastermind.

Usage:
    python learn_from_mistakes.py [--limit N] [--dry-run]

Outputs:
    data/analysis/mistake_report.json
    data/analysis/policy_adjustments.json  (unless --dry-run)
"""

from __future__ import annotations

import argparse
import json
import sys

import model_tracker as mt
import mistake_analysis as ma
from runtime_paths import ensure_runtime_dirs


def main() -> None:
    parser = argparse.ArgumentParser(description="Learn from prediction mistakes")
    parser.add_argument("--limit", type=int, default=200, help="Max completed predictions to analyse")
    parser.add_argument("--dry-run", action="store_true", help="Print report without saving adjustments")
    args = parser.parse_args()

    ensure_runtime_dirs()

    # 1. Gather completed (graded) predictions
    completed = mt.get_completed_predictions(limit=args.limit)
    if not completed:
        print("No completed predictions found. Nothing to analyse.")
        sys.exit(0)

    print(f"Analysing {len(completed)} completed predictions...")

    # 2. Build mistake report
    report = ma.build_mistake_report(completed)
    ma.save_report(report)

    print(f"\n{'─' * 60}")
    print(f"Accuracy: {report['accuracy_pct']}% ({report['total_correct']}/{report['total_analysed']})")
    print(f"Wrong predictions: {report['total_wrong']}")
    print(f"{'─' * 60}")

    for cat, data in report["categories"].items():
        if data["count"] > 0:
            print(f"  {cat}: {data['count']} ({data['rate']}%)")

    # 3. Propose adjustments
    adj_doc = ma.propose_adjustments(report)

    print(f"\n{'─' * 60}")
    print("Proposed adjustments:")
    for note in adj_doc["reasoning"]:
        print(f"  • {note}")

    for sport, deltas in adj_doc["adjustments"].items():
        if deltas:
            print(f"\n  [{sport}]")
            for key, val in deltas.items():
                print(f"    {key}: +{val:+.1f}")

    # 4. Save (unless dry-run)
    if args.dry_run:
        print("\n[dry-run] Adjustments NOT saved.")
    else:
        ma.save_adjustments(adj_doc)
        print(f"\nAdjustments saved. ScorMastermind will pick them up on next prediction.")


if __name__ == "__main__":
    main()
