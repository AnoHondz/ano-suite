"""NBA player props: five best picks per game from ESPN box scores (services.prop_model).

Markets: points, rebounds, assists, threes and points + rebounds + assists. History is
this season and last (ESPN season ids are ending years). Current roster IDs are
required before ranking; historical appearances alone cannot establish membership.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from services import injury_reports, nba_props_ml, prop_model
from services import espn_box_scores as box
from services.data_freshness import metadata, worst
from services.prediction_lifecycle import freshness_state

ROSTER_FRESH_SECONDS = 600
_log = logging.getLogger(__name__)


class RosterUnavailable(RuntimeError):
    pass


def current_roster(team_id):
    """Exact ESPN athlete IDs on the requested current team; stale fallback is unusable."""
    team_id = str(team_id)
    try:
        payload, fetched, stale = injury_reports._fetch(f"nba-props-roster-{team_id}",
            f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/roster",
            fresh_seconds=ROSTER_FRESH_SECONDS)
    except injury_reports.InjuryFeedUnavailable as exc:
        raise RosterUnavailable("Current NBA rosters could not be verified. Please refresh shortly.") from exc
    source = metadata("ESPN NBA roster", datetime.fromtimestamp(fetched, UTC),
                      ROSTER_FRESH_SECONDS, ROSTER_FRESH_SECONDS, degraded=stale)
    team = payload.get("team") if isinstance(payload, dict) else None
    if (freshness_state(source) != "fresh" or not isinstance(payload, dict) or not isinstance(team, dict)
            or str(team.get("id")) != team_id
            or not isinstance(payload.get("athletes"), list)):
        raise RosterUnavailable("Current NBA roster identity or freshness could not be verified.")
    athletes, duplicates = {}, set()
    for athlete in payload["athletes"]:
        if not isinstance(athlete, dict):
            continue
        player_id = str(athlete.get("id") or "").strip()
        if not player_id or not athlete.get("displayName"):
            continue
        if player_id in athletes:
            duplicates.add(player_id)
        athletes[player_id] = athlete
    if not athletes:
        raise RosterUnavailable("The current NBA roster has no verifiable player identities.")
    eligible = {player_id: athlete for player_id, athlete in athletes.items()
                if player_id not in duplicates and athlete.get("active") is not False and not athlete.get("injuries")
                and isinstance(athlete.get("status") or {}, dict)
                and (athlete.get("status") or {}).get("type") in {None, "active"}}
    return eligible, source

CONFIG = {
    "sport": "nba",
    "markets": {"points": (("points",), 0.10), "rebounds": (("rebounds",), 0.10), "assists": (("assists",), 0.15),
                "threes": (("threes",), 0.30), "pra": (("points", "rebounds", "assists"), 0.08),
                "pa": (("points", "assists"), 0.10)},
    "labels": {"points": "Points", "rebounds": "Rebounds", "assists": "Assists", "threes": "3-pointers made",
               "pra": "Pts + reb + ast", "pa": "Points + assists"},
    "position": lambda raw: "C" if "C" in (raw or "") else ("F" if "F" in (raw or "") else "G"),
    "prior_minutes": 120.0, "min_games": 5, "min_minutes": 20.0,
    # scripts/backtest_box_props.py nba 2026 (2025-26, 14,243 player-games per market): every
    # market beats the player's own record on Brier score, but chosen sides run 2-5 points
    # high (points 60.8% vs 56.1%). Picks said 77.2% and hit 67.8% in the first half; the
    # shrink fit there (0.66) shows 67.8% on the second half, which hit 69.2%.
    "pick_markets": {"points": 9.5, "rebounds": 3.5, "assists": 2.5, "threes": 1.5, "pra": 14.5},
    "shrink": 0.66,
}


def project(home_id, away_id, season, tipoff, excluded=frozenset(), event_id=None):
    games = {}
    for team_id in (home_id, away_id):
        for game in box.team_games("nba", team_id, (season - 1, season), before=tipoff):
            games[game["id"]] = game
    # Historical appearances describe past games, not membership in today's
    # matchup. Verify IDs before ranking so departed players cannot take slots.
    team_ids = (str(home_id), str(away_id))
    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshots = list(pool.map(current_roster, team_ids))
    rosters = {team_id: row[0] for team_id, row in zip(team_ids, snapshots)}
    ambiguous = set(rosters[team_ids[0]]) & set(rosters[team_ids[1]])
    allowed = {team_id: set(players) - ambiguous for team_id, players in rosters.items()}
    result = prop_model.game_props(CONFIG, home_id, away_id, list(games.values()), excluded, eligible_players=allowed)
    result['_research_inputs']={'source':'ESPN completed box scores and current rosters',
        'captured_at':datetime.now(UTC).isoformat(),'kickoff':str(tipoff),
        'games':list(games.values()),'eligible_rosters':rosters,'roster_sources':[s[1] for s in snapshots],
        'excluded_names':sorted(excluded),'configuration':{k:v for k,v in CONFIG.items() if not callable(v)},
        'historical_receipt_notice':'Inputs received now cannot establish availability before earlier games.'}
    from services.nba_opportunity import evidence as opportunity_evidence
    histories=prop_model.player_histories(list(games.values()))
    for player in result["players"]:
        player["name"] = rosters[player["team"]][player["id"]]["displayName"]
        for market in player['markets']:
            market['opportunity_evidence']=opportunity_evidence(histories.get(player['id'],[]),tipoff,market.get('minutes'))
    try:
        nba_props_ml.apply(result, home_id, away_id, season, tipoff, event_id)
    except Exception:  # the rule model's markets stand when the learned model cannot run
        _log.warning("Learned NBA prop model unavailable; using rule model", exc_info=True)
    if 'game_environment' not in result:
        from services.nba_history import upcoming
        try:
            result['game_environment']=upcoming(event_id) if event_id else {'status':'unavailable'}
        except Exception:
            result['game_environment']={'status':'unavailable'}
    from services.nba_opportunity import projected_lineups
    lineup=projected_lineups(result['players'],histories,tipoff,team_ids)
    result.setdefault('game_environment',{'status':'unavailable'})
    result['game_environment']['lineup_projection']=lineup
    result['game_environment']['lineup_status']=lineup['status']
    result["picks"] = prop_model.best_picks(result["players"], CONFIG["pick_markets"], CONFIG["shrink"])
    learned_shrink = nba_props_ml.pick_shrink()
    for pick in result["picks"]:
        if pick.get("model") == nba_props_ml.VERSION and learned_shrink:
            pick["chance"] = round(0.5 + learned_shrink * (pick["probability"] - 0.5), 4)
    verified = worst([row[1] for row in snapshots])
    if freshness_state(verified) != "fresh":
        raise RosterUnavailable("NBA roster verification expired. Please refresh shortly.")
    result["roster_verification"] = {**verified, "status": "verified", "team_ids": list(team_ids),
                                     "method": "Exact ESPN player ID on current team roster"}
    return result
