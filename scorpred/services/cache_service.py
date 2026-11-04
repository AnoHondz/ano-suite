from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import time
import copy
import threading
import uuid
from pathlib import Path
from typing import Any

from cachetools import TLRUCache
from runtime_paths import cache_dir

_logger = logging.getLogger(__name__)

_local_cache = TLRUCache(maxsize=2000, ttu=lambda _key, entry, _now: entry["expires_at"], timer=time.time)
_cache_lock = threading.RLock()
_redis_client = None

# Bump this when canonical payload schema changes so stale shapes are evicted.
_CACHE_SCHEMA_VERSION = "v5"

# Disk persistence: compiled dashboard/fixture responses survive Flask restarts
_DISK_CACHE_DIR = cache_dir("service")
_DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class _LazyModuleProxy:
    def __init__(self, module_name: str):
        self._module_name = module_name
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = importlib.import_module(self._module_name)
        return self._module

    def __getattr__(self, name: str):
        return getattr(self._load(), name)


redis = _LazyModuleProxy("redis")


def _get_redis_client():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if not redis_url:
        return None
    try:
        _redis_client = redis.from_url(redis_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        _redis_client.ping()
        return _redis_client
    except Exception as exc:
        _logger.warning("Redis unavailable, using local cache fallback: %s", exc)
        _redis_client = None
        return None


def _disk_path(key: str) -> Path:
    safe = hashlib.md5(key.encode()).hexdigest()
    return _DISK_CACHE_DIR / f"{safe}.json"


def _disk_get(key: str, ttl: int) -> Any:
    path = _disk_path(key)
    try:
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as f:
            entry = json.load(f)
        return entry if _fresh(entry, ttl) else None
    except Exception:
        return None


def _disk_set(key: str, value: Any) -> None:
    path = _disk_path(key)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as f:
            json.dump(value, f, separators=(",", ":"))
        temporary.replace(path)
    except Exception as exc:
        _logger.debug("Disk cache write failed key=%s: %s", key, exc)
    finally:
        temporary.unlink(missing_ok=True)


def _fresh(entry: Any, ttl: int) -> bool:
    if not isinstance(entry, dict) or "expires_at" not in entry or "created_at" not in entry:
        return False
    now = time.time()
    return now < min(entry["expires_at"], entry["created_at"] + ttl)


def make_key(*parts: Any) -> str:
    """Create a namespaced, versioned cache key."""
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"scorpred:{_CACHE_SCHEMA_VERSION}:{digest}"


def invalidate_pattern(prefix: str) -> int:
    with _cache_lock:
        to_delete = [k for k in list(_local_cache.keys()) if str(k).startswith(prefix)]
    for k in to_delete:
        delete(k)
    return len(to_delete)


def get_json(key: str, ttl: int = 3600) -> Any:
    client = _get_redis_client()
    if client is not None:
        try:
            value = client.get(key)
            if value is not None:
                entry = json.loads(value)
                if _fresh(entry, ttl):
                    return entry["value"]
        except Exception as exc:
            _logger.warning("Redis get failed key=%s: %s", key, exc)

    with _cache_lock:
        entry = _local_cache.get(key)
        if _fresh(entry, ttl):
            return copy.deepcopy(entry["value"])
        _local_cache.pop(key, None)

    # Disk fallback: serves cold-start restarts without recomputing
    entry = _disk_get(key, ttl)
    if entry is not None:
        with _cache_lock:
            _local_cache[key] = entry
        _logger.debug("Disk cache hit for key=%s", key)
        return copy.deepcopy(entry["value"])

    return None


def set_json(key: str, value: Any, ttl: int) -> None:
    if value is None or ttl <= 0:
        return
    now = time.time()
    entry = {"value": copy.deepcopy(value), "created_at": now, "expires_at": now + ttl}
    client = _get_redis_client()
    if client is not None:
        try:
            client.setex(key, ttl, json.dumps(entry))
            return
        except Exception as exc:
            _logger.warning("Redis set failed key=%s: %s", key, exc)
    with _cache_lock:
        _local_cache[key] = entry
    _disk_set(key, entry)


def delete(key: str) -> None:
    client = _get_redis_client()
    if client is not None:
        try:
            client.delete(key)
        except Exception as exc:
            _logger.warning("Redis delete failed key=%s: %s", key, exc)
    with _cache_lock:
        _local_cache.pop(key, None)
    path = _disk_path(key)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
