"""Offline, versioned soccer sidecar/views; no download, training or activation.

python -m scripts.build_soccer_research --audit-timestamp <actual audit UTC>
Existing bundle versions may be replayed only byte-for-byte, never overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

from runtime_paths import repo_root
from services.soccer_lineage import VERSION as LINEAGE_VERSION, build_sidecar, canonical_duplicates, fingerprint
from services.soccer_research_views import VERSION as VIEW_VERSION, ResearchViews
from services.soccer_stat_contracts import VERSION as STAT_VERSION, source_row

ROOT = repo_root()
DEFAULT_OUTPUT = ROOT / "docs/assets/soccer_foundation/v1"
EVIDENCE = ROOT / "docs/assets/rights_soccer"


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build(audit_timestamp, *, root=ROOT, evidence=EVIDENCE):
    pinned = json.loads((evidence / "evidence_manifest.json").read_text(encoding="utf-8"))["files"]
    inputs = {}
    def read(path):
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        name = path.relative_to(root).as_posix()
        if name in pinned and pinned[name] != sha:
            raise ValueError("Diagnostic evidence fingerprint mismatch; stop")
        inputs[name] = sha
        return json.loads(data)
    audit = read(evidence / "soccer_lineage.json")
    duplicates = read(evidence / "soccer_duplicates.json")
    old_inputs = read(evidence / "soccer_input_manifest.json")["files"]
    git = read(evidence / "git_lineage_evidence.json")
    contracts = read(evidence / "soccer_stat_contracts.json")
    fingerprints = dict(old_inputs) | {r["locator"]: r["sha256"] for r in git["cached_sources"]}
    # Bind static originals, not a mutable cache's current response version.
    for name, expected in old_inputs.items():
        if name.startswith("data/"):
            raw = (root / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError("Original fixture/source preservation discrepancy; stop")
            inputs[name] = expected
    stage_manifest = read(root / "data/processed/matches_stage1.sources.json")
    originals = csv_rows(root / "data/historical_matches.csv")
    processed = csv_rows(root / "data/processed/matches.csv")
    stage1 = csv_rows(root / "data/processed/matches_stage1.csv")
    if stage_manifest["output_sha256"] != inputs["data/processed/matches_stage1.csv"]:
        raise ValueError("Accepted representative version changed; stop")
    accepted = {r["file"].replace("\\", "/"): r["sha256"] for r in stage_manifest["accepted"]}
    for name, expected in accepted.items():
        if inputs.get(name) != expected:
            raise ValueError("Accepted raw source fingerprint mismatch")
    raw_sources = {name: csv_rows(root / name) for name in accepted}
    # Raw CSV-only candidates are checked against their actual retained row,
    # not merely a file name or the manifest's asserted competition.
    for entry in audit["rows"]:
        original = originals[entry["csv_line"] - 2]
        for candidate in entry["raw_csv_candidates"]:
            if candidate["file"] not in raw_sources:
                raise ValueError("Raw candidate refers to an unaccepted or absent source")
            row = raw_sources[candidate["file"]][candidate["csv_line"] - 2]
            from scripts.plan_soccer_data_repairs import outcome_key
            if outcome_key(row) != outcome_key(original) or row.get("Div") != candidate["competition_code"]:
                raise ValueError("Raw candidate identity differs from retained source")
    lineage = build_sidecar(originals, audit, source_sha256=inputs["data/historical_matches.csv"],
                            audit_timestamp=audit_timestamp, evidence_fingerprints=fingerprints)
    duplicate_view = canonical_duplicates(processed, stage1, duplicates,
                        processed_sha256=inputs["data/processed/matches.csv"],
                        stage1_sha256=inputs["data/processed/matches_stage1.csv"])
    if duplicate_view["canonical_count"] != 2599 or duplicate_view["quarantine"]:
        raise ValueError("Canonical analytical representatives do not reconcile; stop")
    if len(duplicate_view["duplicate_groups"]) != 319:
        raise ValueError("Duplicate-group count changed; stop")
    definition = {"source": "retained_football_data_csv", "definition_id": "e0-source-counts-v1",
                  "verified": True, "unit": "count", "aggregation": "match", "period": "full_time_source",
                  "definition_scope": "explicit retained count columns only; provider card subtypes/advanced semantics not established"}
    corrected, e0, coverage = [], [], Counter()
    for fixture in duplicate_view["canonical_fixtures"]:
        reference = fixture["analytical_representative"]
        raw = stage1[reference["csv_line"] - 2]
        fixture_id = "soccer:e0:research:v1:" + fingerprint(reference)[:24]
        e0.append({"canonical_fixture_identifier": fixture_id, "duplicate_group_id": fixture["duplicate_group_id"],
                   "source_reference": reference, "fixture": fixture["fixture"]})
        for side in ("home", "away"):
            record = source_row(raw, side=side, definition=definition)
            corrected.append({"fixture_id": fixture_id, "side": side, "source_reference": reference,
                              "contract_version": STAT_VERSION, "definition_id": definition["definition_id"],
                              "values": record["values"], "transformations": record["transformations"],
                              "issues": record["issues"], "available_for_prediction": False})
            coverage.update(k for k, value in record["values"].items() if value is not None)
    internal = [{"canonical_fixture_identifier": r["canonical_fixture_identifier"], "source_reference": r["original_fixture_identifier"],
                 "lineage_status": r["status"], "fixture": r["fixture"]} for r in lineage["fixtures"]]
    quality = {"view_version": VIEW_VERSION, "audit_timestamp": audit_timestamp, "research_only": True,
               "historical_fixtures": len(originals), "lineage_counts": lineage["counts"],
               "historical_availability_verified": 0, "legacy_observations_preserved": len(processed),
               "legacy_analytical_fixtures": len(e0), "duplicate_groups": len(duplicate_view["duplicate_groups"]),
               "analytical_duplicate_excess": 0, "statistic_side_denominator": len(corrected),
               "known_statistic_counts": {field: coverage[field] for field in corrected[0]["values"]},
               "contract_issues_addressed_in_disabled_path": [i["issue_id"] for i in contracts["issues"]],
               "production_activation_approval_required": True, "production_enabled": False, "training_enabled": False,
               "limitations": ["Candidate fixture agreement is not original import lineage", "No original pregame receipts",
                               "No new data or provider definition grant", "Corrected semantics require separate production approval"]}
    artifacts = {"lineage_sidecar.json": lineage, "duplicate_view.json": duplicate_view,
                 "canonical_fixtures.json": {"view_version": VIEW_VERSION, "historical_reference": internal, "legacy_e0": e0},
                 "corrected_statistics.json": {"contract_version": STAT_VERSION, "research_only": True,
                                               "definitions": [definition], "observations": corrected},
                 "quality_report.json": quality, "input_manifest.json": {"files": inputs}}
    for name, expected in inputs.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Input changed during build; stop")
    artifacts["manifest.json"] = {"view_version": VIEW_VERSION, "lineage_version": LINEAGE_VERSION,
                                  "stat_contract_version": STAT_VERSION, "audit_timestamp": audit_timestamp,
                                  "production_enabled": False, "training_enabled": False,
                                  "artifact_fingerprints": {name: fingerprint(value) for name, value in artifacts.items()}}
    ResearchViews(artifacts, enabled=True)
    return artifacts


def write(artifacts, output):
    target = output.resolve()
    for directory in (ROOT / "data", ROOT / "cache", ROOT / "services", ROOT / "scripts", EVIDENCE):
        directory = directory.resolve()
        if target.is_relative_to(directory) or directory.is_relative_to(target):
            raise ValueError("Research output must be separate from preserved inputs")
    encoded = {name: (json.dumps(value, indent=2, allow_nan=False) + "\n").encode() for name, value in artifacts.items()}
    for name, data in encoded.items():
        path = target / name
        if path.exists() and path.read_bytes() != data:
            raise ValueError("Existing research version is immutable; choose a new output")
    target.mkdir(parents=True, exist_ok=True)
    for name, data in encoded.items():
        path = target / name
        if not path.exists():
            with path.open("xb") as handle:
                handle.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-timestamp", required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    artifacts = build(args.audit_timestamp)
    write(artifacts, args.output_dir)
    print(json.dumps(artifacts["quality_report.json"]))


if __name__ == "__main__":
    main()
