"""Inspect public roster response shapes without credentials or historical guesses."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

TARGETS = [
    ("soccer", "eng.1", "364"), ("soccer", "ger.1", "132"),
    ("soccer", "esp.1", "83"), ("basketball", "nba", "5"),
    ("football", "nfl", "12"),
]

def fetch(target):
    sport, league, team = target
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{team}/roster"
    response = requests.get(url, timeout=20)
    body = response.json() if response.ok else {}
    return {"sport": sport, "league": league, "team_id": team, "status": response.status_code,
            "payload": body}

output = Path(__file__).resolve().parents[1] / ".tmp" / "soccer-audit" / "roster-probe.json"
rows = list(ThreadPoolExecutor(max_workers=5).map(fetch, TARGETS))
output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
for row in rows:
    body = row["payload"]
    print(json.dumps({**{k:v for k,v in row.items() if k != "payload"},
                      "keys": list(body), "team": body.get("team"),
                      "athlete_example": (body.get("athletes") or [])[:1]}))
