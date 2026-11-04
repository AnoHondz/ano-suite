"""Build B1 research metadata offline: python -m scripts.reconcile_nba_history.

No --download, --enable, --sync or training options exist. Input files are read
only; output is a separate owned research directory, using existing atomic IO.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

from runtime_paths import cache_dir, repo_root
from services.nba_historical_reconciliation import (
    candidate_manifests, fingerprint, local_box_coverage, local_history, read_json, reconcile,
)
from services.nba_provider_registry import REGISTRY_PATH, load_registry
from utils.atomic import atomic_write_json

DEFAULT_OUTPUT = repo_root() / "docs/assets/nba_registry"
EVIDENCE = repo_root() / "docs/assets/github_data_research"
AUDIT = repo_root() / "cache/diagnostics/2026-10-20_multisport/nba_archive_gaps.json"


def build_bundle(registry_path: Path, monthly: Path, live: Path, evidence: Path, audit: Path) -> dict:
    registry = load_registry(registry_path)
    registry_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    history = local_history(monthly)
    boxes = local_box_coverage(live, history["games"])
    candidates = candidate_manifests(registry, evidence)
    prior, audit_sha = read_json(audit) if audit.exists() else (None, None)
    reconciliation = reconcile(registry, history, candidates, boxes, prior)
    inputs = {str(registry_path.resolve()): registry_sha}
    for directory, records in ((monthly, history["source_files"]), (live, boxes["source_files"]),
                               (evidence, candidates["source_files"])):
        for record in records:
            if record["sha256"]:
                inputs[str((directory / record["file"]).resolve())] = record["sha256"]
    if audit_sha:
        inputs[str(audit.resolve())] = audit_sha
    # Detect concurrent updates; never overwrite or relabel source timestamps.
    for name, sha in inputs.items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != sha:
            raise ValueError("An NBA metadata input changed during reconciliation; retry offline snapshot")
    files = {
        "provider_registry.json": registry,
        "candidate_manifests.json": candidates,
        "reconciliation.json": reconciliation,
        "permission_matrix.json": {"schema_version": 1, "providers": [
            {"provider_id": p["provider_id"], "repository_url": p["repository_url"],
             "license": p["license"], "permission": p["permission"]} for p in registry["providers"]]},
        "local_game_index.json": {"namespace": "espn", "eligibility_version": "existing-nba-games-v1",
                                  "games": history["games"]},
        "input_manifest.json": {"monthly": history["source_files"], "live_cache": boxes["source_files"],
                                "retained_evidence": candidates["source_files"], "registry_sha256": registry_sha,
                                "prior_audit_sha256": audit_sha, "source_hashes_unchanged_during_read": True},
    }
    files["bundle.json"] = {"owner": "scorpred_nba_b1_metadata", "schema_version": 1,
                            "content_sha256": fingerprint(files), "metadata_only": True,
                            "asset_downloads": 0, "sports_imports": 0,
                            "artifact_fingerprints": {name: fingerprint(value) for name, value in files.items()}}
    return files


def write_bundle(output: Path, files: dict, input_directories: tuple[Path, ...]) -> None:
    target = output.resolve()
    protected = (*input_directories, repo_root() / "data", repo_root() / "services",
                 repo_root() / "scripts", repo_root() / "frontend", cache_dir("nflverse"))
    for directory in protected:
        directory = directory.resolve()
        if target.is_relative_to(directory) or directory.is_relative_to(target):
            raise ValueError("Research output cannot overlap source/protected directories")
    if target.exists() and any(target.iterdir()):
        marker = target / "bundle.json"
        if not marker.is_file() or json.loads(marker.read_text()).get("owner") != "scorpred_nba_b1_metadata":
            raise ValueError("Refusing to overwrite an unowned output directory")
    # No clock-derived fields: identical sources produce byte-identical output.
    for name, value in files.items():
        atomic_write_json(target / name, value)
    text = io.StringIO(newline="")
    rows = files["reconciliation.json"]["reference_seasons"]
    writer = csv.writer(text)
    writer.writerow(("ending_season", "games", "first_tipoff", "last_tipoff", "summary_games",
                     "paired_team_box_games", "paired_player_box_games", "paired_player_minute_games"))
    for row in rows:
        coverage = row["local_cache_coverage"]
        writer.writerow((row["season"], row["games"], row["first_tipoff"], row["last_tipoff"],
                         coverage["summary"], coverage["paired_team_boxes"], coverage["paired_player_boxes"],
                         coverage["paired_player_minutes"]))
    (target / "season_coverage.csv").write_text(text.getvalue(), encoding="utf-8", newline="")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    parser.add_argument("--monthly-dir", type=Path, default=cache_dir("score_models", "nba_raw"))
    parser.add_argument("--live-cache-dir", type=Path, default=cache_dir("nba_public"))
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE)
    parser.add_argument("--prior-audit", type=Path, default=AUDIT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    files = build_bundle(args.registry, args.monthly_dir, args.live_cache_dir, args.evidence_dir, args.prior_audit)
    write_bundle(args.output_dir, files, (args.monthly_dir, args.live_cache_dir, args.evidence_dir))
    print(json.dumps({"reference_games": files["reconciliation.json"]["reference_games"],
                      "providers_disabled": len(files["provider_registry.json"]["providers"]),
                      "third_party_sports_downloads": 0, "output": str(args.output_dir),
                      "content_sha256": files["bundle.json"]["content_sha256"]}))


if __name__ == "__main__":
    main()
