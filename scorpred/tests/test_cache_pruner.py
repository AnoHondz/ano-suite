import os

from services import cache_pruner


def _write(path, size, age_seconds, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (now - age_seconds, now - age_seconds))


def test_prune_removes_expired_files_then_oldest_until_under_cap(tmp_path):
    now = 1_800_000_000
    root = tmp_path / "football"
    _write(root / "expired.json", 100, 8 * 86400, now)
    _write(root / "old.json", 400, 3 * 86400, now)
    _write(root / "fd" / "middle.json", 400, 2 * 86400, now)
    _write(root / "new.json", 400, 60, now)
    _write(root / "notes.txt", 50, 30 * 86400, now)

    result = cache_pruner.prune(max_bytes=900, max_age_seconds=7 * 86400, roots=[root], now=now)

    assert not (root / "expired.json").exists()
    assert not (root / "old.json").exists()
    assert (root / "fd" / "middle.json").exists()
    assert (root / "new.json").exists()
    assert (root / "notes.txt").exists()
    assert result["files_removed"] == 2
    assert result["bytes_after"] == 800


def test_prune_removes_abandoned_temporary_files_and_ignores_missing_roots(tmp_path):
    now = 1_800_000_000
    root = tmp_path / "service"
    _write(root / "abc.123.tmp", 10, 2 * 86400, now)
    _write(root / "fresh.123.tmp", 10, 60, now)
    result = cache_pruner.prune(max_bytes=10**9, max_age_seconds=7 * 86400,
                                roots=[root, tmp_path / "missing"], now=now)
    assert not (root / "abc.123.tmp").exists()
    assert (root / "fresh.123.tmp").exists()
    assert result["files_removed"] == 1


def test_prunable_folders_exclude_archives_and_history():
    protected = {"nflverse", "score_models", "nba_context", "diagnostics", "ml", "nba_early_season"}
    assert not protected & set(cache_pruner.PRUNABLE_FOLDERS)
