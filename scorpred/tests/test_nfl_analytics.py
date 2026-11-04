"""Offline fixtures verify definitions, incremental imports and strict vintages."""
import inspect
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import sync_nflverse as importer
from services import nfl_analytics as analytics
from services import nfl_features, nflverse_store as store
from services.nfl_catalog import canonical_team, player_ids

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)


def play(identifier=1, **changes):
    row = dict(game_id="2026_01_BUF_NE", season=2026, week=1, play_id=identifier,
               posteam="BUF", defteam="NE", play_type="pass", no_play=0, qb_kneel=0,
               qb_spike=0, two_point_attempt=0, qb_dropback=1, pass_attempt=1,
               rush_attempt=0, complete_pass=1, sack=0, passing_yards=20, yards_gained=20,
               epa=2, success=1, interception=0, fumble_lost=0, down=3,
               third_down_converted=1, third_down_failed=0, yardline_100=15,
               fixed_drive=1, pass_touchdown=1, rush_touchdown=0, passer_player_id="00-123")
    row.update(changes)
    return row


def schedule(identifier="2026_01_BUF_NE", when=None, **changes):
    when = when or NOW - timedelta(days=10)
    local = when.astimezone(__import__("nfl_client").EASTERN)
    row = dict(game_id=identifier, season=2026, week=1, game_type="REG",
               gameday=local.date().isoformat(), gametime=local.strftime("%H:%M"),
               home_team="BUF", away_team="NE", home_score=24, away_score=17, location="Home")
    row.update(changes)
    return row


def seed(path):
    early = NOW - timedelta(days=5)
    store.replace_dataset("teams", 0, [{"team_abbr": t, "team_name": t} for t in ("BUF", "NE")], path=path, synced_at=early)
    store.replace_dataset("schedules", 2026, [schedule(), schedule("target", NOW + timedelta(days=2), home_score=None, away_score=None)], path=path, synced_at=early)
    store.replace_dataset("pbp", 2026, [play(), play(2, posteam="NE", defteam="BUF", epa=-1, passer_player_id="00-456")], path=path, synced_at=early)
    analytics.refresh_analytics(2026, path=path, now=early)


def test_hand_calculated_team_metrics_and_defensive_mirror():
    rows = [play(), play(2, play_type="run", pass_attempt=0, qb_dropback=0, rush_attempt=1,
                        yards_gained=10, passing_yards=None, epa=-1, success=0,
                        third_down_converted=0, third_down_failed=1, pass_touchdown=0, fumble_lost=1)]
    metrics = analytics.offense_metrics(rows)
    assert metrics["epa_per_play"]["value"] == .5
    assert metrics["success_rate"]["value"] == .5
    assert metrics["passing_epa_per_dropback"]["value"] == 2
    assert metrics["rushing_epa_per_play"]["value"] == -1
    assert metrics["explosive_play_rate"]["value"] == 1
    assert metrics["third_down_conversion_rate"]["value"] == .5
    assert metrics["redzone_td_rate"]["value"] == 1
    assert metrics["yards_per_play"]["value"] == 15
    assert metrics["turnover_play_rate"]["value"] == .5


def test_missing_fields_never_become_zero_and_denominators_remain_visible():
    metrics = analytics.offense_metrics([play(epa=None, success=None, yards_gained=None, interception=None, pass_touchdown=None)])
    for name in ("epa_per_play", "success_rate", "yards_per_play", "turnover_play_rate", "redzone_td_rate"):
        assert metrics[name]["value"] is None
        assert metrics[name]["denominator"] == 0
    assert metrics["epa_per_play"]["eligible"] == 1
    combined = analytics.aggregate([{"offense": metrics}], "offense")
    assert combined["epa_per_play"]["eligible"] == 1


@pytest.mark.parametrize("changes", [{"no_play": 1}, {"qb_kneel": 1}, {"qb_spike": 1}, {"two_point_attempt": 1}, {"play_type": "no_play"}])
def test_excluded_plays(changes):
    assert analytics.scrimmage_plays([play(**changes)]) == []


def test_modern_pbp_uses_play_type_when_no_play_column_is_absent():
    row = play()
    row.pop("no_play")
    assert analytics.scrimmage_plays([row]) == [row]


def test_quarterback_metrics_include_incompletions_sacks_and_pressure():
    rows = [play(passing_yards=30, was_pressure=True),
            play(2, complete_pass=0, passing_yards=None, pass_touchdown=0, interception=1, epa=-2, was_pressure=False),
            play(3, sack=1, pass_attempt=0, complete_pass=0, passing_yards=None, pass_touchdown=0, epa=-3, was_pressure=True)]
    metrics = analytics.quarterback_metrics(rows)
    assert metrics["completion_percentage"]["value"] == .5
    assert metrics["adjusted_yards_per_attempt"]["value"] == 2.5  # (30 + 20 - 45) / 2
    assert metrics["interception_rate"]["value"] == .5
    assert metrics["sack_rate"]["value"] == pytest.approx(1 / 3)
    assert metrics["passing_epa_per_dropback"]["value"] == -1
    assert metrics["epa_under_pressure"]["value"] == -.5
    assert metrics["pressure_rate"]["value"] == pytest.approx(2 / 3)


def test_order_independent_idempotency_and_incremental_union(tmp_path):
    path = tmp_path / "nfl.db"
    rows = [play(), play(2)]
    first = store.replace_dataset("pbp", 2026, rows, path=path, synced_at=NOW)
    second = store.replace_dataset("pbp", 2026, reversed(rows), path=path, synced_at=NOW + timedelta(hours=1))
    assert first["sha256"] == second["sha256"]
    assert len(store.versions("pbp", path=path)) == 2
    store.replace_dataset("pbp", 2026, [play(2, epa=4), play(3)], path=path, synced_at=NOW + timedelta(hours=2))
    assert len(store.records("pbp", 2026, path=path)) == 3
    original = store.records_as_of("pbp", 2026, NOW, path=path)
    assert all(row["epa"] == 2 for row in original)
    assert len(store.versions("pbp", path=path)) == 4


def test_out_of_order_receipts_cannot_erase_observed_versions(tmp_path):
    path = tmp_path / "nfl.db"
    store.replace_dataset("pbp", 2026, [play()], path=path, synced_at=NOW)
    for stamp in (NOW, NOW - timedelta(days=1)):
        with pytest.raises(ValueError, match="timestamp"):
            store.replace_dataset("pbp", 2026, [play(epa=999)], path=path, synced_at=stamp)
    assert store.records("pbp", 2026, path=path)[0]["epa"] == 2
    assert len(store.versions("pbp", path=path)) == 1


def test_additive_migration_retains_payloads_and_uses_receipt_not_report_time(tmp_path):
    path = tmp_path / "legacy.db"
    payload = json.dumps({"game_id": "g", "team": "BUF", "player_id": "00-123", "season": 2026})
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE datasets(dataset TEXT,season INTEGER,synced_at TEXT,row_count INTEGER,sha256 TEXT,PRIMARY KEY(dataset,season));"
                         "CREATE TABLE records(dataset TEXT,season INTEGER,record_key TEXT,observed_at TEXT,game_id TEXT,team TEXT,payload TEXT,PRIMARY KEY(dataset,season,record_key));")
        db.execute("INSERT INTO datasets VALUES ('player_stats',2026,?,1,'original')", (NOW.isoformat(),))
        db.execute("INSERT INTO records VALUES ('player_stats',2026,'0','1999-01-01','g','BUF',?)", (payload,))
    assert store.records("player_stats", 2026, path=path) == [json.loads(payload)]
    assert store.metadata(path=path)[0]["sha256"] == "original"
    assert store.records_as_of("player_stats", 2026, NOW - timedelta(days=1), path=path) == []
    assert len(store.versions("player_stats", path=path)) == 1
    assert len(store.records("player_stats", 2026, path=path)) == 1  # migration rerun


def test_identifier_mapping_preserves_relocation_eras_and_unlinked_rows(tmp_path):
    assert canonical_team("LAR") == "LA"
    assert canonical_team("OAK") == "OAK"
    assert player_ids([{"gsis_id": "00-123", "pfr_id": "Example00"}])[("pfr_id", "Example00")] == "00-123"
    row = {"team": "BUF", "season": 2026, "player_id": None, "player_display_name": None, "position": None, "game_id": "g"}
    store.replace_dataset("player_stats", 2026, [row], path=tmp_path / "nfl.db")
    assert store.records("player_stats", 2026, path=tmp_path / "nfl.db")[0]["player_id"] is None


def test_historical_depth_charts_keep_weeks_game_types_and_undated_rows(tmp_path):
    row = {"season": 2024, "club_code": "BUF", "week": 19, "game_type": "REG", "gsis_id": "00-123", "formation": "Offense", "depth_position": "QB", "depth_team": "1"}
    rows = [row, dict(row, game_type="WC"), dict(row, week=None)]
    store.replace_dataset("depth_charts", 2024, rows, path=tmp_path / "nfl.db")
    assert len(store.records("depth_charts", 2024, path=tmp_path / "nfl.db")) == 3


def test_injury_revision_identity_retains_report_dates(tmp_path):
    row = dict(season=2024, week=3, game_type="REG", team="BUF", gsis_id="00-123", date_modified="2024-09-17")
    store.replace_dataset("injuries", 2024, [row, dict(row, date_modified="2024-09-18")], path=tmp_path / "nfl.db")
    assert len(store.records("injuries", 2024, path=tmp_path / "nfl.db")) == 2


def test_stored_analytics_refresh_when_source_changes_and_matchup_is_strict(tmp_path):
    path = tmp_path / "nfl.db"
    seed(path)
    before = analytics.matchup_features("target", path=path, now=NOW)
    assert before["available"] and before["research_only"]
    assert before["home"]["offense"]["epa_per_play"]["value"] == 2
    assert before["away"]["defense"]["epa_per_play"]["value"] == 2
    assert before["rest_difference"] == 0
    assert before["home"]["quarterbacks"][0]["player_id"] == "00-123"
    assert not before["roster_availability"]["available"]
    assert analytics.refresh_analytics(2026, path=path, now=NOW)[0]["status"] == "cached"
    store.replace_dataset("pbp", 2026, [play(epa=4)], path=path, synced_at=NOW)
    assert "rows" in analytics.refresh_analytics(2026, path=path, now=NOW)[0]
    revised = analytics.matchup_features("target", path=path, now=NOW)
    assert revised["home"]["offense"]["epa_per_play"]["value"] == 4
    historical = analytics.matchup_features("target", path=path, as_of=NOW - timedelta(days=1), now=NOW)
    assert historical["home"]["offense"]["epa_per_play"]["value"] == 2


def test_historical_imports_cannot_be_backdated_or_use_present_day_rosters(tmp_path):
    path = tmp_path / "nfl.db"
    seed(path)
    # The game was played ten days ago; these files were first observed five
    # days ago. Replaying six days ago must yield no usable analytics.
    before = analytics.matchup_features("target", path=path, as_of=NOW - timedelta(days=6), now=NOW)
    assert not before["available"]
    store.replace_dataset("rosters", 2026, [{"team": "BUF", "season": 2026, "gsis_id": "00-future"}], path=path, synced_at=NOW)
    after = analytics.matchup_features("target", path=path, as_of=NOW - timedelta(days=6), now=NOW)
    assert before == after
    with pytest.raises(ValueError):
        analytics.matchup_features("target", path=path, as_of=NOW + timedelta(days=2), now=NOW)
    with pytest.raises(ValueError, match="source receipts"):
        analytics.refresh_analytics(2026, path=path, now=NOW - timedelta(days=6))


def test_participation_pressure_joins_numeric_play_ids_without_inference(tmp_path):
    path = tmp_path / "nfl.db"
    seed(path)
    store.replace_dataset("participation", 2026, [{"nflverse_game_id": "2026_01_BUF_NE", "play_id": 1,
                                                   "was_pressure": True}], path=path, synced_at=NOW)
    store.replace_dataset("pbp", 2026, [play(1.0, epa=3)], path=path, synced_at=NOW)
    analytics.refresh_analytics(2026, path=path, now=NOW)
    row = next(r for r in store.records("advanced_qb_games", 2026, path=path) if r["team"] == "BUF")
    assert row["metrics"]["epa_under_pressure"]["value"] == 3
    assert row["metrics"]["pressure_rate"]["value"] == 1


def test_target_schedule_corrections_use_the_version_known_at_cutoff(tmp_path):
    path = tmp_path / "nfl.db"
    seed(path)
    store.replace_dataset("schedules", 2026, [schedule("target", NOW + timedelta(days=2), home_score=None,
                                                      away_score=None, location="Neutral")], path=path, synced_at=NOW)
    prior = analytics.matchup_features("target", path=path, now=NOW, as_of=NOW - timedelta(days=1))
    assert prior["home_field"] == 1
    current = analytics.matchup_features("target", path=path, now=NOW)
    assert current["home_field"] == 0


def test_existing_research_features_reject_late_observations_and_keep_schema(tmp_path):
    path = tmp_path / "nfl.db"
    seed(path)
    current = nfl_features.build_features(path=path, now=NOW, min_games=1)
    assert any(row["game_id"] == "target" for row in current)
    assert nfl_features.build_features(path=path, now=NOW - timedelta(days=6), min_games=1) == []
    assert len(nfl_features.FEATURE_NAMES) == 19


def test_sdk_imports_and_documented_signatures():
    nfl = pytest.importorskip("nflreadpy")
    assert "summary_level" in inspect.signature(nfl.load_team_stats).parameters
    assert "summary_level" in inspect.signature(nfl.load_player_stats).parameters
    assert "stat_type" in inspect.signature(nfl.load_pfr_advstats).parameters
    assert "seasons" in inspect.signature(nfl.load_participation).parameters
    assert callable(nfl.load_ftn_charting)


def mock_loader(monkeypatch, rows, name="load_pfr_advstats"):
    frame = SimpleNamespace(iter_rows=lambda **kwargs: iter(rows))
    fn = Mock(return_value=frame)
    monkeypatch.setitem(sys.modules, "nflreadpy", SimpleNamespace(**{name: fn}))
    monkeypatch.setitem(sys.modules, "nflreadpy.config", SimpleNamespace(update_config=Mock()))
    monkeypatch.setitem(sys.modules, "polars", SimpleNamespace())
    return fn


def test_refresh_skips_cached_history_and_calls_verified_api_signature(tmp_path, monkeypatch):
    row = dict(game_id="g", season=2024, team="BUF", pfr_player_id="Example00")
    fn = mock_loader(monkeypatch, [row])
    path = tmp_path / "nfl.db"
    importer.sync([2024], ["pfr_pass_week"], path=path, refresh=True, now=NOW)
    fn.assert_called_once_with(2024, stat_type="pass", summary_level="week")
    reports = importer.sync([2024], ["pfr_pass_week"], path=path, refresh=True, now=NOW + timedelta(days=1))
    assert reports[0]["status"] == "cached" and fn.call_count == 1
    importer.sync([2024], ["pfr_pass_week"], path=path, force=True, now=NOW + timedelta(days=1))
    assert fn.call_count == 2
    assert len(store.versions("pfr_pass_week", path=path)) == 1


def test_unsupported_seasons_and_missing_assets_are_recorded_without_network(tmp_path, monkeypatch):
    fn = mock_loader(monkeypatch, [], "load_participation")
    path = tmp_path / "nfl.db"
    reports = importer.sync([2010, 2026], ["participation"], path=path, keep_going=True, now=NOW)
    assert all(r["status"] == "unsupported" for r in reports)
    fn.assert_not_called()
    fn = mock_loader(monkeypatch, [])
    reports = importer.sync([2026], ["pfr_pass_season"], path=path, keep_going=True, now=NOW)
    assert reports[0]["status"] == "unavailable"
    importer.sync([2026], ["pfr_pass_season"], path=path, keep_going=True, refresh=True, now=NOW + timedelta(minutes=1))
    assert fn.call_count == 1  # failed/missing feeds back off too
    assert store.refresh_status(path=path)


def test_per_dataset_refresh_interval_override(tmp_path, monkeypatch):
    fn = mock_loader(monkeypatch, [dict(game_id="g", season=2026, team="BUF", pfr_player_id="Example00")])
    path = tmp_path / "nfl.db"
    monkeypatch.setenv("NFL_REFRESH_PFR_PASS_WEEK_SECONDS", "60")
    importer.sync([2026], ["pfr_pass_week"], path=path, refresh=True, now=NOW)
    importer.sync([2026], ["pfr_pass_week"], path=path, refresh=True, now=NOW + timedelta(minutes=2))
    assert fn.call_count == 2


def test_research_routes_are_opt_in_and_do_not_expose_forecasts(tmp_path, monkeypatch):
    from app import app
    path = tmp_path / "nfl.db"
    seed(path)
    monkeypatch.setattr(store, "database_path", lambda: path)
    client = app.test_client()
    assert client.get("/api/nfl/research/matchups/target").status_code == 404
    monkeypatch.setenv("NFL_ADVANCED_RESEARCH_ENABLED", "1")
    response = client.get("/api/nfl/research/matchups/target")
    assert response.status_code == 200
    assert response.json["result"]["research_only"]
    assert "prediction" not in response.json["result"]
    assert client.get("/api/nfl/research/coverage").status_code == 200
    assert client.get("/api/nfl/research/matchups/target?as_of=invalid").status_code == 400
