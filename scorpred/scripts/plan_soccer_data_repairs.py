"""Offline soccer duplicate/lineage inventory; never execute collection/training.

CSV row numbers are physical lines including the header. Candidate joins use
exact date, both oriented team names and both scores, never team names alone.
Candidate corroboration is not proof of the classifier's original import.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess

from runtime_paths import repo_root

ROOT = repo_root()
IMPORT_COMMIT = "c26606d50f1aedca63ec5da897f05701529b887c"
OUTPUT = ROOT / "docs/assets/rights_soccer"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def date_key(value: str) -> str:
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Unrecognized fixture date")


def fixture_key(row: dict) -> tuple:
    if "Date" in row:
        return date_key(row["Date"]), row["HomeTeam"].strip(), row["AwayTeam"].strip()
    return date_key(row["date"]), row["home_team"].strip(), row["away_team"].strip()


def outcome_key(row: dict) -> tuple:
    if "FTHG" in row:
        goals = row["FTHG"], row["FTAG"]
    else:
        goals = row["goals_scored"], row["goals_conceded"]
    return fixture_key(row) + tuple(int(v) for v in goals)


def payload(row: dict, columns: tuple[str, ...]) -> dict:
    return {k: date_key(row[k]) if k == "Date" else row.get(k, "") for k in columns}


def duplicate_inventory(processed: list[dict], raw: dict[str, list[dict]], classifier: list[dict],
                        features: list[dict], stage1: list[dict]) -> dict:
    groups, originals = defaultdict(list), defaultdict(list)
    columns = tuple(k for k in processed[0] if k != "Season")
    for index, row in enumerate(processed, 2):
        groups[fixture_key(row)].append((index, row))
    for name, rows in sorted(raw.items()):
        for index, row in enumerate(rows, 2):
            originals[digest(payload(row, columns))].append({"file": f"data/raw/{name}", "csv_line": index,
                                                            "competition_code": row.get("Div"), "source_match_id": None})
    classifier_lookup, feature_lookup, stage_lookup = defaultdict(list), defaultdict(list), defaultdict(list)
    for i, row in enumerate(classifier, 2):
        classifier_lookup[outcome_key(row)].append(i)
    for i, row in enumerate(features, 2):
        feature_lookup[fixture_key(row)].append(i)
    for i, row in enumerate(stage1, 2):
        stage_lookup[outcome_key(row)].append(i)
    issues = []
    for key, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        field_differences = sorted(k for k in members[0][1] if len({str(r.get(k)) for _, r in members}) > 1)
        identical_payload = len({digest(payload(r, columns)) for _, r in members}) == 1
        sources = originals[digest(payload(members[0][1], columns))]
        issues.append({"issue_id": "SOC-DUP-" + digest(key)[:12], "date": key[0], "home_team": key[1],
                       "away_team": key[2], "competition": {"namespace": "football_data_csv", "code": "E0",
                           "evidence": "Exact source-row payload matches with explicit Div=E0"}
                       if sources and all(s["competition_code"] == "E0" for s in sources) else None,
                       "season_candidate": 2025 if {r["Season"] for _, r in members} == {"E0_2018.csv", "E0_2025.csv"} else None,
                       "classification": "identical_observation_conflicting_season_labels" if identical_payload and field_differences == ["Season"]
                       else "exact_duplicate" if not field_differences else "conflicting_observations",
                       "different_fields": field_differences, "excess_rows": len(members) - 1,
                       "processed_members": [{"csv_line": i, "declared_source_identifier": r["Season"],
                                              "observation_sha256": digest(payload(r, columns))} for i, r in members],
                       "original_local_source_rows": sources, "original_provider_match_id": None,
                       "original_download_url": None, "source_family_claim": "football-data.co.uk CSV format; original receipt unavailable",
                       "classifier_exact_fixture_candidates": classifier_lookup[outcome_key(members[0][1])],
                       "derived_feature_exact_identity_candidates": feature_lookup[key],
                       "stage1_exact_fixture_rows": stage_lookup[outcome_key(members[0][1])],
                       "pipeline_references": {"legacy_processed_csv": True,
                           "current_preparation": "separate matches_stage1.csv; mislabeled E0_2018 quarantined",
                           "scoring_source": "E0_2025 eligible; E0_2018 excluded; per-target training inclusion not established",
                           "classifier_training": "historical_matches.csv, not legacy processed/matches.csv"}})
    return {"schema_version": 1, "source_rows": len(processed), "unique_fixtures": len(groups),
            "duplicate_groups": len(issues), "excess_rows": sum(i["excess_rows"] for i in issues),
            "classifications": dict(Counter(i["classification"] for i in issues)), "groups": issues,
            "records_deleted_or_merged": 0}


def git_evidence(root: Path) -> tuple[dict, list[tuple[str, dict]]]:
    def git(*args, binary=False):
        return subprocess.check_output(["git", "-C", str(root), *args], text=not binary)
    raw = git("show", f"{IMPORT_COMMIT}:data/historical_matches.csv", binary=True)
    history = git("log", "--follow", "--format=%H %aI %s", "--", "data/historical_matches.csv")
    paths = git("ls-tree", "-r", "--name-only", IMPORT_COMMIT, "--", "cache/football").splitlines()
    cached, sources = [], []
    for path in paths:
        if not path.endswith(".json"):
            continue
        body = git("show", f"{IMPORT_COMMIT}:{path}", binary=True)
        locator = f"git:{IMPORT_COMMIT}:{path}"
        cached.append((locator, json.loads(body)))
        sources.append({"locator": locator, "sha256": sha(body)})
    return {"import_commit": IMPORT_COMMIT, "history": history.splitlines(),
            "historical_csv_commit_sha256": sha(raw),
            "current_file_identical_to_commit": sha(raw) == sha((root / "data/historical_matches.csv").read_bytes()),
            "producer_script": "scripts/fetch_training_data.py; same commit documents ESPN and drops provider identities/league/receipts",
            "batch_id": None, "original_observed_at": None, "historical_availability": "unverified",
            "commit_time_is_provider_observation": False, "cached_sources": sources}, cached


def cached_fixture_metadata(payloads: list[tuple[str, dict]]) -> list[dict]:
    """Explicit event/scoreboard league metadata only; no name-based league lookup."""
    leagues = defaultdict(dict)
    observations = []
    for locator, data in payloads:
        for event in data.get("events", []):
            game_id = str(event.get("id", ""))
            explicit = ([event["league"]] if isinstance(event.get("league"), dict) else [])
            explicit += data.get("leagues", [])
            for league in explicit:
                if league.get("slug") and league.get("id"):
                    value = {k: league.get(k) for k in ("id", "slug", "name")}
                    leagues[game_id][digest(value)] = value
            competition = (event.get("competitions") or [{}])[0]
            teams = {t.get("homeAway"): t for t in competition.get("competitors", [])}
            if not {"home", "away"} <= set(teams):
                continue
            status = (competition.get("status") or event.get("status") or {}).get("type", {})
            if not status.get("completed"):
                continue
            def score(team):
                value = team.get("score")
                if isinstance(value, dict):
                    value = value.get("value", value.get("displayValue"))
                try:
                    numeric = float(value)
                    return int(numeric) if (not isinstance(value, bool) and math.isfinite(numeric)
                                            and numeric >= 0 and numeric.is_integer()) else None
                except (ValueError, TypeError):
                    return None
            home, away = teams["home"], teams["away"]
            hg, ag = score(home), score(away)
            if hg is None or ag is None:
                continue
            observations.append({"game_id": game_id, "namespace": "espn", "date": event["date"][:10],
                                 "home_team": home.get("team", {}).get("displayName", ""),
                                 "away_team": away.get("team", {}).get("displayName", ""),
                                 "home_id": str(home.get("id", "")), "away_id": str(away.get("id", "")),
                                 "home_goals": hg, "away_goals": ag, "season": event.get("season"),
                                 "locator": locator, "cache_observation": (data.get("_scorpred_source") or {}).get("observed_at"),
                                 "original_import_observation": None})
    unique = {}
    versions = defaultdict(set)
    for row in observations:
        versions[row["game_id"]].add((row["date"], row["home_id"], row["away_id"], row["home_goals"], row["away_goals"]))
    for row in observations:
        row["explicit_competitions"] = sorted(leagues[row["game_id"]].values(), key=lambda r: str(r["id"]))
        row["conflicting_cached_identity_or_score_versions"] = len(versions[row["game_id"]]) > 1
        unique[digest(row)] = row
    return sorted(unique.values(), key=lambda r: (r["game_id"], r["locator"]))


def lineage_inventory(classifier: list[dict], features: list[dict], cached: list[dict],
                      valid_raw: dict[str, list[dict]]) -> dict:
    candidates, raw_candidates, feature_lookup = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in cached:
        key = (row["date"], row["home_team"], row["away_team"], row["home_goals"], row["away_goals"])
        candidates[key].append(row)
    for file, records in sorted(valid_raw.items()):
        for i, row in enumerate(records, 2):
            raw_candidates[outcome_key(row)].append({"file": f"data/raw/{file}", "csv_line": i,
                                                    "competition_code": row.get("Div"), "filename_season": file.removesuffix(".csv")})
    for i, row in enumerate(features, 2):
        feature_lookup[fixture_key(row) + (row["result"],)].append(i)
    issues, internal_feature_links = [], 0
    for i, row in enumerate(classifier, 2):
        key = outcome_key(row)
        matches = candidates[key]
        identities = {m["game_id"] for m in matches}
        competitions = {digest(c) for m in matches for c in m["explicit_competitions"]}
        if (len(identities) == 1 and len(competitions) == 1
                and not any(m.get("conflicting_cached_identity_or_score_versions") for m in matches)):
            status = "unique_exact_fixture_metadata_candidate"
        elif matches:
            status = "ambiguous_or_incomplete_fixture_metadata_candidate"
        elif raw_candidates[key]:
            status = "raw_csv_fixture_candidate_only"
        else:
            status = "unrecoverable_from_inspected_metadata"
        feature_lines = feature_lookup[fixture_key(row) + (row["result"],)]
        internal_feature_links += len(feature_lines)
        # Never fill original provenance from a later corroborating cache.
        issues.append({"csv_line": i, "date": key[0], "home_team": key[1], "away_team": key[2],
                       "classification": status, "fixture_metadata_candidates": matches,
                       "raw_csv_candidates": raw_candidates[key], "derived_feature_csv_lines": feature_lines,
                       "original_league_id": row.get("league_id") or None,
                       "original_season_id": row.get("season") or None,
                       "original_source": None, "original_match_id": None,
                       "import_batch": None, "original_observed_at": None, "historical_availability": "unverified"})
    return {"schema_version": 1, "classifier_rows": len(classifier),
            "classifications": dict(Counter(i["classification"] for i in issues)),
            "verified_original_provider_lineage_rows": 0,
            "derived_feature_rows": len(features), "internal_feature_row_links": internal_feature_links,
            "fixture_metadata_is_original_import_proof": False, "rows": issues}


def build(root: Path = ROOT) -> dict:
    paths = [root / name for name in ("data/processed/matches.csv", "data/historical_matches.csv",
             "data/processed/soccer_training_data_clean.csv", "data/processed/matches_stage1.csv",
             "data/processed/matches_stage1.sources.json")]
    raw_paths = sorted((root / "data/raw").glob("E0_*.csv"))
    cache_paths = sorted((root / "cache/football").glob("*.json"))
    hashes = {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in paths + raw_paths + cache_paths}
    processed, classifier, features, stage1 = [csv_rows(p) for p in paths[:4]]
    raw = {p.name: csv_rows(p) for p in raw_paths}
    prior = json.loads(paths[4].read_text())
    accepted = prior["accepted"]
    if prior["output_sha256"] != hashes["data/processed/matches_stage1.csv"]:
        raise ValueError("Stage 1 output fingerprint mismatch")
    for record in accepted:
        name = record["file"].replace("\\", "/")
        if hashes.get(name) != record["sha256"]:
            raise ValueError("Accepted raw source fingerprint mismatch")
    valid_raw = {Path(r["file"].replace("\\", "/")).name: raw[Path(r["file"].replace("\\", "/")).name] for r in accepted}
    git, historic_cache = git_evidence(root)
    cached = cached_fixture_metadata([(p.relative_to(root).as_posix(), json.loads(p.read_text())) for p in cache_paths] + historic_cache)
    result = {"soccer_duplicates.json": duplicate_inventory(processed, raw, classifier, features, stage1),
              "soccer_lineage.json": lineage_inventory(classifier, features, cached, valid_raw),
              "git_lineage_evidence.json": git, "soccer_input_manifest.json": {"files": hashes}}
    changed = [name for name, expected in hashes.items() if sha((root / name).read_bytes()) != expected]
    if changed:
        raise ValueError("Soccer inputs changed during read; retry a stable snapshot")
    return result


def write_reports(output: Path, reports: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, value in reports.items():
        path = output / name
        if path.exists():
            if json.loads(path.read_text()) != value:
                raise ValueError("Existing issue manifest preserved; choose a new output directory")
            continue
        with path.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, allow_nan=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = build()
    write_reports(args.output_dir, result)
    print(json.dumps({"duplicates": result["soccer_duplicates.json"]["classifications"],
                      "lineage": result["soccer_lineage.json"]["classifications"],
                      "sports_downloads": 0, "original_records_modified": 0}))


if __name__ == "__main__":
    main()
