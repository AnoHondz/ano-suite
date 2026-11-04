"""Read-only live soccer API audit; saves provider provenance and availability."""
import json
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:5173"
report = {"mocked": False, "responses": []}
paths = [
    "/api/dashboard/soccer?league_id=39&date=2026-10-25",
    "/api/matches/560612?sport=soccer&league_id=39&date=2026-10-25",
    "/api/soccer/matches/560612/injuries?league_id=39&date=2026-10-25",
    "/api/soccer/matches/560612/props?league_id=39&date=2026-10-25",
    "/api/scoreline/soccer?league_id=39&team_a_id=64&team_b_id=397&team_a_name=Liverpool%20FC&team_b_name=Brighton%20%26%20Hove%20Albion%20FC&event_id=560612&before=2026-10-25T14:00:00Z",
    "/api/dashboard/soccer?league_id=78&date=2026-10-25",
    "/api/dashboard/soccer?league_id=140&date=2026-10-25",
    "/api/health",
]
for path in paths:
    start = time.monotonic()
    try:
        response = requests.get(BASE + path, timeout=90)
        payload = response.json()
        row = {"path": path, "status": response.status_code,
               "seconds": round(time.monotonic() - start, 2), "payload": payload}
    except (requests.RequestException, ValueError) as exc:
        row = {"path": path, "error": str(exc)}
    report["responses"].append(row)
    summary = {key: value for key, value in row.items() if key != "payload"}
    body = row.get("payload") or {}
    summary.update(error=body.get("error"), games=len(body.get("slate") or []),
                   picks=len((body.get("result") or {}).get("picks") or []))
    print(json.dumps(summary), flush=True)
output = Path(__file__).resolve().parents[1] / ".tmp" / "soccer-audit" / "verified.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2), encoding="utf-8")
