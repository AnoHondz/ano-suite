"""Read-only recovery evidence: receipt metadata and bounded public probes."""
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cache/diagnostics/stage1_2026-10-21"


def probe(name, url, method):
    result = {"name": name, "url": url, "method": method, "attempted_at": datetime.now(UTC).isoformat()}
    try:
        with urlopen(Request(url, method=method, headers={"User-Agent": "ScorPred-Stage1-verification/1.0"}), timeout=15) as response:
            result.update(status=response.status, received_at=datetime.now(UTC).isoformat(), last_modified=response.headers.get("Last-Modified"))
            if method == "GET":
                data = json.loads(response.read(4_000_000))
                if name == "app_health":
                    result["health"] = safe_health(data)
                elif name == "nba_player_overview":
                    result["season_stat_names"] = (data.get("statistics") or {}).get("names")
                    log = data.get("gameLog") or {}
                    result["recent_statistics_groups"] = len(log.get("statistics") or [])
                    result["limitation"] = "Response availability does not prove season-aligned numeric-ID prop coverage or historical pregame timestamps"
                else:
                    result["event_count"] = len(data.get("events") or [])
    except (HTTPError, URLError, OSError, ValueError) as exc:
        result.update(status=getattr(exc, "code", None), error_type=type(exc).__name__, received_at=datetime.now(UTC).isoformat())
        if isinstance(exc, HTTPError) and name == "app_health":
            try:
                result["health"] = safe_health(json.loads(exc.read(200_000)))
            except (OSError, ValueError):
                pass
    return result


def safe_health(data):
    fields = ("status", "last_attempt", "last_success", "last_success_mins_ago", "consecutive_failures")
    return {"overall": data.get("overall"), "pipelines": {name: {k: row.get(k) for k in fields}
            for name, row in data.get("pipelines", {}).items()}}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    target = ROOT / "cache/nflverse/nflverse.sqlite3"
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        receipts = [dict(r) for r in db.execute("SELECT id,dataset,season,attempted_at,collected_at,status,row_count,error FROM collection_receipts WHERE id IN (SELECT MAX(id) FROM collection_receipts GROUP BY dataset,season) OR id IN (SELECT MAX(id) FROM collection_receipts WHERE status='success' GROUP BY dataset,season) ORDER BY dataset,season,id")]
        recent_failures = [dict(r) for r in db.execute("SELECT id,dataset,attempted_at,status,error FROM collection_receipts WHERE status IN ('failed','unavailable') ORDER BY id DESC LIMIT 12")]
    base = "https://github.com/nflverse/nflverse-data/releases/download/"
    day = datetime.now(UTC).strftime("%Y%m%d")
    probes = [probe(*item) for item in (
        ("nfl_schedules_metadata", base + "schedules/games.csv.gz", "HEAD"),
        ("nfl_pbp_metadata", base + "pbp/play_by_play_2026.parquet", "HEAD"),
        ("nba_scoreboard", "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=" + day, "GET"),
        ("soccer_scoreboard", "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard?dates=" + day, "GET"),
        ("nba_player_overview", "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/1966/overview", "GET"),
        ("app_health", "http://127.0.0.1:5001/api/health", "GET"))]
    result = {"observed_at": datetime.now(UTC).isoformat(), "receipt_metadata": receipts, "recent_failures": recent_failures,
              "collector": json.loads((ROOT / "cache/nflverse/collector/status.json").read_text()), "probes": probes,
              "nfl_outcomes_downloaded": False, "historical_timestamps_modified": False}
    stamp = datetime.now(UTC).strftime("%H%M%S")
    path = OUT / ("operations_" + stamp + ".json")
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"evidence": str(path), "probes": [{"name": p["name"], "status": p.get("status"), "error": p.get("error_type")} for p in probes]}, indent=2))


if __name__ == "__main__":
    main()
