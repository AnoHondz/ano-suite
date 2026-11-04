"""Explicit, asynchronous props delivery with durable duplicate protection."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from html import escape
from uuid import uuid4

from filelock import FileLock, Timeout

from services import daily_digest
from services.prediction_lifecycle import calendar_date, freshness_state, is_pregame

SPORTS = ("soccer", "nba", "nfl")
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="props-email")
_guard = threading.Lock()
_active = set()


def root():
    return daily_digest.root().parent / "props-email"


def ready():
    return bool(os.getenv("SMTP_USER", "").strip() and os.getenv("SMTP_PASS", "").strip())


def identity(owner, day, sports):
    return hashlib.sha256(json.dumps([str(owner), day, sorted(sports)]).encode()).hexdigest()


def read(job_id, owner):
    if len(job_id) != 64 or any(c not in "0123456789abcdef" for c in job_id):
        return None
    path = root() / f"{job_id}.json"
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    return value if value and value["owner"] == str(owner) else None


def _write(job):
    daily_digest._save(root() / f'{job["id"]}.json', job)


def collect(day, sports, api_get, props_for, *, clock=lambda: datetime.now(UTC), sleep=time.sleep):
    from league_config import SUPPORTED_LEAGUE_IDS
    jobs, unavailable = [], []
    for sport in sports:
        for league in SUPPORTED_LEAGUE_IDS if sport == "soccer" else [None]:
            params = {"date": day, **({"league": league} if league else {})}
            payload, code = {}, 503
            for attempt in range(2):
                try:
                    payload, code = api_get(f"/api/dashboard/{sport}", params)
                except Exception:
                    payload, code = {}, 503
                cards = [c for c in payload.get("slate", []) if calendar_date(c.get("matchDate")) == day
                         and is_pregame(c.get("matchDate"), c.get("eventStatus"), clock())]
                # Re-read after a provider refresh: its receipt can postdate the
                # first request's evaluation clock. Never rewrite source times.
                if code == 200 and not payload.get("refresh_pending") and (
                        not cards or any(c.get("pregameAvailable") for c in cards)):
                    break
                if attempt == 0:
                    sleep(2)
            if code != 200:
                unavailable.append({"sport": sport, "matchup": "Schedule", "reason": payload.get("error") or "Schedule unavailable."})
                continue
            for card in cards:
                jobs.append((sport, card))

    def fetch(item):
        sport, card = item
        payload, code = {}, 503
        for attempt in range(2):
            try:
                payload, code = props_for(sport, card, day)
            except Exception:
                payload, code = {"error": "Player data could not be loaded."}, 503
            if code == 200 or code not in {202, 422, 429, 503, 504}:
                break
            if attempt == 0:
                sleep(4)
        result = payload.get("result") or {}
        verified = result.get("roster_verification")
        if code != 200 or (verified and freshness_state(verified, clock()) != "fresh"):
            return {"sport": sport, "matchup": card.get("matchup"),
                    "reason": payload.get("error") or "Player forecasts are still refreshing; try again shortly."}, None
        picks = [p for p in result.get("picks", [])
                 if daily_digest._finite(p.get("chance")) and 0 < p["chance"] < 1
                 and daily_digest._finite(p.get("line")) and p["line"] >= 0]
        return None, {"sport": sport, "matchup": card["matchup"], "kickoff": card["matchDate"],
                      "picks": picks, "roster_verification": verified, "sportsbook": result.get("sportsbook")}

    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(fetch, jobs))
    games = []
    for problem, game in outcomes:
        if problem:
            unavailable.append(problem)
        elif game and is_pregame(game["kickoff"], now=clock()):
            verified = game.get("roster_verification")
            if verified and freshness_state(verified, clock()) != "fresh":
                unavailable.append({"sport": game["sport"], "matchup": game["matchup"], "reason": "Roster verification expired during collection."})
            else:
                games.append(game)
    return {"date": day, "sports": list(sports), "generated_at": clock().isoformat(), "games": games,
            "unavailable": unavailable, "pick_count": sum(len(g["picks"]) for g in games)}


def render(report):
    sections = []
    for sport in report["sports"]:
        parts = []
        for game in report["games"]:
            if game["sport"] != sport:
                continue
            when = datetime.fromisoformat(game["kickoff"].replace("Z", "+00:00")).astimezone(daily_digest.ZONE).strftime("%I:%M %p ET")
            rows = []
            for pick in game["picks"]:
                source = {"historical": "Historical player median", "model_target": "Experimental model target",
                          "sportsbook": "Sportsbook comparison — recheck current price"}.get(pick.get("line_source"), "Model target")
                rows.append(f'<tr><td>{escape(str(pick.get("name", "")))} ({escape(str(pick.get("team", "")))})</td>'
                            f'<td>{escape(str(pick.get("side", "")))} {pick["line"]:g} {escape(str(pick.get("label", "")))}</td>'
                            f'<td>{pick["chance"] * 100:.1f}%</td><td>{source}</td></tr>')
            parts.append(f'<h3>{escape(game["matchup"])} — {when}</h3><table border="1" cellpadding="6">'
                         '<tr><th>Player</th><th>Selection</th><th>Model estimate</th><th>Line source</th></tr>'
                         + ''.join(rows) + '</table>')
            if (game.get("sportsbook") or {}).get("reason"):
                parts.append('<p>' + escape(game["sportsbook"]["reason"]) + '</p>')
        sections.append(f'<h2>{sport.upper()}</h2>' + (''.join(parts) or '<p>No available pregame props.</p>'))
    problems = ''.join(f'<li>{escape(p["sport"].upper())}: {escape(p["matchup"])} — {escape(p["reason"])}</li>' for p in report["unavailable"])
    return (f'<h1>ScorPred player props — {report["date"]}</h1><p>All times Eastern. {report["pick_count"]} available selections.</p>'
           '<p>Experimental model estimates, not guaranteed outcomes. Historical and model targets are not confirmed sportsbook offers. '
           'Recheck current starters, injuries, playing time, line and price. No betting edge is established.</p>'
           + ''.join(sections) + '<h2>Unavailable games</h2><ul>' + problems + '</ul>')


def execute(job, collector, *, sender=None):
    with FileLock(str(root() / f'{job["id"]}.lock'), timeout=0):
        current = read(job["id"], job["owner"])
        if current and current.get("attempt") != job.get("attempt"):
            return
        _execute(job, collector, sender=sender)


def _execute(job, collector, *, sender=None):
    sender = sender or daily_digest._send
    try:
        report = collector()
        if not report["pick_count"]:
            job.update(state="no_props", message="No props available yet. You can retry after sources refresh.")
            _write(job)
            return
        job["pick_count"] = report["pick_count"]
        job["unavailable_count"] = len(report["unavailable"])
        html = render(report)
        (root() / f'{job["id"]}.html').write_text(html, encoding="utf-8")

        def before_submit():
            checked = datetime.now(UTC)
            if any(not is_pregame(g["kickoff"], now=checked) or
                   (g.get("roster_verification") and freshness_state(g["roster_verification"], checked) != "fresh")
                   for g in report["games"]):
                raise ValueError("Report expired before submission")
            job.update(state="submitting", message="Submitting email.")
            _write(job)

        message_id = sender(f'ScorPred {" / ".join(s.upper() for s in job["sports"])} props — {job["date"]}',
                            html, job["recipient"], before_submit)
        job.update(state="sent", message="Email accepted by your mail server.", message_id=message_id)
    except Exception as exc:
        ambiguous = job["state"] == "submitting"
        job.update(state="delivery_unknown" if ambiguous else "failed", error_type=type(exc).__name__,
                   message="Delivery is uncertain. Check Sent mail before sending again." if ambiguous
                   else "Email was not submitted. Check email configuration or retry after data refreshes.")
    finally:
        job["updated_at"] = datetime.now(UTC).isoformat()
        _write(job)
        with _guard:
            _active.discard(job["id"])


def start(owner, recipient, day, sports, collector, *, submit=None):
    date.fromisoformat(day)
    if not sports or any(s not in SPORTS for s in sports):
        raise ValueError("Choose soccer, nba or nfl.")
    sports = sorted(set(sports))
    job_id = identity(owner, day, sports)
    root().mkdir(parents=True, exist_ok=True)
    with FileLock(str(root() / "queue.lock"), timeout=2):
        previous = read(job_id, owner)
        if previous and previous["state"] in {"sent", "submitting", "delivery_unknown"}:
            return previous
        if previous and previous["state"] == "collecting":
            # A second web worker must share the first request, not enqueue a
            # duplicate. Interrupted collection may be retried after 15 minutes.
            age = (datetime.now(UTC) - datetime.fromisoformat(previous["created_at"])).total_seconds()
            if age < 900:
                return previous
            try:
                with FileLock(str(root() / f"{job_id}.lock"), timeout=0):
                    pass
            except Timeout:
                return previous
        with _guard:
            if job_id in _active:
                return previous
            if not ready():
                return {"state": "blocked", "message": "Email setup required: configure SMTP_PASS in the local .env file and restart ScorPred."}
            # Bound queued expensive collections and accidental repeated requests.
            if len(_active) >= 2:
                return {"state": "busy", "message": "Two email requests are already running. Try again when one finishes."}
            _active.add(job_id)
        job = {"id": job_id, "owner": str(owner), "recipient": recipient, "date": day, "sports": sports,
               "state": "collecting", "message": "Collecting props; this can take several minutes.",
               "created_at": datetime.now(UTC).isoformat(), "attempt": uuid4().hex}
        _write(job)
        try:
            (submit or _pool.submit)(execute, job, collector)
        except Exception:
            with _guard:
                _active.discard(job_id)
            job.update(state="failed", message="Email collection could not start. Please retry.")
            _write(job)
        return job
