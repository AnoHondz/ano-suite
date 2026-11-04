"""NBA B1 contracts use synthetic games and retained metadata, never new assets."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import urllib.request

import pytest
import requests

from runtime_paths import repo_root
from scripts.reconcile_nba_history import build_bundle, write_bundle
from services import nba_historical_reconciliation as reconciliation
from services import nba_provider_registry as registry


def event(game_id="401000001", season=2023, score="100", completed=True, season_type=2):
    return {"id": game_id, "date": "2023-01-02T00:00:00Z",
            "season": {"year": season, "type": season_type},
            "status": {"type": {"completed": completed}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "id": "1", "score": score},
                {"homeAway": "away", "id": "2", "score": "90"}]}]}


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_bundled_candidates_all_disabled_even_with_environment_override(monkeypatch):
    monkeypatch.setenv("NBA_PROVIDERS_ENABLED", "1")
    document = registry.load_registry()
    assert {registry.canonical_repository(p["repository_url"]) for p in document["providers"]} == {
        registry.canonical_repository(r) for r in (
            "sportsdataverse/sportsdataverse-data", "sportsdataverse/hoopR-nba-stats-data", "shufinskiy/nba_data")}
    for provider in document["providers"]:
        assert all(provider[flag] is False for flag in registry.FLAGS)
        for purpose in registry.PURPOSES:
            assert not registry.asset_access_decision(provider, purpose)["allowed"]
            with pytest.raises(PermissionError):
                registry.require_asset_access(provider, purpose)


@pytest.mark.parametrize("status", sorted(registry.PERMISSION_STATUSES))
def test_permission_gate_cannot_activate_collection(status):
    document = deepcopy(registry.load_registry())
    provider = document["providers"][0]
    provider["permission"]["status"] = status
    if status == registry.PERMITTED:
        with pytest.raises(ValueError, match="approval"):
            registry.validate_registry(document)
        provider["permission"]["approval"] = {
            "verified": True, "evidence_url": "https://example.org/purpose-specific-permission",
            "reviewed_at": "2026-10-22", "purposes": ["collection"]}
        registry.validate_registry(document)
    provider.update(enabled=True, collection_enabled=True)
    decision = registry.asset_access_decision(provider)
    assert decision["allowed"] is False
    assert "metadata_only_phase_has_no_asset_adapter" in decision["reasons"]
    if status != registry.PERMITTED:
        assert "intended_use_permission_not_verified" in decision["reasons"]
    with pytest.raises(ValueError, match="disabled"):
        registry.validate_registry(document)


@pytest.mark.parametrize("flag,value", [("enabled", True), ("collection_enabled", "false"),
                                        ("training_enabled", 0), ("serving_enabled", None)])
def test_flags_fail_closed(flag, value):
    document = deepcopy(registry.load_registry())
    document["providers"][0][flag] = value
    with pytest.raises(ValueError, match="disabled"):
        registry.validate_registry(document)


def test_duplicate_candidates_and_dataset_aliases_rejected():
    document = deepcopy(registry.load_registry())
    duplicate = deepcopy(document["providers"][0])
    duplicate.update(provider_id="different_id", repository_url="https://github.com/SPORTSDATAVERSE/sportsdataverse-data.git/")
    document["providers"].append(duplicate)
    with pytest.raises(ValueError, match="Duplicate"):
        registry.validate_registry(document)
    document["providers"].pop()
    document["providers"][0]["datasets"].append(deepcopy(document["providers"][0]["datasets"][0]))
    with pytest.raises(ValueError, match="duplicate dataset"):
        registry.validate_registry(document)


def test_asset_url_not_repository_and_unknown_permission_rejected():
    with pytest.raises(ValueError, match="repository"):
        registry.canonical_repository("https://github.com/owner/repo/releases/download/asset.parquet")
    document = deepcopy(registry.load_registry())
    document["providers"][0]["permission"]["status"] = "public"
    with pytest.raises(ValueError, match="permission"):
        registry.validate_registry(document)


def test_local_eligibility_deduplication_and_no_mutation(tmp_path):
    monthly = tmp_path / "monthly"
    put(monthly / "one.json", {"events": [event(), event(completed=False), event(season_type=1), event(score="0")]})
    put(monthly / "two.json", {"events": [event()]})
    before = {p.name: p.read_bytes() for p in monthly.iterdir()}
    result = reconciliation.local_history(monthly)
    assert result["eligible_games"] == 1
    assert result["raw_events"] == 5
    assert result["duplicate_eligible_observations"] == 1
    assert result["games"][0]["source_files"] == ["one.json", "two.json"]
    assert "score" not in json.dumps(result["games"])
    assert before == {p.name: p.read_bytes() for p in monthly.iterdir()}
    put(monthly / "three.json", {"events": [event(score="101")]})
    with pytest.raises(ValueError, match="Conflicting"):
        reconciliation.local_history(monthly)


def test_no_reference_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="empty reference"):
        reconciliation.local_history(tmp_path)


def test_release_metadata_filters_sidecars_and_preserves_unknowns():
    dataset = {"dataset_id": "schedules", "release_tag": "test", "file_stem": "nba_schedule"}
    asset = {"id": 5, "name": "nba_schedule_2023.parquet", "updated_at": "2026-10-21T00:00:00Z",
             "browser_download_url": "https://example.org/must-not-fetch.parquet", "digest": "sha256:sample"}
    release = {"tag": "test", "repository": "https://github.com/sportsdataverse/sportsdataverse-data",
               "assets": [asset, asset | {"name": "timestamp_2050.json"},
                          asset | {"name": "nba_games_in_data_repo.csv"}],
               "payload_downloaded": False, "observed_at": "2026-10-22", "release_created_at": "2023-01-01",
               "evidence_url": "https://api.github.com/metadata"}
    result = reconciliation.release_manifest(dataset, [release])
    assert result["asset_seasons"] == [2023]
    assert len(result["assets"]) == 1
    assert result["game_ids"] is None and result["completeness"] == "unverifiable"
    assert result["assets"][0]["id"] == 5
    with pytest.raises(ValueError, match="Duplicate release"):
        reconciliation.release_manifest(dataset, [release, release])
    release["assets"].append(asset)
    with pytest.raises(ValueError, match="Duplicate season asset"):
        reconciliation.release_manifest(dataset, [release])


def test_file_index_years_scopes_and_duplicates():
    dataset = {"dataset_id": "nbastatsv3", "file_stem": "nbastatsv3"}
    document = {"status": 200, "observed_at": "2026-10-22", "url": "https://example.org/index.txt",
                "text": "nbastatsv3_2025=https://example.org/regular.tar.xz\n"
                        "nbastatsv3_po_2025=https://example.org/playoff.tar.xz\n"
                        "wnba_nbastatsv3_2026=https://example.org/wnba.tar.xz\n"}
    result = reconciliation.index_manifest(dataset, document)
    assert result["asset_seasons"] == [2026]
    assert {a["competition"] for a in result["assets"]} == {"regular", "playoffs"}
    assert all(a["source_season"] == 2025 for a in result["assets"])
    assert all(a["updated_at"] is None for a in result["assets"])
    document["text"] += "nbastatsv3_2025=https://example.org/duplicate.tar.xz"
    with pytest.raises(ValueError, match="Duplicate file-index"):
        reconciliation.index_manifest(dataset, document)


def test_absent_evidence_no_fallback_download_or_game_matches(tmp_path):
    candidates = reconciliation.candidate_manifests(registry.load_registry(), tmp_path)
    assert all(source["status"] == "absent" for source in candidates["source_files"])
    assert all(d["manifest_status"] == "absent" and d["game_ids"] is None
               for p in candidates["providers"] for d in p["datasets"])


def test_changed_metadata_snapshot_is_rejected(tmp_path):
    put(tmp_path / "release_manifests.json", [])
    with pytest.raises(ValueError, match="hash mismatch"):
        reconciliation.candidate_manifests(registry.load_registry(), tmp_path)


def test_cached_minutes_dnp_zero_missing_and_ids(tmp_path):
    history = [{"game_id": "001", "home_team_id": "1", "away_team_id": "2"}]
    def group(team, value):
        return {"team": {"id": team}, "statistics": [{"names": ["MIN"], "athletes": [
            {"athlete": {"id": "009"}, "stats": [value]},
            {"athlete": {"id": "010"}, "didNotPlay": True, "stats": []}]}]}
    payload = {"header": {"id": "001"}, "boxscore": {"teams": [
        {"team": {"id": t}, "statistics": [{"name": "points"}]} for t in ("1", "2")],
        "players": [group("1", "0"), group("2", "12:30")]}}
    put(tmp_path / "summary.json", payload)
    result = reconciliation.local_box_coverage(tmp_path, history)
    assert result["paired_player_minutes_game_ids"] == ["001"]
    payload["boxscore"]["players"][1] = group("2", None)
    put(tmp_path / "summary.json", payload)
    result = reconciliation.local_box_coverage(tmp_path, history)
    assert result["paired_player_minutes_game_ids"] == []
    assert result["missing_minute_rows_in_cached_boxes"] == [{"game_id": "001", "team_id": "2", "player_id": "009"}]


def test_offline_bundle_deterministic_and_sources_unchanged(monkeypatch, tmp_path):
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(str(args))
        raise AssertionError("No network calls are permitted, including asset HEAD/GET")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monthly, live, evidence = [tmp_path / kind for kind in ("monthly", "live", "evidence")]
    for directory in (monthly, live, evidence):
        directory.mkdir()
    put(monthly / "month.json", {"events": [event()]})
    paths = list(monthly.iterdir())
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    args = (registry.REGISTRY_PATH, monthly, live, evidence, tmp_path / "absent-audit.json")
    first, second = build_bundle(*args), build_bundle(*args)
    assert first == second
    assert first["bundle.json"]["content_sha256"] == second["bundle.json"]["content_sha256"]
    report = first["reconciliation.json"]
    assert report["reference_games"] == 1
    assert report["third_party_game_level_matches"] is None
    assert report["upstream_missing_game_count"] is None
    assert all(c["verified_game_matches"] is None and c["net_new_games"] is None
               for c in report["candidate_comparisons"])
    output = tmp_path / "output"
    write_bundle(output, first, (monthly, live, evidence))
    saved = {p.name: p.read_bytes() for p in output.iterdir()}
    write_bundle(output, second, (monthly, live, evidence))
    assert saved == {p.name: p.read_bytes() for p in output.iterdir()}
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    assert attempts == []
    with pytest.raises(ValueError, match="overlap"):
        write_bundle(monthly / "output", first, (monthly, live, evidence))
    unowned = tmp_path / "unowned"
    put(unowned / "existing.json", {"preserve": True})
    with pytest.raises(ValueError, match="unowned"):
        write_bundle(unowned, first, (monthly, live, evidence))


def test_no_importer_database_app_or_prediction_dependencies():
    forbidden = {"requests", "httpx", "socket", "sqlite3", "sqlalchemy", "app", "nba_live_client",
                 "nba_client", "nba_train_model", "nfl_model", "nfl_client", "sklearn"}
    paths = [Path(registry.__file__), Path(reconciliation.__file__), repo_root() / "scripts/reconcile_nba_history.py"]
    for path in paths:
        tree = ast.parse(path.read_text())
        imports = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imports |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        assert not imports & forbidden
    # Existing collection/serving modules do not import this research registry.
    for name in ("app.py", "nba_routes.py", "nba_live_client.py", "nba_predictor.py"):
        assert "nba_provider_registry" not in (repo_root() / name).read_text(encoding="utf-8")
