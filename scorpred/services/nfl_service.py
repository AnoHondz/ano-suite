"""NFL recent-scoring scenarios, deliberately separate from trained soccer models.

These descriptive estimates have no holdout validation. They do not ingest odds,
injuries, weather or opponent adjustments, and cannot produce a BET decision.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from statistics import mean

import decision_ui
import nfl_client
from services import decision_store, nfl_odds, nflverse_provider
from services.data_freshness import SOURCE_KEY, worst
from services.decision_engine import DecisionEngine
from services.prediction_lifecycle import (
    calendar_date,
    freshness_state,
    is_pregame,
    parse_kickoff,
    sanitize_decision,
    unavailable_reason,
)


def load_slate(selected: date | None = None, now: datetime | None = None):
    live_clock = now is None
    now = now or datetime.now(UTC)
    anchor = selected or now.astimezone(nfl_client.EASTERN).date()
    end = anchor + timedelta(days=14)
    # January/February games belong to the preceding NFL season. Fetch that
    # calendar year too for regular-season form; never mix preseason records.
    years = {anchor.year, end.year}
    if anchor.month <= 2:
        years.add(anchor.year - 1)
    provider = {"source": "espn", "warning": None, "coverage": []}
    records = None
    if os.getenv("NFL_DATA_SOURCE", "nflverse").lower() == "nflverse":
        try:
            records, provider = nflverse_provider.load_records(anchor, end, now)
        except nflverse_provider.NFLVerseUnavailable:
            provider["warning"] = "nflverse is unavailable or not synchronized; using the ESPN schedule fallback."
    if records is None:
        with ThreadPoolExecutor(max_workers=3) as pool:
            batches = list(pool.map(nfl_client.calendar_events, sorted(years)))
        events = {event["id"]: event for batch in batches for event in batch}
        records = list(events.values())
    # A real download's receipt is later than the request-start clock. Evaluate
    # live data after I/O; preserve explicit as-of clocks for offline callers.
    if live_clock:
        now = datetime.now(UTC)
    records = [{**record, "date": calendar_date(record["kickoff"])} for record in records]
    records.sort(key=lambda event: event["kickoff"])
    games = [game for game in records if anchor <= date.fromisoformat(game["date"]) <= end
             and game["season_type"] in {2, 3} and game["standard"]]
    upcoming = [game for game in games if is_upcoming(game, now)]
    available = sorted({game["date"] for game in upcoming})
    selected_date = selected.isoformat() if selected else (available[0] if available else anchor.isoformat())
    visible = [game for game in games if game["date"] == selected_date]
    cards = [build_card(game, records, now) for game in visible]
    snapshot = nfl_odds.fetch_snapshot(now)
    for card, game in zip(cards, visible, strict=True):
        if not unavailable_reason(game["kickoff"], game.get("status_name"), card.get("data_freshness"), now):
            card["market"] = nfl_odds.match_quote(snapshot, game, now)
    source_freshness = worst([record.get("schedule_freshness") or record.get("data_freshness") or record.get(SOURCE_KEY)
                              for record in records], now=now) or provider.get("data_freshness", {})
    refresh_warning = source_freshness.get("warning")
    warnings = list(dict.fromkeys(filter(None, [provider["warning"], refresh_warning])))
    datasets = provider.get("datasets") or {}
    return {
        "slate": decision_store.publish(cards, "nfl"),
        "topOpportunities": decision_store.publish(decision_ui.top_opportunities(cards), "nfl"),
        "plan": decision_ui.plan_summary(cards), "availableDates": available,
        "selectedDate": selected_date, "error": None,
        "calendarTimezone": "America/New_York",
        "last_updated": source_freshness.get("observed_at"), "dataFreshness": source_freshness,
        "dataSources": {"form": "available" if not source_freshness or freshness_state(source_freshness, now) == "fresh" else "needs_refresh",
                        "provider": provider["source"], "warning": " ".join(warnings) or None,
                        "teamStats": ("available" if freshness_state(datasets.get("team_stats"), now) == "fresh" else "needs_refresh")
                                     if "team_stats" in provider["coverage"] else "not_synced",
                        "depthCharts": ("available" if freshness_state(datasets.get("depth_charts"), now) == "fresh" else "needs_refresh")
                                       if "depth_charts" in provider["coverage"] else "not_synced",
                        "odds": snapshot["status"], "injuries": "not_connected", "weather": "not_connected",
                        "researchDatasets": [
                            {"name": name, "status": ("available" if freshness_state(info, now) == "fresh" else "needs_refresh")
                             if info.get("rows") else "not_synced",
                             "rows": info.get("rows", 0), "season": info.get("season"),
                             "latestWeek": info.get("latest_week"), "observedAt": info.get("observed_at"),
                             "expiresAt": info.get("expires_at")}
                            for name, info in datasets.items()
                        ]},
        "source": provider["source"], "warnings": warnings,
        "notice": "Experimental recent-scoring scenarios. No validated NFL accuracy; injuries, weather and odds are not modeled. Dates use US Eastern time.",
    }, records


def is_upcoming(game, now):
    return (game.get("state") == "pre" and not game.get("completed")
            and game.get("status_name") == "STATUS_SCHEDULED"
            and is_pregame(game.get("kickoff"), game.get("status_name"), now))


def _recent_games(team_id, target, records, now):
    kickoff = parse_kickoff(target.get("kickoff"))
    if kickoff is None:
        return []
    cutoff = min(kickoff, now) - timedelta(hours=24)
    games = [game for game in records if game["completed"] and game["status_name"] == "STATUS_FINAL" and game["standard"]
             and game["season"] == target["season"] and game["season_type"] in {2, 3}
             and (played := parse_kickoff(game.get("kickoff"))) is not None and played < cutoff
             and team_id in {game["home_id"], game["away_id"]}
             and all((score := nflverse_provider.number(game.get(f"{side}_score"))) is not None and score >= 0
                     for side in ("home", "away"))]
    games.sort(key=lambda game: game["kickoff"], reverse=True)
    return games[:8]


def prediction_freshness(game, records, now):
    sources = [game.get("schedule_freshness") or game.get("data_freshness") or game.get(SOURCE_KEY)]
    for team_id in (game["home_id"], game["away_id"]):
        for prior in _recent_games(team_id, game, records, now):
            sources.append(prior.get("form_freshness") or prior.get("data_freshness") or prior.get(SOURCE_KEY))
    return worst(sources, now=now)


def recent_form(team_id, target, records, now):
    games = _recent_games(team_id, target, records, now)
    if any((source := game.get("form_freshness") or game.get("data_freshness") or game.get(SOURCE_KEY))
           and freshness_state(source, now) != "fresh" for game in games):
        return []
    return [(game["home_score"], game["away_score"]) if game["home_id"] == team_id
            else (game["away_score"], game["home_score"]) for game in games]


def analyze(game, records, now):
    if not is_upcoming(game, now):
        return None
    source_freshness = prediction_freshness(game, records, now)
    if source_freshness and freshness_state(source_freshness, now) != "fresh":
        return None
    home = recent_form(game["home_id"], game, records, now)
    away = recent_form(game["away_id"], game, records, now)
    if min(len(home), len(away)) < 3:
        return None
    # Pair actual scoring/conceding observations. No invented league baseline
    # or home advantage. Tied scenarios are retained rather than forced to a win.
    scenarios = [((hs + aa) / 2, (aws + ha) / 2) for hs, ha in home for aws, aa in away]
    n = len(scenarios)
    probabilities = {"home": 100 * sum(h > a for h, a in scenarios) / n,
                     "draw": 100 * sum(h == a for h, a in scenarios) / n,
                     "away": 100 * sum(h < a for h, a in scenarios) / n}
    return {"projected_score": {"home": round(mean(h for h, _ in scenarios), 1),
                                "away": round(mean(a for _, a in scenarios), 1)},
            "probabilities": probabilities, "samples": {"home": len(home), "away": len(away)},
            "model": "nflverse_recent_scoring_v1" if game.get("data_source") == "nflverse" else "nfl_recent_scoring_v1", "validated": False,
            "data_source": game.get("data_source", "espn"),
            "data_freshness": source_freshness,
            "notice": "Scenario shares are descriptive estimates, not calibrated win or final-tie probabilities. No injury, weather, odds or opponent-strength adjustment. No home-field bonus is applied."}


def build_card(game, records, now):
    analysis = analyze(game, records, now)
    card = {"match_id": game["id"], "team_a": game["home_name"], "team_b": game["away_name"],
            "team_a_id": game["home_id"], "team_b_id": game["away_id"],
            "team_a_logo": game["home_logo"], "team_b_logo": game["away_logo"],
            "matchup": f'{game["home_name"]} vs {game["away_name"]}',
            "game_date": game["kickoff"], "slate_date": game["date"],
            "event_status": {"state": game.get("state"), "name": game.get("status_name"), "completed": game.get("completed")}, "data_freshness": prediction_freshness(game, records, now),
            "model_source": "nflverse_recent_scoring_v1" if game.get("data_source") == "nflverse" else "nfl_recent_scoring_v1",
            "competition": f'NFL · Week {game["week"]} | {"Neutral site · " if game["neutral"] else ""}{game["venue"]}',
            "data_confidence": {"label": "Limited Data"}, "action": "SKIP", "confidence_pct": 0,
            "recommended_side": "Unavailable", "probabilities": {},
            "reason": "At least three completed current-season games per team are required."}
    if not is_upcoming(game, now):
        card["reason"] = f'{game["status"]}: pregame analysis is unavailable after kickoff or for rescheduled games.'
    if analysis:
        probs = analysis["probabilities"]
        decision = DecisionEngine().build_decision({"home_name": game["home_name"], "away_name": game["away_name"],
                                                    "probabilities": probs, "data_quality": 45})
        # Descriptive only (audit, 2026-11-04): pairing recent scores is not a forecast of this matchup, so it never
        # becomes a CONSIDER or BET. The side shown is the scenario plurality; a tie may correctly show Draw.
        card.update(action="SKIP", recommended_side=decision["side"],
                    confidence_pct=decision["confidence"],
                    probabilities={"a": probs["home"], "draw": probs["draw"], "b": probs["away"]},
                    reason=f'{decision["side"]} leads recent scoring scenarios. '
                           f'{analysis["samples"]["home"]} home-team and {analysis["samples"]["away"]} away-team games sampled. '
                           'Experimental estimate; no odds, injury or weather adjustment.')
    return sanitize_decision(card, now)
