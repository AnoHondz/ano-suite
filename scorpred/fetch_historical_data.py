"""
fetch_historical_data.py — Bulk-fetch historical soccer match results from
API-Football and append them to data/historical_matches.csv.

Usage:
    python fetch_historical_data.py           # fetch and append
    python fetch_historical_data.py --dry-run # preview without writing

Pulls the last SEASONS_BACK completed seasons for every league defined in
league_config.SUPPORTED_LEAGUE_IDS, deduplicates against existing rows, and
writes the enriched CSV back.

Output schema (superset of existing CSV schema):
    date, home_team, away_team, form, goals_scored, goals_conceded,
    goal_diff, result, league_id, season, referee,
    home_xg, away_xg, odds_home, odds_away, odds_draw

Columns not present in existing rows (league_id, season, form) are left empty;
train_model.py only reads: date, home_team, away_team, goals_scored,
goals_conceded, result — all of which are present in both old and new rows.
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

from api_client import api_get
from league_config import CURRENT_SEASON, SUPPORTED_LEAGUE_IDS
from runtime_paths import historical_dataset_path

SEASONS_BACK = 3   # how many prior seasons to pull in addition to current

# Canonical CSV column order — keeps backward compatibility with train_model.py
FIELDNAMES = [
    "date", "home_team", "away_team", "form",
    "goals_scored", "goals_conceded", "goal_diff",
    "result", "league_id", "season",
    "referee", "home_xg", "away_xg",
    "odds_home", "odds_away", "odds_draw",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _result_label(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "HomeWin"
    if home_goals < away_goals:
        return "AwayWin"
    return "Draw"


def _dedup_key(row: dict) -> tuple[str, str, str]:
    """Deduplication key: (date, home_team, away_team)."""
    return (
        str(row.get("date", "")).strip()[:10],
        str(row.get("home_team", "")).strip().lower(),
        str(row.get("away_team", "")).strip().lower(),
    )


def _load_existing(path: Path) -> tuple[list[dict], set[tuple]]:
    """Return (rows, dedup_keys) from an existing CSV, or empty if missing."""
    if not path.exists():
        return [], set()
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    keys = {_dedup_key(r) for r in rows}
    return rows, keys


def _safe_xg(score: dict) -> str:
    """Extract home xG from the score sub-object when present (API-Football v3)."""
    # Some responses include xG under score.home / score.away as floats
    val = (score.get("home") if isinstance(score.get("home"), float) else None)
    return str(val) if val is not None else ""


def _safe_xg_away(score: dict) -> str:
    val = (score.get("away") if isinstance(score.get("away"), float) else None)
    return str(val) if val is not None else ""


def _fetch_season_odds(league_id: int, season: int) -> dict[tuple[str, str, str], dict]:
    """Fetch pre-match 1X2 bookmaker odds for one league/season.

    Returns a dict keyed by (date[:10], home_name_lower, away_name_lower) mapping
    to {"odds_home": str, "odds_away": str, "odds_draw": str} with average decimal odds.
    Falls back to empty dict on any API error.
    """
    try:
        data = api_get(
            "odds",
            {"league": league_id, "season": season, "bet": 1},  # bet=1 = Match Winner
            cache_hours=168,  # cache for 1 week; historical odds don't change
        )
    except Exception as exc:
        print(f"  [warn] odds league={league_id} season={season}: {exc}")
        return {}

    result: dict[tuple[str, str, str], dict] = {}
    for entry in (data.get("response") or []):
        fixture = (entry.get("fixture") or {})
        raw_date = str(fixture.get("date") or "")[:10]
        teams = (entry.get("teams") or {})
        home_name = str((teams.get("home") or {}).get("name") or "").strip().lower()
        away_name = str((teams.get("away") or {}).get("name") or "").strip().lower()
        if not raw_date or not home_name or not away_name:
            continue

        # Collect all bookmaker values for bet "Match Winner" (values[0]=home, [1]=draw, [2]=away)
        home_vals: list[float] = []
        draw_vals: list[float] = []
        away_vals: list[float] = []
        for bookie in (entry.get("bookmakers") or []):
            for bet in (bookie.get("bets") or []):
                if str(bet.get("name") or "").strip() != "Match Winner":
                    continue
                values = bet.get("values") or []
                for v in values:
                    try:
                        odd_val = float(v.get("odd") or 0)
                        label = str(v.get("value") or "").strip()
                        if label == "Home":
                            home_vals.append(odd_val)
                        elif label == "Draw":
                            draw_vals.append(odd_val)
                        elif label == "Away":
                            away_vals.append(odd_val)
                    except (TypeError, ValueError):
                        continue

        if home_vals and draw_vals and away_vals:
            result[(raw_date, home_name, away_name)] = {
                "odds_home": str(round(sum(home_vals) / len(home_vals), 3)),
                "odds_draw": str(round(sum(draw_vals) / len(draw_vals), 3)),
                "odds_away": str(round(sum(away_vals) / len(away_vals), 3)),
            }
    return result


def _fetch_season(league_id: int, season: int) -> list[dict]:
    """Fetch all finished fixtures for one league/season from API-Football.

    Returns a list of normalised row dicts ready to append to the CSV.
    Falls back to empty list on any API error.
    """
    try:
        data = api_get(
            "fixtures",
            {"league": league_id, "season": season, "status": "FT"},
            cache_hours=24,
        )
    except Exception as exc:
        print(f"  [warn] league={league_id} season={season}: {exc}")
        return []

    fixtures = data.get("response") or []
    rows: list[dict] = []
    for f in fixtures:
        fixture_meta = f.get("fixture") or {}
        teams        = f.get("teams") or {}
        goals        = f.get("goals") or {}

        raw_date   = str(fixture_meta.get("date") or "")[:10]
        home_name  = str((teams.get("home") or {}).get("name") or "").strip()
        away_name  = str((teams.get("away") or {}).get("name") or "").strip()
        home_goals = goals.get("home")
        away_goals = goals.get("away")

        if not raw_date or not home_name or not away_name:
            continue
        if home_goals is None or away_goals is None:
            continue

        try:
            hg = int(home_goals)
            ag = int(away_goals)
        except (TypeError, ValueError):
            continue

        rows.append({
            "date":          raw_date,
            "home_team":     home_name,
            "away_team":     away_name,
            "form":          "",              # not available from this endpoint
            "goals_scored":  hg,             # home goals (matches train_model.py schema)
            "goals_conceded": ag,            # away goals
            "goal_diff":     hg - ag,
            "result":        _result_label(hg, ag),
            "league_id":     league_id,
            "season":        season,
            "referee":       str(fixture_meta.get("referee") or "").strip(),
            "home_xg":       _safe_xg(f.get("score") or {}),
            "away_xg":       _safe_xg_away(f.get("score") or {}),
            "odds_home":     "",
            "odds_away":     "",
            "odds_draw":     "",
        })
    return rows


def _pad_row(row: dict) -> dict:
    """Ensure every row has all FIELDNAMES keys (backfill missing with empty)."""
    return {col: row.get(col, "") for col in FIELDNAMES}


# ── Main ───────────────────────────────────────────────────────────────────────

def fetch_historical(dry_run: bool = False) -> None:
    path = historical_dataset_path()
    existing_rows, existing_keys = _load_existing(path)
    rows_before = len(existing_rows)
    print(f"Existing rows : {rows_before}")

    seasons = [CURRENT_SEASON - i for i in range(SEASONS_BACK + 1)]
    new_rows: list[dict] = []

    for league_id in SUPPORTED_LEAGUE_IDS:
        for season in seasons:
            print(f"  Fetching league={league_id} season={season} …", end=" ", flush=True)
            fetched = _fetch_season(league_id, season)
            added = 0
            for row in fetched:
                key = _dedup_key(row)
                if key not in existing_keys:
                    existing_keys.add(key)
                    new_rows.append(row)
                    added += 1
            print(f"{len(fetched)} fetched, {added} new")
            # small sleep to stay within API rate limits
            time.sleep(0.1)

    # Enrich new rows with pre-match odds from API-Football /odds endpoint
    print("\nFetching historical odds …")
    for league_id in SUPPORTED_LEAGUE_IDS:
        for season in seasons:
            odds_map = _fetch_season_odds(league_id, season)
            if not odds_map:
                continue
            matched = 0
            for row in new_rows:
                if int(row.get("league_id") or 0) != league_id or int(row.get("season") or 0) != season:
                    continue
                key = (
                    str(row.get("date", ""))[:10],
                    str(row.get("home_team", "")).strip().lower(),
                    str(row.get("away_team", "")).strip().lower(),
                )
                if key in odds_map:
                    row.update(odds_map[key])
                    matched += 1
            if matched:
                print(f"  odds matched {matched} rows for league={league_id} season={season}")
            time.sleep(0.1)

    rows_added = len(new_rows)
    rows_after = rows_before + rows_added
    print(f"\nRows before : {rows_before}")
    print(f"Rows added  : {rows_added}")
    print(f"Rows after  : {rows_after}")

    if dry_run:
        print("\n[dry-run] No changes written.")
        return

    if rows_added == 0:
        print("Nothing new to write.")
        return

    # Merge: pad existing rows with missing columns, append new rows
    all_rows = [_pad_row(r) for r in existing_rows] + [_pad_row(r) for r in new_rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Saved → {path}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch historical match results from API-Football and enrich "
                    "data/historical_matches.csv."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview new rows without writing to disk.",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    fetch_historical(dry_run=args.dry_run)
