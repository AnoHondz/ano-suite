"""Read existing NBA JSON and retained file manifests; never fetch sports assets.

No app, database, model, NFL store, HTTP client or provider loader is imported.
The output contains identities and coverage metadata, never player statistics.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re

from services.nba_provider_registry import asset_access_decision


def fingerprint(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def read_json(path: Path) -> tuple[dict | list, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def positive_score(value) -> bool:
    try:
        return not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def local_history(directory: Path) -> dict:
    """Mirror nba_games eligibility without importing or invoking the trainer.

    Keep provider types 2/3 (including the existing Cup/play-in semantics).
    Conflicting duplicate identities/results fail closed instead of last-wins.
    Scores are compared in memory only and are not exported.
    """
    games, signatures, sources, excluded = {}, {}, [], Counter()
    raw_count = duplicates = 0
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError("No existing NBA monthly archive; refusing an empty reference")
    for path in paths:
        payload, sha = read_json(path)
        if not isinstance(payload.get("events"), list):
            raise ValueError(f"Invalid NBA events: {path.name}")
        sources.append({"file": path.name, "sha256": sha})
        for event in payload["events"]:
            raw_count += 1
            if (not event.get("status", {}).get("type", {}).get("completed")
                    or event.get("season", {}).get("type") not in (2, 3)):
                excluded["incomplete_or_excluded_season_type"] += 1
                continue
            competition = event["competitions"][0]
            teams = {team["homeAway"]: team for team in competition["competitors"]}
            home, away = teams["home"], teams["away"]
            team_ids = [str(team.get("id", "")) for team in (home, away)]
            if any(not value.isdigit() or not 1 <= int(value) <= 30 for value in team_ids):
                excluded["non_nba_team"] += 1
                continue
            if not all(positive_score(team.get("score")) for team in (home, away)):
                excluded["missing_or_nonpositive_score"] += 1
                continue
            date = datetime.fromisoformat(event["date"])
            if date.tzinfo is None or home["id"] == away["id"]:
                raise ValueError("Invalid local NBA identity/date")
            game_id = str(event["id"])
            row = {"game_id": game_id, "season": int(event["season"]["year"]),
                   "season_type": event["season"]["type"], "tipoff": date.isoformat(),
                   "home_team_id": team_ids[0], "away_team_id": team_ids[1],
                   "namespace": "espn", "source_files": [path.name]}
            signature = {k: v for k, v in row.items() if k != "source_files"}
            signature["scores"] = [float(home["score"]), float(away["score"])]
            if game_id in games:
                if signatures[game_id] != signature:
                    raise ValueError(f"Conflicting local NBA game: {game_id}")
                duplicates += 1
                games[game_id]["source_files"].append(path.name)
            else:
                signatures[game_id], games[game_id] = signature, row
    ordered = sorted(games.values(), key=lambda row: (row["season"], row["tipoff"], row["game_id"]))
    seasons = []
    for season in sorted({row["season"] for row in ordered}):
        rows = [row for row in ordered if row["season"] == season]
        seasons.append({"season": season, "games": len(rows), "first_tipoff": rows[0]["tipoff"],
                        "last_tipoff": rows[-1]["tipoff"],
                        "season_types": dict(sorted(Counter(str(r["season_type"]) for r in rows).items()))})
    return {"games": ordered, "seasons": seasons, "source_files": sources,
            "raw_events": raw_count, "eligible_games": len(ordered),
            "duplicate_eligible_observations": duplicates, "excluded_events": dict(sorted(excluded.items()))}


def release_manifest(dataset: dict, releases: list) -> dict:
    matches = [r for r in releases if r.get("tag") == dataset["release_tag"]
               and r.get("repository") == "https://github.com/sportsdataverse/sportsdataverse-data"]
    if len(matches) > 1:
        raise ValueError("Duplicate release manifest")
    result = {"dataset_id": dataset["dataset_id"], "release_tag": dataset["release_tag"],
              "manifest_status": "absent", "assets": [], "asset_seasons": [],
              "documented_seasons": dataset.get("documented_seasons", []),
              "game_ids": None, "completeness": "unverifiable", "payload_downloaded": False}
    if not matches:
        return result
    release = matches[0]
    if release.get("payload_downloaded") is not False:
        raise ValueError("Expected metadata-only release evidence")
    seen = set()
    for asset in release["assets"]:
        match = re.fullmatch(re.escape(dataset["file_stem"]) + r"_(\d{4})\.(csv|parquet|rds)", asset["name"])
        if not match:
            continue  # Sidecars/master files are not additional seasons/games.
        if asset["name"] in seen:
            raise ValueError("Duplicate season asset")
        seen.add(asset["name"])
        result["assets"].append({k: asset.get(k) for k in (
            "id", "name", "size", "updated_at", "browser_download_url", "digest")}
            | {"season": int(match[1]), "season_convention": "ending_year", "format": match[2]})
    result["assets"].sort(key=lambda a: a["name"])
    result.update(manifest_status="present", observed_at=release["observed_at"],
                  evidence_url=release["evidence_url"], release_created_at=release["release_created_at"],
                  asset_seasons=sorted({a["season"] for a in result["assets"]}),
                  latest_season_asset_update=max((a["updated_at"] for a in result["assets"]), default=None))
    return result


def index_manifest(dataset: dict, document: dict | None) -> dict:
    result = {"dataset_id": dataset["dataset_id"], "manifest_status": "absent", "assets": [],
              "asset_seasons": [], "game_ids": None, "completeness": "unverifiable",
              "payload_downloaded": False}
    if not document or document.get("status") != 200:
        return result
    seen = set()
    for line in document["text"].splitlines():
        key, separator, url = line.strip().partition("=")
        match = re.fullmatch(re.escape(dataset["file_stem"]) + r"(_po)?_(\d{4})", key)
        if not separator or not match:
            continue  # Excludes WNBA and every unrelated data variant.
        if key in seen:
            raise ValueError("Duplicate file-index candidate")
        seen.add(key)
        result["assets"].append({"name": key + ".tar.xz", "browser_download_url": url,
                                  "source_season": int(match[2]), "season": int(match[2]) + 1,
                                  "season_convention": "start_year_converted_to_ending_year",
                                  "competition": "playoffs" if match[1] else "regular",
                                  "format": "csv.tar.xz", "updated_at": None, "digest": None})
    result["assets"].sort(key=lambda a: a["name"])
    result.update(manifest_status="present", observed_at=document["observed_at"],
                  evidence_url=document["url"], asset_seasons=sorted({a["season"] for a in result["assets"]}),
                  latest_season_asset_update=None)
    return result


def candidate_manifests(registry: dict, evidence_dir: Path) -> dict:
    documents, source_files = {}, []
    for name, expected_sha in sorted(registry["retained_evidence_sha256"].items()):
        if name not in ("release_manifests.json", "supplemental_evidence.json"):
            raise ValueError("Only retained metadata snapshots are allowed")
        path = evidence_dir / name
        if not path.exists():
            source_files.append({"file": name, "status": "absent", "sha256": None})
            continue
        value, sha = read_json(path)
        if sha != expected_sha:
            raise ValueError(f"Retained evidence hash mismatch: {name}")
        source_files.append({"file": name, "status": "present", "sha256": sha})
        documents[name] = value
    releases = documents.get("release_manifests.json", [])
    index = next((d for d in documents.get("supplemental_evidence.json", {}).get("evidence", [])
                  if d.get("url") == "https://raw.githubusercontent.com/shufinskiy/nba_data/main/list_data.txt"), None)
    providers = []
    for provider in registry["providers"]:
        datasets = [release_manifest(d, releases) if d.get("release_tag") else index_manifest(d, index)
                    for d in provider["datasets"]]
        providers.append({"provider_id": provider["provider_id"], "datasets": datasets})
    return {"schema_version": 1, "source_files": source_files, "providers": providers}


def minute_value(value) -> bool:
    if value is None or isinstance(value, bool):
        return False
    text = str(value).strip()
    if re.fullmatch(r"\d+:\d{2}", text):
        return int(text.split(":")[1]) < 60
    try:
        return math.isfinite(float(text)) and float(text) >= 0
    except ValueError:
        return False


def local_box_coverage(directory: Path, games: list) -> dict:
    """Check only existing response structure/IDs/minute presence, not new stats.

    'Absent' means absent in this cache, never proven absent upstream. Team-box
    presence does not validate count definitions, paired totals or possessions.
    """
    identities = {g["game_id"]: {g["home_team_id"], g["away_team_id"]} for g in games}
    seen, team_boxes, player_boxes, minutes = set(), set(), set(), set()
    sources, missing_minutes = [], []
    for path in sorted(directory.glob("*.json")):
        payload, sha = read_json(path)
        sources.append({"file": path.name, "sha256": sha})
        game_id = str(payload.get("header", {}).get("id", ""))
        if game_id not in identities or "boxscore" not in payload:
            continue
        seen.add(game_id)
        box = payload["boxscore"]
        expected = identities[game_id]
        if {str(t.get("team", {}).get("id")) for t in box.get("teams", []) if t.get("statistics")} == expected:
            team_boxes.add(game_id)
        groups = box.get("players", [])
        present, valid = set(), set()
        for group in groups:
            team_id = str(group.get("team", {}).get("id"))
            athletes_seen = False
            team_valid = True
            for table in group.get("statistics", []):
                names = table.get("names", [])
                index = names.index("MIN") if "MIN" in names else None
                for athlete in table.get("athletes", []):
                    if athlete.get("didNotPlay"):
                        continue  # DNP is not a fabricated zero-minute appearance.
                    athletes_seen = True
                    stats = athlete.get("stats", [])
                    if index is None or len(stats) <= index or not minute_value(stats[index]):
                        team_valid = False
                        missing_minutes.append({"game_id": game_id, "team_id": team_id,
                                                "player_id": str(athlete.get("athlete", {}).get("id", ""))})
            if athletes_seen:
                present.add(team_id)
                if team_valid:
                    valid.add(team_id)
        if present == expected:
            player_boxes.add(game_id)
        if valid == expected:
            minutes.add(game_id)
    return {"source_files": sources, "summary_game_ids": sorted(seen),
            "paired_team_box_game_ids": sorted(team_boxes), "paired_player_box_game_ids": sorted(player_boxes),
            "paired_player_minutes_game_ids": sorted(minutes), "missing_minute_rows_in_cached_boxes": missing_minutes,
            "interpretation": "Structural cache coverage only; no original receipt proof or complete-roster/count validation"}


def reconcile(registry: dict, history: dict, candidates: dict, boxes: dict, audit: dict | None = None) -> dict:
    local_seasons = {r["season"] for r in history["seasons"]}
    by_provider = {p["provider_id"]: p for p in registry["providers"]}
    comparisons = []
    for candidate in candidates["providers"]:
        provider = by_provider[candidate["provider_id"]]
        for dataset in candidate["datasets"]:
            years = set(dataset["asset_seasons"])
            documented = set(dataset.get("documented_seasons", []))
            comparisons.append({"provider_id": provider["provider_id"], "dataset_id": dataset["dataset_id"],
                                "manifest_status": dataset["manifest_status"], "asset_seasons": sorted(years),
                                "documented_seasons": sorted(documented),
                                "potential_season_overlap": sorted(local_seasons & (years | documented)),
                                "seasons_outside_reference": sorted(years - local_seasons),
                                "verified_game_matches": None, "potentially_missing_game_ids": None,
                                "net_new_games": None, "coverage_verified_at_game_level": False,
                                "identifier_compatibility": provider["identifiers"],
                                "asset_access": asset_access_decision(provider)})
    categories = {"summary": set(boxes["summary_game_ids"]),
                  "paired_team_boxes": set(boxes["paired_team_box_game_ids"]),
                  "paired_player_boxes": set(boxes["paired_player_box_game_ids"]),
                  "paired_player_minutes": set(boxes["paired_player_minutes_game_ids"])}
    seasons, gaps = [], []
    for season in history["seasons"]:
        ids = {g["game_id"] for g in history["games"] if g["season"] == season["season"]}
        seasons.append(season | {"local_cache_coverage": {k: len(ids & values) for k, values in categories.items()},
                                 "verified_historical_pregame_receipts": 0})
        for kind, values in categories.items():
            gaps.append({"season": season["season"], "category": kind,
                         "reference_games": len(ids), "not_represented_in_local_cache": sorted(ids - values)})
    baseline = audit.get("by_season", {}) if audit else {}
    actual = {str(s["season"]): s["games"] for s in seasons}
    expected = {str(k): v["reference_games"] for k, v in baseline.items()}
    return {"schema_version": 1, "mode": "metadata_only", "reference_games": history["eligible_games"],
            "raw_events": history["raw_events"], "monthly_files": len(history["source_files"]),
            "duplicate_eligible_observations": history["duplicate_eligible_observations"],
            "excluded_events": history["excluded_events"], "reference_matches_prior_audit": actual == expected if audit else None,
            "reference_seasons": seasons, "candidate_comparisons": comparisons, "local_cache_gaps": gaps,
            "missing_minute_rows_in_cached_boxes": boxes["missing_minute_rows_in_cached_boxes"],
            "prior_audit_live_cache_gaps": baseline,
            "pregame_provenance": {"games_without_verified_original_receipts_in_inspected_sources": history["eligible_games"],
                                    "classification": "unverifiable; reconstruction would be Class B at best",
                                    "file_timestamps_are_receipts": False},
            "upstream_missing_game_count": None, "third_party_game_level_matches": None,
            "sports_assets_downloaded": 0, "sports_records_imported": 0,
            "limitations": ["No candidate game-ID manifest; exact overlaps, missing games and net additions unknown",
                            "2022 local archive begins January 2022; October-December 2021 not represented",
                            "Provider type 2/3 counts are not a uniform regular-season universe",
                            "Cached box/minute presence is not complete historical field validation",
                            "Final boxes/lineups and current file dates do not establish pregame availability"]}
