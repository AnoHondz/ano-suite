import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import requests

import nfl_client
from services import nfl_features
from services import nflverse_provider as provider
from services import nflverse_store as store

NOW = datetime.now(UTC)
SEASON = NOW.year - 1 if NOW.month <= 2 else NOW.year
TEAMS = [{"team_abbr": code, "team_name": f"Team {code}", "team_logo_espn": ""} for code in ("AA", "BB", "CC", "DD")]


def game(game_id, days, home="AA", away="BB", hs=None, aws=None, **changes):
    kickoff = (NOW + timedelta(days=days)).astimezone(nfl_client.EASTERN)
    row = {"game_id": game_id, "season": SEASON, "game_type": "REG", "week": 6,
           "gameday": kickoff.date().isoformat(), "gametime": kickoff.strftime("%H:%M"),
           "home_team": home, "away_team": away, "home_score": hs, "away_score": aws,
           "espn": "123" if days > 0 else None, "location": "Home", "stadium": "Test stadium"}
    row.update(changes)
    return row


def seed(path, stamp=NOW):
    rows = []
    for index in range(4):
        rows.extend([game(f"a-{index}", -7 * (index + 1), home="AA", away="CC", hs=28, aws=14),
                     game(f"b-{index}", -7 * (index + 1), home="BB", away="DD", hs=21, aws=24)])
    rows.append(game("target", 3))
    store.replace_dataset("teams", 0, TEAMS, path=path, synced_at=stamp)
    store.replace_dataset("schedules", SEASON, rows, path=path, synced_at=stamp)
    stats = []
    for row in rows:
        for team in (row["home_team"], row["away_team"]):
            stats.append({"game_id": row["game_id"], "season": SEASON, "team": team,
                          "passing_epa": 3, "rushing_epa": 2, "attempts": 30, "carries": 20,
                          "sacks_suffered": 2, "passing_cpoe": 1, "passing_interceptions": 1, "fumbles_lost_total": 0})
    store.replace_dataset("team_stats", SEASON, stats, path=path, synced_at=stamp)
    return rows, stats


def test_store_rejects_empty_and_duplicate_import_without_losing_old_rows(tmp_path):
    path = tmp_path / "nfl.db"
    seed(path)
    before = store.records("schedules", path=path)
    for invalid in ([], [before[0], before[0]], [{"game_id": "invalid"}]):
        with pytest.raises((ValueError, TypeError)):
            store.replace_dataset("schedules", SEASON, invalid, path=path)
        assert store.records("schedules", path=path) == before
    assert store.metadata(path=path)


def test_provider_preserves_external_event_ids_but_does_not_reuse_them_as_team_ids(tmp_path, monkeypatch):
    path = tmp_path / "nfl.db"
    seed(path)
    monkeypatch.setattr(store, "database_path", lambda: path)
    monkeypatch.setattr(provider, "refresh_schedules", Mock(side_effect=AssertionError("Fresh snapshots need no network")))
    records, source = provider.load_records(NOW.date(), (NOW + timedelta(days=14)).date(), NOW)
    target = next(row for row in records if row["nflverse_id"] == "target")
    assert target["id"] == "123"
    assert target["home_id"] == "AA"
    assert target["data_source"] == source["source"] == "nflverse"
    assert target["state"] == "pre"


def test_stale_snapshot_is_labeled_and_old_snapshot_fails_to_explicit_fallback(tmp_path, monkeypatch):
    path = tmp_path / "nfl.db"
    seed(path, NOW - timedelta(hours=2))
    monkeypatch.setattr(store, "database_path", lambda: path)
    monkeypatch.setattr(provider, "refresh_schedules", Mock(side_effect=requests.Timeout()))
    _, source = provider.load_records(NOW.date(), (NOW + timedelta(days=14)).date(), NOW)
    assert "delayed" in source["warning"]
    with pytest.raises(provider.NFLVerseUnavailable):
        provider.load_records(NOW.date(), (NOW + timedelta(days=14)).date(), NOW + timedelta(days=2))


def test_tbd_kickoff_is_not_invented_and_neutral_site_is_preserved():
    teams = {row["team_abbr"]: row for row in TEAMS}
    assert provider.normalize_game(game("x", 3, gametime=None), teams, NOW) is None
    target = provider.normalize_game(game("x", 3, location="Neutral"), teams, NOW)
    assert target["neutral"] is True


def test_current_scores_stats_weather_and_closing_odds_cannot_enter_current_features(tmp_path):
    path = tmp_path / "nfl.db"
    schedules, stats = seed(path)
    before = next(row for row in nfl_features.build_features(path=path, now=NOW) if row["game_id"] == "target")
    assert before["home_points_for"] == 28
    assert before["away_points_for"] == 21
    assert datetime.fromisoformat(before["history_max_kickoff"]) < datetime.fromisoformat(before["feature_cutoff"])
    changed = deepcopy(schedules)
    changed[-1].update(home_score=99, away_score=0, home_moneyline=-999, temp=-100, wind=90)
    store.replace_dataset("schedules", SEASON, changed, path=path)
    for row in stats:
        if row["game_id"] == "target":
            row.update(passing_epa=99999, rushing_epa=99999)
    store.replace_dataset("team_stats", SEASON, stats, path=path)
    after = next(row for row in nfl_features.build_features(path=path, now=NOW) if row["game_id"] == "target")
    assert {name: before[name] for name in nfl_features.FEATURE_NAMES} == {name: after[name] for name in nfl_features.FEATURE_NAMES}
    assert after["target"] is None  # Future reported scores cannot create a completed target.


def test_nfl_dashboard_uses_local_nflverse_and_keeps_experimental_actions(tmp_path, monkeypatch):
    from app import app

    path = tmp_path / "nfl.db"
    seed(path)
    store.replace_dataset("ngs_passing", SEASON, [{"season": SEASON, "week": 6, "season_type": "REG",
                                                "team_abbr": "AA", "player_gsis_id": "00-123"}], path=path)
    store.replace_dataset("players", 0, [{"gsis_id": "00-123", "display_name": "Test Player"}], path=path)
    monkeypatch.setenv("NFL_DATA_SOURCE", "nflverse")
    monkeypatch.setattr(store, "database_path", lambda: path)
    monkeypatch.setattr(nfl_client, "calendar_events", lambda *_: pytest.fail("Do not fetch ESPN when nflverse is ready"))
    response = app.test_client().get("/api/dashboard/nfl")
    assert response.status_code == 200
    assert response.json["source"] == "nflverse"
    card = response.json["slate"][0]
    assert card["matchId"] == "123"
    assert card["action"] in {"SKIP", "CONSIDER"}
    assert card["modelSource"] == "nflverse_recent_scoring_v1"
    sources = response.json["dataSources"]
    passing = next(row for row in sources["researchDatasets"] if row["name"] == "ngs_passing")
    assert passing["rows"] == 1 and passing["latestWeek"] == 6
    assert passing["status"] == "available"
    assert passing["observedAt"] and passing["expiresAt"]
    assert next(row for row in sources["researchDatasets"] if row["name"] == "players")["season"] == 0
    assert sources["injuries"] == "not_connected"  # Research snapshots cannot confirm pregame availability.


def test_missing_local_store_uses_labeled_espn_fallback(tmp_path, monkeypatch):
    from app import app

    monkeypatch.setenv("NFL_DATA_SOURCE", "nflverse")
    monkeypatch.setattr(store, "database_path", lambda: tmp_path / "missing.db")
    monkeypatch.setattr(nfl_client, "calendar_events", lambda *_: [])
    response = app.test_client().get("/api/dashboard/nfl")
    assert response.status_code == 200
    assert response.json["source"] == "espn"
    assert "fallback" in response.json["warnings"][0]


def test_research_training_is_time_ordered_and_never_activates_itself(tmp_path, monkeypatch):
    import nfl_model

    with pytest.raises(ValueError, match="50 completed games"):
        nfl_model.split_rows([])
    rows = []
    for index in range(300):
        kickoff = NOW - timedelta(days=400 - index)
        row = {name: float(index % 13) for name in nfl_features.FEATURE_NAMES}
        row.update(game_id=str(index), date=kickoff.date().isoformat(), kickoff=kickoff.isoformat(),
                   target=index % 3, home_score=20 + index % 14, away_score=17 + index % 14)
        rows.append(row)
    monkeypatch.setattr(nfl_model, "build_features", lambda **kwargs: rows)
    monkeypatch.setattr(nfl_model, "metadata", lambda **kwargs: [])
    report = nfl_model.train(output=tmp_path / "research.joblib", report_path=tmp_path / "evaluation.json", now=NOW)
    assert report["live_use"] is False
    assert report["windows"]["train"]["end"] < report["windows"]["calibration"]["start"]
    assert report["windows"]["calibration"]["end"] < report["windows"]["test"]["start"]
    assert report["artifact_sha256"]
    assert json.loads((tmp_path / "evaluation.json").read_text())["role"] == "research"
