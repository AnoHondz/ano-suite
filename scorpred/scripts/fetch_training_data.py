"""Fetch 3 seasons of historical match data from ESPN for ML training.

Pulls completed matches for each team across the 5 major European leagues
for seasons 2022, 2023, 2024, 2025 (partial). Writes historical_matches.csv
in the format expected by train_model.py.

Usage:
    python scripts/fetch_training_data.py

Output: data/historical_matches.csv (~3500-5500 rows)
"""
from __future__ import annotations

import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Allow running from repo root or scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SCORPRED_DATA_ROOT", str(Path(__file__).resolve().parent.parent))

import api_client as ac
from runtime_paths import historical_dataset_path, ensure_runtime_dirs

# ── Config ────────────────────────────────────────────────────────────────────

LEAGUE_CONFIGS = [
    {"id": 39,  "slug": "eng.1",  "name": "Premier League"},
    {"id": 140, "slug": "esp.1",  "name": "La Liga"},
    {"id": 135, "slug": "ita.1",  "name": "Serie A"},
    {"id": 78,  "slug": "ger.1",  "name": "Bundesliga"},
    {"id": 61,  "slug": "fra.1",  "name": "Ligue 1"},
]

# 3 full seasons + current partial season
SEASONS = [2022, 2023, 2024, 2025]

MAX_WORKERS = 8   # concurrent ESPN calls


# ── Helpers ──────────────────────────────────────────────────────────────────

def _result_label(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "HomeWin"
    if home_goals == away_goals:
        return "Draw"
    return "AwayWin"


def _is_completed(fixture: dict) -> bool:
    status = (fixture.get("fixture") or {}).get("status") or {}
    short = str(status.get("short") or "").upper()
    long_ = str(status.get("long") or "").lower()
    return short in {"FT", "AET", "PEN", "FT_PEN"} or "finished" in long_ or "full time" in long_


def fetch_team_season(team_id: int, league_id: int, season: int) -> list[dict]:
    """Fetch all completed matches for one team/season. Returns normalised fixture list."""
    try:
        fixtures = ac.get_team_fixtures(team_id, league_id, season, last=60)
        return [f for f in fixtures if _is_completed(f)]
    except Exception as exc:
        print(f"  [warn] team={team_id} league={league_id} season={season}: {exc}", flush=True)
        return []


def fixtures_to_rows(fixtures: list[dict]) -> list[dict]:
    """Convert normalised fixture dicts → historical_matches.csv rows."""
    rows = []
    for f in fixtures:
        try:
            teams = f.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            goals = f.get("goals") or {}
            fixture_meta = f.get("fixture") or {}

            home_name = str(home.get("name") or "").strip()
            away_name = str(away.get("name") or "").strip()
            if not home_name or not away_name:
                continue

            home_goals = goals.get("home")
            away_goals = goals.get("away")
            if home_goals is None or away_goals is None:
                continue

            date_str = str(fixture_meta.get("date") or "")[:10]
            if not date_str or len(date_str) < 8:
                continue

            rows.append({
                "date": date_str,
                "home_team": home_name,
                "away_team": away_name,
                "goals_scored": int(home_goals),
                "goals_conceded": int(away_goals),
                "result": _result_label(int(home_goals), int(away_goals)),
            })
        except Exception:
            continue
    return rows


def dedup_rows(rows: list[dict]) -> list[dict]:
    """Remove duplicate matches (each game appears in both teams' schedules)."""
    seen: set[tuple] = set()
    out = []
    for row in sorted(rows, key=lambda r: r["date"]):
        key = (row["date"], row["home_team"], row["away_team"])
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ensure_runtime_dirs()
    out_path = historical_dataset_path()

    all_rows: list[dict] = []
    total_fetches = 0
    skipped = 0

    for league in LEAGUE_CONFIGS:
        league_id = league["id"]
        league_name = league["name"]
        print(f"\n{'='*60}", flush=True)
        print(f"League: {league_name} (id={league_id})", flush=True)

        # Fetch team list
        try:
            teams_raw = ac.get_teams(league_id, SEASONS[-1])
            if not teams_raw:
                teams_raw = ac.get_teams(league_id, SEASONS[-2])
        except Exception as exc:
            print(f"  [error] Could not fetch teams for {league_name}: {exc}", flush=True)
            continue

        team_list = []
        for entry in teams_raw:
            t = entry.get("team") or entry
            team_id = t.get("id")
            team_name = t.get("name", "")
            if team_id:
                team_list.append({"id": int(team_id), "name": team_name})

        if not team_list:
            print(f"  [warn] No teams found for {league_name}", flush=True)
            continue

        print(f"  Teams: {len(team_list)} | Seasons: {SEASONS}", flush=True)

        # Build all (team, season) pairs for parallel fetching
        tasks = [(t["id"], t["name"], season) for t in team_list for season in SEASONS]

        league_fixtures: list[dict] = []
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(fetch_team_season, team_id, league_id, season): (team_id, name, season)
                for team_id, name, season in tasks
            }
            for fut in as_completed(futures):
                team_id, name, season = futures[fut]
                done += 1
                try:
                    fixtures = fut.result()
                    league_fixtures.extend(fixtures)
                    total_fetches += 1
                    if done % 20 == 0 or done == len(tasks):
                        print(f"  [{done}/{len(tasks)}] fetched {len(league_fixtures)} fixtures so far", flush=True)
                except Exception as exc:
                    skipped += 1
                    print(f"  [err] {name} {season}: {exc}", flush=True)

        rows = fixtures_to_rows(league_fixtures)
        rows = dedup_rows(rows)
        print(f"  -> {len(rows)} unique completed matches for {league_name}", flush=True)
        all_rows.extend(rows)

    # Global dedup (teams from different leagues share no fixtures, but same
    # dataset can be run twice, etc.)
    all_rows = dedup_rows(all_rows)
    all_rows.sort(key=lambda r: r["date"])

    # Write CSV
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "home_team", "away_team", "goals_scored", "goals_conceded", "result"]
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n{'='*60}")
    print(f"Done. Total rows: {len(all_rows)}  |  Skipped: {skipped}")
    print(f"Output: {out_path}")
    print(f"\nNext step: python train_model.py")


if __name__ == "__main__":
    main()
