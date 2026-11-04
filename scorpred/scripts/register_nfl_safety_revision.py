"""Explicit source-only migration manifest. Never changes frozen artifacts."""
import hashlib
import json

from services.nfl_safety_revision import ALLOWED, MANIFEST, ROOT


def main():
    path = ROOT / "docs/assets/nfl_phase2_3/study.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    base = hashlib.sha256(canonical).hexdigest()
    if base != "fd4526c5da021c30c28538dcdb67faadc18f581d7ac451a2d56f1f6589534cb7":
        raise ValueError("Unexpected original study; do not replace it")
    changes = {}
    for name, entry in plan["source_bundle"].items():
        current = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if current != entry["sha256"]:
            if name not in ALLOWED:
                raise ValueError("Unreviewed frozen-source change: " + name)
            changes[name] = {"before": entry["sha256"], "after": current}
    manifest = {"revision": "stage1-holdout-boundary-v1", "base_study_sha256": base,
        "base_feature_code_sha256": plan["feature_code_sha256"], "source_changes": changes,
        "new_safety_modules": {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest()
            for n in ("services/nfl_holdout.py", "services/nfl_safety_revision.py")},
        "scope": "Sealed read boundary, target denial, snapshot exclusion and exact source-only compatibility. No model coefficients, preprocessing, calibration or probabilities changed."}
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Recorded explicit safety revision for", len(changes), "source files; original study unchanged")


if __name__ == "__main__":
    main()
