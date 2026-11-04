"""Bounded, single-flight loading for slow cold soccer forecasts."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic
from datetime import datetime, UTC

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="soccer-props")
_lock = Lock()
_jobs = {}


def _expired(value):
    if isinstance(value, dict):
        expires = value.get("expires_at")
        if isinstance(expires, str):
            try:
                if datetime.fromisoformat(expires.replace("Z", "+00:00")) <= datetime.now(UTC):
                    return True
            except (ValueError, TypeError):
                return True
        return any(_expired(item) for item in value.values())
    return isinstance(value, list) and any(_expired(item) for item in value)


def load(key, calculate):
    with _lock:
        now = monotonic()
        for old_key, (future, started) in list(_jobs.items()):
            if future.done() and not hasattr(future, "ready_at"):
                future.ready_at = now
            if future.done() and (now - future.ready_at > 120 or
                                  (future.exception() is None and _expired(future.result()[0]))):
                del _jobs[old_key]
        if key not in _jobs:
            if len(_jobs) >= 32:
                return {"error": "Player forecasts are busy. Please retry shortly."}, 503
            _jobs[key] = (_pool.submit(calculate), now)
        future, _ = _jobs[key]
    if not future.done():
        return {"refresh_pending": True, "retry_after": 4}, 202
    try:
        result = future.result()
        if result[1] != 200:
            with _lock:
                _jobs.pop(key, None)
        return result
    except Exception:
        with _lock:
            _jobs.pop(key, None)
        return {"error": "Player forecasts could not refresh. Please try again."}, 503
