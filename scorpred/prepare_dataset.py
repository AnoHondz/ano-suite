
"""Validate retained soccer CSVs into a new version; never mutate originals."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

DATA_PATH = "data/raw"
OUT_PATH = "data/processed/matches_stage1.csv"
feature_columns = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR",
                   "HS", "AS", "HST", "AST", "HF", "AF", "HC", "AC", "HY", "AY", "HR", "AR"]


def prepare(source=DATA_PATH, output=OUT_PATH):
    output = Path(output)
    manifest_path = output.with_suffix(".sources.json")
    if output.exists() or manifest_path.exists():
        raise ValueError("Use a new versioned output path; existing datasets are preserved")
    accepted, sources, rejected = [], [], []
    for path in sorted(Path(source).glob("*.csv")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        match = re.fullmatch(r"E0_(\d{4})\.csv", path.name)
        if not match:
            raise ValueError("Unsupported source identity: " + path.name)
        year = int(match[1])
        frame = pd.read_csv(path)
        required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}
        if not required <= set(frame.columns):
            raise ValueError("Missing required fixture fields: " + path.name)
        dates = pd.to_datetime(frame["Date"], format="mixed", dayfirst=True, errors="raise")
        end = pd.Timestamp(2020, 8, 1) if year == 2019 else pd.Timestamp(year + 1, 7, 1)
        if not ((dates >= pd.Timestamp(year, 8, 1)) & (dates < end)).all():
            rejected.append({"file": str(path), "sha256": digest, "rows": len(frame), "reason": "Date outside filename season; quarantined by manifest, original retained"})
            continue
        for key in ("FTHG", "FTAG"):
            values = pd.to_numeric(frame[key], errors="raise")
            if not ((values >= 0) & (values % 1 == 0)).all():
                raise ValueError("Invalid result counts: " + path.name)
        results = ["H" if h > a else "A" if a > h else "D" for h, a in zip(frame.FTHG, frame.FTAG, strict=True)]
        if list(frame.FTR) != results or (frame.HomeTeam == frame.AwayTeam).any():
            raise ValueError("Inconsistent result or team identity: " + path.name)
        frame = frame[[c for c in feature_columns if c in frame.columns]].copy()
        frame["Date"] = dates.dt.strftime("%Y-%m-%d")
        frame["Season"] = year
        frame["League"] = "E0"
        accepted.append(frame)
        sources.append({"file": str(path), "sha256": digest, "rows": len(frame)})
    if not accepted:
        raise ValueError("No validated fixtures")
    combined = pd.concat(accepted, ignore_index=True)
    if combined.duplicated(["Date", "HomeTeam", "AwayTeam"]).any():
        raise ValueError("Duplicate fixture identity; no output written")
    manifest = {"issue_id": "SOC-001", "accepted": sources, "quarantined": rejected,
                "rows": len(combined), "duplicate_excess": 0, "original_data_modified": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="") as handle:
        combined.to_csv(handle, index=False)
    manifest["output_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    with manifest_path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DATA_PATH)
    parser.add_argument("--output", default=OUT_PATH)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output), indent=2))
