"""Refresh every supported team's current roster; report failures without historical fallback."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services import current_rosters
from api_client import ESPN_SLUG_BY_LEAGUE
from league_config import SUPPORTED_LEAGUE_IDS


def main():
    jobs, failures = [], []
    for sport, league in [("nba", None), ("nfl", None)] + [
            ("soccer", ESPN_SLUG_BY_LEAGUE[lid]) for lid in SUPPORTED_LEAGUE_IDS]:
        try:
            jobs.extend((sport, league, team) for team in current_rosters.teams(sport, league, refresh=True))
        except Exception as exc:
            failures.append({"sport": sport, "league": league, "error": str(exc)})

    def refresh(job):
        sport, league, team = job
        players, source = current_rosters.current(sport, team["id"], league, refresh=True)
        return {"sport": sport, "league": league, "team_id": str(team["id"]),
                "team": team.get("displayName"), "verification": source,
                "players": [{"id": pid, "name": player["displayName"]} for pid, player in players.items()]}

    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(refresh, job): job for job in jobs}
        for future in as_completed(pending):
            job = pending[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                failures.append({"sport": job[0], "league": job[1], "team_id": str(job[2]["id"]), "error": str(exc)})
            if (len(rows) + len(failures)) % 20 == 0:
                print(f"Roster checks: {len(rows)} verified, {len(failures)} unavailable", flush=True)
    report = {"observed_at": datetime.now(UTC).isoformat(), "mocked": False,
              "verified_teams": len(rows), "failures": failures, "rosters": rows}
    output = ROOT / "data" / "validation" / "current_roster_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"verified_teams": len(rows), "verified_players": sum(len(r["players"]) for r in rows),
                      "unavailable": failures, "report": str(output)}), flush=True)


if __name__ == "__main__":
    main()
