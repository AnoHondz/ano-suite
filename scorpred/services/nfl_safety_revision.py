"""Explicit source-only Stage 1 transition; original model/study hashes stay put.

The reviewed manifest permits exact new hashes for a small safety-only file set.
It is not generated at startup and never accepts arbitrary source drift.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/assets/nfl_stage1/safety_revision.json"
ALLOWED = {"services/nflverse_store.py", "services/nfl_frozen_models.py",
           "services/nfl_winner_features.py", "services/nfl_winner_research.py", "scripts/nfl_collector.py"}


def compatible_sources(plan, names, current_fingerprint):
    current = {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in names}
    actual = hashlib.sha256(json.dumps(current, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if actual != current_fingerprint:
        return False
    original = {n: entry["sha256"] for n, entry in plan["source_bundle"].items()}
    if current == original:
        return True
    if not MANIFEST.exists():
        return False
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    changed = {n for n in names if current[n] != original[n]}
    repairs = manifest.get("source_changes", {})
    if (manifest.get("revision") != "stage1-holdout-boundary-v1" or manifest.get("base_study_sha256") != digest
            or manifest.get("base_feature_code_sha256") != plan["feature_code_sha256"]
            or changed != set(repairs) or not changed <= ALLOWED):
        return False
    extensions = manifest.get("new_safety_modules", {})
    if set(extensions) != {"services/nfl_holdout.py", "services/nfl_safety_revision.py"}:
        return False
    return (all(repairs[n] == {"before": original[n], "after": current[n]} for n in changed)
            and all(hashlib.sha256((ROOT / n).read_bytes()).hexdigest() == h for n, h in extensions.items()))
