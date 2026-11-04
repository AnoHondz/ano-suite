"""Synthetic sealed outcomes and runtime data contracts, never real held results."""
import copy
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

import app as module
import nba_routes
import props_engine as props
from scripts import nfl_collector, sync_nflverse
from services import nfl_holdout as seal
from services import nflverse_store as store
from services.data_freshness import metadata
from services.nfl_winner_features import Vintages, digest
from services.prediction_lifecycle import sanitize_decision, unavailable_reason


@pytest.fixture
def sealed(tmp_path, monkeypatch):
    monkeypatch.setenv("SCORPRED_DATA_ROOT", str(tmp_path))
    path = tmp_path / "nfl.db"
    old = {"game_id": "2026_01_NE_BUF", "season": 2026, "week": 1, "home_team": "BUF", "away_team": "NE",
           "gameday": "2026-09-01", "gametime": "13:00", "game_type": "REG", "home_score": 21, "away_score": 14}
    held = {**old, "game_id": "2026_12_NE_BUF", "week": 12, "gameday": "2026-11-26",
            "home_score": 99, "away_score": 98, "result": "SYNTHETIC_SECRET", "spread_line": 999}
    store.replace_dataset("schedules", 2026, [old, held], path=path)
    store.replace_dataset("pbp", 2026, [{"game_id": held["game_id"], "season": 2026, "play_id": 1, "week": 12, "epa": 99}], path=path)
    protocol = {"holdout": {"sealed": True, "game_ids": [held["game_id"]], "season": 2026, "weeks": [12],
                            "earliest_review_at": "2027-03-01T00:00:00+00:00"}}
    protocol["protocol_sha256"] = digest(protocol)
    target = tmp_path / "cache/nflverse/phase2_2/protocol.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(protocol))
    return path, old, held


def test_all_generic_readers_mask_before_decode_and_writer_preserves_raw(sealed):
    path, old, held = sealed
    before = store.records("schedules", 2026, path=path)
    assert before[0] == old
    assert before[1]["game_id"] == held["game_id"]
    assert not {"home_score", "away_score", "spread_line", "result"} & before[1].keys()
    assert store.records("pbp", 2026, path=path) == []
    assert len(store.versions("schedules", season=2026, path=path)) == 2
    assert Vintages("pbp", 2026, path).snapshot(datetime.now(UTC)) == []
    assert "SYNTHETIC_SECRET" not in json.dumps(store.records_as_of("schedules", 2026, datetime.now(UTC), path=path))
    # Repeat ingestion remains allowed, idempotent and retains source bytes.
    store.replace_dataset("schedules", 2026, [old, held], path=path)
    with sqlite3.connect(path) as raw:  # Synthetic fixture only.
        assert raw.execute("SELECT COUNT(*) FROM record_versions WHERE dataset='schedules'").fetchone()[0] == 2
        assert "SYNTHETIC_SECRET" in raw.execute("SELECT payload FROM records WHERE record_key=?", (held["game_id"],)).fetchone()[0]


@pytest.mark.parametrize("query", ["SELECT payload FROM main.records", "SELECT payload FROM main.record_versions",
                                  "DROP VIEW temp.records", "DELETE FROM _sealed_ids", "ATTACH DATABASE ':memory:' AS bypass",
                                  "ALTER TABLE temp._sealed_ids RENAME TO bypass", "DROP TABLE main.records",
                                  "WITH records AS (SELECT payload FROM main.records) SELECT * FROM records"])
def test_raw_sql_bypasses_denied(sealed, query):
    with store.connection(sealed[0]) as db, pytest.raises(sqlite3.DatabaseError):
        db.execute(query).fetchall()


def test_sealed_handle_cannot_export_raw_database_or_disable_guard(sealed):
    with store.connection(sealed[0]) as db:
        with pytest.raises(sqlite3.DatabaseError):
            db.serialize()
        with pytest.raises(sqlite3.DatabaseError):
            db.set_authorizer(None)
        with pytest.raises(sqlite3.DatabaseError), sqlite3.connect(":memory:") as target:
            db.backup(target)
        with pytest.raises(sqlite3.DatabaseError):
            db.cursor().execute("WITH records AS (SELECT payload FROM main.records) SELECT * FROM records").fetchall()
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("SELECT 1").execute("WITH records AS (SELECT payload FROM main.records) SELECT * FROM records").fetchall()


def test_unknown_postholdout_aggregates_and_new_weeks_cannot_leak(sealed):
    path, _, _ = sealed
    store.replace_dataset("team_season", 2026, [{"team": "BUF", "season": 2026, "season_type": "REG", "wins": 99}], path=path)
    assert store.records("team_season", 2026, path=path) == []
    with pytest.raises(seal.HoldoutAccessDenied):
        seal.reject(["2026_12_NE_BUF"])
    with pytest.raises(seal.HoldoutAccessDenied, match="explicit authorized"):
        seal.review_connection(authorized=True, now="2028-01-01T00:00:00Z")


def test_missing_protocol_fails_closed_for_registered_database(sealed):
    path, _, _ = sealed
    with store.connection(path) as db, db:
        db.execute("INSERT INTO nfl_forecast_studies VALUES ('fixture', 'hash', '{}')")
    (Path(os.environ["SCORPRED_DATA_ROOT"]) / "cache/nflverse/phase2_2/protocol.json").unlink()
    with pytest.raises(seal.HoldoutAccessDenied, match="requires"):
        store.records("schedules", path=path)


@pytest.mark.parametrize("source", [None, {}, {"state": "fresh"}, {"observed_at": "bad"}])
def test_unknown_freshness_is_unavailable_at_runtime(source):
    kickoff = datetime.now(UTC) + timedelta(hours=2)
    assert unavailable_reason(kickoff, "NS", source)
    card = {"matchId": "fixture", "matchDate": kickoff.isoformat(), "action": "BET", "dataFreshness": source}
    assert sanitize_decision(card)["action"] == "SKIP"
    now = datetime.now(UTC)
    assert unavailable_reason(kickoff, "NS", metadata("source", now.isoformat(), 120, now=now), now) is None


@pytest.mark.parametrize("market", ["clearances", "aerial_duels_won", "passes_completed", "chances_created"])
def test_soccer_unverified_market_definitions_are_not_proxies(market):
    raw = [{"players": [{"player": {"id": 1}, "statistics": [
        {"tackles": {"blocks": 7}, "duels": {"won": 9}, "passes": {"total": 40, "key": 3}}]}]}]
    assert props._soccer_fixture_player_value(raw, 1, market) is None
    assert props._soccer_season_avg(raw[0]["players"][0]["statistics"][0], market) is None


@pytest.mark.parametrize("value", [None, "bad", -1, float("nan"), True])
def test_missing_malformed_stats_never_become_zero(value):
    raw = [{"players": [{"player": {"id": 1}, "statistics": [{"goals": {"total": value, "assists": 1}}]}]}]
    assert props._soccer_fixture_player_value(raw, 1, "goals") is None
    assert props._soccer_fixture_player_value(raw, 1, "goal_or_assist") is None


def test_legitimate_zero_and_complete_composite_retained():
    raw = [{"players": [{"player": {"id": 1}, "statistics": [{"goals": {"total": 0, "assists": 0}}]}]}]
    assert props._soccer_fixture_player_value(raw, 1, "goals") == 0
    assert props._soccer_fixture_player_value(raw, 1, "goal_or_assist") == 0


def test_nba_missing_coverage_cannot_manufacture_market(monkeypatch):
    monkeypatch.setattr(props, "_collect_nba_samples", lambda *a: {"full_season_log": []})
    monkeypatch.setattr(props, "_fetch_nba_opponent_stats", lambda *a: pytest.fail("No opponent fetch needed without player coverage"))
    result = props.generate_props(sport="nba", player_id=1, player_name="Fixture", player_team_id=2,
        opponent_team_id=3, opponent_name="Other", is_home=True, markets=["points"], season=2027)
    assert result["props"] == [] and not result["coverage"]["available"]
    assert "coverage is unavailable" in result["errors"][0]
    assert nba_routes._build_nba_props_from_avgs({"avgPoints": 30}, False) == []


def test_nba_props_api_rejects_malformed_ids_and_uses_current_season(monkeypatch):
    client = module.app.test_client()
    assert client.post("/nba/props/generate", json={"player_id": "bad"}).status_code == 400
    captured = {}
    def generate(**kwargs):
        captured.update(kwargs)
        return {"props": [], "coverage": {"available": False}}
    monkeypatch.setattr(props, "generate_props", generate)
    response = client.post("/nba/props/generate", json={"player_id": 1, "player_team_id": 2, "opponent_id": 3})
    assert response.status_code == 200
    assert captured["season"] == nba_routes.nc.NBA_SEASON
    assert response.json["last_updated"] is None


def test_collector_owner_and_failure_retry_preserve_rows(tmp_path, monkeypatch):
    path, state = tmp_path / "nfl.db", tmp_path / "state"
    with FileLock(str(path.resolve()) + ".collector.lock", timeout=0), pytest.raises(Timeout):
        nfl_collector.run(path, state, once=True)
    # Existing importer defers failed attempts without rewriting successful data.
    from tests.test_nfl_analytics import play
    now = datetime.now(UTC)
    store.replace_dataset("pbp", now.year, [play(season=now.year)], path=path, synced_at=now - timedelta(days=1))
    before = copy.deepcopy(store.metadata(path=path))
    store.record_refresh("pbp", now.year, "failed", error="ConnectionError", now=now, path=path)
    result = sync_nflverse.sync([now.year], ["pbp"], path=path, refresh=True, keep_going=True, now=now)
    assert result[0]["retry_backoff"] and result[0]["status"] == "failed"
    assert store.metadata(path=path) == before


def test_slow_collection_does_not_add_a_full_extra_wait(tmp_path, monkeypatch):
    path, state = tmp_path / "cadence.db", tmp_path / "cadence"
    class Clock(datetime):
        value = datetime(2026, 10, 21, tzinfo=UTC)
        @classmethod
        def now(cls, tz=None):
            return cls.value
    class Stop:
        def is_set(self):
            return False
        def set(self):
            pass
        def wait(self, seconds):
            Clock.value += timedelta(seconds=seconds)
            return False
    starts = []
    def cycle(*args, **kwargs):
        starts.append(Clock.value)
        if len(starts) == 1:
            Clock.value += timedelta(seconds=239)
        else:
            (state / "stop.request").write_text("Synthetic stop after cadence check")
        return {"status": "success", "snapshot_count": 0}
    monkeypatch.setattr(nfl_collector, "datetime", Clock)
    monkeypatch.setattr(nfl_collector.threading, "Event", Stop)
    monkeypatch.setattr(nfl_collector, "collect_cycle", cycle)
    nfl_collector.run(path, state, interval_seconds=240)
    assert len(starts) == 2
    assert (starts[1] - starts[0]).total_seconds() <= 245


def test_frozen_export_replay_and_original_hashes_unchanged():
    from scripts.verify_nfl_forecast_exports import verify
    root = Path(__file__).resolve().parents[1]
    baseline = json.loads((root / "docs/assets/nfl_stage1/preservation_artifacts.json").read_text())
    for name, expected in baseline["files"].items():
        assert hashlib.sha256((root / Path(name.replace("\\", "/"))).read_bytes()).hexdigest() == expected
    result = verify(root / "docs/assets/nfl_phase2_3", "fd4526c5da021c30c28538dcdb67faadc18f581d7ac451a2d56f1f6589534cb7")
    assert result["verified_forecasts"] == 2 and result["maximum_probability_error"] == 0


def test_expired_nba_overview_is_preserved_but_not_served(tmp_path, monkeypatch):
    monkeypatch.setattr(nba_routes, "_ESPN_NBA_OVERVIEW_CACHE", tmp_path)
    monkeypatch.setattr(nba_routes, "_ESPN_RETRIES", 1)
    key = hashlib.md5(b"nba_espn_overview:1966").hexdigest()
    path = tmp_path / ("espn_ov_" + key + ".json")
    path.write_text('{"statistics": {}}')
    old = (datetime.now(UTC) - timedelta(days=3)).timestamp()
    os.utime(path, (old, old))
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match="unavailable"):
        nba_routes._espn_player_overview("1966")
    assert path.read_bytes() == original and path.stat().st_mtime == old


def test_nba_overview_never_invents_missing_stats_or_prop_confidence(monkeypatch):
    source = metadata("synthetic overview", datetime.now(UTC).isoformat(), 1800)
    overview = {"statistics": {"names": ["avgPoints"], "splits": [{"displayName": "Regular Season", "stats": [20]}]},
                "_scorpred_source": source}
    monkeypatch.setattr(nba_routes, "_espn_player_overview", lambda *_: overview)
    result = nba_routes._build_player_analysis("1966")
    assert result["season_avgs"]["points"] == 20
    assert result["season_avgs"]["tpm"] is None
    assert result["season_avgs"]["assists"] is None
    assert result["props"] == [] and result["last_updated"] == source["observed_at"]


def test_soccer_preparation_quarantines_wrong_season_without_mutating_source(tmp_path):
    from prepare_dataset import prepare
    folder = tmp_path / "raw"
    folder.mkdir()
    content = "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n30/08/2025,A,B,2,1,H\n"
    for name in ("E0_2018.csv", "E0_2025.csv"):
        (folder / name).write_text(content)
    output = tmp_path / "new.csv"
    manifest = prepare(folder, output)
    assert manifest["rows"] == 1 and manifest["duplicate_excess"] == 0
    assert len(manifest["quarantined"]) == 1
    assert (folder / "E0_2018.csv").read_text() == content
    with pytest.raises(ValueError, match="preserved"):
        prepare(folder, output)


@pytest.mark.parametrize("route", ["/prediction", "/matchup"])
def test_soccer_routes_supply_distinct_rule5_ml10_and_target_date(monkeypatch, route):
    from tests.source_contracts import fresh_source
    client = module.app.test_client()
    target = datetime.now(UTC) + timedelta(days=3)
    module._FIXTURE_INDEX["stage1-target"] = {"fixture": {"id": "stage1-target", "date": target.isoformat(), "status": {"short": "NS"}},
                                           "data_freshness": fresh_source()}
    with client.session_transaction() as session:
        session.update(team_a_id=33, team_b_id=40, team_a_name="Home", team_b_name="Away", team_a_logo="", team_b_logo="",
                       selected_fixture={"fixture_id": "stage1-target", "date": target.isoformat()})
    rows = [{"fixture": {"id": i, "date": (datetime.now(UTC) - timedelta(days=i + 2)).isoformat(), "status": {"short": "FT"}},
             "teams": {"home": {"id": 33, "name": "Home", "logo": ""}, "away": {"id": 40, "name": "Away", "logo": ""}},
             "goals": {"home": 2 if i < 5 else 0, "away": 1}, "league": {"id": 39, "name": "Premier League", "season": module.SEASON},
             "data_freshness": fresh_source()} for i in range(10)]
    monkeypatch.setattr(module.ac, "get_team_fixtures", lambda *a, **k: copy.deepcopy(rows))
    monkeypatch.setattr(module.ac, "get_h2h", lambda *a, **k: [])
    monkeypatch.setattr(module.ac, "get_injuries", lambda *a, **k: [])
    monkeypatch.setattr(module.ac, "get_standings", lambda *a, **k: [])
    monkeypatch.setattr(module.ac, "get_squad", lambda *a, **k: [])
    contexts = []
    original = module.sm.predict_match
    def capture(context):
        contexts.append(context)
        return original(context)
    monkeypatch.setattr(module.sm, "predict_match", capture)
    response = client.get(route)
    assert response.status_code == 200 and len(contexts) == 1
    context = contexts[0]
    assert len(context["form_a"]) == len(context["form_b"]) == 5
    assert len(context["ml_form_a"]) == len(context["ml_form_b"]) == 10
    assert context["target_date"] == target.isoformat()
    features = module.sm._ml_features(context)
    assert features["home_avg_gf_10"] == 1
    assert features["home_gf_delta_5v10"] == 1
