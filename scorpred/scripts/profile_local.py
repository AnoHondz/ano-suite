"""Measure running local APIs without importing the application or reading research outcomes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from pathlib import Path
from statistics import median
from time import perf_counter

import requests

PATHS = ("/live", "/health", "/api/health", "/api/dashboard/nfl", "/api/dashboard/nba",
         "/api/dashboard/soccer", "/api/dashboard/insights?sport=all")


def measure(path):
    start = perf_counter()
    try:
        response = requests.get("http://127.0.0.1:5001" + path, timeout=40)
        payload = response.json()
        return {"path": path, "status": response.status_code, "ms": round((perf_counter() - start) * 1000, 1),
                "bytes": len(response.content), "games": len(payload.get("slate", [])), "error": payload.get("error")}
    except (requests.RequestException, ValueError) as error:
        return {"path": path, "status": None, "ms": round((perf_counter() - start) * 1000, 1), "error": str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rounds = []
    for index in range(3):
        with ThreadPoolExecutor(max_workers=len(PATHS)) as pool:
            rows = list(pool.map(measure, PATHS))
        rounds.append(rows)
        print(json.dumps({"round": index + 1, "measurements": rows}), flush=True)
    summaries = [{"path": path, "median_ms": median(row["ms"] for rows in rounds for row in rows if row["path"] == path)} for path in PATHS]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "rounds": rounds,
                                       "summary": summaries, "conditions": "Sequential rounds of concurrent loopback requests; existing provider caches retained."}, indent=2), encoding="utf-8")
    if any(row["status"] != 200 for rows in rounds for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
