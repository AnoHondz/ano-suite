"""Versioned descriptive NBA context from existing archives and the existing live client.

No training, historical downloads, probability fallback, or original-record writes.
The derived SQLite index is disposable; historical availability is never invented.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import closing
from datetime import UTC, datetime, timedelta
import hashlib
import json
import sqlite3
import threading
import time
import uuid

import nba_live_client as live
from runtime_paths import cache_dir
from services.prediction_lifecycle import CALENDAR_ZONE, calendar_date, freshness_state, parse_kickoff, sanitize_decision

VERSION = "nba-descriptive-context-v1"
ARCHIVE = cache_dir("score_models", "nba_raw")
INDEX = cache_dir("nba_context", "history-v1.sqlite3")
# Bump when normalized payload fields change so existing indexes are rebuilt.
INDEX_SCHEMA = 2
_index_lock = threading.Lock()
_form_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nba-context")
_forms = {}


def _eligible(game):
    home, away = game.get("teams", {}).get("home", {}), game.get("teams", {}).get("visitors", {})
    scores = [live._as_int(game.get("scores", {}).get(side, {}).get("points")) for side in ("home", "visitors")]
    return (all(str(team.get("id", "")).isdigit() and 1 <= int(team["id"]) <= 30 for team in (home, away))
            and str(home["id"]) != str(away["id"]) and game.get("season_type") in (2, 3)
            and game.get("status", {}).get("completed") is True
            and game.get("status", {}).get("state") == "post"
            and all(score is not None and score > 0 for score in scores)
            and scores[0] != scores[1] and parse_kickoff(game.get("date", {}).get("start")) is not None)


def build_index(*, force=False):
    """Index only the 48 already-present monthly files; never import the trainer."""
    paths = sorted(ARCHIVE.glob("*.json"))
    if not paths:
        return {"available": False, "reason": "No existing local NBA history."}
    signature = hashlib.sha256(json.dumps([INDEX_SCHEMA, *[(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in paths]]).encode()).hexdigest()
    with _index_lock:
        if INDEX.exists() and not force:
            with closing(sqlite3.connect(INDEX)) as db:
                row = db.execute("SELECT value FROM metadata WHERE name='signature'").fetchone()
                if row and row[0] == signature:
                    return {"available": True, "games": db.execute("SELECT count(*) FROM games").fetchone()[0]}
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        temporary = INDEX.with_name(f"history-{uuid.uuid4().hex}.tmp")
        games = {}
        sources = []
        try:
            for path in paths:
                raw = path.read_bytes()
                sources.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
                payload = json.loads(raw)
                for event in payload.get("events", []):
                    game = live._normalize_event(event)
                    if not game or not _eligible(game):
                        continue
                    home, away = game["teams"]["home"], game["teams"]["visitors"]
                    row = (game["id"], int(game["season"]), int(game["season_type"]),
                           parse_kickoff(game["date"]["start"]).isoformat(), str(home["id"]), str(away["id"]),
                           json.dumps(game, separators=(",", ":")), path.name)
                    old = games.get(game["id"])
                    if old and (old[1:6] != row[1:6] or json.loads(old[6])["scores"] != game["scores"]):
                        raise ValueError("Conflicting existing NBA archive observations; index not replaced.")
                    games[game["id"]] = row
            with closing(sqlite3.connect(temporary)) as db:
                db.executescript("""CREATE TABLE metadata(name TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE games(id TEXT PRIMARY KEY,season INTEGER,season_type INTEGER,kickoff TEXT,
                home TEXT,away TEXT,payload TEXT,source_file TEXT);
                CREATE INDEX games_home ON games(home,season,kickoff);
                CREATE INDEX games_away ON games(away,season,kickoff);
                CREATE INDEX games_date ON games(kickoff);""")
                db.executemany("INSERT INTO games VALUES(?,?,?,?,?,?,?,?)", games.values())
                db.executemany("INSERT INTO metadata VALUES(?,?)", [("signature", signature), ("version", VERSION), ("sources", json.dumps(sources))])
                db.commit()
            temporary.replace(INDEX)
        finally:
            temporary.unlink(missing_ok=True)
    return {"available": True, "games": len(games), "files": len(paths)}


def _read(sql, args=()):
    if not build_index().get("available"):
        return []
    with closing(sqlite3.connect(f"{INDEX.as_uri()}?mode=ro", uri=True)) as db:
        result = []
        for payload, file in db.execute(sql, args):
            game = json.loads(payload)
            game["context_source"] = {"kind": "existing_local_archive", "file": file,
                                      "received_at": None, "historical_pregame_availability": "unverified"}
            result.append(game)
        return result


def archived_game(game_id):
    rows = _read("SELECT payload,source_file FROM games WHERE id=?", (str(game_id),))
    return rows[0] if rows else None


def games_on_date(day=None):
    if day is None:
        rows = _read("SELECT payload,source_file FROM games ORDER BY kickoff DESC LIMIT 1")
        if not rows:
            return [], ""
        day = calendar_date(rows[0]["date"]["start"])
    start = datetime.fromisoformat(day).replace(tzinfo=CALENDAR_ZONE)
    end = start + timedelta(days=1)
    return _read("SELECT payload,source_file FROM games WHERE kickoff>=? AND kickoff<? ORDER BY kickoff,id",
                 (start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat())), day


def game_for_id(game_id, date_hint=None):
    if not str(game_id).isdigit() or len(str(game_id)) > 12:
        raise ValueError("Use a valid ESPN NBA game identifier.")
    archived = archived_game(game_id)
    if archived:
        return archived
    # Exactly one explicit recent/current day; never probe historical seasons.
    day = parse_kickoff(date_hint) if date_hint and "T" in str(date_hint) else None
    if not day and date_hint:
        day = datetime.fromisoformat(str(date_hint)).replace(tzinfo=CALENDAR_ZONE)
    day = day or datetime.now(CALENDAR_ZONE)
    if abs((day.astimezone(CALENDAR_ZONE).date() - datetime.now(CALENDAR_ZONE).date()).days) > 14:
        return None
    try:
        return next((game for game in live.get_scoreboard_games(day, "page") if game["id"] == str(game_id)), None)
    except RuntimeError:
        from services import decision_store
        saved = decision_store.get("nba", str(game_id), allow_stale=True)
        if not saved:
            raise
        kickoff = parse_kickoff(saved.get("matchDate"))
        ids = [str(saved.get(key) or "") for key in ("homeId", "awayId")]
        if (kickoff is None or (date_hint and calendar_date(kickoff) != day.astimezone(CALENDAR_ZONE).date().isoformat())
                or not all(value.isdigit() and 1 <= int(value) <= 30 for value in ids) or ids[0] == ids[1]):
            raise
        # Only recorded identities/tipoff are retained. Unknown competition
        # type, venue neutrality and final score cannot become model inputs.
        status = saved.get("eventStatus") or {}
        if not isinstance(status, dict):
            status = {"state": "unknown", "detail": str(status)}
        return {"id": str(game_id), "date": {"start": saved["matchDate"]},
                "status": status, "season_type": None, "neutral_site": None,
                "teams": {"home": {"id": ids[0], "name": saved["teamA"]}, "visitors": {"id": ids[1], "name": saved["teamB"]}},
                "data_freshness": saved.get("dataFreshness") or {}, "context_source": {"kind": "cached_schedule"}}


def schedule_decision(game):
    import decision_ui
    home, away = game["teams"]["home"], game["teams"]["visitors"]
    return sanitize_decision(decision_ui.card_to_decision({
        "match_id": game["id"], "sport": "nba", "team_a": home["name"], "team_b": away["name"],
        "team_a_id": home["id"], "team_b_id": away["id"], "team_a_logo": home.get("logo"), "team_b_logo": away.get("logo"),
        "matchup": f'{home["name"]} vs {away["name"]}', "game_date": game["date"]["start"],
        "slate_date": calendar_date(game["date"]["start"]), "event_status": game["status"],
        "data_freshness": game.get("data_freshness", {}), "competition": f'NBA | {game.get("venue", {}).get("name") or "Venue unavailable"}',
        "action": "SKIP", "recommended_side": "Unavailable", "confidence": 0, "probabilities": {},
        "data_quality": "Limited Data", "model_source": "unavailable", "reason": "Descriptive context is available; no supported pregame prediction."}))


def _perspective(game, team_id):
    home = str(game["teams"]["home"]["id"]) == str(team_id)
    own, other = ("home", "visitors") if home else ("visitors", "home")
    gf = live._as_int(game["scores"][own]["points"])
    ga = live._as_int(game["scores"][other]["points"])
    return {"game_id": game["id"], "date": game["date"]["start"], "season": game["season"],
            "season_type": game["season_type"], "opponent": game["teams"][other]["name"],
            "points_for": gf, "points_against": ga, "result": "W" if gf > ga else "L", "home": home}


def _summary(games, team_id):
    rows = [_perspective(game, team_id) for game in games if game.get("standings_game", True)]
    return {"games": len(rows), "wins": sum(row["result"] == "W" for row in rows),
            "losses": sum(row["result"] == "L" for row in rows),
            "points_for": round(sum(row["points_for"] for row in rows) / len(rows), 1) if rows else None,
            "points_against": round(sum(row["points_against"] for row in rows) / len(rows), 1) if rows else None,
            "recent_results": rows[:6], "scope": "Observed regular-season games (provider type 2); not official standings"}


def _current(team_id, season):
    payload = live._espn_get(f"teams/{team_id}/schedule", params={"season": season}, ttl_seconds=live.NBA_SCHEDULE_TTL_SECONDS, request_profile="page")
    source = payload.get("_scorpred_source") or {}
    games = [dict(game, data_freshness=source) for event in payload.get("events", []) if (game := live._normalize_event(event))]
    return {"games": games, "source": source, "status": "available" if freshness_state(source) == "fresh" else "delayed"}


def _current_snapshot(team_id, season):
    key = (str(team_id), season)
    with _form_lock:
        existing = _forms.get(key)
        expired = existing is None
        if existing is not None and existing[1].done():
            try:
                expired = (time.monotonic() - existing[0] > 60
                           or freshness_state(existing[1].result().get("source")) != "fresh")
            except Exception:
                expired = time.monotonic() - existing[0] > 5
        if expired:
            existing = (time.monotonic(), _pool.submit(_current, team_id, season))
            _forms[key] = existing
    try:
        snapshot = existing[1].result(timeout=.3)
        source = dict(snapshot.get("source") or {})
        source["state"] = freshness_state(source)
        return {**snapshot, "source": source, "status": "available" if source["state"] == "fresh" else "delayed"}
    except TimeoutError:
        return {"games": [], "status": "refreshing", "source": {}}
    except Exception:
        return {"games": [], "status": "unavailable", "source": {}}


def context(game, *, now=None):
    now = now or datetime.now(UTC)
    kickoff = parse_kickoff(game["date"]["start"])
    cutoff = min(kickoff, now) - timedelta(hours=24)
    season = game.get("season") or live._season_from_game_date(game)
    teams = {}
    for side in ("home", "visitors"):
        team = game["teams"][side]
        team_id = str(team["id"])
        previous = _read("SELECT payload,source_file FROM games WHERE (home=? OR away=?) AND season=? AND season_type=2 AND kickoff<? ORDER BY kickoff DESC,id",
                         (team_id, team_id, season - 1, cutoff.isoformat()))
        # Historical observations stay descriptive even if fetched after the target game.
        current = _read("SELECT payload,source_file FROM games WHERE (home=? OR away=?) AND season=? AND season_type=2 AND kickoff<? ORDER BY kickoff DESC,id",
                        (team_id, team_id, season, cutoff.isoformat()))
        live_context = _current_snapshot(team_id, season) if kickoff > now else {"games": [], "status": "archive", "source": {}}
        by_id = {g["id"]: g for g in current}
        for row in live_context["games"]:
            if _eligible(row) and row["season"] == season and row["season_type"] == 2 and parse_kickoff(row["date"]["start"]) < cutoff:
                by_id[row["id"]] = row
        current = sorted(by_id.values(), key=lambda g: g["date"]["start"], reverse=True)
        teams["home" if side == "home" else "away"] = {"team": team, "current": _summary(current, team_id),
            "previous": _summary(previous, team_id), "current_season": season, "previous_season": season - 1,
            "current_status": live_context["status"], "current_source": live_context["source"],
            "historical_source": {"kind": "existing_local_archive", "files": sorted({g["context_source"]["file"] for g in previous}),
                                  "received_at": None, "historical_pregame_availability": "unverified"}}
    return {"version": VERSION, "role": "descriptive_only", "prediction_fallback": False,
            "game": {"id": game["id"], "kickoff": game["date"]["start"], "status": game["status"], "venue": game.get("venue"),
                     "schedule_source": (game.get("context_source") or {}).get("kind", "provider"),
                     "season_type": game.get("season_type"), "neutral_site": game.get("neutral_site"),
                     "result": game.get("scores") if game["status"].get("completed") else None},
            "teams": teams, "statistics_cutoff": cutoff.isoformat(), "availability_lag_hours": 24,
            "refresh_pending": any(t["current_status"] == "refreshing" for t in teams.values()),
            "prediction": {"winner_model": "No validated NBA winner classifier is installed; existing weighted rules are separate.",
                           "minimum_current_games_rules": 2, "minimum_current_games_scoring": 3,
                           "prior_season_probability_fallback": "unsupported"},
            "notice": "Historical team comparisons are descriptive, not win probabilities or betting recommendations. Archive records are retrospective; historical pregame availability is unverified. All comparison games precede the selected tipoff by at least 24 hours."}
