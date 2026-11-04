"""
parlay_routes.py — Flask wiring for Parlay Mode.

* /parlays               page (tabs: NBA · NFL · Soccer)
* /api/parlays/<sport>   JSON for the latest slate
* register_parlay_jobs() scheduled builds (US Eastern):
      NBA     daily       11:05
      NFL     Sundays     10:35
      Soccer  daily       08:35  → Fridays build the weekend slate, other days
                                   only spend credits if UCL/UEL games are on.

Slates are also built lazily the first time someone opens the page on a slate
day, which covers hosts (like Render's free plan) that sleep and miss jobs.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from flask import Blueprint, abort, jsonify, render_template, request

import parlay_engine as pe

_log = logging.getLogger("parlay_mode")

parlay_bp = Blueprint("parlays", __name__)

SPORT_ORDER = ["nba", "nfl", "soccer"]


# ── ScorPred model providers ────────────────────────────────────────────────
# Each returns {odds_event_id: {"home": p, "away": p, "draw"?: p}} for the games
# ScorPred's own model covers. Anything it can't match falls back to the market.

def _nba_model_rows() -> list[dict[str, Any]]:
    import nba_routes

    nc = nba_routes.nc
    try:
        team_map = {str(t["id"]): t for t in nc.get_teams()}
    except Exception:
        team_map = {}
    games = nc.get_today_games() or nc.get_upcoming_games(next_n=15, days_ahead=1) or []
    rows = []
    for game in games:
        try:
            card = nba_routes._build_today_prediction_card(game, team_map, {})
        except Exception as exc:
            _log.debug("parlay nba model: card failed: %s", exc)
            continue
        if not card:
            continue
        # The card shows 50/50 when the model has no prediction; only real model probabilities are blended.
        probs = (card.get("prediction") or {}).get("win_probabilities") or {}
        if probs.get("a") is None or probs.get("b") is None:
            continue
        rows.append({
            "home": (card.get("home_team") or {}).get("name", ""),
            "away": (card.get("away_team") or {}).get("name", ""),
            "p_home": probs["a"],
            "p_away": probs["b"],
        })
    return rows


def _soccer_model_rows() -> list[dict[str, Any]]:
    from league_config import SUPPORTED_LEAGUE_IDS
    from services import prediction_service

    rows = []
    for league_id in SUPPORTED_LEAGUE_IDS:
        try:
            cards, *_ = prediction_service.get_fixture_cards(league_id)
        except Exception as exc:
            _log.debug("parlay soccer model: league %s failed: %s", league_id, exc)
            continue
        for card in cards or []:
            probs = card.get("probabilities") or {}
            rows.append({
                "home": card.get("team_a", ""),
                "away": card.get("team_b", ""),
                "p_home": probs.get("a"),
                "p_draw": probs.get("draw"),
                "p_away": probs.get("b"),
            })
    return rows


def model_provider(sport: str, events: list[dict]) -> dict[str, dict[str, float]]:
    if sport == "nba":
        rows = _nba_model_rows()
    elif sport == "soccer":
        rows = _soccer_model_rows()
    else:
        return {}  # no NFL model in ScorPred yet — market consensus only
    return pe.match_model_rows(events, rows)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _slate_for(sport: str, *, build: bool = True) -> dict | None:
    """Today's slate (built once if due), else the most recent ready slate."""
    store = pe.SlateStore()
    today = pe.today_et()
    slate = store.load(sport, today)
    note = None  # why today's slate could not be built, shown on the page
    if slate is None and build and pe.slate_window(sport, today) is not None and pe.default_client().api_key:
        try:
            slate = pe.ensure_slate(sport, today, store=store, model_provider=model_provider)
        except pe.OddsApiError as exc:
            _log.warning("parlay build failed for %s: %s", sport, exc)
            note = str(exc)
            slate = None
    if slate is None or slate.get("status") != "ready":
        latest = store.latest(sport)
        if latest is not None:
            latest = {**latest, "is_previous": latest.get("slate_date") != today.isoformat()}
            if slate is not None:
                latest["today_status"] = slate.get("status")
            if note:
                latest["today_note"] = note
            return latest
        if slate is None and note:
            return {"sport": sport, "status": "unavailable", "today_note": note}
    return slate


def _kickoff_et(iso: str | None) -> str:
    """'2026-11-03T00:30:00Z' → 'Mon 7:30 PM ET' (portable — no %-I, which Windows lacks)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(pe.ET)
    except ValueError:
        return ""
    return f"{dt:%a} {dt.hour % 12 or 12}:{dt:%M %p} ET"


# ── Routes ──────────────────────────────────────────────────────────────────

@parlay_bp.route("/parlays", methods=["GET"])
def parlays_page():
    sport = (request.args.get("sport") or "nba").lower()
    if sport not in pe.SPORTS:
        sport = "nba"
    slate = _slate_for(sport)
    if slate:
        for p in slate.get("picks", []):
            p["kickoff_et"] = _kickoff_et(p.get("commence_time"))
    return render_template(
        "parlays.html",
        sport=sport,
        sports=[{"key": k, **pe.SPORTS[k]} for k in SPORT_ORDER],
        slate=slate,
        has_key=bool(pe.default_client().api_key),
        min_odds=pe.PARLAY_MIN_ODDS,
    )


@parlay_bp.route("/api/parlays/<sport>", methods=["GET"])
def parlays_api(sport: str):
    sport = sport.lower()
    if sport not in pe.SPORTS:
        abort(404)
    slate = _slate_for(sport)
    if slate is None:
        return jsonify({"sport": sport, "status": "unavailable"}), 200
    return jsonify(slate)


@parlay_bp.route("/api/ano/email/<kind>", methods=["POST"])
def ano_email(kind: str):
    """Ano's typed report commands. Loopback only, CSRF-protected like every POST, and the recipient is always
    the configured morning-email address (the request cannot choose one)."""
    from flask import current_app
    from services import parlay_email
    if request.remote_addr not in ("127.0.0.1", "::1"):
        abort(403)
    if kind not in parlay_email.COMMANDS:
        abort(404)
    return jsonify(parlay_email.run_command(current_app._get_current_object(), kind))


@parlay_bp.route("/api/odds-credits", methods=["GET"])
def odds_credits():
    """Read-only status for Ano's wallpaper: this month's odds credits and today's parlay slates."""
    from services import prop_markets
    today = pe.today_et()
    store = pe.SlateStore()
    return jsonify({"used": prop_markets.credit_usage(), "cap": prop_markets.MONTHLY_CREDIT_CAP,
                    "parlay": prop_markets.credit_usage(share="parlay"), "parlay_cap": pe.PARLAY_MONTHLY_CREDITS,
                    "parlays_today": {sport: (store.load(sport, today) or {}).get("status")
                                      for sport in SPORT_ORDER if pe.slate_window(sport, today) is not None}})


@parlay_bp.route("/api/pick-record", methods=["GET"])
def pick_record():
    """How the emailed picks and parlays did (Ano: "how did my picks do"). Grades what is due first. Loopback only."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        abort(403)
    from services import pick_tracker
    days = max(1, min(90, request.args.get("days", 1, type=int)))
    try:
        pick_tracker.settle()
    except Exception as exc:
        _log.warning("pick settle failed: %s", exc)
    return jsonify({**pick_tracker.report(days), "text": pick_tracker.describe(days)})


def _settle_job(app):
    def run():
        with app.app_context():
            try:
                from services import pick_tracker
                _log.info("picks graded: %s", pick_tracker.settle())
            except Exception as exc:
                _log.warning("pick settle job failed: %s", exc)
    run.__name__ = "settle_picks"
    return run


# ── Scheduler ───────────────────────────────────────────────────────────────

def _job(app, sport: str):
    def run():
        with app.app_context():
            try:
                slate = pe.ensure_slate(sport, pe.today_et(), model_provider=model_provider)
                _log.info("parlay %s slate: status=%s picks=%s quota_left=%s", sport,
                          (slate or {}).get("status"), len((slate or {}).get("picks", [])),
                          (slate or {}).get("quota_remaining"))
            except Exception as exc:
                _log.warning("parlay %s job failed: %s", sport, exc)
    run.__name__ = f"parlay_{sport}"
    return run


def _closing_job(app):
    def run():
        with app.app_context():
            try:
                from services import pick_tracker
                captured = pick_tracker.capture_closing()
                if captured:
                    _log.info("closing prices captured: %s", captured)
            except Exception as exc:
                _log.warning("closing price job failed: %s", exc)
    run.__name__ = "closing_prices"
    return run


def register_parlay_jobs(sched, app) -> None:
    """Add Parlay Mode cron jobs to the existing APScheduler instance."""
    tz = "America/New_York"
    # grade last night's picks before the morning email (it settles again itself; this keeps it fast)
    sched.add_job(_settle_job(app), "cron", hour="2,5", minute=15, timezone=tz, id="settle_picks", replace_existing=True)
    # each emailed leg's price in its last 20 minutes before kickoff (closing-line value; ESPN, no odds credits)
    sched.add_job(_closing_job(app), "interval", minutes=10, id="closing_prices", replace_existing=True)
    sched.add_job(_job(app, "nba"), "cron", hour=11, minute=5, timezone=tz,
                  id="parlay_nba", replace_existing=True)
    sched.add_job(_job(app, "nfl"), "cron", hour=10, minute=35, timezone=tz,  # Thu / Sun / Mon games; other days spend nothing
                  id="parlay_nfl", replace_existing=True)
    sched.add_job(_job(app, "soccer"), "cron", hour=8, minute=35, timezone=tz,
                  id="parlay_soccer", replace_existing=True)
