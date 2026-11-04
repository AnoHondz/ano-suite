"""Offline diagnostic contracts; no source acquisition, merge or model fitting."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts import inspect_nba_release_metadata as nba
from scripts import plan_soccer_data_repairs as soccer


def raw_row(date="15/08/2025", **extra):
    return {"Date": date, "HomeTeam": "Liverpool", "AwayTeam": "Bournemouth",
            "FTHG": "4", "FTAG": "2", "FTR": "H", "HS": "19", "AS": "10", "Div": "E0"} | extra


def historical(date="2025-08-15", **extra):
    return {"date": date, "home_team": "Liverpool", "away_team": "Bournemouth",
            "goals_scored": "4", "goals_conceded": "2", "result": "HomeWin"} | extra


def test_duplicate_payloads_have_conflicting_season_labels_and_source_rows():
    base = raw_row()
    processed = [{k: v for k, v in base.items() if k != "Div"} | {"Season": label}
                 for label in ("E0_2018.csv", "E0_2025.csv")]
    before = deepcopy(processed)
    result = soccer.duplicate_inventory(processed, {"E0_2018.csv": [base], "E0_2025.csv": [base]},
                                         [historical()], [historical()], [base])
    assert result["excess_rows"] == 1 and result["duplicate_groups"] == 1
    group = result["groups"][0]
    assert group["classification"] == "identical_observation_conflicting_season_labels"
    assert group["different_fields"] == ["Season"]
    assert group["competition"]["code"] == "E0"
    assert group["original_provider_match_id"] is None
    assert group["classifier_exact_fixture_candidates"] == [2]
    assert len(group["original_local_source_rows"]) == 2
    assert processed == before


def test_conflicting_goals_are_not_silently_exact_duplicates():
    one = raw_row(Season="E0_2025.csv")
    two = one | {"FTHG": "3"}
    result = soccer.duplicate_inventory([one, two], {}, [], [], [])
    assert result["classifications"] == {"conflicting_observations": 1}
    assert result["groups"][0]["competition"] is None


def test_same_teams_different_dates_do_not_form_a_duplicate():
    rows = [raw_row(Season="E0_2025.csv"), raw_row(date="16/08/2025", Season="E0_2025.csv")]
    result = soccer.duplicate_inventory(rows, {}, [], [], [])
    assert result["duplicate_groups"] == 0


def cached(game_id="123", date="2025-08-15", home_goals=4, league=True):
    result = {"game_id": game_id, "date": date, "home_team": "Liverpool", "away_team": "Bournemouth",
              "home_goals": home_goals, "away_goals": 2, "locator": "cache/football/example.json",
              "season": {"year": 2025}, "cache_observation": "2026-10-22T00:00:00Z"}
    result["explicit_competitions"] = [{"id": "700", "slug": "eng.1", "name": "Premier League"}] if league else []
    return result


def test_exact_fixture_candidate_does_not_manufacture_original_lineage():
    result = soccer.lineage_inventory([historical()], [historical()], [cached()], {})
    row = result["rows"][0]
    assert row["classification"] == "unique_exact_fixture_metadata_candidate"
    for field in ("original_league_id", "original_season_id", "original_source", "original_match_id",
                  "import_batch", "original_observed_at"):
        assert row[field] is None
    assert row["historical_availability"] == "unverified"
    assert result["internal_feature_row_links"] == 1


@pytest.mark.parametrize("record", [cached(date="2025-08-16"), cached(home_goals=3)])
def test_names_alone_or_conflicting_score_cannot_recover_fixture(record):
    result = soccer.lineage_inventory([historical()], [], [record], {})
    assert result["rows"][0]["classification"] == "unrecoverable_from_inspected_metadata"


@pytest.mark.parametrize("records", [[cached(), cached(game_id="456")], [cached(league=False)],
                                   [cached() | {"conflicting_cached_identity_or_score_versions": True}]])
def test_nonunique_ids_missing_competition_or_conflict_stay_ambiguous(records):
    result = soccer.lineage_inventory([historical()], [], records, {})
    assert result["rows"][0]["classification"] == "ambiguous_or_incomplete_fixture_metadata_candidate"


def test_local_csv_candidate_is_not_a_provider_id_or_import_receipt():
    result = soccer.lineage_inventory([historical()], [], [], {"E0_2025.csv": [raw_row()]})
    row = result["rows"][0]
    assert row["classification"] == "raw_csv_fixture_candidate_only"
    assert row["raw_csv_candidates"][0]["competition_code"] == "E0"
    assert row["original_match_id"] is None


def espn_payload(score="0"):
    return {"leagues": [{"id": "700", "slug": "eng.1", "name": "Premier League"}], "events": [
        {"id": "001", "date": "2025-08-15T20:00:00Z", "season": {"year": 2025},
         "competitions": [{"status": {"type": {"completed": True}}, "competitors": [
             {"id": "1", "homeAway": "home", "score": score, "team": {"displayName": "Liverpool"}},
             {"id": "2", "homeAway": "away", "score": "0", "team": {"displayName": "Bournemouth"}}]}]}]}


def test_cached_metadata_keeps_zero_ids_and_explicit_competition():
    result = soccer.cached_fixture_metadata([("source.json", espn_payload())])
    assert result[0]["home_goals"] == 0
    assert result[0]["game_id"] == "001"
    assert result[0]["explicit_competitions"][0]["id"] == "700"
    assert result[0]["original_import_observation"] is None


@pytest.mark.parametrize("score", [True, -1, "NaN", 1.5, None])
def test_invalid_goal_counts_cannot_create_exact_matches(score):
    assert soccer.cached_fixture_metadata([("source.json", espn_payload(score))]) == []


def test_conflicting_cached_versions_remain_flagged():
    result = soccer.cached_fixture_metadata([("first.json", espn_payload("0")), ("second.json", espn_payload("1"))])
    assert len(result) == 2
    assert all(r["conflicting_cached_identity_or_score_versions"] for r in result)


@pytest.mark.parametrize("url", [
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_team_boxscores/team_box_2023.parquet",
    "https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/assets/123",
    "https://raw.githubusercontent.com/shufinskiy/nba_data/main/datasets/nbastats_2022.tar.xz",
    "https://data.sportsdataverse.org/v1/nba/team_box?season=2023",
])
def test_sports_asset_urls_rejected_before_any_network(monkeypatch, url):
    calls = []
    monkeypatch.setattr(nba, "build_opener", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="assets are prohibited"):
        nba.fetch_metadata(url)
    assert calls == []


def test_metadata_pagination_not_embedded_asset_length(monkeypatch):
    calls = []
    def fake_fetch(url, **kwargs):
        calls.append(url)
        if url in nba.DOCUMENTS:
            return {"url": url, "status": 404}
        if "/tags/" in url:
            return {"url": url, "status": 200, "observed_at": "2026-10-22", "text": json.dumps(
                {"id": 123, "created_at": "2023-03-30", "assets": [], "body": "Published schema"})}
        assert nba.allowed_url(url, (123,))
        count = 100 if url.endswith("&page=1") else 1
        return {"url": url, "status": 200, "text": json.dumps([
            {"id": i, "name": f"team_box_{i}.csv"} for i in range(count)])}
    monkeypatch.setattr(nba, "fetch_metadata", fake_fetch)
    result = nba.inspect()
    assert all(r["asset_count"] == 101 and r["asset_listing_complete"] for r in result["releases"])
    assert result["sports_assets_downloaded"] == 0
    assert all("/download/" not in url for url in calls)


def test_issue_exports_are_deterministic_and_never_overwrite_other_results(tmp_path):
    value = {"issues.json": {"original_observed_at": None, "records_deleted_or_merged": 0}}
    soccer.write_reports(tmp_path, value)
    before = (tmp_path / "issues.json").read_bytes()
    soccer.write_reports(tmp_path, value)
    assert (tmp_path / "issues.json").read_bytes() == before
    with pytest.raises(ValueError, match="preserved"):
        soccer.write_reports(tmp_path, {"issues.json": {"changed": True}})
    assert (tmp_path / "issues.json").read_bytes() == before


def test_preparation_code_does_not_import_collection_or_models():
    source = Path(soccer.__file__).read_text()
    for module in ("api_client", "train_model", "production_model", "nflverse_store", "nfl_holdout"):
        assert f"import {module}" not in source
