"""Additive, evidence-separated soccer lineage and duplicate research metadata."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import json

VERSION = "soccer-lineage-v1"
BASELINE = {"unique_exact_fixture_metadata_candidate": 895, "raw_csv_fixture_candidate_only": 291,
            "unrecoverable_from_inspected_metadata": 6702}
CLASSIFICATIONS = {"unique_exact_fixture_metadata_candidate", "raw_csv_fixture_candidate_only",
                   "unrecoverable_from_inspected_metadata", "ambiguous_or_incomplete_fixture_metadata_candidate"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def key(row):
    return row["date"], row["home_team"], row["away_team"]


def validate_population(rows, manifest, expected=BASELINE):
    entries = manifest.get("rows", []) if manifest else []
    if not manifest:
        return None
    lines = [entry["csv_line"] for entry in entries]
    if len(lines) != len(set(lines)) or set(lines) != set(range(2, len(rows) + 2)):
        raise ValueError("Lineage population is not a mutually exclusive exhaustive row partition")
    actual = dict(Counter(entry["classification"] for entry in entries))
    if actual != manifest.get("classifications") or (expected is not None and actual != expected):
        raise ValueError("Lineage population does not reconcile to audited groups")
    if manifest.get("classifier_rows") != len(rows) or not set(actual) <= CLASSIFICATIONS:
        raise ValueError("Unsupported lineage population/classification")
    for entry in entries:
        row = rows[entry["csv_line"] - 2]
        if key(row) != key(entry):
            raise ValueError("Source row identity differs from audited lineage locator")
        if any(entry.get(field) is not None for field in (
                "original_league_id", "original_season_id", "original_source", "original_match_id",
                "import_batch", "original_observed_at")):
            raise ValueError("Original provenance claim lacks authenticated receipt evidence")
    return {entry["csv_line"]: entry for entry in entries}


def build_sidecar(rows, manifest, *, source_sha256, audit_timestamp, evidence_fingerprints,
                  expected=BASELINE, version=VERSION):
    if version != VERSION:
        raise ValueError("Incompatible soccer lineage version")
    if datetime.fromisoformat(audit_timestamp).tzinfo is None:
        raise ValueError("Audit timestamp requires a timezone; it is not a historical observation")
    entries = validate_population(rows, manifest, expected)
    identities = Counter(key(row) for row in rows)
    result = []
    for line, row in enumerate(rows, 2):
        entry = entries[line] if entries else None
        status, method, candidates = "unresolved", "no_source_manifest", []
        if entry:
            candidates = deepcopy(entry["fixture_metadata_candidates"])
            if entry["classification"] == "unique_exact_fixture_metadata_candidate":
                ids, leagues = set(), set()
                valid = True
                for candidate in candidates:
                    ids.add(candidate["game_id"])
                    leagues |= {fingerprint(c) for c in candidate["explicit_competitions"]}
                    valid &= (key(candidate) == key(row)
                              and candidate["home_goals"] == int(row["goals_scored"])
                              and candidate["away_goals"] == int(row["goals_conceded"])
                              and not candidate.get("conflicting_cached_identity_or_score_versions", False)
                              and bool(evidence_fingerprints.get(candidate["locator"])))
                status = "candidate" if valid and len(ids) == len(leagues) == 1 else "ambiguous"
                method = "exact_date_oriented_names_both_scores_explicit_source_competition"
            elif entry["classification"] == "raw_csv_fixture_candidate_only":
                status, method = "candidate", "exact_local_csv_fixture_candidate_without_original_provider_id"
            elif entry["classification"] == "ambiguous_or_incomplete_fixture_metadata_candidate":
                status, method = "ambiguous", "nonunique_or_incomplete_source_identity"
            else:
                method = "no_exact_fixture_evidence"
        locator = {"file": "data/historical_matches.csv", "sha256": source_sha256, "csv_line": line,
                   "row_sha256": fingerprint(row), "classification": "verified_internal_relationship"}
        candidate_fields = []
        for candidate in candidates:
            location = candidate["locator"]
            candidate_fields.append({"provider_fixture_id": candidate["game_id"], "namespace": "espn",
                "league": candidate["explicit_competitions"], "season": candidate.get("season"),
                "source_observation_reference": {"locator": location, "sha256": evidence_fingerprints.get(location),
                    "evidence_status": "pinned_source_hash" if evidence_fingerprints.get(location) else "missing_source_hash",
                    "later_cache_observed_at": candidate.get("cache_observation"),
                    "original_import_observed_at": None}, "classification": "candidate_inferred"})
        raw_candidates = deepcopy(entry["raw_csv_candidates"]) if entry else []
        league_candidates = [c["league"] for c in candidate_fields]
        season_candidates = [c["season"] for c in candidate_fields]
        for candidate in raw_candidates:
            league_candidates.append({"namespace": "football_data_csv", "code": candidate.get("competition_code"),
                                      "classification": "candidate_inferred"})
            season_candidates.append({"source_season_label": candidate.get("filename_season"),
                                      "classification": "candidate_inferred"})
        canonical = "soccer:reference:v1:" + fingerprint([source_sha256, key(row)])[:24] if identities[key(row)] == 1 else None
        if canonical is None:
            status = "ambiguous"
        result.append({"sidecar_version": VERSION, "audit_timestamp": audit_timestamp,
                       "fixture": {k: row[k] for k in ("date", "home_team", "away_team")},
                       "original_fixture_identifier": locator, "canonical_fixture_identifier": canonical,
                       "canonical_id_scope": "versioned_local_reference; not a recovered upstream ID",
                       "status": status, "matching_method": method,
                       "confidence_classification": "unverified_original_lineage" if status == "candidate" else status,
                       "verified_original_lineage": None, "original_provider_fixture_id": None,
                       "league": {"verified_original": None, "candidates": league_candidates},
                       "season": {"verified_original": None, "candidates": season_candidates},
                       "source_identifier": {"verified_internal": locator["file"], "verified_original_provider": None},
                       "import_batch": None, "original_observation_reference": None,
                       "historical_availability": "unverified", "fixture_metadata_candidates": candidate_fields,
                       "raw_csv_candidates": raw_candidates,
                       "internal_derived_feature_csv_lines": deepcopy(entry["derived_feature_csv_lines"]) if entry else []})
    counts = Counter(record["status"] for record in result)
    return {"schema_version": 1, "sidecar_version": VERSION, "research_only": True,
            "audit_timestamp": audit_timestamp, "source_sha256": source_sha256, "population": len(rows),
            "audited_population_groups": manifest.get("classifications") if manifest else None,
            "counts": {state: counts[state] for state in ("verified", "candidate", "ambiguous", "unresolved")},
            "verified_internal_source_locators": len(result), "fixtures": result}


def canonical_duplicates(processed, stage1, manifest, *, processed_sha256, stage1_sha256):
    """Use an accepted Stage 1 row as representative; retain every legacy member.

    Representatives require equality of all observation fields, not just score.
    The Season conflict remains in membership metadata. Unknown/conflicts have
    no representative. No source row is removed, renamed or overwritten.
    """
    def identity(row):
        return row["Date"], row["HomeTeam"], row["AwayTeam"]
    from scripts.plan_soccer_data_repairs import date_key
    def canonical_key(row):
        return (date_key(row["Date"]), *identity(row)[1:])
    def observation(row):
        return {k: date_key(v) if k == "Date" else v for k, v in row.items() if k not in {"Season", "League"}}
    grouped, accepted = defaultdict(list), defaultdict(list)
    for line, row in enumerate(processed, 2):
        grouped[canonical_key(row)].append((line, row))
    for line, row in enumerate(stage1, 2):
        accepted[canonical_key(row)].append((line, row))
    audited = {tuple((g["date"], g["home_team"], g["away_team"])): g for g in manifest.get("groups", [])} if manifest else {}
    actual_duplicate_keys = {key for key, members in grouped.items() if len(members) > 1}
    if manifest and (set(audited) != actual_duplicate_keys or manifest["excess_rows"] != len(processed) - len(grouped)):
        raise ValueError("Duplicate-group population differs from audited manifest")
    view, groups, quarantine = [], [], []
    for fixture, members in sorted(grouped.items()):
        unique_payloads = {fingerprint(observation(row)) for _, row in members}
        possible = accepted[fixture]
        representative = None
        if len(unique_payloads) == 1 and len(possible) == 1:
            line, row = possible[0]
            if observation(row) == observation(members[0][1]):
                representative = {"file": "data/processed/matches_stage1.csv", "sha256": stage1_sha256,
                                  "csv_line": line, "row_sha256": fingerprint(row)}
        group_id = audited.get(fixture, {}).get("issue_id")
        membership = [{"file": "data/processed/matches.csv", "sha256": processed_sha256,
                       "csv_line": line, "row_sha256": fingerprint(row), "declared_source": row.get("Season")}
                      for line, row in members]
        if group_id and sorted(m["csv_line"] for m in membership) != sorted(m["csv_line"] for m in audited[fixture]["processed_members"]):
            raise ValueError("Duplicate membership differs from audited locators")
        record = {"fixture": fixture, "duplicate_group_id": group_id,
                  "members": membership, "analytical_representative": representative,
                  "observation_status": "identical" if len(unique_payloads) == 1 else "conflicting",
                  "metadata_conflicts": ["Season"] if len({r.get("Season") for _, r in members}) > 1 else [],
                  "selection_rule": "unique Stage 1 accepted row with all observation fields equal"}
        if len(members) > 1:
            groups.append(record)
        if representative:
            view.append(record)
        else:
            quarantine.append(record)
    return {"sidecar_version": VERSION, "research_only": True, "original_observations": len(processed),
            "duplicate_groups": groups, "canonical_fixtures": view, "quarantine": quarantine,
            "canonical_count": len(view), "counting_rule": "one accepted representative per fixture; original rows remain separate"}
