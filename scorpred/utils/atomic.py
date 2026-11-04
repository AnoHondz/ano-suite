"""Cross-process transactions and atomic JSON replacement for single-disk storage."""
from contextlib import contextmanager
from functools import wraps
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from filelock import FileLock

_registry_lock = threading.Lock()
_locks = {}


@contextmanager
def file_transaction(path):
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _registry_lock:
        locks = _locks.setdefault(str(target), (threading.RLock(), FileLock(str(target) + ".lock", timeout=30)))
    with locks[0], locks[1]:
        yield


def atomic_write_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows can briefly deny replacement while another reader or an
        # antivirus scanner holds the destination. Preserve atomicity on retry.
        for attempt in range(6):
            try:
                os.replace(temporary, target)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02 * 2 ** attempt)
    finally:
        Path(temporary).unlink(missing_ok=True)


def transaction(path_getter):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with file_transaction(path_getter()):
                return function(*args, **kwargs)
        return wrapped
    return decorate
