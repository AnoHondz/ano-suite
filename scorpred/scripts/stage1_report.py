"""Assemble Stage 1 evidence and issue dispositions without collecting or fitting."""
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cache/diagnostics/stage1_2026-10-21"


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    operations = max(OUT.glob("operations_*.json"))
    ops, preserved = load(operations), load(OUT / "preservation_after.json")
    status = load(ROOT / "cache/nflverse/collector/status.json")
    health = next((p.get("health") for p in ops["probes"] if p["name"] == "app_health"), None)
    raw_log = (OUT / "backend_tests.txt").read_bytes()
    log = raw_log.decode("utf-16" if raw_log.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    match = re.search(r"(\d+) passed in ([0-9.]+)s", log)
    if not match or "FAILED " in log or "ERROR " in log:
        raise ValueError("Successful final regression evidence required")
    issues = [
        ("NFL-001", "Resolved in current application source and active collector; offline/admin and unsealing paths blocked", "Guarded source/runtime tests; original v1 artifacts unchanged; existing app activation requires controlled rollout"),
        ("OPS-001", "Partially resolved /ongoing operational monitoring", "NFL recovery and cadence fix verified; NBA adapter and actual scheduler success; current app freshness reported separately"),
        ("OPS-002", "Resolved in current source and isolated runtime", "Absent metadata rejects; malformed probability vector cannot become neutral model output"),
        ("SOC-003", "Resolved through runtime verification of existing correction", "Both Flask routes preserve rule5 /ML10 /target-date contracts"),
        ("SOC-002", "Resolved mappings and missingness; coverage limited", "Unsupported definitions unavailable; supported zero retained; no unit substitution"),
        ("NBA-001", "Partially resolved", "Unsafe defaults/stale/proxy outputs repaired; verified real-ID season-aligned prop adapter still absent"),
        ("SOC-001", "Resolved versioned preparation; original legacy data retained", "2599 unique new fixtures;319rows quarantined, originals untouched"),
        ("VAL-001 /SOC-004 /NFL-002", "Deferred coverage/provenance work", "Durable pregame archives, league lineage and auxiliary ownership remain")]
    result = {"generated_at": datetime.now(UTC).isoformat(), "research_enabled": False, "production_active": False,
        "deployed": False, "sealed_outcomes_inspected": 0, "holdout_games": 109,
        "backend_passed": int(match[1]), "new_repair_tests": 36, "browser_passed": 25, "frontend_build": "passed",
        "operations_evidence": str(operations.relative_to(ROOT)), "application_health": health,
        "collector": {k: status.get(k) for k in ("pid", "started_at", "code_sha256", "state", "heartbeat_at", "cycles", "last_cycle")},
        "preservation": preserved, "issues": [{"id": i, "status": s, "evidence": e} for i, s, e in issues],
        "next": "Verify schedule recovery after receipt174 and sustained collector cadence; close active-app freshness/loaded-version sign-off, then Stage 2 coverage and pregame provenance"}
    (OUT / "remediation_summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    paths = [p for p in OUT.iterdir() if p.is_file() and p.suffix != ".sqlite3" and p.name != "evidence_manifest.json"]
    paths += [ROOT / "docs/SCORPRED_STAGE1_REMEDIATION.md", *sorted((ROOT / "docs/assets/nfl_stage1").glob("*.json"))]
    (OUT / "evidence_manifest.json").write_text(json.dumps({"generated_at": datetime.now(UTC).isoformat(),
        "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}, indent=2) + "\n", encoding="utf-8")
    print("Stage 1 summary, issue dispositions and evidence manifest written")


if __name__ == "__main__":
    main()
