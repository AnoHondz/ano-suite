"""Offline fixed-input predictions, using existing production functions/artifact.

No fitting, app startup, provider calls or tracking writes. Run in a dedicated
SCORPRED_DATA_ROOT so optional performance history cannot read live tracking.
"""
import argparse
import csv
from datetime import UTC, datetime
import json
from pathlib import Path
import socket


def capture():
    def offline(*args, **kwargs):
        raise AssertionError("Network prohibited in production preservation probe")
    socket.create_connection = offline
    socket.socket.connect = offline
    import ml_service
    import scorpred_engine as engine
    from runtime_paths import repo_root
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 22, 12, tzinfo=UTC)
    engine.datetime = Clock
    root = repo_root()
    with (root / "data/processed/soccer_training_data_clean.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    classifier = []
    for index in (0, len(rows) // 2, len(rows) - 1):
        result = ml_service.predict_match(rows[index], model_path=root / "data/models/soccer_random_forest_clean.pkl")
        if not result.get("available"):
            raise ValueError("Existing model must be available for preservation check")
        classifier.append({"feature_csv_line": index + 2, "output": result})
    rules = []
    for left, right in ((2, 0), (0, 2), (1, 1)):
        def form(gf, ga):
            return [{"result": "W" if gf > ga else "L" if gf < ga else "D", "gf": gf, "ga": ga,
                     "date": "2026-10-20", "home": True, "opponent": "Opponent"}] * 5
        result = engine.scorpred_predict(form(left, right), form(right, left), [], [], [], [], True,
                                        "Home", "Away", "soccer", {"opponent": 5.0})
        rules.append(result)
    return {"method": "fixed clock, isolated empty tracking, three saved feature vectors and three rule contexts",
            "classifier": classifier, "primary_rule_engine": rules}


def canonical_snapshot(value):
    """Compare the actual JSON output contract, including integer label keys."""
    return json.loads(json.dumps(value, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    value = canonical_snapshot(capture())
    if args.compare and json.loads(args.compare.read_text(encoding="utf-8")) != value:
        raise ValueError("Production prediction preservation discrepancy; stop")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    print("Six fixed-input production prediction outputs captured" + (" and unchanged" if args.compare else ""))


if __name__ == "__main__":
    main()
