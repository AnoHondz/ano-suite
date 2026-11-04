"""Bounded public-provider and local-health probes; write only diagnostic exports.

No application imports, auth tokens, collection triggers, or cache refresh routes.
NFL probes use HEAD exclusively, so no NFL outcomes are downloaded or decoded.
"""
import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.audit_scorp import DEFAULT_OUTPUT, espn_event, load, write


def probe(item):
    name, url, method = item
    start, clock = time.monotonic(), datetime.now(UTC)
    result = {"name": name, "url": url, "method": method, "attempted_at": clock.isoformat()}
    try:
        request = Request(url, method=method, headers={"User-Agent": "ScorPred-diagnostic-audit/1.0", "Accept": "application/json"})
        with urlopen(request, timeout=15) as response:
            result.update(status=response.status, received_at=datetime.now(UTC).isoformat(),
                          source_date=response.headers.get("Date"), last_modified=response.headers.get("Last-Modified"),
                          retry_after=response.headers.get("Retry-After"), content_type=response.headers.get("Content-Type"))
            if method == "GET":
                body = response.read(6_000_000)
                result["body_sha256"] = hashlib.sha256(body).hexdigest()
                payload = json.loads(body)
                if name == "local_pipeline_health":
                    result["health"] = {"overall": payload.get("overall"), "pipelines": {
                        key: {k: v for k, v in value.items() if k != "error"} for key, value in payload.get("pipelines", {}).items()}}
                else:
                    result["provider_timestamp"] = payload.get("timestamp")
                    result["events"] = [r for event in payload.get("events", []) if (r := espn_event(event, (payload.get("season") or {}).get("year"))) is not None]
                    result["response_event_count"] = len(payload.get("events", []))
    except HTTPError as exc:
        result.update(status=exc.code, received_at=datetime.now(UTC).isoformat(), error_type="HTTPError")
        if name == "local_pipeline_health":
            try:
                payload = json.loads(exc.read(100_000))
                result["health"] = {"overall": payload.get("overall"), "pipelines": {
                    key: {k: v for k, v in value.items() if k != "error"} for key, value in payload.get("pipelines", {}).items()}}
            except (ValueError, OSError):
                pass
    except (URLError, OSError, ValueError) as exc:
        result["error_type"] = type(exc).__name__
        result["status"] = None
    result["duration_seconds"] = round(time.monotonic() - start, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--local-url", default="http://127.0.0.1:5001/api/health")
    args = parser.parse_args()
    now = datetime.now(UTC)
    items = []
    # Use the provider's supported individual-day form, as the app does.
    for offset in (0, 4, 11):
        day = (now + timedelta(days=offset)).strftime("%Y%m%d")
        items.append(("nba_scoreboard_" + day, "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=" + day + "&limit=1000", "GET"))
        for slug in ("eng.1", "ger.1", "esp.1"):
            items.append(("soccer_" + slug + "_" + day, "https://site.api.espn.com/apis/site/v2/sports/soccer/" + slug + "/scoreboard?dates=" + day + "&limit=1000", "GET"))
    base = "https://github.com/nflverse/nflverse-data/releases/download/"
    items += [("nfl_schedules_metadata", base + "schedules/games.csv.gz", "HEAD"),
              ("nfl_pbp_metadata", base + "pbp/play_by_play_2026.parquet", "HEAD"),
              ("nfl_injuries_metadata", base + "injuries/injuries_2026.parquet", "HEAD"),
              ("nfl_participation_metadata", base + "pbp_participation/pbp_participation_2026.parquet", "HEAD"),
              ("local_pipeline_health", args.local_url, "GET")]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(probe, items))
    cached_path = args.output / "cached_events.json"
    cached = load(cached_path) if cached_path.exists() else {}
    comparisons = []
    for result in results:
        if "events" not in result:
            continue
        sport = "nba" if result["name"].startswith("nba_scoreboard") else "soccer"
        prior = {r["event"]["id"]: r["event"] for r in cached.get(sport, [])}
        rows = result["events"]
        comparisons.append({"provider": result["name"], "returned_games": len(rows),
                            "cached_ids": sum(r["id"] in prior for r in rows),
                            "not_in_audited_cache": [r["id"] for r in rows if r["id"] not in prior],
                            "schedule_conflicts": [{"game_id": r["id"], "field": key, "cached": prior[r["id"]][key], "observed_now": r[key]}
                                                   for r in rows if r["id"] in prior for key in ("date", "home_id", "away_id") if r[key] != prior[r["id"]][key]],
                            "denominator": "This provider's response for one of three selected dates (today, +4 and +11 days), not a complete season or independently verified fixture universe."})
    write(args.output, "provider_probes.json", {"audited_at": now, "requests": results, "cache_comparison": comparisons,
          "nfl_outcomes_downloaded": False, "app_caches_modified": False,
          "limitations": ["One successful request does not establish uptime or publication latency.",
                          "HTTP availability is not a grant of redistribution or commercial training rights.",
                          "Credentialed endpoints were not probed; audit-process credentials are absent.",
                          "football-data.co.uk was not downloaded: its current published use restriction must be resolved before new automated training-data acquisition."]})
    print(json.dumps({"probes": [{"name": r["name"], "status": r["status"], "error_type": r.get("error_type"), "events": len(r.get("events", []))} for r in results], "cache_comparison": comparisons}, indent=2))


if __name__ == "__main__":
    main()
