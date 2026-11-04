"""Read-only live roster and props verification, without mocked responses."""
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services import current_rosters, nba_props
from services.prediction_lifecycle import parse_kickoff

BASE = "http://127.0.0.1:5173"


def get(path):
    result = requests.get(BASE + path, timeout=90)
    return result.status_code, result.json()


def main():
    report = {"mocked": False, "observed_at": datetime.now(UTC).isoformat(), "sports": []}
    today = datetime.now(UTC).date()
    for sport in ("soccer", "nba", "nfl"):
        day = "2026-10-25" if sport == "soccer" else str(today + timedelta(days=sport == "nfl"))
        status, slate = get(f"/api/dashboard/{sport}?league_id=39&date={day}")
        upcoming = [row for row in slate.get("slate", []) if (start := parse_kickoff(row.get("matchDate")))
                    and start > datetime.now(UTC)]
        row = next((r for r in upcoming if r.get("matchId") == "560612"), upcoming[0] if upcoming else None)
        if not row:
            report["sports"].append({"sport": sport, "dashboard_status": status, "status": "no_upcoming_slate"})
            continue
        params = f"date={day}&league_id={row.get('leagueId', 39)}"
        status, body = get(f"/api/{sport}/matches/{row['matchId']}/props?{params}")
        result = body.get("result") or {}
        picks = result.get("picks") or []
        forecasts = result.get("forecasts") or []
        summary = {"sport": sport, "matchup": row.get("matchup"), "status": status,
                   "error": body.get("error"), "verification": result.get("roster_verification"),
                   "picks": [{k:p.get(k) for k in ("name", "player_id", "team", "market", "side", "line", "chance", "calibration")} for p in picks]}
        if sport == "soccer":
            summary["forecast_count"] = len(forecasts)
            summary["trained_forecasts"] = sum(p.get("model") == "soccer-player-counts-v2" for p in forecasts)
            summary["market_status"] = result.get("sportsbook")
        if sport == "soccer" and (picks or forecasts):
            home, home_source = current_rosters.current("soccer", "364", "eng.1")
            away, away_source = current_rosters.current("soccer", "331", "eng.1")
            summary["salah_on_current_liverpool_roster"] = "173896" in home
            summary["all_picks_current_ids"] = all(str(p.get("player_id")) in (home if "Liverpool" in p["team"] else away) for p in picks + forecasts)
            assert not summary["salah_on_current_liverpool_roster"]
            assert summary["all_picks_current_ids"]
            assert all("salah" not in p["name"].lower() for p in picks + forecasts)
            assert all(p.get("line_source") == "sportsbook" for p in picks)
        report["sports"].append(summary)
        print(json.dumps(summary), flush=True)
    status, health = get("/api/health")
    report["health"] = {"status": status, "payload": health}
    output = ROOT / "data/validation/live_roster_props.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
