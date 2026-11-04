"""Evidence classifications, all 16 reviewed cases, research isolation/preservation."""
from copy import deepcopy
import json

import pytest

from runtime_paths import repo_root
from services import soccer_lineage as lineage
from services import soccer_stat_contracts as stats
from services.soccer_research_views import ResearchViews

SPEC = json.loads((repo_root() / "docs/assets/rights_soccer/soccer_stat_contracts.json").read_text(encoding="utf-8"))
DEFINITION = {"source": "synthetic reviewed provider", "definition_id": "test-v1", "verified": True,
              "unit": "count", "aggregation": "match", "period": "regulation"}


@pytest.mark.parametrize("case", SPEC["planned_acceptance_tests"], ids=lambda case: case["case"])
def test_all_reviewed_statistic_acceptance_cases(case):
    name, raw, expected = case["case"], deepcopy(case["input"]), case["expected"]
    original = deepcopy(raw)
    if name == "negative_nonfinite_or_fractional_counts":
        for value in raw:
            with pytest.raises(ValueError):
                stats.number(value)
    elif name == "count_percent_unit_mismatch":
        result = stats.normalize(raw, DEFINITION)
        assert result["values"]["corners"] is None and result["issues"]
    elif name == "shootout_result":
        result = stats.regulation_result(raw)
        assert result["regulation_result"] == "Draw" and result["source_periods"] == raw
    elif name == "fouls_committed_vs_drawn":
        result = stats.normalize(raw, DEFINITION)["values"]
        assert result["fouls_committed"] == 7 and result["fouls_drawn"] == 12
    elif name == "second_yellow_direct_red":
        result = stats.normalize(raw, DEFINITION)["values"]
        assert result["yellow_cards"] == 2 and result["second_yellow_red"] == 1 and result["direct_red"] == 0
        assert result["red_cards"] is None and "total_cards" not in result
    elif name == "shots_on_target_exceeds_total":
        result = stats.normalize(raw, DEFINITION)
        assert result["values"]["shots"] is result["values"]["shots_on_target"] is None
        assert "pair_quarantined" in result["issues"][0]["reason"]
    elif name == "pregame_cutoff":
        definition = DEFINITION | {"stat_scope": raw["stat_scope"]}
        result = stats.normalize({"goals": 2}, definition, prediction_cutoff="2026-10-22T12:00:00Z")
        assert result["available_for_prediction"] is False
    else:
        definition = dict(DEFINITION)
        if name == "explicit_zero_xg":
            definition["xg_model_version"] = "synthetic-model-v1"
        result = stats.normalize(raw, definition)
        for field, value in expected.items():
            assert result["values"][field] == value
        assert result["source_values"] == original
    assert raw == original


@pytest.mark.parametrize("override", [{"unit": "percent"}, {"aggregation": "per90"}, {"period": "unknown"}, {"verified": False}])
def test_incompatible_definition_cannot_become_a_match_count(override):
    result = stats.normalize({"goals": 0, "corners": 5}, DEFINITION | override)
    assert result["values"]["goals"] is result["values"]["corners"] is None
    assert result["issues"]


def test_explicit_xg_alias_with_source_model_definition_and_missing_aliases():
    rows = [{"team": {"id": "1"}, "statistics": [{"type": "expected_goals", "value": 0.0}]}]
    result = stats.team_stat_table(rows, 1, definition=DEFINITION | {"xg_model_version": "provider-model-v1"})
    assert result["values"]["xg"] == 0
    assert result["source_values"] == rows
    assert stats.team_stat_table([], 1, definition=DEFINITION)["values"]["corners"] is None
    with pytest.raises(ValueError, match="Ambiguous"):
        stats.team_stat_table(rows + rows, 1, definition=DEFINITION)


def test_overview_missing_fields_never_synthesize_zero_or_wrong_competition():
    payload = {"statistics": {"names": ["totalGoals"], "splits": [{"leagueSlug": "eng.1", "stats": [0]}]}}
    result = stats.player_overview(payload, league_slug="eng.1", definition=DEFINITION)
    assert result["values"]["goals"] == 0
    assert result["values"]["shots"] is result["values"]["minutes"] is None
    assert result["definition"]["aggregation"] == "season"
    with pytest.raises(ValueError, match="explicit competition"):
        stats.player_overview(payload, league_slug="ger.1", definition=DEFINITION)


def test_complete_window_means_and_actual_rate_denominators():
    assert stats.nullable_mean([0, 0]) == 0
    assert stats.nullable_mean([None, 1]) is None
    assert stats.nullable_mean([]) is None
    assert stats.rate(0, 90, scale=90) == 0
    assert stats.rate(None, 90, scale=90) is None
    assert stats.rate(1, None, scale=90) is None
    assert stats.rate(1, 0, scale=90) is None


def test_model_share_is_labeled_and_does_not_supply_xg_or_corners():
    result = stats.normalize({"xg_a": .6, "xg_b": .4, "goals": 4}, DEFINITION)
    assert result["values"]["xg"] is result["values"]["corners"] is None
    assert result["labeled_model_quantities"]["model_attack_share_home"]["value"] == .6
    assert result["labeled_model_quantities"]["model_attack_share_home"]["validated_for_prediction"] is False


def test_prior_game_receipt_cutoff_requires_timezone_and_strict_chronology():
    definition = DEFINITION | {"stat_scope": "prior_game_final_box", "observed_at": "2026-10-20T12:00:00Z",
                              "event_ended_at": "2026-10-19T18:00:00Z"}
    assert stats.normalize({"goals": 0}, definition, prediction_cutoff="2026-10-22T12:00:00Z")["available_for_prediction"]
    for invalid in ({"observed_at": None}, {"observed_at": "2026-10-22T12:00:00Z"}, {"event_ended_at": "2026-10-23T00:00:00Z"}):
        assert not stats.normalize({"goals": 0}, definition | invalid, prediction_cutoff="2026-10-22T12:00:00Z")["available_for_prediction"]


def historical():
    return {"date": "2025-08-15", "home_team": "Home", "away_team": "Away", "goals_scored": "1", "goals_conceded": "0", "result": "HomeWin"}


def evidence():
    row = historical()
    return {"classifier_rows": 1, "classifications": {"unique_exact_fixture_metadata_candidate": 1}, "rows": [
        {"csv_line": 2, "date": row["date"], "home_team": "Home", "away_team": "Away",
         "classification": "unique_exact_fixture_metadata_candidate", "fixture_metadata_candidates": [
             {"game_id": "001", "date": row["date"], "home_team": "Home", "away_team": "Away", "home_goals": 1,
              "away_goals": 0, "locator": "cache/football/source.json", "season": {"year": 2025},
              "explicit_competitions": [{"id": "700", "slug": "eng.1"}]}], "raw_csv_candidates": [], "derived_feature_csv_lines": []}]}


def sidecar(manifest=None, **kwargs):
    return lineage.build_sidecar([historical()], evidence() if manifest is None else manifest,
        source_sha256="a" * 64, audit_timestamp="2026-10-22T13:24:00Z", expected=None,
        evidence_fingerprints=kwargs.pop("fingerprints", {"cache/football/source.json": "b" * 64}), **kwargs)


def test_candidate_never_promoted_to_verified_original_receipt():
    record = sidecar()["fixtures"][0]
    assert record["status"] == "candidate"
    assert record["original_provider_fixture_id"] is record["original_observation_reference"] is None
    assert record["original_fixture_identifier"]["classification"] == "verified_internal_relationship"
    assert record["fixture_metadata_candidates"][0]["provider_fixture_id"] == "001"
    assert record["historical_availability"] == "unverified"


def test_ambiguous_or_missing_evidence_cannot_establish_unique_lineage():
    data = evidence()
    other = deepcopy(data["rows"][0]["fixture_metadata_candidates"][0])
    other["game_id"] = "002"
    data["rows"][0]["fixture_metadata_candidates"].append(other)
    assert sidecar(data)["counts"]["ambiguous"] == 1
    assert sidecar(fingerprints={})["counts"]["ambiguous"] == 1
    empty = lineage.build_sidecar([historical()], None, source_sha256="a" * 64,
        audit_timestamp="2026-10-22T13:24:00Z", evidence_fingerprints={})
    assert empty["counts"]["unresolved"] == 1


def test_population_reconciliation_and_fabricated_original_claim_fail_closed():
    for change in ("duplicate", "missing", "identity", "original_claim", "count"):
        manifest = evidence()
        if change == "duplicate":
            manifest["rows"] *= 2
        if change == "missing":
            manifest["rows"] = []
        if change == "identity":
            manifest["rows"][0]["home_team"] = "Other"
        if change == "original_claim":
            manifest["rows"][0]["original_observed_at"] = "2025-01-01T00:00:00Z"
        if change == "count":
            manifest["classifications"] = {"unrecoverable_from_inspected_metadata": 1}
        with pytest.raises(ValueError):
            sidecar(manifest)


def test_canonical_duplicate_selection_preserves_all_rows_and_quarantines_conflict():
    one = {"Date": "15/08/2025", "HomeTeam": "Home", "AwayTeam": "Away", "FTHG": "1", "FTAG": "0", "Season": "E0_2018.csv"}
    two = one | {"Season": "E0_2025.csv"}
    accepted = one | {"Date": "2025-08-15", "Season": "2025", "League": "E0"}
    originals = deepcopy([one, two])
    result = lineage.canonical_duplicates(originals, [accepted], None, processed_sha256="a", stage1_sha256="b")
    assert result["canonical_count"] == 1 and len(result["duplicate_groups"]) == 1
    group = result["duplicate_groups"][0]
    assert len(group["members"]) == 2 and group["metadata_conflicts"] == ["Season"]
    assert group["analytical_representative"]["file"].endswith("matches_stage1.csv")
    assert originals == [one, two]
    conflict = lineage.canonical_duplicates([one, two | {"FTHG": "2"}], [accepted], None, processed_sha256="a", stage1_sha256="b")
    assert conflict["canonical_count"] == 0 and conflict["quarantine"]
    missing = lineage.canonical_duplicates([one, two], [], None, processed_sha256="a", stage1_sha256="b")
    assert missing["canonical_count"] == 0


def test_version_compatibility_and_disabled_research_access(tmp_path):
    with pytest.raises(PermissionError):
        ResearchViews.open(tmp_path)
    with pytest.raises(ValueError, match="version"):
        stats.normalize({}, DEFINITION, version="soccer-stats-v0")
    with pytest.raises(ValueError, match="version"):
        sidecar(version="soccer-lineage-v0")
    with pytest.raises(ValueError, match="version"):
        ResearchViews({"manifest.json": {}}, enabled=True, version="old")


@pytest.mark.skipif(not (repo_root() / "docs/assets/rights_soccer/soccer_lineage.json").exists(),
                    reason="Large soccer lineage evidence (>1 MB) is kept outside Git; run where it is present.")
def test_real_versioned_views_are_consistent_idempotent_and_do_not_mutate_sources():
    root = repo_root()
    from scripts.build_soccer_research import build
    output = root / "docs/assets/soccer_foundation/v1"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    first = build(manifest["audit_timestamp"])
    second = build(manifest["audit_timestamp"])
    assert first == second
    views = ResearchViews(first, enabled=True)
    assert views.quality()["lineage_counts"] == {"verified": 0, "candidate": 1186, "ambiguous": 0, "unresolved": 6702}
    assert len(views.duplicate_groups()) == 319
    assert len(views.canonical_fixtures("legacy_e0")) == 2599
    assert len(views.canonical_fixtures("historical_reference")) == 7888
    assert len(views.corrected_statistics()["observations"]) == 5198
    assert len(views.lineage("unresolved")) == 6702
    changed = views.lineage("candidate")
    changed[0]["status"] = "verified"
    assert views.lineage("verified") == []
    broken = deepcopy(first)
    broken["quality_report.json"]["historical_fixtures"] = 1
    with pytest.raises(ValueError, match="fingerprint"):
        ResearchViews(broken, enabled=True)


def test_production_modules_do_not_import_new_research_modules():
    for file in ("app.py", "api_client.py", "predictor.py", "scormastermind.py", "train_model.py", "production_model.py"):
        source = (repo_root() / file).read_text(encoding="utf-8")
        assert "soccer_stat_contracts" not in source and "soccer_research_views" not in source and "soccer_lineage" not in source


def test_preservation_probe_compares_json_label_key_types_without_ignoring_probabilities():
    from scripts.soccer_preservation_probe import canonical_snapshot
    actual = {"class_labels": {0: "HomeWin", 1: "Draw", 2: "AwayWin"}, "probabilities": [.5, .2, .3]}
    saved = json.loads(json.dumps(actual))
    assert canonical_snapshot(actual) == saved
    changed = deepcopy(actual)
    changed["probabilities"] = [.4, .3, .3]
    assert canonical_snapshot(changed) != saved
