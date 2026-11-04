"""Opt-in daily sports email. Reads existing pregame APIs; never trains models.

One persistent delivery journal per recipient/date prevents retries after an
ambiguous SMTP submission. Secrets are read only from the existing SMTP env.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, formataddr
from html import escape
from uuid import uuid4
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout

from runtime_paths import persistent_root
from services.prediction_lifecycle import calendar_date, freshness_state, is_pregame, parse_kickoff, sanitize_decision

ZONE = ZoneInfo("America/New_York")
SPORTS = ("nba", "soccer", "nfl")
DEFAULTS = {"enabled": False, "recipient": "", "time": "13:00", "timezone": "America/New_York",
            "lookahead_hours": 72, "matches_per_sport": 5, "props_per_sport": 5}


def root():
    return persistent_root() / "notifications" / "daily-digest"


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def settings():
    path = root() / "config.json"
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    config = {**DEFAULTS, **value}
    # Hosted services configure delivery through secrets/settings, not an
    # ephemeral local config file. Keep local configuration as the fallback.
    for field, variable in (("recipient", "DAILY_DIGEST_RECIPIENT"), ("time", "DAILY_DIGEST_TIME")):
        if os.getenv(variable, "").strip():
            config[field] = os.environ[variable].strip()
    enabled = os.getenv("DAILY_DIGEST_ENABLED", "").strip().lower()
    if enabled:
        if enabled not in {"true", "false", "1", "0"}:
            raise ValueError("Invalid daily digest enabled setting.")
        config["enabled"] = enabled in {"true", "1"}
    if not isinstance(config["enabled"], bool) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", config["time"]):
        raise ValueError("Invalid daily digest schedule.")
    if config["timezone"] != DEFAULTS["timezone"]:
        raise ValueError("Daily digest uses the existing Eastern sports calendar.")
    for field, maximum in (("lookahead_hours", 168), ("matches_per_sport", 10), ("props_per_sport", 10)):
        if type(config[field]) is not int or not 1 <= config[field] <= maximum:
            raise ValueError("Invalid digest coverage limit.")
    if config["enabled"] and not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", config["recipient"]):
        raise ValueError("Configure one valid recipient.")
    return config


def configure(recipient, time="13:00", enabled=True):
    config = {**DEFAULTS, "enabled": enabled, "recipient": recipient, "time": time}
    if not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", recipient):
        raise ValueError("Configure one valid recipient.")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time):
        raise ValueError("Use a 24-hour Eastern time, for example 13:00.")
    _save(root() / "config.json", config)
    return config


def status(scheduler=None):
    try:
        config = settings()
    except (OSError, ValueError, TypeError):
        return {"enabled": False, "delivery": "blocked_invalid_configuration", "scheduler_registered": False,
                "next_run": None, "requires_running_app": True}
    ready = bool(os.getenv("SMTP_USER", "").strip() and os.getenv("SMTP_PASS", "").strip())
    job = scheduler.get_job("daily_sports_digest") if scheduler else None
    next_run = getattr(job, "next_run_time", None)
    return {"enabled": config["enabled"], "recipient": config["recipient"], "time": config["time"],
            "timezone": config["timezone"], "lookahead_hours": config["lookahead_hours"],
            "delivery": "ready" if ready else "blocked_missing_smtp_credentials",
            "scheduler_registered": job is not None if scheduler is not None else None, "next_run": next_run.isoformat() if next_run else None,
            "requires_running_app": True}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _eligible(card, now, end):
    start = parse_kickoff(card.get("matchDate"))
    clean = sanitize_decision(card, now=now)
    return bool(start and now < start <= end and clean["pregameAvailable"]
                and clean.get("side") not in {None, "Unavailable"} and clean.get("action") in {"BET", "CONSIDER", "SKIP"}
                and all(isinstance(clean.get(key), str) and clean[key].strip() for key in ("teamA", "teamB"))
                and clean.get("matchId") is not None)


def _rank(card):
    # Rank within a sport. Rule confidence and NFL scenarios are not comparable
    # with calibrated win probabilities across different sports/models.
    return ({"BET": 0, "CONSIDER": 1}.get(card.get("action"), 2),
            {"Strong Data": 0, "Partial Data": 1, "Limited Data": 2}.get(card.get("data"), 3),
            -float(card["confidence"]) if _finite(card.get("confidence")) else 0, card.get("matchDate") or "", str(card.get("matchId")))


def _forecast(sport, card, payload, now):
    result = payload.get("result") or {}
    points = result.get("projected_score") or {}
    if not all(_finite(points.get(key)) and points[key] >= 0 for key in ("home", "away")):
        return None
    freshness = result.get("data_freshness")
    if freshness_state(freshness, now) != "fresh":
        return None
    row = {"sport": sport, "match_id": str(card["matchId"]), "home": card["teamA"], "away": card["teamB"],
           "kickoff": card["matchDate"], "side": card["side"], "action": card["action"], "pick_label":card.get("pickLabel"), "reason": card.get("reason", ""),
           "score": {key: points[key] for key in ("home", "away")}, "data_freshness": freshness,
           "model": result.get("model") or result.get("data_source") or "Existing scoring method",
           "score_label": "Expected mean points" if sport != "soccer" else "Expected goals", "measure": None}
    if sport == "nfl":
        value, label = card.get("scenarioShare"), "Descriptive scenario share (not win probability)"
        row["score_label"] = "Descriptive recent-scoring mean points (experimental)"
    elif _finite(card.get("winProbability")):
        value, label = card["winProbability"], "Win probability" if str(card["side"]).lower() != "draw" else "Draw probability"
    else:
        value, label = card.get("confidence"), "Rule confidence (not win probability)"
    if _finite(value) and 0 <= value <= 100:
        row["measure"] = {"value": value, "label": label}
    if sport == "soccer":
        likely = result.get("most_likely_score") or {}
        if all(_finite(likely.get(key)) for key in ("home", "away", "prob")) and 0 <= likely["prob"] <= 100:
            row["most_likely_score"] = {key: likely[key] for key in ("home", "away", "prob")}
    return row


def _score_request(sport, card):
    if sport == "nfl":
        return f'/api/nfl/matches/{card["matchId"]}/analysis', {"date": calendar_date(card["matchDate"])}
    return f"/api/scoreline/{sport}", {"team_a_id": card.get("homeId"), "team_b_id": card.get("awayId"),
            "team_a_name": card["teamA"], "team_b_name": card["teamB"], "league_id": card.get("leagueId"),
            "event_id": card["matchId"], "before": card["matchDate"]}


def _prop_market(pick, now):
    for quote in pick.get("sportsbook_quotes") or []:
        expires, updated = parse_kickoff(quote.get("expires_at")), parse_kickoff(quote.get("updated_at"))
        if (expires and expires > now and updated and updated <= now and quote.get("side") == pick.get("side")
                and _finite(quote.get("model_chance")) and 0 < quote["model_chance"] < 1
                and _finite(quote.get("line")) and quote["line"] >= 0
                and _finite(quote.get("decimal_odds")) and quote["decimal_odds"] > 1 and quote.get("bookmaker")):
            return {key: quote[key] for key in ("line", "model_chance", "decimal_odds", "bookmaker", "updated_at", "expires_at")}
    return None


# Picks are made only at real sportsbook lines. A player's own recent median (the old "historical" line) is a
# line they stay at or under at least half the time by definition, so judging props there made almost every
# pick an under and gave lines no book offers. The model's chance at the book's line is compared with the
# book's no-vig chance for BOTH sides; the side with the edge is the pick.
MIN_EDGE = 0.04     # model must beat the book's fair chance by 4 percentage points
MIN_CHANCE = 0.52
# Sportsbook lookups cost credits on the free plan, so only the top games are priced each morning.
PRICED_GAMES = {"nba": 2, "soccer": 2, "nfl": 1}  # fits the 450/month cap (user chose fewer games, 2026-11-02)


def _book_pick(pick, now):
    """Best side at a fresh sportsbook line for this player and market, or None if no side has a clear edge."""
    from services.prop_markets import _model_at_line
    best = None
    for quote in pick.get("sportsbook_quotes") or []:
        expires, updated = parse_kickoff(quote.get("expires_at")), parse_kickoff(quote.get("updated_at"))
        line, over_odds, under_odds = quote.get("line"), quote.get("over_decimal"), quote.get("under_decimal")
        if not (expires and expires > now and updated and updated <= now and quote.get("bookmaker")
                and _finite(line) and line >= 0 and _finite(over_odds) and _finite(under_odds) and over_odds > 1 and under_odds > 1):
            continue
        over = _model_at_line({**pick, "side": "over"}, line)
        if over is None:
            continue
        fair_over = (1 / over_odds) / (1 / over_odds + 1 / under_odds)
        for side, chance, fair, odds in (("over", over, fair_over, over_odds), ("under", 1 - over, 1 - fair_over, under_odds)):
            edge = chance - fair
            if edge >= MIN_EDGE and chance >= MIN_CHANCE and (best is None or edge > best["edge"]):
                best = {"side": side, "line": line, "chance": round(chance, 4), "fair": round(fair, 4), "edge": round(edge, 4),
                        "market": {"bookmaker": quote["bookmaker"], "decimal_odds": odds, "line": line,
                                   "updated_at": quote["updated_at"], "expires_at": quote["expires_at"]}}
    return best


def roster_snapshot(sport, team_id, league_id=None):
    """Existing permitted ESPN feed/cache. Reject stale fallback metadata."""
    from api_client import ESPN_SLUG_BY_LEAGUE
    from services import injury_reports
    if not team_id:
        return []
    league = ESPN_SLUG_BY_LEAGUE.get(int(league_id or 0)) if sport == "soccer" else None
    path = {"nba": "basketball/nba", "nfl": "football/nfl"}.get(sport) or (f"soccer/{league}" if league else None)
    if not path:
        return []
    payload, fetched, stale = injury_reports._fetch(f"digest-roster-{sport}-{team_id}",
        f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams/{team_id}/roster", fresh_seconds=600)
    if stale or not 0 <= datetime.now(UTC).timestamp() - fetched < 600:
        return []
    athletes = payload.get("athletes") or []
    # NFL rosters group athletes by position.
    if any(isinstance(group, dict) and "items" in group for group in athletes):
        athletes = [athlete for group in athletes for athlete in group.get("items", [])]
    return [athlete for athlete in athletes if isinstance(athlete, dict)]


def collect(api_get, props_for, *, now=None, clock=None, leagues=None, roster_loader=roster_snapshot, injury_loader=None):
    """Collect a snapshot via existing public pregame APIs and prop payloads.

    api_get(path, params) and props_for(sport, decision, slate_date) return
    (JSON, HTTP status). Injection keeps contract tests fully offline.
    """
    from league_config import SUPPORTED_LEAGUE_IDS
    from services import injury_reports
    config = settings()
    now = now or datetime.now(UTC)
    clock = clock or (lambda: datetime.now(UTC))
    end = now + timedelta(hours=config["lookahead_hours"])
    leagues = SUPPORTED_LEAGUE_IDS if leagues is None else leagues
    days = [(now.astimezone(ZONE).date() + timedelta(days=i)).isoformat()
            for i in range((end.astimezone(ZONE).date() - now.astimezone(ZONE).date()).days + 1)]
    cards = {sport: {} for sport in SPORTS}
    notes = []
    pending = []
    for day in days:
        for sport in SPORTS:
            for league in leagues if sport == "soccer" else [None]:
                params = {"date": day, **({"league": league} if league else {})}
                try:
                    payload, code = api_get(f"/api/dashboard/{sport}", params)
                except Exception as exc:
                    notes.append(f"{sport.upper()} schedule unavailable ({type(exc).__name__}).")
                    continue
                if payload.get("refresh_pending"):
                    pending.append((sport, dict(params)))
                    continue
                if code != 200 or payload.get("error"):
                    notes.append(f"{sport.upper()} schedule unavailable (HTTP {code}).")
                    continue
                for card in payload.get("slate") or []:
                    if _eligible(card, clock(), end):
                        cards[sport][str(card["matchId"])] = card
    # Give the bounded dashboard workers time to finish while other partitions
    # load, then retry each warming partition once. Never loop until a quota fills.
    for sport, params in pending:
        try:
            payload, code = api_get(f"/api/dashboard/{sport}", params)
            if code != 200 or payload.get("error") or payload.get("refresh_pending"):
                notes.append(f"{sport.upper()} analysis still refreshing or unavailable after one retry.")
                continue
            for card in payload.get("slate") or []:
                if _eligible(card, clock(), end):
                    cards[sport][str(card["matchId"])] = card
        except Exception as exc:
            notes.append(f"{sport.upper()} schedule retry unavailable ({type(exc).__name__}).")
    matches = {sport: [] for sport in SPORTS}
    selected = {sport: [] for sport in SPORTS}
    for sport in SPORTS:
        # A slow schedule provider can outlast another sport's short market TTL
        # before score collection even starts. Renew each expired partition once
        # through its original API, replacing withdrawn/changed decisions too.
        expired = {(calendar_date(card["matchDate"]), card.get("leagueId"))
                   for card in cards[sport].values()
                   if freshness_state(card.get("dataFreshness"), clock()) != "fresh"}
        for day, league in sorted(expired, key=str):
            cards[sport] = {key: card for key, card in cards[sport].items()
                            if (calendar_date(card["matchDate"]), card.get("leagueId")) != (day, league)}
            try:
                payload, code = api_get(f"/api/dashboard/{sport}",
                                       {"date": day, **({"league": league} if league else {})})
                if code != 200 or payload.get("error"):
                    notes.append(f"{sport.upper()} expired schedule could not be refreshed (HTTP {code}).")
                    continue
                for card in payload.get("slate") or []:
                    if _eligible(card, clock(), end):
                        cards[sport][str(card["matchId"])] = card
            except Exception as exc:
                notes.append(f"{sport.upper()} expired schedule could not be refreshed ({type(exc).__name__}).")
        # Bounded work, no attempts to fill the quota by bypassing abstentions.
        for card in sorted(cards[sport].values(), key=_rank)[:10]:
            path, params = _score_request(sport, card)
            try:
                payload, code = api_get(path, params)
                row = _forecast(sport, card, payload, clock()) if code == 200 else None
            except Exception as exc:
                notes.append(f"{sport.upper()} score forecast unavailable ({type(exc).__name__}).")
                continue
            if row and _eligible(card, clock(), end):
                matches[sport].append(row)
                selected[sport].append(card)
            if len(matches[sport]) >= config["matches_per_sport"]:
                break
    props = {sport: [] for sport in SPORTS}
    for sport in SPORTS:
        if not selected[sport]:
            continue
        try:
            rows, freshness = (injury_loader or injury_reports.espn)(sport) if sport in {"nba", "nfl"} else ([], {})
            if sport != "soccer" and freshness_state(freshness, clock()) != "fresh":
                raise ValueError("Fresh injury feed required")
            excluded = {injury_reports.normalize_name(row.get("name")) for row in rows}
        except Exception:
            notes.append(f"{sport.upper()} props omitted: no fresh injury report.")
            continue
        candidates, roster_cache = [], {}
        priced = selected[sport][:PRICED_GAMES.get(sport, 0)]
        if not priced:
            notes.append(f"{sport.upper()} props not priced this morning (free sportsbook-line budget).")
        for card in priced:
            try:
                payload, code = props_for(sport, card, calendar_date(card["matchDate"]))
                if code != 200 or not is_pregame(card["matchDate"], card.get("eventStatus"), now=clock()):
                    continue
                if sport == "soccer":
                    # Soccer props verify current rosters themselves (FPL availability or ESPN athlete IDs);
                    # the slate's team ids are not ESPN ids, so a second ESPN roster lookup always 404s.
                    verification = (payload.get("result") or {}).get("roster_verification") or {}
                    if verification.get("status") != "verified" or freshness_state(verification, clock()) != "fresh":
                        notes.append("SOCCER props omitted: roster not verified.")
                        continue
                    for team in (card["teamA"], card["teamB"]):
                        roster_cache[(team, card.get("leagueId"))] = None
                for side in () if sport == "soccer" else ("home", "away"):
                    team = card["teamA" if side == "home" else "teamB"]
                    key = (team, card.get("leagueId"))
                    if key not in roster_cache:
                        athletes = roster_loader(sport, card.get(side + "Id"), card.get("leagueId"))
                        roster_cache[key] = {injury_reports.normalize_name(a.get("displayName")): a for a in athletes}
                for pick in (payload.get("result") or {}).get("picks") or []:
                    name = injury_reports.normalize_name(pick.get("name"))
                    team = pick.get("team")
                    # NFL model teams use canonical abbreviations, not display names.
                    if team == card.get("homeId"):
                        team = card["teamA"]
                    elif team == card.get("awayId"):
                        team = card["teamB"]
                    if sport == "soccer":
                        if (team, card.get("leagueId")) not in roster_cache:
                            continue
                    else:
                        athlete = roster_cache.get((team, card.get("leagueId")), {}).get(name)
                        if (not athlete or name in excluded or athlete.get("injuries")
                                or (athlete.get("status") or {}).get("type") not in {None, "active"}):
                            continue
                    best = _book_pick(pick, clock())
                    if not best:
                        continue  # no fresh book line with a clear edge: no pick, never a median-line stand-in
                    candidates.append({"name": pick.get("name"), "label": pick.get("label"), "games": pick.get("games"),
                                       "team": team, "kickoff": card["matchDate"], "match_id": str(card["matchId"]),
                                       **best, "line_source": "sportsbook", "sport": sport, "research_only": True,
                                       "injury_note": "Injury status not independently verified outside supported league feeds" if sport == "soccer" else "Fresh injury check"})
            except Exception as exc:
                notes.append(f"{sport.upper()} props unavailable ({type(exc).__name__}).")
        seen = set()
        for pick in sorted(candidates, key=lambda value: (-value["edge"], -value["chance"])):
            key = injury_reports.normalize_name(pick["name"])
            if key not in seen:
                seen.add(key)
                props[sport].append(pick)
            if len(props[sport]) >= config["props_per_sport"]:
                break
    # Props can take longer than a short schedule TTL. Refresh expired selected
    # forecasts through the original APIs once, never by rewriting timestamps.
    for sport in SPORTS:
        renewed = []
        for row in matches[sport]:
            if freshness_state(row["data_freshness"], clock()) == "fresh":
                renewed.append(row)
                continue
            original = next((card for card in selected[sport] if str(card["matchId"]) == row["match_id"]), None)
            if original is None or not is_pregame(row["kickoff"], now=clock()):
                continue
            try:
                params = {"date": calendar_date(row["kickoff"])}
                if sport == "soccer":
                    params["league"] = original.get("leagueId")
                slate, code = api_get(f"/api/dashboard/{sport}", params)
                card = next((item for item in slate.get("slate", []) if str(item.get("matchId")) == row["match_id"]), None)
                if code != 200 or card is None or not _eligible(card, clock(), end):
                    continue
                path, params = _score_request(sport, card)
                payload, code = api_get(path, params)
                fresh_row = _forecast(sport, card, payload, clock()) if code == 200 else None
                if fresh_row:
                    renewed.append(fresh_row)
            except Exception:
                notes.append(f"{sport.upper()} forecast expired and could not be refreshed.")
        matches[sport] = renewed
    finished = clock()
    for sport in SPORTS:
        # Never send a selection that expired during slow upstream work.
        matches[sport] = [row for row in matches[sport] if is_pregame(row["kickoff"], now=finished)
                          and freshness_state(row["data_freshness"], finished) == "fresh"]
        valid_ids = {row["match_id"] for row in matches[sport]}
        # A pick whose book price expired during collection is dropped, never reverted to a median line.
        props[sport] = [row for row in props[sport] if row["match_id"] in valid_ids and is_pregame(row["kickoff"], now=finished)
                        and parse_kickoff(row["market"]["expires_at"]) > finished]
        if len(matches[sport]) < config["matches_per_sport"]:
            notes.append(f'{sport.upper()}: {len(matches[sport])} of {config["matches_per_sport"]} requested match/score selections available; no weak or unavailable predictions were added.')
    return {"generated_at": finished.isoformat(), "window_end": end.isoformat(), "matches": matches, "props": props,
            "notes": list(dict.fromkeys(notes)), "match_count": sum(map(len, matches.values()))}


def render(report):
    """One screen: the five best picks at real sportsbook lines, then one line of game calls per sport."""
    def when(value):
        return parse_kickoff(value).astimezone(ZONE).strftime("%a %I:%M %p").replace(" 0", " ")
    def e(value):
        return escape(str(value))
    picks = sorted((p for rows in report["props"].values() for p in rows), key=lambda p: -p["edge"])[:5]
    lines = []
    for p in picks:
        q = p["market"]
        lines.append(f'<tr><td style="padding:8px 0;border-bottom:1px solid #e6ece9"><b>{e(p["name"])}</b> <span style="color:#6b7b80">{e(p["team"])}</span><br>'
                     f'<b style="color:#0c6b4f">{e(str(p["side"]).upper())} {p["line"]:g} {e(p["label"])}</b> &nbsp;·&nbsp; {e(q["bookmaker"])} {q["decimal_odds"]:.2f}<br>'
                     f'<span style="color:#6b7b80;font-size:12px">model {p["chance"] * 100:.0f}% vs book {p["fair"] * 100:.0f}% · {e(str(p.get("sport", "")).upper())} · {when(p["kickoff"])}</span></td></tr>')
    top = (f'<table style="width:100%;border-collapse:collapse">{"".join(lines)}</table>' if lines else
           '<p>No picks today: no sportsbook line gave the model a clear edge (or lines were unavailable).</p>')
    games = []
    for sport in SPORTS:
        calls = []
        for item in report["matches"][sport][:3]:
            measure, skip = item.get("measure"), item.get("action") == "SKIP"
            percent = f' {measure["value"]:.0f}%' if measure and not skip else ''
            calls.append(e(item["side"]) + percent + (' (no bet)' if skip else ''))
        if calls:
            games.append(f'<p style="margin:4px 0"><b>{sport.upper()}</b> &nbsp;{" · ".join(calls)}</p>')
    from services.parlay_email import render_edges, render_section, render_tiers_section
    body = (f'<!doctype html><html><head><meta charset="utf-8"></head><body style="font:14px Arial;line-height:1.55;color:#172329">'
            f'{report.get("recap") or ""}'
            f'{render_edges(report.get("edges") or {})}'
            f'<h2 style="margin:0 0 6px">Top picks</h2>{top}'
            f'{render_section(report.get("parlays"))}'
            f'{render_tiers_section(report.get("player_parlays") or {})}'
            f'<h3 style="margin:18px 0 4px">Games</h3>{"".join(games) or "<p>No qualified game forecasts.</p>"}'
            f'<p style="color:#8a9599;font-size:11px;margin-top:16px">Paper research only. Picks are made at real sportsbook lines; prices as of this email and lines move. '
            f'Model percentages are estimates, not a proven edge. Full details are in ScorPred.</p></body></html>')
    from services.ano_email import brand
    return brand(body, generated_at=report["generated_at"], metrics=(('Picks', len(picks)), ('Games', report['match_count'])))


def render_detailed(report):
    """The previous long format, kept for the ScorPred page and debugging."""
    def when(value):
        return parse_kickoff(value).astimezone(ZONE).strftime("%a %b %d, %I:%M %p ET")
    def e(value):
        return escape(str(value))
    sections = []
    for sport in SPORTS:
        rows = []
        for item in report["matches"][sport]:
            measure = item.get("measure")
            extra = f'{measure["value"]:.1f}% {e(measure["label"])}' if measure else "Probability unavailable"
            score = item["score"]
            likely = item.get("most_likely_score")
            exact = f'<br>Most likely score {likely["home"]:g}–{likely["away"]:g}: {likely["prob"]:.1f}% (uncertain)' if likely else ""
            action = item.get('pick_label') or ('SKIP — No bet recommended' if item['action'] == 'SKIP' else item['action'])
            rows.append(f'<tr><td>{e(item["home"])} vs {e(item["away"])}<br>{when(item["kickoff"])}</td><td>{e(item["side"])}<br>{extra}<br>App action: {e(action)}</td><td>{score["home"]:.1f}–{score["away"]:.1f}<br>{e(item["score_label"])}{exact}</td></tr><tr><td colspan="3"><small>{e(item["reason"])} Source observed {e(item["data_freshness"].get("observed_at"))}. Method: {e(item["model"])}.</small></td></tr>')
        table = '<table><thead><tr><th>Match / Eastern time</th><th>Existing pick / measure</th><th>Home–away forecast</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>' if rows else '<p>No qualified match/score selections in this window.</p>'
        picks = []
        for p in report["props"][sport]:
            quote = p.get("market")
            source = f'{e(quote["bookmaker"])}: {quote["decimal_odds"]:.2f} decimal odds; updated {when(quote["updated_at"])}' if quote else 'Historical target; no verified sportsbook quote'
            if not quote and p.get("line_source") == "model_target":
                source = 'Model target; no verified sportsbook quote. ' + e(p.get("rationale", ""))
            picks.append(f'<li><b>{e(p["name"])}</b> ({e(p["team"])}): {e(str(p["side"]).title())} {e(p["line"]) if p["line"] is not None else ""} {e(p["label"])} — {p["chance"] * 100:.1f}% experimental model estimate at this target. {source}. {when(p["kickoff"])}. {e(p["injury_note"])}.</li>')
        sections.append(f'<h2>{sport.upper()}</h2>{table}<h3>Player props — experimental</h3><ol>{"".join(picks)}</ol>' if picks else f'<h2>{sport.upper()}</h2>{table}<p>Player props unavailable after source, roster and injury checks.</p>')
    notes = ''.join(f'<li>{e(note)}</li>' for note in report["notes"])
    legacy = f'<!doctype html><html><head><meta charset="utf-8"><style>body{{font:14px Arial;line-height:1.6;color:#172329}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;text-align:left;border:1px solid #ddd}}th{{background:#edf5f2}}</style></head><body><h1>ScorPred daily sports digest</h1><p>Generated {when(report["generated_at"])}. Upcoming games through {when(report["window_end"])}.</p><p><b>Up to five qualified match/score forecasts per sport.</b> Rankings stay separate: rule confidence, win probabilities and NFL descriptive scenarios are different measures. Scores are forecasts, not guaranteed final results. No-bet decisions remain no-bet, even when a descriptive forecast is available.</p>{"".join(sections)}<h2>Availability and limits</h2><ul>{notes}</ul><p>Prop targets are historical references unless a named, fresh sportsbook quote appears. Model percentages do not establish a betting edge. Early-season roles, minutes, lineup changes and correlated games remain uncertain. Availability is a timestamped snapshot, not a confirmed lineup. Data: existing ScorPred pregame APIs, permitted ESPN/nflverse/FPL feeds and current ESPN rosters; configured The Odds API quotes where available.</p></body></html>'
    from services.ano_email import brand
    return brand(legacy, generated_at=report["generated_at"], metrics=(
        ('Sports', len(SPORTS)), ('Match forecasts', report['match_count']),
        ('Prop estimates', sum(len(rows) for rows in report['props'].values()))))


def flask_readers(app):
    def api_get(path, params):
        with app.test_client() as client:
            response = client.get(path, query_string={k: v for k, v in params.items() if v is not None})
            return response.get_json() or {}, response.status_code
    def props_for(sport, card, day):
        with app.app_context():
            import app as application
            return application._best_props_for(sport, card, day)
    return api_get, props_for


def receipt_path(config, day):
    identity = hashlib.sha256(config["recipient"].lower().encode()).hexdigest()[:16]
    return root() / f"{day}_{identity}.json"


def _send(subject, html, recipient, before_submit):
    sender = os.environ["SMTP_USER"].strip()
    message = EmailMessage()
    from services.ano_email import mail_subject, LOGO
    message["Subject"], message["From"], message["To"] = mail_subject(subject), formataddr(("Ano", sender)), recipient
    message["Auto-Submitted"] = "auto-generated"
    message["X-Ano-Report"] = "daily-sports-v1"
    message["Date"] = format_datetime(datetime.now(UTC))
    message["Message-ID"] = make_msgid()
    message.set_content("ScorPred daily digest. View the HTML part for match forecasts and experimental player props.")
    message.add_alternative(html, subtype="html")
    if LOGO.is_file():
        message.get_payload()[-1].add_related(LOGO.read_bytes(), maintype="image", subtype="png", cid="<ano-logo>", filename="ano-logo.png")
    # TLS is mandatory; the existing alerter's plaintext opt-out is not inherited.
    with smtplib.SMTP(os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587")), timeout=20) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(sender, os.environ["SMTP_PASS"])
        before_submit()
        server.send_message(message)
    return message["Message-ID"]


def run(app, *, now=None, preview=False, collector=None, sender=_send):
    now = now or datetime.now(UTC)
    config = settings()
    if not config["enabled"] and not preview:
        return {"state": "disabled"}
    if not preview and status()["delivery"] != "ready":
        return {"state": "blocked_missing_smtp_credentials"}
    day = now.astimezone(ZONE).date().isoformat()
    path = receipt_path(config, day)
    root().mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(root() / "delivery.lock"), timeout=0):
            previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not preview and previous.get("state") in {"sent", "submitting", "delivery_unknown"}:
                return {"state": "already_sent_or_needs_review", "receipt_state": previous["state"]}
            api_get, props_for = flask_readers(app) if collector is None else (None, None)
            extras = {}
            if collector is None:
                # Parlays take minutes (the whole NBA slate), so they are built BEFORE the picks are collected:
                # the picks' sportsbook prices are fresh for 5 minutes and are re-checked just before sending.
                try:
                    from services.parlay_email import daily_parlays
                    extras["parlays"] = daily_parlays(app)
                except Exception as exc:  # parlays are an extra; never block the morning email
                    extras["parlays"] = {"slates": [], "combined": None, "notes": [f"Parlays unavailable ({type(exc).__name__})."]}
                # Ladder / Easy / Medium / Fly / 5-leg player parlays from ESPN's free DraftKings board; each sport on its own.
                from services.parlay_email import SPORT_BUILDERS, builder
                extras["player_parlays"], extras["edges"] = {}, {}
                for sport in SPORT_BUILDERS:
                    try:
                        result = builder(sport)(app)
                        if not any((result.get("tiers") or {}).values()) and not result.get("edges"):
                            continue
                        extras["player_parlays"][sport], extras["edges"][sport] = result["tiers"], result.get("edges") or []
                    except Exception:
                        pass
            if collector is None:
                from services.pick_tracker import recap_for_email
                extras["recap"] = recap_for_email()  # yesterday graded, before today's picks are priced
            report = collector() if collector else collect(api_get, props_for, now=now)
            report.update(extras)
            html = render(report)
            _save(root() / "latest-report.json", report)
            (root() / "latest-email.html").write_text(html, encoding="utf-8")
            if preview:
                return {"state": "preview", "match_count": report["match_count"], "html": str(root() / "latest-email.html")}
            receipt = {"day": day, "recipient": config["recipient"], "state": "prepared", "generated_at": report["generated_at"]}
            _save(path, receipt)
            def before_submit():
                checked = datetime.now(UTC)
                if any(not is_pregame(row["kickoff"], now=checked) or freshness_state(row["data_freshness"], checked) != "fresh"
                       for rows in report["matches"].values() for row in rows):
                    raise ValueError("Report expired before email submission; generate a fresh report.")
                if any(pick.get("market") and parse_kickoff(pick["market"]["expires_at"]) <= checked
                       for rows in report["props"].values() for pick in rows):
                    raise ValueError("Sportsbook quote expired before email submission.")
                receipt.update(state="submitting", attempted_at=datetime.now(UTC).isoformat())
                _save(path, receipt)
            try:
                message_id = sender(f"ScorPred NBA / Soccer / NFL — {day}", html, config["recipient"], before_submit)
                receipt.update(state="sent", message_id=message_id, sent_at=datetime.now(UTC).isoformat())
                _save(path, receipt)
                from services.pick_tracker import record_sent
                record_sent(day, "morning", report=report)
            except Exception as exc:
                receipt.update(state="delivery_unknown" if receipt["state"] == "submitting" else "failed_before_submission",
                               error_type=type(exc).__name__)
                # Only ScorPred's own freshness checks are explained; mail-server errors can carry private details.
                if isinstance(exc, ValueError) and str(exc).startswith(("Report expired", "Sportsbook quote expired")):
                    receipt["error"] = str(exc)
                _save(path, receipt)
            return {"state": receipt["state"], "match_count": report["match_count"]}
    except Timeout:
        return {"state": "already_running"}
