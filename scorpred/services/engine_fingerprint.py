"""Identify the exact engine that produced a tracked prediction.

A pick is only comparable with another pick from the same engine. The fingerprint
hashes the source of the modules that turn inputs into the published probabilities,
so a code change starts a new series.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any

from runtime_paths import repo_root

_ENGINE_FILES = {
    # Published soccer probabilities come from the ratings model; the rules engine is the fallback.
    "soccer": ("services/soccer_model.py", "services/soccer_ratings.py", "data/models/soccer_ratings_v1.json",
               "scorpred_engine.py"),
    "nba": ("services/nba_pregame_model.py",),
}


def _sha(chunks: list[bytes]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(hashlib.sha256(chunk).digest())
    return digest.hexdigest()[:12]


@lru_cache(maxsize=None)
def for_sport(sport: str) -> dict[str, Any]:
    sport = str(sport or "").lower()
    files = _ENGINE_FILES.get(sport, ())
    chunks = []
    for name in files:
        try:
            chunks.append((repo_root() / name).read_bytes())
        except OSError:
            chunks.append(b"missing:" + name.encode())
    fingerprint: dict[str, Any] = {"code_sha": _sha(chunks) if files else None}
    if sport == "nba":
        from services import nba_pregame_model
        fingerprint["version"] = nba_pregame_model.VERSION
    if sport == "soccer":
        from services import soccer_model
        fingerprint["version"] = (soccer_model.artifact() or {}).get("version")
    return fingerprint
