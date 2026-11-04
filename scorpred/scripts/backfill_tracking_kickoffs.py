"""Add the provider's scheduled kickoff to tracked picks recorded before kickoffs were stored.

Picks, probabilities and results are never changed. A backup of the tracking file is written first.
Records whose kickoff cannot be confirmed are left without one, so they never count as verified pregame.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_tracker as mt  # noqa: E402

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
SOCCER_SLUGS = ("eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions", "eng.2", "eng.league_cup", "esp.copa_del_rey")
SPORT_PATHS = {"nba": ["basketball/nba"], "soccer": [f"soccer/{slug}" for slug in SOCCER_SLUGS]}


def _summary(sport: str, event_id: str) -> dict | None:
    for path in SPORT_PATHS.get(sport, []):
        response = requests.get(f"{ESPN}/{path}/summary", params={"event": event_id}, timeout=20)
        if response.status_code != 200:
            continue
        competitions = (response.json().get("header") or {}).get("competitions") or []
        if competitions and str(competitions[0].get("id") or event_id) == str(event_id) and competitions[0].get("date"):
            header = response.json()["header"]
            return {"kickoff": competitions[0]["date"], "season_type": (header.get("season") or {}).get("type"), "path": path}
    return None


def main() -> None:
    source = Path(mt._TRACKING_FILE)
    backup = source.with_name(f"{source.stem}.before-kickoff-backfill-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
    shutil.copy2(source, backup)
    predictions = mt._load_predictions()
    filled = unresolved = 0
    for prediction in predictions:
        if prediction.get("kickoff_at") or not prediction.get("fixture_id"):
            continue
        found = _summary(str(prediction.get("sport", "")).lower(), str(prediction["fixture_id"]))
        if not found:
            unresolved += 1
            continue
        kickoff = mt._aware_datetime(found["kickoff"].replace("Z", "+00:00") if "T" in found["kickoff"] else None)
        if kickoff is None:
            unresolved += 1
            continue
        prediction["kickoff_at"] = mt._isoformat_z(kickoff)
        prediction["kickoff_source"] = f"espn:{found['path']}:summary (backfilled)"
        if found["season_type"] is not None:
            prediction["season_type"] = int(found["season_type"])
        filled += 1
    mt._save_predictions(predictions)
    verified = sum(mt.verified_pregame(p) for p in predictions)
    print(json.dumps({"backup": str(backup), "filled": filled, "unresolved": unresolved,
                      "records": len(predictions), "verified_pregame": verified}, indent=2))


if __name__ == "__main__":
    main()
