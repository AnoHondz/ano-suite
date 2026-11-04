"""Free imports must preserve old snapshots and honest coverage on failures."""
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import sync_nflverse as importer
from services import nflverse_provider as provider
from services import nflverse_store as store


def context_row(dataset):
    if dataset == "players":
        return {"gsis_id": "00-123", "display_name": "Player Example"}
    row = {"season": 2026, "week": 6, "team": "BUF", "season_type": "REG",
           "game_type": "REG", "gsis_id": "00-123"}
    if dataset == "snap_counts":
        row.update(game_id="2026_06_BUF_NE", pfr_player_id="ExamPl00")
    if dataset.startswith("ngs_"):
        row.pop("team")
        row.update(team_abbr="BUF", player_gsis_id="00-123")
    return row


@pytest.mark.parametrize("dataset", ["snap_counts", "ngs_passing", "ngs_rushing", "ngs_receiving", "weekly_rosters", "players", "injuries"])
def test_invalid_context_preserves_rows_and_original_observation(tmp_path, dataset):
    path = tmp_path / "nfl.db"
    season = 0 if dataset == "players" else 2026
    row = context_row(dataset)
    store.replace_dataset(dataset, season, [row], path=path, synced_at=datetime(2026, 10, 17, tzinfo=UTC))
    before = store.metadata(path=path)
    invalid = dict(row)
    invalid.pop("gsis_id") if dataset == "players" else invalid.update(season=2025)
    for rows in ([invalid], [row, row], []):
        with pytest.raises(ValueError):
            store.replace_dataset(dataset, season, rows, path=path)
        assert store.records(dataset, season, path=path) == [row]
        assert store.metadata(path=path) == before


def test_weekly_roster_without_gsis_is_retained_without_fabricating_an_id(tmp_path):
    row = context_row("weekly_rosters")
    row.update(gsis_id=None, full_name="Unlinked Player", birth_date="2000-01-01", position="WR")
    path = tmp_path / "nfl.db"
    store.replace_dataset("weekly_rosters", 2026, [row], path=path)
    assert store.records("weekly_rosters", 2026, path=path)[0]["gsis_id"] is None


def test_ngs_summary_week_is_not_an_extra_game_and_expired_import_stays_expired(tmp_path, monkeypatch):
    path = tmp_path / "nfl.db"
    now = datetime(2026, 10, 18, tzinfo=UTC)
    row = context_row("ngs_passing")
    store.replace_dataset("ngs_passing", 2026, [dict(row, week=0), row], path=path,
                          synced_at=now - timedelta(hours=49))
    monkeypatch.setattr(store, "database_path", lambda: path)
    meta = store.metadata()[0]
    assert meta["coverage"] == {"weeks": [6], "latest_week": 6, "teams": ["BUF"]}
    freshness = provider.dataset_freshness("ngs_passing", 2026, now=now)
    assert freshness["rows"] == 2 and freshness["latest_week"] == 6
    assert freshness["state"] == "unavailable"
    assert freshness["observed_at"] == (now - timedelta(hours=49)).isoformat()


def mock_sdk(monkeypatch):
    names = ("load_team_stats", "load_player_stats", "load_rosters", "load_depth_charts", "load_pbp",
             "load_snap_counts", "load_rosters_weekly", "load_injuries", "load_nextgen_stats", "load_players")
    sdk = SimpleNamespace(**{name: Mock() for name in names})
    monkeypatch.setitem(sys.modules, "nflreadpy", sdk)
    monkeypatch.setitem(sys.modules, "nflreadpy.config", SimpleNamespace(update_config=Mock()))
    monkeypatch.setitem(sys.modules, "polars", SimpleNamespace())
    return sdk


def test_failed_feed_does_not_block_other_feeds_or_retimestamp_old_data(tmp_path, monkeypatch):
    sdk = mock_sdk(monkeypatch)
    path = tmp_path / "nfl.db"
    row = context_row("snap_counts")
    store.replace_dataset("snap_counts", 2026, [row], path=path, synced_at=datetime(2026, 10, 16, tzinfo=UTC))
    previous = store.metadata(path=path)[0]
    sdk.load_snap_counts.side_effect = TimeoutError("provider down")
    sdk.load_players.return_value = SimpleNamespace(iter_rows=lambda **_: iter([context_row("players")]))
    reports = importer.sync([2025, 2026], ["snap_counts", "players"], path=path, keep_going=True)
    assert [report.get("status") for report in reports] == ["failed", "failed", None]
    assert next(row for row in store.metadata(path=path) if row["dataset"] == "snap_counts") == previous
    assert sdk.load_players.call_count == 1  # global directory is not downloaded per season
    assert store.records("players", 0, path=path)
    with pytest.raises(TimeoutError):
        importer.sync([2026], ["snap_counts"], path=path)


def test_unknown_dataset_rejected_before_any_provider_or_store_write(tmp_path, monkeypatch):
    sdk = mock_sdk(monkeypatch)
    with pytest.raises(ValueError, match="Unknown"):
        importer.sync([2026], ["players", "typo"], path=tmp_path / "nfl.db")
    sdk.load_players.assert_not_called()
    assert not (tmp_path / "nfl.db").exists()


def test_cli_reports_partial_failure_with_nonzero_exit(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["sync_nflverse", "--profile", "free", "--keep-going"])
    sync = Mock(return_value=[{"dataset": "snap_counts", "status": "failed", "error": "TimeoutError"}])
    monkeypatch.setattr(importer, "sync", sync)
    with pytest.raises(SystemExit) as exc:
        importer.main()
    assert exc.value.code == 1
    assert sync.call_args.args[1] == importer.FREE_DATASETS
    assert sync.call_args.kwargs["keep_going"] is True
    assert '"status": "failed"' in capsys.readouterr().out


def test_january_default_uses_the_nfl_season_not_calendar_year():
    assert importer.default_season(datetime(2027, 1, 15, tzinfo=UTC)) == 2026
    assert importer.default_season(datetime(2026, 10, 18, tzinfo=UTC)) == 2026
