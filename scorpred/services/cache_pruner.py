"""Bound the disk used by provider response caches.

Only folders that hold re-fetchable HTTP responses are pruned. Archives, feature
stores, prediction history and the auth database live elsewhere and are never touched.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from runtime_paths import cache_dir

_log = logging.getLogger(__name__)

PRUNABLE_FOLDERS = ("football", "nba", "nba_public", "props", "odds", "service")
DEFAULT_MAX_MB = 300
DEFAULT_MAX_AGE_DAYS = 7
_TEMP_MAX_AGE_SECONDS = 24 * 3600


def _limits() -> tuple[int, float]:
    max_mb = int(os.getenv("SCORPRED_CACHE_MAX_MB", str(DEFAULT_MAX_MB)))
    max_age_days = float(os.getenv("SCORPRED_CACHE_MAX_AGE_DAYS", str(DEFAULT_MAX_AGE_DAYS)))
    return max_mb * 1024 * 1024, max_age_days * 86400


def _cache_files(roots: list[Path]) -> list[tuple[float, int, Path]]:
    files = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".json", ".tmp"} or not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((stat.st_mtime, stat.st_size, path))
    return files


def prune(*, max_bytes: int | None = None, max_age_seconds: float | None = None,
          roots: list[Path] | None = None, now: float | None = None) -> dict:
    """Delete expired response files, then the oldest ones until under the size cap."""
    default_bytes, default_age = _limits()
    max_bytes = default_bytes if max_bytes is None else max_bytes
    max_age_seconds = default_age if max_age_seconds is None else max_age_seconds
    roots = roots if roots is not None else [cache_dir(name) for name in PRUNABLE_FOLDERS]
    now = time.time() if now is None else now

    files = sorted(_cache_files(roots))
    before = sum(size for _, size, _ in files)
    removed = freed = 0
    kept = []
    for mtime, size, path in files:
        age = now - mtime
        expired = age > max_age_seconds or (path.suffix == ".tmp" and age > _TEMP_MAX_AGE_SECONDS)
        if expired and _unlink(path):
            removed += 1
            freed += size
        else:
            kept.append((mtime, size, path))

    total = before - freed
    for _, size, path in kept:
        if total <= max_bytes:
            break
        if _unlink(path):
            removed += 1
            freed += size
            total -= size

    result = {"files_removed": removed, "bytes_freed": freed, "bytes_before": before,
              "bytes_after": before - freed, "max_bytes": max_bytes}
    _log.info("cache_prune removed=%s freed_mb=%.1f after_mb=%.1f", removed, freed / 1048576, (before - freed) / 1048576)
    return result


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        _log.debug("cache_prune could not remove %s", path, exc_info=True)
        return False
