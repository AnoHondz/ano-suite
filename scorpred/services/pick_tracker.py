"""Pick tracker: every pick and parlay ScorPred emails is recorded, then graded from ESPN's final results.

Recorded when an email goes out (morning, midday player parlays, on-request reports). Graded from ESPN box
scores (player stats) and final scores (BTTS, goals, moneyline, spread, totals). Free ESPN data, no odds credits.
The morning email opens with yesterday graded; Ano reads /api/pick-record for "how did my picks do" and the
Sunday scorecard. Parlays are judged at the price when built: a $1 stake returns decimal_odds when it wins.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from runtime_paths import persistent_root

ET = ZoneInfo("America/New_York")
GIVE_UP_DAYS = 3  # a game ESPN never finalises (postponed, abandoned) is voided after this
SOCCER_PATHS = tuple(f"soccer/{slug}" for slug in ("eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "uefa.champions", "uefa.europa"))
LABEL_STATS = (("pts + reb + ast", "pra"), ("points + rebounds + assists", "pra"), ("shots on target", "shots_on_target"),
               ("3-point", "threes"), ("threes", "threes"), ("points", "points"), ("rebounds", "rebounds"),
               ("assists", "assists"), ("shots", "shots"), ("goals", "goals"), ("saves", "saves"), ("fouls", "fouls"))
MARK = {"won": "✅", "lost": "❌", "push": "➖", "void": "➖", "pending": "⏳"}
MODEL_VERSION = "value-policy-2026-11-04"
TIER_WORDS = {"low": "Easy", "medium": "Medium", "fly": "Fly", "five": "5-leg", "ladder": "Ladder"}
SNAPSHOT_COLUMNS = (("parlays", "tag", "TEXT"), ("parlays", "version", "TEXT"),
                    ("legs", "book", "TEXT"), ("legs", "fair", "REAL"), ("legs", "story", "REAL"), ("legs", "sport", "TEXT"),
                    ("legs", "close_price", "REAL"), ("legs", "close_at", "TEXT"))
CLOSE_BEFORE = timedelta(minutes=20)  # the closing price is read in the last 20 minutes before kickoff


def path():
    return persistent_root() / "pick-tracker" / "picks.sqlite3"


@contextmanager
def db(location=None):
    target = location or path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS parlays (key TEXT PRIMARY KEY, day TEXT, source TEXT, sport TEXT, "
                               "kind TEXT, tier TEXT, decimal_odds REAL, hit_prob REAL, result TEXT DEFAULT 'pending', recorded_at TEXT)")
            connection.execute("CREATE TABLE IF NOT EXISTS legs (parlay TEXT, n INTEGER, selection TEXT, kickoff TEXT, price REAL, "
                               "chance REAL, grade TEXT, result TEXT DEFAULT 'pending', actual TEXT, settled_at TEXT, PRIMARY KEY (parlay, n))")
            # Snapshot columns (audit, 2026-11-04): what was sent stays exactly as sent; the closing price is added beside it.
            for table, column, kind in SNAPSHOT_COLUMNS:
                if column not in {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            yield connection
    finally:
        connection.close()


# ── recording ────────────────────────────────────────────────────────────────

def _key(day, sport, kind, tier, selections):
    return hashlib.sha256(json.dumps([day, sport, kind, tier, sorted(selections)]).encode()).hexdigest()[:20]


def record(day, source, sport, kind, tier, parlay, legs, *, location=None):
    """One parlay (or a single pick when legs has one entry). The same legs on the same day are kept once, and a
    recorded pick is never rewritten: its price, chance, book, fair chance and tag stay exactly as sent."""
    if not legs:
        return None
    key = _key(str(day), sport, kind, tier, [leg["selection"] for leg in legs])
    with db(location) as connection:
        if connection.execute("SELECT 1 FROM parlays WHERE key=?", (key,)).fetchone():
            return key
        connection.execute("INSERT INTO parlays (key,day,source,sport,kind,tier,decimal_odds,hit_prob,recorded_at,tag,version) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (key, str(day), source, sport, kind, tier, (parlay or {}).get("decimal_odds"), (parlay or {}).get("hit_prob"),
                            datetime.now(UTC).isoformat(), (parlay or {}).get("tag"), MODEL_VERSION))
        for n, leg in enumerate(legs):
            connection.execute("INSERT INTO legs (parlay,n,selection,kickoff,price,chance,grade,book,fair,story,sport) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (key, n, leg["selection"], leg.get("kickoff"), leg.get("price"), leg.get("chance"),
                                json.dumps(leg.get("grade") or {}), leg.get("book"), leg.get("fair"),
                                leg.get("story") or 0, leg.get("sport") or sport))
    return key


def infer_grade(leg):
    """Grading facts read from a leg's text, for legs saved before they carried them (player props only)."""
    import re
    text, game = leg.get("selection") or "", str(leg.get("game_id") or "")
    if m := re.fullmatch(r"(.+) (Over|Under) ([\d.]+) (Points|Rebounds|Assists|Pts \+ Reb \+ Ast)", text):
        if game.startswith("espn-") and leg.get("sport") == "nba":
            return {"kind": "player", "path": "basketball/nba", "event": game.removeprefix("espn-"), "player": m[1],
                    "stat": stat_for(m[4]), "line": float(m[3]), "side": m[2].lower()}
    if m := re.fullmatch(r"(.+) ([\d.]+)\+ (Shots on Target|Shots|Assists|Fouls|Saves|Goals)", text):
        return {"kind": "player", "sport": "soccer", "team": leg.get("team"), "player": m[1], "stat": stat_for(m[3]),
                "line": float(m[2]) - 0.5, "side": "over"}
    event = game.removeprefix("espn-") if game.startswith("espn-") else None
    if not event or leg.get("sport") != "soccer":
        return {}
    if m := re.fullmatch(r"Both teams to score: (Yes|No)", text):
        return {"kind": "btts", "event": event, "side": m[1].lower()}
    if m := re.fullmatch(r"(Over|Under) ([\d.]+) goals", text):
        return {"kind": "total", "event": event, "line": float(m[2]), "side": m[1].lower()}
    if m := re.fullmatch(r"(.+) (over|under) ([\d.]+) goals", text):
        return {"kind": "team_total", "event": event, "team_name": m[1], "line": float(m[3]), "side": m[2]}
    return {}


def _soccer_summary(event):
    """A soccer game's summary when its league is unknown: the first competition ESPN knows it under."""
    for espn_path in SOCCER_PATHS:
        try:
            summary = _summary(espn_path, event)
        except Exception:
            continue
        if summary["competitors"]:
            return espn_path, summary
    return None, None


def record_tiers(day, source, tiers_by_sport, *, location=None):
    for sport, tiers in (tiers_by_sport or {}).items():
        for tier, parlay in (tiers or {}).items():
            if parlay:
                legs = [leg if leg.get("grade") else {**leg, "grade": infer_grade(leg)} for leg in parlay["legs"]]
                record(day, source, sport, "player_parlay", tier, parlay, legs, location=location)


def game_grade(leg):
    """Grading facts for a Parlay Mode game-line leg (moneyline, spread, total)."""
    import parlay_engine as pe
    kind = {"h2h": "moneyline", "spreads": "spread", "totals": "game_total"}.get(leg.get("market"))
    event = str(leg.get("event_id") or "")
    if not kind or not event.startswith("espn-") or not pe.ESPN_PATHS.get(leg.get("sport_key")):
        return {}
    return {"kind": kind, "path": pe.ESPN_PATHS[leg["sport_key"]], "event": event.removeprefix("espn-"),
            "outcome": leg.get("outcome"), "point": leg.get("point"), "home": leg.get("home"), "away": leg.get("away")}


def record_slates(day, source, parlays, *, location=None):
    """Parlay Mode: each sport's parlay and the all-sports parlay."""
    if not parlays:
        return
    picks = [pick for slate in parlays.get("slates") or [] for pick in slate.get("picks") or []]
    labelled = {f"{p['selection']} ({p['american']})": p for p in picks}
    def legs_of(parlay):
        return [{"selection": p["selection"], "kickoff": p.get("commence_time"), "price": p.get("price"), "book": p.get("book"),
                 "fair": p.get("consensus_prob"), "chance": p.get("prob"), "grade": game_grade(p)}
                for p in (labelled.get(s) for s in parlay["selections"]) if p]
    for slate in parlays.get("slates") or []:
        if slate.get("parlay"):
            record(day, source, slate.get("sport") or "", "game_parlay", "", slate["parlay"], legs_of(slate["parlay"]), location=location)
    if parlays.get("combined"):
        record(day, source, "all", "game_parlay", "all sports", parlays["combined"], legs_of(parlays["combined"]), location=location)


def record_sent(day, source, *, report=None, tiers_by_sport=None, parlays=None, location=None):
    """Record what an email just sent. Tracking never blocks or fails an email."""
    try:
        report = report or {}
        record_tiers(day, source, tiers_by_sport or report.get("player_parlays"), location=location)
        record_slates(day, source, parlays or report.get("parlays"), location=location)
        record_top_picks(day, source, report.get("props"), location=location)
    except Exception:
        pass


def recap_for_email():
    """Grade what is due, then the 'Yesterday's results' block; '' on any failure."""
    try:
        settle()
        return render_recap()
    except Exception:
        return ""


def stat_for(label):
    text = str(label or "").casefold()
    return next((stat for word, stat in LABEL_STATS if word in text), None)


def record_top_picks(day, source, props_by_sport, *, location=None):
    """The morning email's single picks at sportsbook lines; the game is found on ESPN's scoreboard when grading."""
    for sport, rows in (props_by_sport or {}).items():
        for p in rows or []:
            if p.get("line") is None or not p.get("market"):
                continue
            leg = {"selection": f'{p["name"]} {str(p["side"]).capitalize()} {p["line"]:g} {p.get("label", "")}'.strip(),
                   "kickoff": p.get("kickoff"), "price": p["market"].get("decimal_odds"), "chance": p.get("chance"),
                   "grade": {"kind": "player", "sport": sport, "team": p.get("team"), "player": p["name"],
                             "stat": stat_for(p.get("label")), "line": p["line"], "side": p["side"]}}
            record(day, source, sport, "pick", "", {"decimal_odds": leg["price"], "hit_prob": leg["chance"]}, [leg], location=location)


# ── ESPN results ─────────────────────────────────────────────────────────────

def _summary(espn_path, event):
    """{completed, competitors: [{id, name, homeAway, score}]} for one ESPN event; cached once final."""
    import requests
    from runtime_paths import cache_dir
    target = cache_dir("pick_results") / f"{espn_path.replace('/', '-')}-{event}.json"
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    response = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{espn_path}/summary", params={"event": event}, timeout=(4, 20))
    response.raise_for_status()
    competition = ((response.json().get("header") or {}).get("competitions") or [{}])[0]
    status = (competition.get("status") or {}).get("type") or {}
    result = {"completed": bool(status.get("completed")), "state": status.get("state"),
              "competitors": [{"id": str((c.get("team") or {}).get("id")), "name": (c.get("team") or {}).get("displayName"),
                               "homeAway": c.get("homeAway"), "score": float(c["score"]) if c.get("score") not in (None, "") else None}
                              for c in competition.get("competitors") or []]}
    if result["completed"]:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result), encoding="utf-8")
    return result


def find_event(sport, team, kickoff):
    """(espn path, event id) for the team's game on the kickoff's date, from ESPN's scoreboard."""
    import requests
    import parlay_engine as pe
    from services.prediction_lifecycle import parse_kickoff
    when = parse_kickoff(kickoff)
    if not when or not team:
        return None
    paths = {"nba": ("basketball/nba",), "nfl": ("football/nfl",), "soccer": SOCCER_PATHS}.get(sport, ())
    for espn_path in paths:
        for day in {when.astimezone(ET).date(), when.astimezone(UTC).date()}:
            try:
                events = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{espn_path}/scoreboard",
                                      params={"dates": day.strftime("%Y%m%d")}, timeout=15).json().get("events") or []
            except Exception:
                continue
            for event in events:
                names = [(c.get("team") or {}).get(k) for c in (event.get("competitions") or [{}])[0].get("competitors") or []
                         for k in ("displayName", "shortDisplayName", "name")]
                if any(name and pe.teams_match(name, team) for name in names):
                    return espn_path, str(event["id"])
    return None


# nflverse field -> (ESPN box-score category, stat key, part of an "a/b" value)
NFL_BOX = {"passing_yards": ("passing", "passingYards", None), "completions": ("passing", "completions/passingAttempts", 0),
           "attempts": ("passing", "completions/passingAttempts", 1), "rushing_yards": ("rushing", "rushingYards", None),
           "carries": ("rushing", "rushingAttempts", None), "receptions": ("receiving", "receptions", None),
           "receiving_yards": ("receiving", "receivingYards", None)}


def nfl_player_value(event, player, stat):
    """A player's final NFL stat from ESPN's box score; stat is nflverse fields joined by '+'. None if he had none."""
    import requests
    from runtime_paths import cache_dir
    from services.prop_markets import _name
    target = cache_dir("pick_results") / f"nfl-box-{event}.json"
    if target.exists():
        box = json.loads(target.read_text(encoding="utf-8"))
    else:
        box = requests.get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary",
                           params={"event": event}, timeout=(4, 20)).json().get("boxscore") or {}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(box), encoding="utf-8")
    total, seen = 0.0, False
    for field in str(stat).split("+"):
        category, key, part = NFL_BOX.get(field, (None, None, None))
        for team in box.get("players") or []:
            for block in team.get("statistics") or []:
                if block.get("name") != category or key not in (block.get("keys") or []):
                    continue
                index = block["keys"].index(key)
                for athlete in block.get("athletes") or []:
                    if _name((athlete.get("athlete") or {}).get("displayName")) != _name(player):
                        continue
                    raw = str((athlete.get("stats") or [])[index] if index < len(athlete.get("stats") or []) else "")
                    piece = raw.split("/")[part] if part is not None and "/" in raw else raw
                    try:
                        total += float(piece)
                        seen = True
                    except ValueError:
                        pass
    return total if seen else None


def _value(row, stat):
    if stat == "pra":
        parts = [row.get("points"), row.get("rebounds"), row.get("assists")]
        return None if any(p is None for p in parts) else sum(parts)
    return row.get(stat)


def _over_under(value, line, side):
    if value is None or line is None:
        return None
    if value == line:
        return "push"
    return "won" if (value > line) == (str(side).lower() in ("over", "yes")) else "lost"


def grade(facts):
    """(result, actual) for one leg, or (None, None) while the game is not final."""
    from services import espn_box_scores as box
    from services.prop_markets import _name
    kind = facts.get("kind")
    if not kind:
        return "void", "not gradeable"
    if kind == "player" and not facts.get("event"):
        found = find_event(facts.get("sport"), facts.get("team"), facts.get("kickoff"))
        if not found:
            return None, None
        facts = {**facts, "path": found[0], "event": found[1]}
    espn_path = facts.get("path") or ""
    if espn_path:
        summary = _summary(espn_path, facts["event"])
    else:
        espn_path, summary = _soccer_summary(facts["event"])
        if not summary:
            return None, None
    if not summary["completed"]:
        return None, None
    teams = summary["competitors"]
    scores = [t["score"] for t in teams]
    if kind == "player":
        stat = facts.get("stat")
        if not stat:
            return "void", "stat not tracked"
        if espn_path.startswith("football/"):
            value = nfl_player_value(facts["event"], facts["player"], stat)
            if value is None:
                return "void", "did not play"
            return _over_under(value, float(facts["line"]), facts.get("side")), f"{value:g}"
        sport, league = ("nba", None) if espn_path == "basketball/nba" else ("soccer", espn_path.split("/", 1)[1])
        game = box.box_score(sport, facts["event"], league)
        if not game:
            return None, None
        row = next((r for t in game["teams"].values() for r in t["players"] if _name(r["name"]) == _name(facts["player"])), None)
        if row is None:
            return "void", "did not play"
        value = _value(row, stat)
        return _over_under(value, float(facts["line"]), facts.get("side")), f"{value:g}" if value is not None else "?"
    if None in scores or len(scores) != 2:
        return "void", "no final score"
    total = sum(scores)
    if kind == "btts":
        both = all(s > 0 for s in scores)
        return ("won" if both == (facts["side"] == "yes") else "lost"), f"{scores[0]:g}-{scores[1]:g}"
    if kind in ("total", "game_total"):
        side = facts.get("side") or str(facts.get("outcome", "")).lower()
        return _over_under(total, float(facts.get("line", facts.get("point"))), side), f"{total:g} total"
    if kind == "team_total":
        import parlay_engine as pe
        mine = next((t["score"] for t in teams if t["id"] == str(facts.get("team_id"))
                     or (facts.get("team_name") and t["name"] and pe.teams_match(t["name"], facts["team_name"]))), None)
        return _over_under(mine, float(facts["line"]), facts["side"]), f"{mine:g}" if mine is not None else "?"
    import parlay_engine as pe
    named = {t["name"]: t["score"] for t in teams}
    def score_of(name):
        return next((s for n, s in named.items() if n and pe.teams_match(n, name)), None)
    if kind == "moneyline":
        if str(facts["outcome"]).lower() == "draw":
            return ("won" if scores[0] == scores[1] else "lost"), f"{scores[0]:g}-{scores[1]:g}"
        mine = score_of(facts["outcome"])
        other = next((s for n, s in named.items() if n and not pe.teams_match(n, facts["outcome"])), None)
        if mine is None or other is None:
            return "void", "team not found"
        return ("won" if mine > other else "push" if mine == other and not espn_path.startswith("soccer") else "lost"), f"{mine:g}-{other:g}"
    if kind == "spread":
        mine = score_of(facts["outcome"])
        other = next((s for n, s in named.items() if n and not pe.teams_match(n, facts["outcome"])), None)
        if mine is None or other is None:
            return "void", "team not found"
        margin = mine + float(facts.get("point") or 0) - other
        return ("push" if margin == 0 else "won" if margin > 0 else "lost"), f"{mine:g}-{other:g}"
    return "void", "not gradeable"


def parlay_result(results):
    if "lost" in results:
        return "lost"
    if "pending" in results:
        return "pending"
    if all(r in ("void", "push") for r in results):
        return "void"
    return "won"


def settle(*, now=None, location=None, grader=grade):
    """Grade every pending leg whose game should be over; returns how many legs were graded."""
    now = now or datetime.now(UTC)
    from services.prediction_lifecycle import parse_kickoff
    graded = 0
    with db(location) as connection:
        rows = connection.execute("SELECT l.*, p.day FROM legs l JOIN parlays p ON p.key=l.parlay WHERE l.result='pending'").fetchall()
    for row in rows:
        kickoff = parse_kickoff(row["kickoff"]) or datetime.fromisoformat(row["day"]).replace(tzinfo=ET) + timedelta(hours=23)
        if now < kickoff + timedelta(hours=2, minutes=30):
            continue
        facts = {**json.loads(row["grade"] or "{}"), "kickoff": row["kickoff"]}
        try:
            result, actual = grader(facts)
        except Exception:
            result, actual = None, None
        if result is None and now > kickoff + timedelta(days=GIVE_UP_DAYS):
            result, actual = "void", "no final result"
        if result is None:
            continue
        graded += 1
        with db(location) as connection:
            connection.execute("UPDATE legs SET result=?, actual=?, settled_at=? WHERE parlay=? AND n=?",
                               (result, actual, now.isoformat(), row["parlay"], row["n"]))
    with db(location) as connection:
        for (key,) in connection.execute("SELECT key FROM parlays WHERE result='pending'").fetchall():
            results = [r[0] for r in connection.execute("SELECT result FROM legs WHERE parlay=?", (key,))]
            connection.execute("UPDATE parlays SET result=? WHERE key=?", (parlay_result(results), key))
    return graded


# ── closing prices ───────────────────────────────────────────────────────────

def _market_for(sport, stat):
    from services import tiered_parlays as tp
    if sport == "nba":
        return next((m for m, s in tp.MARKET_STATS.items() if s == stat), None)
    if sport == "soccer":
        return next((m for m, s in tp.SOCCER_STATS.items() if s[0] == stat), None)
    return next((m for m, s in tp.NFL_STATS.items() if "+".join(s[0]) == stat), None)


def _scoreboard_markets(espn_path, event, kickoff):
    """DraftKings game markets (moneyline, spreads, totals) for one ESPN event from that day's scoreboard."""
    import requests
    import parlay_engine as pe
    from services.prediction_lifecycle import parse_kickoff
    when = parse_kickoff(kickoff)
    if not when:
        return []
    raw = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{espn_path}/scoreboard",
                       params={"dates": when.astimezone(ET).strftime("%Y%m%d")}, timeout=15).json()
    found = next((e for e in raw.get("events") or [] if str(e.get("id")) == str(event)), None)
    return (((pe.espn_event(found) or {}).get("bookmakers") or [{}])[0].get("markets") or []) if found else []


def closing_price(facts, kickoff=None):
    """DraftKings' current price for a recorded leg, read free from ESPN; None when it is no longer listed."""
    import parlay_engine as pe
    from services import espn_props
    from services.prop_markets import _name
    espn_path, event, kind = facts.get("path") or "", facts.get("event"), facts.get("kind")
    if not espn_path or not event:
        return None
    league = tuple(espn_path.split("/", 1))
    sport = {"basketball": "nba", "football": "nfl", "soccer": "soccer"}.get(league[0])
    side, line = facts.get("side"), facts.get("line")
    if kind in ("moneyline", "spread", "game_total", "total"):
        want = {"moneyline": "h2h", "spread": "spreads", "game_total": "totals", "total": "totals"}[kind]
        for market in _scoreboard_markets(espn_path, event, kickoff):
            if market["key"] != want:
                continue
            for outcome in market["outcomes"]:
                if kind in ("game_total", "total"):
                    target = str(side or facts.get("outcome") or "").lower()
                    if outcome["name"].lower() == target and float(outcome.get("point") or 0) == float(line if line is not None else facts.get("point")):
                        return outcome["price"]
                elif pe.teams_match(outcome["name"], str(facts.get("outcome") or "")) and (
                        kind == "moneyline" or float(outcome.get("point") or 0) == float(facts.get("point") or 0)):
                    return outcome["price"]
        return None
    items = espn_props._board(league, str(event))["items"]
    if kind == "btts":
        prices = [espn_props._decimal(i) for i in items if (i.get("type") or {}).get("name") == "Both Teams To Score"]
        return (prices[0] if side == "yes" else prices[1]) if len(prices) == 2 else None
    if kind == "team_total":
        prices = [espn_props._decimal(i) for i in items if (i.get("type") or {}).get("name") == "Team Total Goals"
                  and str((i.get("team") or {}).get("$ref", "")).split("/teams/")[-1].split("?")[0] == str(facts.get("team_id"))
                  and espn_props._line(i) == line]
        return (prices[0] if side == "over" else prices[1]) if len(prices) == 2 else None
    if kind != "player":
        return None
    market = _market_for(sport, facts.get("stat"))
    board = espn_props._board(league, str(event))
    names = espn_props._names({(i.get("athlete") or {}).get("$ref") for i in board["items"]} - {None})
    pairs, alternates = espn_props.shape(sport, board["items"], names, board["fetched_at"], None)
    who = _name(facts.get("player"))
    for pair in pairs:
        if pair["player"] == who and pair["market"] == market and pair["line"] == line:
            return pair["over_decimal" if side == "over" else "under_decimal"]
    return next((a["price"] for a in alternates if a["player"] == who and a["market"] == f"{market}_alternate"
                 and a["line"] == line and side == "over"), None)


def capture_closing(*, now=None, location=None, pricer=closing_price):
    """Every 10 minutes: each pending leg kicking off within CLOSE_BEFORE gets its current price as the closing
    price (the last reading before kickoff wins). The price it was sent at is never touched."""
    from services.prediction_lifecycle import parse_kickoff
    now = now or datetime.now(UTC)
    with db(location) as connection:
        rows = connection.execute("SELECT parlay, n, kickoff, grade FROM legs WHERE result='pending'").fetchall()
    seen, captured = {}, 0
    for row in rows:
        kickoff = parse_kickoff(row["kickoff"])
        if not kickoff or not (kickoff - CLOSE_BEFORE <= now < kickoff):
            continue
        facts = row["grade"] or "{}"
        if facts not in seen:
            try:
                seen[facts] = pricer(json.loads(facts), row["kickoff"])
            except Exception:
                seen[facts] = None
        if seen[facts]:
            with db(location) as connection:
                connection.execute("UPDATE legs SET close_price=?, close_at=? WHERE parlay=? AND n=?",
                                   (seen[facts], now.isoformat(), row["parlay"], row["n"]))
            captured += 1
    return captured


# ── scorecard ────────────────────────────────────────────────────────────────

BANDS = ((0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01))


def _market_label(grade):
    kind = grade.get("kind")
    if kind == "player":
        return str(grade.get("stat") or "player").replace("_", " ").replace("+", " + ")
    return {"btts": "both teams to score", "total": "match goals", "team_total": "team goals", "game_total": "game total",
            "moneyline": "moneyline", "spread": "spread"}.get(kind, kind or "other")


def scorecard(days=7, *, today=None, location=None):
    """Settled legs over the window, each counted once: accuracy (Brier score, log loss), hit rate per probability
    band, closing-line value, and the return of $1 on each leg at the price recorded, by sport, market, tag and story."""
    import math
    today = today or datetime.now(ET).date()
    first, last = today - timedelta(days=days), today - timedelta(days=1)
    with db(location) as connection:
        rows = connection.execute(
            "SELECT l.*, p.tag AS slip_tag FROM legs l JOIN parlays p ON p.key=l.parlay WHERE p.day BETWEEN ? AND ? "
            "AND l.result IN ('won','lost')", (str(first), str(last))).fetchall()
    legs = {}
    for row in rows:
        legs.setdefault((row["selection"], row["kickoff"]), row)
    groups = {}

    def add(name, row, y, p):
        g = groups.setdefault(name, {"n": 0, "won": 0, "brier": 0.0, "log": 0.0, "chance": 0.0, "profit": 0.0, "priced": 0,
                                     "closed": 0, "beat": 0, "clv": 0.0})
        g["n"] += 1
        g["won"] += y
        if p is not None:
            p = min(max(p, 0.01), 0.99)
            g["chance"] += p
            g["brier"] += (p - y) ** 2
            g["log"] -= math.log(p if y else 1 - p)
        if row["price"]:
            g["priced"] += 1
            g["profit"] += (row["price"] - 1) if y else -1
            if row["close_price"]:
                g["closed"] += 1
                g["beat"] += row["price"] > row["close_price"]
                g["clv"] += row["price"] / row["close_price"] - 1

    for row in legs.values():
        y, p = int(row["result"] == "won"), row["chance"]
        grade = json.loads(row["grade"] or "{}")
        names = ["All", f'Sport: {(row["sport"] or "?").upper()}', f"Market: {_market_label(grade)}",
                 "Matchup story" if (row["story"] or 0) > 0 else "No story",
                 f'Slip tag: {row["slip_tag"]}' if row["slip_tag"] else "Slip tag: before tags"]
        if p is not None:
            low, high = next(b for b in BANDS if b[0] <= p < b[1])
            names.append(f"We said {low:.0%}–{min(high, 1):.0%}")
        for name in names:
            add(name, row, y, p)
    for g in groups.values():
        n = max(g["n"], 1)
        g.update(hit_rate=g["won"] / n, avg_chance=g["chance"] / n, brier=g["brier"] / n, log_loss=g["log"] / n,
                 roi=g["profit"] / g["priced"] if g["priced"] else None,
                 beat_close=g["beat"] / g["closed"] if g["closed"] else None, avg_clv=g["clv"] / g["closed"] if g["closed"] else None)
    return {"days": days, "legs": len(legs), "groups": groups}


def describe_scorecard(days=7, *, today=None, location=None):
    card = scorecard(days, today=today, location=location)
    if not card["legs"]:
        return "Scorecard: no settled legs in this window yet."
    lines = [f'Scorecard, last {days} days ({card["legs"]} settled legs; small samples swing a lot):']
    for name, g in card["groups"].items():
        parts = [f'{g["won"]}/{g["n"]} hit ({g["hit_rate"]:.0%}) vs {g["avg_chance"]:.0%} we said',
                 f'Brier {g["brier"]:.3f}']
        if g["roi"] is not None:
            parts.append(f'{g["roi"]:+.0%} return at $1 a leg')
        if g["beat_close"] is not None:
            parts.append(f'beat the closing price {g["beat_close"]:.0%} of {g["closed"]}')
        lines.append(f'· {name}: ' + ", ".join(parts))
    return "\n".join(lines)


# ── reading ──────────────────────────────────────────────────────────────────

def parlays(first, last, *, location=None):
    with db(location) as connection:
        rows = [dict(r) for r in connection.execute("SELECT * FROM parlays WHERE day BETWEEN ? AND ? ORDER BY day, sport, kind, tier",
                                                    (str(first), str(last)))]
        for row in rows:
            row["legs"] = [dict(l) for l in connection.execute("SELECT selection, result, actual, price FROM legs WHERE parlay=? ORDER BY n",
                                                               (row["key"],))]
    return rows


def summarize(rows):
    """Record and $1-a-play profit per group, settled plays only."""
    groups = {}
    for row in rows:
        name = "Single picks" if row["kind"] == "pick" else ("Game-line parlays" if row["kind"] == "game_parlay"
                                                              else f'{row["sport"].upper()} {row["tier"].capitalize()} parlays')
        group = groups.setdefault(name, {"won": 0, "lost": 0, "pending": 0, "void": 0, "profit": 0.0})
        group[row["result"]] = group.get(row["result"], 0) + 1
        if row["result"] == "won" and row["decimal_odds"]:
            group["profit"] += row["decimal_odds"] - 1
        elif row["result"] == "lost":
            group["profit"] -= 1
    legs = [leg["result"] for row in rows for leg in row["legs"]]
    return {"groups": groups, "legs_won": legs.count("won"), "legs_lost": legs.count("lost"),
            "parlays_won": sum(1 for r in rows if r["result"] == "won"), "parlays_lost": sum(1 for r in rows if r["result"] == "lost")}


def report(days=1, *, today=None, location=None):
    """{'yesterday': [...parlays], 'window': summary over the last `days` days} for emails and Ano."""
    today = today or datetime.now(ET).date()
    yesterday = today - timedelta(days=1)
    return {"day": str(yesterday), "yesterday": parlays(yesterday, yesterday, location=location),
            "days": days, "window": summarize(parlays(today - timedelta(days=days), yesterday, location=location))}


def describe(days=1, *, today=None, location=None):
    """Plain text for Ano's chat: yesterday's plays leg by leg, then the record for the window."""
    data = report(days, today=today, location=location)
    lines = []
    if days == 1:
        if not data["yesterday"]:
            return f'No emailed picks were recorded for {data["day"]}. Tracking starts with the next email.'
        for row in data["yesterday"]:
            tier = TIER_WORDS.get(row["tier"], row["tier"])
            label = "Single pick" if row["kind"] == "pick" else f'{row["sport"].upper()} {tier or ""} parlay'.replace("  ", " ")
            odds = f' (paid {row["decimal_odds"]:.2f}x)' if row["decimal_odds"] and row["kind"] != "pick" else ""
            lines.append(f'{MARK.get(row["result"], "")} {label}{odds}: {row["result"]}')
            lines += [f'   {MARK.get(l["result"], "")} {l["selection"]}' + (f' ({l["actual"]})' if l["actual"] else "") for l in row["legs"]]
        sweep = data["yesterday"] and all(r["result"] == "won" for r in data["yesterday"])
        if sweep:
            lines.insert(0, "Clean sweep yesterday! Every play hit.")
    window = data["window"]
    picks = window["legs_won"] + window["legs_lost"]
    lines.append("")
    lines.append(f'{"Yesterday" if days == 1 else f"Over the last {days} days"}: {window["legs_won"]} of {picks} '
                 f'individual picks hit, and {window["parlays_won"]} of {window["parlays_won"] + window["parlays_lost"]} bets won.')
    for name, group in window["groups"].items():
        money = f'up ${group["profit"]:.2f}' if group["profit"] >= 0 else f'down ${-group["profit"]:.2f}'
        lines.append(f'· {name}: won {group["won"]}, lost {group["lost"]}' + (f', {group["pending"]} still to play' if group["pending"] else "")
                     + f'. {money} if you’d put $1 on each.')
    if days > 1:
        lines += ["", describe_scorecard(days, today=today, location=location)]
    return "\n".join(lines).strip()


def render_recap(*, today=None, location=None):
    """Morning-email block: yesterday graded, then the last 7 days' record. '' when nothing was tracked."""
    data, week = report(1, today=today, location=location), report(7, today=today, location=location)["window"]
    if not data["yesterday"] and not week["groups"]:
        return ""
    e = lambda value: escape(str(value))
    color = {"won": "#0c6b4f", "lost": "#b3261e"}
    rows = []
    for row in data["yesterday"]:
        label = "Pick" if row["kind"] == "pick" else f'{row["sport"].upper()} {e(TIER_WORDS.get(row["tier"], row["tier"]) or "parlay")}'
        odds = f' · {row["decimal_odds"]:.2f}×' if row["decimal_odds"] and row["kind"] != "pick" else ""
        legs = "".join(f'<br><span style="font-size:12px;color:#4d5b60">{MARK.get(l["result"], "")} {e(l["selection"])}'
                       + (f' — {e(l["actual"])}' if l["actual"] else "") + "</span>" for l in row["legs"])
        rows.append(f'<p style="margin:6px 0"><b style="color:{color.get(row["result"], "#6b7b80")}">{MARK.get(row["result"], "")} '
                    f'{label}{odds}: {e(row["result"].upper())}</b>{legs}</p>')
    sweep = data["yesterday"] and all(r["result"] == "won" for r in data["yesterday"])
    head = '<p style="margin:4px 0;padding:6px 10px;background:#e8f6ef;border-radius:6px"><b>Clean sweep: every play hit.</b></p>' if sweep else ""
    record = "".join(f'<li>{e(name)}: {g["won"]}-{g["lost"]}' + (f' ({g["pending"]} pending)' if g["pending"] else "")
                     + f', {g["profit"]:+.2f} units at $1 a play</li>' for name, g in week["groups"].items())
    return (f'<h3 style="margin:0 0 4px">Yesterday\'s results</h3>{head}{"".join(rows) or "<p>No emailed picks yesterday.</p>"}'
            f'<p style="margin:8px 0 2px"><b>Last 7 days</b>: legs {week["legs_won"]}-{week["legs_lost"]}</p><ul style="margin:2px 0 14px">{record}</ul>')
