"""Validate Phase 3 evidence, exports, links and unchanged protected files."""
import csv
import hashlib
import json
import re
from datetime import UTC, datetime

from scripts.build_github_research_exports import WEIGHTS
from scripts.research_github_sources import OUT, ROOT

DOCS = ["SCORPRED_GITHUB_DATA_RESEARCH.md", "NFL_GITHUB_DATA_SOURCES.md",
        "NBA_GITHUB_DATA_SOURCES.md", "SOCCER_GITHUB_DATA_SOURCES.md",
        "SCORPRED_DATA_INTEGRATION_ARCHITECTURE.md", "SCORPRED_GITHUB_INTEGRATION_ROADMAP.md"]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def walk(value):
    if isinstance(value, dict):
        if "text" in value and "sha256" in value:
            assert hashlib.sha256(value["text"].encode()).hexdigest() == value["sha256"], value.get("url")
        for sub in value.values():
            walk(sub)
    elif isinstance(value, list):
        for sub in value:
            walk(sub)


def main():
    source = load(OUT / "sources.json")
    gaps = load(OUT / "gaps.json")
    rows = source["sources"]
    required = ["repository", "url", "maintainer", "sport", "datasets", "seasons_and_competitions",
        "latest_verified_dataset_update", "latest_verified_code_maintenance", "formats", "access_method",
        "license_and_permitted_use", "automation_restrictions", "rate_limits", "historical_availability",
        "pregame_timestamp_reliability", "missing_data_limitations", "compatibility", "integration_complexity", "expected_improvement"]
    assert len(rows) == 27 and len({r["repository"] for r in rows}) == len(rows)
    assert sum(r["accessible"] for r in rows) == source["accessible_repositories"] == 25
    assert sum(r["verified_suitable_bounded_data_source"] for r in rows) == 7
    assert not any(r["unconditional_production_recommendation"] for r in rows)
    for row in rows:
        assert all(k in row and row[k] != "" for k in required), row["repository"]
        assert set(row["score_components"]) == set(WEIGHTS)
        assert all(0 <= s <= 5 for s in row["score_components"].values())
        score = round(sum(WEIGHTS[k] * row["score_components"][k] / 5 for k in WEIGHTS), 1)
        assert score == row["score_100"]
        assert row["net_new_usable_records"] is None
    with (OUT / "sources.csv").open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == len(rows)
    with (OUT / "gaps.csv").open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == len(gaps["gaps"]) == 39
    assert all(r["classification"] in gaps["classifications"] and r["net_new_records"] is None for r in gaps["gaps"])
    for name in ("repository_evidence.json", "supplemental_evidence.json", "technical_evidence.json", "additional_candidates_evidence.json"):
        walk(load(OUT / name))
    local = load(OUT / "existing_inventory.json")
    assert local["nfl_database_read_only"] and local["holdout_outcomes_read"] == 0 and local["holdout_games"] == 109
    before = load(OUT / "preservation_before.json")
    changed = [name for name, sha in before["files"].items() if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha]
    assert not changed, changed
    frozen = load(ROOT / "docs/assets/nfl_stage1/preservation_artifacts.json")
    assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == sha for name, sha in frozen["files"].items())
    missing = []
    for name in DOCS:
        path = ROOT / "docs" / name
        text = path.read_text(encoding="utf-8")
        assert len(text) > 3000 and "<!-- GAP_MATRIX -->" not in text
        for link in re.findall(r"\]\(([^)]+)\)", text):
            if not link.startswith(("http:", "https:", "#")) and not (path.parent / link.split("#")[0]).exists():
                missing.append((name, link))
    assert not missing, missing
    preserved = {"verified_at": datetime.now(UTC).isoformat(), "files_checked": len(before["files"]),
        "changed_files": changed, "frozen_export_hashes_unchanged": True, "database_writes_by_research": 0,
        "sports_records_imported": 0, "sealed_outcomes_read": 0}
    (OUT / "preservation_after.json").write_text(json.dumps(preserved, indent=2), encoding="utf-8")
    result = {"verified_at": datetime.now(UTC).isoformat(), "candidate_urls": 27,
        "accessible_repositories": 25, "bounded_suitable_data_repositories": 7, "gap_rows": 39,
        "source_scores_and_classifications": "passed", "response_hashes": "passed",
        "required_source_fields": "passed", "local_report_links": "passed", "preservation": preserved,
        "application_tests": "not rerun for research-only changes; preceding launch735backend/25browser/build evidence is distinct"}
    (OUT / "validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    files = [p for p in OUT.iterdir() if p.is_file() and p.name != "evidence_manifest.json"]
    files += [ROOT / "docs" / name for name in DOCS]
    files += [ROOT / "scripts" / name for name in ("research_github_sources.py", "research_github_details.py", "research_local_inventory.py",
        "build_github_research_exports.py", "build_github_gap_exports.py", "render_github_source_profiles.py", "verify_github_research.py")]
    (OUT / "evidence_manifest.json").write_text(json.dumps({"generated_at": datetime.now(UTC).isoformat(),
        "files": {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
