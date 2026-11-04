"""Read-only ScorPred diagnostics; never start the app, train, collect, or unseal.

Outputs contain aggregate diagnostics and public sports identifiers, never account
records or credential values. NFL source queries project schedule fields and
exclude all reserved IDs before returning any scores or play payloads to Python.
"""
import argparse
import ast
import csv
import hashlib
import json
import math
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "cache/diagnostics/2026-10-20_multisport"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(folder, name, payload):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(payload, indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")


def fingerprint(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def literal(path, name):
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(n, ast.Name) and n.id == name for n in names):
                return ast.literal_eval(node.value)
    raise ValueError("Literal declaration absent: " + name)


def inventory(out):
    files = list(ROOT.glob("*.py")) + [p for d in ("services", "scripts", "utils", "tests") for p in (ROOT / d).rglob("*.py")]
    modules, routes = [], []
    for path in sorted(files):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        imports, definitions = [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(n.name for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definitions.append({"name": node.name, "line": node.lineno})
                for decorator in getattr(node, "decorator_list", []):
                    if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr in {"route", "get", "post", "put", "delete"}
                            and decorator.args and isinstance(decorator.args[0], ast.Constant)):
                        routes.append({"file": str(path.relative_to(ROOT)), "function": node.name, "line": node.lineno,
                                       "route": decorator.args[0].value, "decorator": decorator.func.attr,
                                       "methods": next((ast.literal_eval(k.value) for k in decorator.keywords if k.arg == "methods"), None)})
        modules.append({"file": str(path.relative_to(ROOT)), "sha256": fingerprint(path), "lines": len(path.read_text(encoding="utf-8-sig").splitlines()),
                        "imports": sorted(set(imports)), "definitions": definitions})
    databases = []
    for folder in ("persistent", "cache", "data"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix not in {".db", ".sqlite", ".sqlite3"}:
                continue
            with readonly(path) as db:
                tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                schemas = {t: [dict(r) for r in db.execute('PRAGMA table_info("' + t.replace('"', '""') + '")')] for t in tables}
                databases.append({"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "tables": schemas,
                                  "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0]})
    write(out, "architecture_inventory.json", {"audited_at": datetime.now(UTC), "modules": modules, "routes": routes, "databases": databases,
          "frontend_files": [str(p.relative_to(ROOT)) for p in (ROOT / "frontend/src").rglob("*") if p.suffix in {".ts", ".tsx"}],
          "credential_presence_only": {k: bool(os.getenv(k)) for k in ("FOOTBALL_DATA_KEY", "NBA_API_KEY", "APISPORTS_KEY", "BALLDONTLIE_API_KEY", "DATABASE_URL")},
          "limitation": "Shell configuration is not proof of the environment loaded by the already-running web worker."})
    print("inventory", len(modules), "Python files;", len(routes), "route declarations", flush=True)


def csv_audit(out):
    files, combined, by_sha = [], [], defaultdict(list)
    paths = [ROOT / "data/historical_matches.csv", ROOT / "data/processed/soccer_training_data_clean.csv", ROOT / "data/processed/matches.csv", ROOT / "data/matches.csv"]
    paths.extend(sorted((ROOT / "data/raw").glob("*.csv")))
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        columns, keys, dates, teams = list(rows[0]) if rows else [], [], [], set()
        missing = Counter()
        outcomes, team_days = Counter(), Counter()
        bad_scores, bad_labels, team_self = [], [], []
        for i, row in enumerate(rows):
            raw_date = row.get("date", row.get("Date", ""))
            try:
                date = datetime.strptime(raw_date, "%d/%m/%Y").date().isoformat() if "/" in raw_date else datetime.fromisoformat(raw_date).date().isoformat()
            except ValueError:
                date = "invalid:" + raw_date
            home, away = row.get("home_team", row.get("HomeTeam", "")), row.get("away_team", row.get("AwayTeam", ""))
            hs, aws = number(row.get("goals_scored", row.get("FTHG"))), number(row.get("goals_conceded", row.get("FTAG")))
            keys.append((date, home.casefold(), away.casefold()))
            dates.append(date)
            teams.update((home, away))
            team_days[(date, home)] += 1
            team_days[(date, away)] += 1
            missing.update(k for k in columns if row.get(k) in (None, ""))
            if home == away:
                team_self.append(i)
            if hs is not None and aws is not None:
                if min(hs, aws) < 0 or max(hs, aws) > 30 or hs % 1 or aws % 1:
                    bad_scores.append(i)
                outcome = "HomeWin" if hs > aws else "AwayWin" if hs < aws else "Draw"
                outcomes[outcome] += 1
                label = row.get("result", row.get("FTR"))
                label = {"H": "HomeWin", "A": "AwayWin", "D": "Draw"}.get(label, label)
                if label and label != outcome:
                    bad_labels.append(i)
        duplicates = Counter(keys)
        record = {"file": str(path.relative_to(ROOT)), "sha256": fingerprint(path), "rows": len(rows), "columns": columns,
                  "unique_fixtures": len(duplicates), "duplicate_excess": sum(n - 1 for n in duplicates.values()),
                  "duplicate_examples": [list(k) + [n] for k, n in duplicates.items() if n > 1][:10],
                  "teams": len(teams), "team_names": sorted(teams), "min_date": min(dates, default=None), "max_date": max(dates, default=None),
                  "outcomes": dict(outcomes), "missing": dict(missing), "impossible_score_rows": bad_scores[:20], "bad_score_count": len(bad_scores),
                  "inconsistent_result_count": len(bad_labels), "self_match_count": len(team_self),
                  "same_team_day_multiple_count": sum(n > 1 for n in team_days.values()),
                  "same_team_day_examples": [list(k) + [n] for k, n in team_days.items() if n > 1][:10],
                  "date_counts_by_year": dict(sorted(Counter(d[:4] for d in dates).items())),
                  "league_counts": dict(Counter(r.get("league_id", "missing") for r in rows)),
                  "season_counts": dict(Counter(r.get("season", r.get("Season", "missing")) for r in rows))}
        identity_count = sum(not d.startswith("invalid:") and bool(h) and bool(a) for d, h, a in keys)
        record["rows_with_fixture_identity"] = identity_count
        if identity_count != len(rows):
            for key in ("unique_fixtures", "duplicate_excess", "duplicate_examples", "self_match_count", "same_team_day_multiple_count", "same_team_day_examples"):
                record[key] = None
            record["fixture_integrity_limitation"] = "This schema does not supply dated fixture identities; fixture duplicates cannot be measured."
        if path.parent.name == "raw":
            season = int(path.stem.split("_")[-1])
            upper = str(season + 1) + ("-08-01" if season == 2019 else "-07-01")
            record["out_of_labeled_season"] = sum(not (str(season) + "-07-01" <= d < upper) for d in dates)
            record["unique_league_fixture_capacity"] = len(teams) * (len(teams) - 1)
            combined.extend(keys)
        if path.name == "soccer_training_data_clean.csv":
            record["feature_stats"] = {col: {"missing": sum(r.get(col) in (None, "") for r in rows),
                                             "zero": sum(number(r.get(col)) == 0 for r in rows),
                                             "unique": len({r.get(col) for r in rows})} for col in columns if col not in {"date", "home_team", "away_team", "result", "target"}}
        by_sha[record["sha256"]].append(record["file"])
        files.append(record)
    write(out, "soccer_csv_integrity.json", {"audited_at": datetime.now(UTC), "files": files,
          "byte_identical_file_groups": [paths for paths in by_sha.values() if len(paths) > 1],
          "raw_combined_rows": len(combined), "raw_combined_unique_fixtures": len(set(combined)),
          "raw_combined_duplicate_excess": len(combined) - len(set(combined))})
    print("soccer CSVs", len(files), "checked", flush=True)


def espn_event(event, fallback_season=None):
    comps = event.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    if not {"home", "away"} <= set(sides):
        return None
    def score(side):
        value = sides[side].get("score")
        if isinstance(value, dict):
            value = value.get("value", value.get("displayValue"))
        return number(value)
    status = comp.get("status") or event.get("status") or {}
    status = status.get("type") or status
    return {"id": str(event.get("id") or comp.get("id") or ""), "date": event.get("date", comp.get("date")),
            "season": (event.get("season") or {}).get("year", fallback_season),
            "season_type": (event.get("season") or {}).get("type", (event.get("seasonType") or {}).get("type")),
            "completed": bool(status.get("completed", status.get("state") == "post" and status.get("name") == "STATUS_FINAL")),
            "state": status.get("state", "unknown"), "status_name": status.get("name"), "home_id": str((sides["home"].get("team") or {}).get("id", "")),
            "away_id": str((sides["away"].get("team") or {}).get("id", "")),
            "home_name": (sides["home"].get("team") or {}).get("displayName"),
            "away_name": (sides["away"].get("team") or {}).get("displayName"),
            "home_score": score("home"), "away_score": score("away"),
            "uid": event.get("uid", "")}


def cache_audit(out):
    now = datetime.now(UTC)
    manifests, sports, summaries, rosters = [], defaultdict(dict), [], []
    conflicts, repeats, errors = [], Counter(), []
    for directory, sport in (("cache/nba_public", "nba"), ("cache/football", "soccer"), ("cache/score_models/nba_raw", "nba_training")):
        for path in sorted((ROOT / directory).glob("*.json")):
            try:
                raw = path.read_bytes()
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    manifests.append({"file": str(path.relative_to(ROOT)), "kind": type(payload).__name__})
                    continue
                meta = payload.get("_scorpred_source") or {}
                kind = "schedule" if "team" in payload and "events" in payload else "scoreboard" if "events" in payload else "summary" if "header" in payload else "roster" if "athletes" in payload else "teams" if "sports" in payload else "other"
                events = payload.get("events") or ([payload["header"]] if "header" in payload else [])
                mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                entry = {"file": str(path.relative_to(ROOT)), "sport": sport, "kind": kind, "bytes": len(raw),
                         "sha256": hashlib.sha256(raw).hexdigest(), "mtime": mtime.isoformat(), "mtime_age_seconds": (now - mtime).total_seconds(),
                         "embedded_observed_at": meta.get("observed_at"), "embedded_expiry": meta.get("expires_at"),
                         "embedded_source": meta.get("source"), "events": len(events), "top_level_fields": sorted(payload)}
                if kind == "schedule":
                    entry.update(team_id=payload["team"].get("id"), season=(payload.get("season") or {}).get("year"))
                if kind == "roster":
                    athletes = payload.get("athletes") or []
                    if athletes and isinstance(athletes[0], dict) and "items" in athletes[0]:
                        athletes = [r for group in athletes for r in group.get("items", [])]
                    rosters.append({"file": entry["file"], "sport": sport, "players": len(athletes),
                                    "missing_id": sum(not r.get("id") for r in athletes), "injury_records": sum(len(r.get("injuries") or []) for r in athletes),
                                    "injury_dates_missing": sum(not inj.get("date") for r in athletes for inj in r.get("injuries") or []), "mtime": entry["mtime"]})
                if kind == "summary":
                    box = payload.get("boxscore") or {}
                    summaries.append({"file": entry["file"], "sport": sport, "event_id": (payload.get("header") or {}).get("id"),
                                      "team_stat_records": len(box.get("teams") or []), "player_groups": len(box.get("players") or []),
                                      "game_state": (payload.get("meta") or {}).get("gameState"), "provider_updated_at": (payload.get("meta") or {}).get("lastUpdatedAt")})
                manifests.append(entry)
                for event in events:
                    normalized = espn_event(event, (payload.get("season") or {}).get("year"))
                    if normalized is None or not normalized["id"]:
                        continue
                    key = normalized["id"]
                    repeats[sport] += key in sports[sport]
                    prior = sports[sport].get(key)
                    if prior:
                        a, b = prior["event"], normalized
                        fields = [f for f in ("home_id", "away_id", "date") if a[f] != b[f]]
                        if a["completed"] and b["completed"]:
                            fields += [f for f in ("home_score", "away_score") if a[f] is not None and b[f] is not None and a[f] != b[f]]
                        if fields:
                            conflicts.append({"sport": sport, "id": key, "fields": fields, "file_a": prior["file"], "file_b": entry["file"], "a": a, "b": b})
                    if not prior or mtime.isoformat() > prior["mtime"]:
                        sports[sport][key] = {"event": normalized, "file": entry["file"], "mtime": mtime.isoformat()}
            except (ValueError, OSError, TypeError, KeyError) as exc:
                errors.append({"file": str(path.relative_to(ROOT)), "error": type(exc).__name__})
        print("parsed", directory, flush=True)
    coverage = {}
    for sport, records in sports.items():
        grouped = defaultdict(list)
        for value in records.values():
            e = value["event"]
            season = str(e["season"] or (e["date"] or "unknown")[:4])
            grouped[season].append(e)
        coverage[sport] = {season: {"unique_games": len(events), "completed": sum(e["completed"] for e in events),
                                   "scheduled_or_live": sum(not e["completed"] and e["state"] in {"pre", "in"} for e in events),
                                   "postponed_or_other_nonfinal": sum(not e["completed"] and e["state"] not in {"pre", "in"} for e in events),
                                   "completed_missing_scores": sum(e["completed"] and (e["home_score"] is None or e["away_score"] is None) for e in events),
                                   "invalid_dates": sum(stamp(e["date"]) is None for e in events),
                                   "self_match": sum(e["home_id"] == e["away_id"] for e in events),
                                   "negative_scores": sum(any(s is not None and s < 0 for s in (e["home_score"], e["away_score"])) for e in events),
                                   "teams": len({e[s] for e in events for s in ("home_id", "away_id")}),
                                   "season_types": dict(Counter(str(e["season_type"]) for e in events)),
                                   "min_date": min(e["date"] for e in events), "max_date": max(e["date"] for e in events),
                                   "draws": sum(e["completed"] and e["home_score"] is not None and e["home_score"] == e["away_score"] for e in events)} for season, events in sorted(grouped.items())}
    write(out, "cache_manifest.json", {"audited_at": now, "files": manifests, "errors": errors,
          "summary": {sport: {"files": sum(e.get("sport") == sport for e in manifests), "embedded_observation_files": sum(e.get("sport") == sport and bool(e.get("embedded_observed_at")) for e in manifests),
                               "unique_events": len(records), "repeat_event_references": repeats[sport]} for sport, records in sports.items()}})
    write(out, "cached_event_coverage.json", {"audited_at": now, "coverage": coverage, "conflicts": conflicts, "rosters": rosters, "summaries": summaries,
          "scope": "Union of cached responses, deduplicated by provider event ID with newest file retained. Repeated cache references are not duplicate database fixtures. Soccer season grouping uses provider year and is not a competition/season completeness denominator."})
    write(out, "cached_events.json", {s: list(r.values()) for s, r in sports.items()})


def nfl_audit(out):
    protocol = load(ROOT / "cache/nflverse/phase2_2/protocol.json")
    held = protocol["holdout"]["game_ids"]
    require = len(held) == 109
    if not require:
        raise ValueError("Unexpected holdout seal; NFL audit refused")
    marks = ",".join("?" for _ in held)
    path = ROOT / "cache/nflverse/nflverse.sqlite3"
    with readonly(path) as db:
        db.execute("BEGIN")
        datasets = [dict(r) for r in db.execute("SELECT * FROM datasets ORDER BY dataset,season")]
        columns = [r[1] for r in db.execute("PRAGMA table_info(refresh_attempts)")]
        attempts = [dict(r) for r in db.execute("SELECT * FROM refresh_attempts ORDER BY id DESC")]
        receipts = [dict(r) for r in db.execute("SELECT id,dataset,season,attempted_at,collected_at,ingested_at,source_identifier,source_publication_at,status,content_sha256,row_count,writer_version,error FROM collection_receipts ORDER BY id")]
        migration = [dict(r) for r in db.execute("SELECT * FROM schema_migrations")]
        versions_ceiling = db.execute("SELECT MAX(rowid) FROM record_versions").fetchone()[0]
        # This SQL projects every field explicitly, redacting reserved scores in SQLite.
        schedule = [dict(r) for r in db.execute(
            "SELECT record_key,observed_at,json_extract(payload,'$.game_id') game_id,season,"
            "json_extract(payload,'$.week') week,json_extract(payload,'$.game_type') game_type,"
            "json_extract(payload,'$.gameday') gameday,json_extract(payload,'$.gametime') gametime,"
            "json_extract(payload,'$.home_team') home_team,json_extract(payload,'$.away_team') away_team,"
            f"CASE WHEN json_extract(payload,'$.game_id') IN ({marks}) THEN NULL ELSE json_extract(payload,'$.home_score') END home_score,"
            f"CASE WHEN json_extract(payload,'$.game_id') IN ({marks}) THEN NULL ELSE json_extract(payload,'$.away_score') END away_score "
            "FROM records WHERE dataset='schedules' ORDER BY season,record_key", (*held, *held))]
        projections = {"pbp": ("game_id", "posteam", "defteam", "play_type", "epa", "success", "yards_gained", "down", "third_down_converted", "yardline_100", "passer_player_id", "rush_attempt", "pass_attempt", "sack", "cpoe", "qb_kneel", "qb_spike", "no_play", "two_point_attempt", "fixed_drive", "interception", "fumble_lost"),
                       "player_stats": ("player_id", "recent_team", "week", "passing_yards", "rushing_yards", "receiving_yards"),
                       "snap_counts": ("pfr_player_id", "player", "team", "week", "offense_snaps", "offense_pct"),
                       "injuries": ("gsis_id", "full_name", "team", "week", "report_status", "date_modified"),
                       "participation": ("nflverse_game_id", "play_id", "offense_players", "defense_players"),
                       "depth_charts": ("gsis_id", "full_name", "club_code", "week", "depth_team"),
                       "rosters": ("gsis_id", "team", "position"), "players": ("gsis_id", "pfr_id", "espn_id")}
        field_stats, pbp_games = {}, defaultdict(set)
        for dataset, fields in projections.items():
            select = ",".join("json_extract(payload,'$." + f + "') AS '" + f + "'" for f in fields)
            stats = defaultdict(lambda: {"rows": 0, "missing": Counter(), "impossible": Counter()})
            for row in db.execute(f"SELECT season,observed_at,{select} FROM records WHERE dataset=? AND COALESCE(game_id,'') NOT IN ({marks}) "
                                  f"AND COALESCE(json_extract(payload,'$.game_id'),json_extract(payload,'$.nflverse_game_id'),'') NOT IN ({marks}) "
                                  "AND NOT (season=2026 AND COALESCE(json_extract(payload,'$.week'),0)>=12)", (dataset, *held, *held)):
                group = stats[row["season"]]
                group["rows"] += 1
                group["missing"].update(f for f in fields if row[f] is None or row[f] == "")
                if dataset == "pbp":
                    pbp_games[row["season"]].add(row["game_id"])
                    for f in ("success", "rush_attempt", "pass_attempt", "sack", "qb_kneel", "qb_spike", "no_play", "two_point_attempt", "interception", "fumble_lost"):
                        if row[f] is not None and row[f] not in (0, 1):
                            group["impossible"][f] += 1
                    if number(row["yardline_100"]) is not None and not 0 <= float(row["yardline_100"]) <= 100:
                        group["impossible"]["yardline_100"] += 1
            field_stats[dataset] = {s: {"rows": v["rows"], "missing": dict(v["missing"]), "impossible": dict(v["impossible"])} for s, v in stats.items()}
            print("NFL audited", dataset, flush=True)
        forecasts = [dict(r) for r in db.execute("SELECT * FROM nfl_forecasts")]
        models = [{"model_id": r["model_id"], "sha256": r["sha256"], "checksum_valid": hashlib.sha256(r["payload"].encode()).hexdigest() == r["sha256"]} for r in db.execute("SELECT * FROM nfl_frozen_models")]
        studies = [{"study_id": r["study_id"], "sha256": r["sha256"], "checksum_valid": hashlib.sha256(r["payload"].encode()).hexdigest() == r["sha256"]} for r in db.execute("SELECT * FROM nfl_forecast_studies")]
        events = [dict(r) for r in db.execute("SELECT id,created_at,game_id,kind,payload FROM nfl_forecast_events")]
        triggers = [dict(r) for r in db.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'")]
    now, groups, bad, unscored_past = datetime.now(UTC), defaultdict(list), [], []
    for row in schedule:
        if row["game_id"] in held:
            row["outcome_access"] = "sealed_redacted_in_sql"
        try:
            kickoff = datetime.fromisoformat(row["gameday"] + "T" + row["gametime"]).replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
        except (TypeError, ValueError):
            kickoff = None
        row["kickoff"] = kickoff.isoformat() if kickoff else None
        groups[row["season"]].append(row)
        if row["game_id"] in held:
            continue
        hs, aws = number(row["home_score"]), number(row["away_score"])
        if row["home_team"] == row["away_team"] or (hs is not None and (hs < 0 or hs > 100 or hs % 1)) or (aws is not None and (aws < 0 or aws > 100 or aws % 1)):
            bad.append(row["game_id"])
        if kickoff and (now - kickoff).total_seconds() > 86400 and (hs is None or aws is None):
            unscored_past.append(row["game_id"])
    coverage = {season: {"schedules": len(rows), "game_types": dict(Counter(r["game_type"] for r in rows)),
                        "known_complete_unsealed": sum(r["game_id"] not in held and r["home_score"] is not None and r["away_score"] is not None and r["kickoff"] is not None and stamp(r["kickoff"]) < now for r in rows),
                        "reserved_outcomes_not_accessed": sum(r["game_id"] in held for r in rows),
                        "future_schedule_count": sum(bool(r["kickoff"]) and stamp(r["kickoff"]) > now for r in rows),
                        "pbp_game_ids": len(pbp_games[season]),
                        "completed_without_pbp": [r["game_id"] for r in rows if r["game_id"] not in held and r["home_score"] is not None and r["kickoff"] is not None and stamp(r["kickoff"]) < now and r["game_id"] not in pbp_games[season]],
                        "teams": len({r[k] for r in rows for k in ("home_team", "away_team")}),
                        "duplicate_game_ids": len(rows) - len({r["game_id"] for r in rows})} for season, rows in groups.items()}
    write(out, "nfl_data_audit.json", {"audited_at": now, "read_only": True, "source_version_ceiling": versions_ceiling,
          "holdout_games": len(held), "holdout_outcomes_read": 0, "datasets": datasets, "migrations": migration,
          "refresh_attempt_columns": columns, "refresh_attempts": attempts, "collection_receipts": receipts,
          "coverage": coverage, "fields": field_stats, "bad_schedule_game_ids": bad, "past_missing_results": unscored_past,
          "frozen_models": models, "frozen_studies": studies, "archive_forecasts": [json.loads(r["payload"]) for r in forecasts],
          "archive_forecast_hashes_valid": all(hashlib.sha256(r["payload"].encode()).hexdigest() == r["sha256"] for r in forecasts),
          "forecast_events": events, "immutability_triggers": triggers})
    write(out, "nfl_schedule_redacted.json", {"holdout_scores_redacted": True, "games": schedule})
    print("NFL audit complete; reserved outcomes excluded", flush=True)


def tracking_audit(out):
    source = ROOT / "cache/prediction_tracking.json"
    # Never emit usernames, stored notes, or free-text account content.
    safe_keys = ("id", "prediction_id", "match_id", "sport", "timestamp", "created_at", "date", "kickoff", "event_date", "prediction", "predicted_winner", "predicted_outcome", "actual_result", "actual_winner", "actual_outcome", "result", "status", "confidence", "verified", "correct", "is_correct", "source", "model_version")
    # Project only non-NFL rows in SQLite before Python decodes prediction data.
    # The live tracking store is separate from the frozen NFL forecast archive.
    with sqlite3.connect(":memory:") as scratch:
        projection = ",".join("'" + k + "',json_extract(value,'$." + k + "')" for k in safe_keys)
        rows = [json.loads(r[0]) for r in scratch.execute(
            "SELECT json_object(" + projection + ") FROM json_each(?,'$.predictions') "
            "WHERE LOWER(COALESCE(json_extract(value,'$.sport'),'')) NOT IN ('nfl','football_nfl')", (source.read_text(encoding="utf-8"),))]
    summary = {"file": str(source.relative_to(ROOT)), "rows": len(rows), "fields": sorted({k for r in rows for k in r}),
               "sports": dict(Counter(str(r.get("sport")) for r in rows)), "statuses": dict(Counter(str(r.get("status")) for r in rows)),
               "id_duplicate_excess": len(rows) - len({r.get("id", r.get("prediction_id")) for r in rows}),
               "safe_rows": [{k: r.get(k) for k in safe_keys if k in r} for r in rows]}
    write(out, "tracking_audit.json", summary)
    print("tracking rows", len(rows), flush=True)


def preservation(out):
    before = load(out / "preservation_before.json")
    result = {"verified_at": datetime.now(UTC), "files_checked": len(before["files"]),
              "changed": [name for name, expected in before["files"].items() if not (ROOT / name).exists() or fingerprint(ROOT / name) != expected],
              "scope": "Production/research source, frozen exports, model manifests/artifacts, validation reports and soccer training inputs. Operational caches and append-only live NFL collection continue normally."}
    write(out, "preservation_after.json", result)
    if result["changed"]:
        raise ValueError("Pre-existing audited artifacts changed: " + str(result["changed"]))
    print("preservation", result["files_checked"], "files unchanged", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", choices=("inventory", "csv", "caches", "nfl", "tracking", "preservation", "all"), default="all")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    actions = {"inventory": inventory, "csv": csv_audit, "caches": cache_audit, "nfl": nfl_audit, "tracking": tracking_audit, "preservation": preservation}
    for name, action in actions.items():
        if args.section in (name, "all"):
            action(args.output)


if __name__ == "__main__":
    main()
