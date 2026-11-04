"""Premier League tackles props from Fantasy Premier League data (ESPN box scores have no tackles).

Markets: anytime goalscorer and anytime assist (expected goals or assists per 90, last
season blended in, scaled by this match's expected team goals) and goalkeeper saves and
tackles (over/under the player's median in matches started). Counts are Poisson. Team
expected goals come from both sides' season expected goals for and against, with home
advantage. Five picks per match, ranked like NFL props: the largest gaps between the
model and the player's own record, for likely starters with no injury flag. Only the
tackles market passed the backtest as picks (``PICK_MARKETS``).
"""
import math
from concurrent.futures import ThreadPoolExecutor
from statistics import median

from scipy.stats import poisson

from services import injury_reports

SUMMARY_URL = "https://fantasy.premierleague.com/api/element-summary/{id}/"
PRIOR_MINUTES = 900      # last season counts as at most this many minutes of evidence
RECENT_MATCHES = 5       # expected minutes: average over the latest team matches
STARTER_MINUTES = 60     # picks only for players averaging this much
MIN_PICK_GAMES = 8       # A four-game discrepancy is not enough evidence to rank a tackle prop.
HOME_EDGE = 1.12         # home side's goals multiplier (away divides by it)
FACTOR_LIMITS = (0.6, 1.6)
# scripts/backtest_fpl_props.py, 2026-27 rounds 3-7 (small sample):
#   tackles: 62.1% predicted vs 61.5% actual (545); as picks (favoured side) 75.4% raw, 74.9% hit (195).
#   The fit shrink is 0.98 in-sample; 0.9 is used because five rounds is a small sample.
#   anytime goal 12.2% vs 10.7%, assist 8.1% vs 10.7%: fair probabilities, but as picks
#   (players "due" against their xG) goals hit 21% of a shown 41% (24), so they are not picks.
#   saves 67.0% vs 50.7% (71), worse than the keeper's own record: not used.
GOAL_SCALE = 1.0
PICK_SHRINK = 0.9
PICK_MARKETS = {"tackles": 1.5}
LABELS = {"goal": "Anytime goalscorer", "assist": "Anytime assist", "saves": "Goalkeeper saves", "tackles": "Tackles"}
GOALKEEPER = 1


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def per_90(history, past, field):
    """Season rate per 90 with last season blended in as up to ``PRIOR_MINUTES`` of evidence."""
    known = [row for row in history if row.get(field) is not None]
    minutes = sum(_float(row.get("minutes")) for row in known)
    total = sum(_float(row.get(field)) for row in known)
    if past and past.get(field) is not None and _float(past.get("minutes")) > 0:
        weight = min(1.0, PRIOR_MINUTES / _float(past["minutes"]))
        minutes += weight * _float(past["minutes"])
        total += weight * _float(past.get(field))
    return 90 * total / minutes if minutes else 0.0


def expected_minutes(history):
    recent = history[-RECENT_MATCHES:]
    return sum(_float(row.get("minutes")) for row in recent) / len(recent) if recent else 0.0


def team_rates(teams, home, away):
    """Expected goals for each side from season xG for and against per match, league-normalised."""
    league = sum(t["xg"] for t in teams.values()) / max(1, sum(t["matches"] for t in teams.values()))
    rate = lambda attack, defence: (attack["xg"] / attack["matches"]) * (defence["xgc"] / defence["matches"]) / league
    return rate(teams[home], teams[away]) * HOME_EDGE, rate(teams[away], teams[home]) / HOME_EDGE


def _factor(match_rate, season_rate):
    return min(FACTOR_LIMITS[1], max(FACTOR_LIMITS[0], match_rate / season_rate)) if season_rate else 1.0


def player_markets(element_type, history, past, team_rate, team_season_rate, opponent_rate, opponent_season_rate):
    """Markets for one player from matches before this one (oldest first)."""
    minutes = expected_minutes(history)
    if minutes <= 0:
        return []
    share = minutes / 90
    started = [row for row in history if _float(row.get("minutes")) >= STARTER_MINUTES]
    markets = []
    for key, field, factor in (("goal", "expected_goals", _factor(team_rate, team_season_rate)),
                               ("assist", "expected_assists", _factor(team_rate, team_season_rate))):
        if element_type == GOALKEEPER:
            break
        rate = per_90(history, past, field) * share * factor * GOAL_SCALE
        actual = "goals_scored" if key == "goal" else "assists"
        record = (sum(_float(row.get(actual)) >= 1 for row in started) + 1) / (len(started) + 2)
        markets.append({"market": key, "label": LABELS[key], "projection": round(rate, 3), "side": "yes", "line": None,
                        "probability": round(1 - math.exp(-rate), 4), "record": round(record, 4), "games": len(started)})
    count_markets = [("saves", "saves", _factor(opponent_rate, opponent_season_rate))] if element_type == GOALKEEPER \
        else [("tackles", "tackles", 1.0)]
    for key, field, factor in count_markets:
        observed = [row for row in started if row.get(field) is not None]
        if len(observed) < 3:
            continue
        rate = per_90(history, past, field) * share * factor
        line = math.floor(median(_float(row.get(field)) for row in observed)) + 0.5
        over = float(poisson.sf(math.floor(line), rate))
        record = (sum(_float(row.get(field)) > line for row in observed) + 1) / (len(observed) + 2)
        side = "over" if over >= record else "under"
        markets.append({"market": key, "label": LABELS[key], "projection": round(rate, 2), "side": side, "line": line,
                        "probability": round(over if side == "over" else 1 - over, 4),
                        "record": round(record if side == "over" else 1 - record, 4), "games": len(observed),
                        "ladder": {"start": 0, "over": [round(float(poisson.sf(k, rate)), 4) for k in range(int(rate * 3) + 6)]}})
    return markets


def best_picks(candidates, limit=5):
    """One pick per player: the market with the largest positive edge over their record."""
    picks = []
    for player in candidates:
        options = [m for m in player["markets"] if m["market"] in PICK_MARKETS
                   and m.get("games", 0) >= MIN_PICK_GAMES
                   and (PICK_MARKETS[m["market"]] is None or m["line"] >= PICK_MARKETS[m["market"]])
                   and m["probability"] > max(m["record"], 0.5)]  # beats the record and is the likelier side
        if options:
            best = max(options, key=lambda m: m["probability"] - m["record"])
            picks.append({**best, "edge": best["probability"] - best["record"], "name": player["name"],
                          "player_id": player.get("player_id"), "full_name": player.get("full_name"),
                          "team": player["team"], "position": player["position"],
                          "chance": round(0.5 + PICK_SHRINK * (best["probability"] - 0.5), 4) if best["side"] != "yes"
                          else round(best["probability"], 4)})
    return sorted(picks, key=lambda p: -p["edge"])[:limit]


def team_season(elements, team_ids, matches):
    """Season expected goals for and against per team, from player totals and goalkeepers."""
    teams = {team: {"xg": 0.0, "xgc": 0.0, "matches": max(1, matches)} for team in team_ids}
    for player in elements:
        team = teams.get(player.get("team"))
        if team is None:
            continue
        team["xg"] += _float(player.get("expected_goals"))
        if player.get("element_type") == GOALKEEPER:
            team["xgc"] += _float(player.get("expected_goals_conceded"))
    return teams


def _summary(player_id):
    payload, _fetched, _stale = injury_reports._fetch(f"fpl-{player_id}", SUMMARY_URL.format(id=player_id),
                                                      fresh_seconds=3600)
    return payload


def project(payload, home_name, away_name, kickoff=None):
    """Five best picks for a fixture between two Premier League clubs, plus the match rates."""
    from services.injury_impact import ImpactUnavailable, fpl_team
    elements, teams = payload["elements"], payload["teams"]
    clubs = [fpl_team(teams, home_name), fpl_team(teams, away_name)]
    if None in clubs:
        raise ImpactUnavailable("One of these clubs was not found in the Fantasy Premier League feed.")
    matches = sum(1 for event in payload.get("events") or [] if event.get("finished"))
    season = team_season(elements, {t["id"] for t in teams}, matches)
    home, away = clubs[0]["id"], clubs[1]["id"]
    home_rate, away_rate = team_rates(season, home, away)
    rates = {home: (home_rate, away_rate, away), away: (away_rate, home_rate, home)}
    eligible = [p for p in elements if p.get("team") in rates and p.get("status") == "a"
                and _float(p.get("starts")) >= 2 and _float(p.get("minutes")) / max(1, matches) >= STARTER_MINUTES * 0.6]
    from services import soccer_player_model as learned
    fitted = learned.artifact().get("markets", {}).get("tackles")
    if fitted and fitted.get("enabled") is False: fitted = None
    members = [p for p in elements if p.get("team") in rates and _float(p.get("minutes")) > 0] if fitted else eligible
    with ThreadPoolExecutor(max_workers=8) as pool:
        summaries = dict(zip((p["id"] for p in members), pool.map(lambda p: _summary(p["id"]), members)))
    context = {}
    if fitted and kickoff:
        from collections import defaultdict
        try:
            fixtures, _observed, _stale = injury_reports._fetch("fpl-current-fixtures", "https://fantasy.premierleague.com/api/fixtures/", fresh_seconds=3600)
            fixtures = {r["id"]: r for r in fixtures}
            groups = defaultdict(list)
            for summary in summaries.values():
                for row in summary.get("history") or []:
                    fixture = fixtures.get(row["fixture"])
                    if not fixture or row.get("tackles") is None or not row.get("kickoff_time") or row["kickoff_time"][:10] >= kickoff.date().isoformat(): continue
                    # Match fixture identity and date, never today's membership, establish the historical team.
                    if row["kickoff_time"][:10] != str(fixture.get("kickoff_time"))[:10]: continue
                    team_id = fixture["team_h"] if row["was_home"] else fixture["team_a"]
                    groups[(row["fixture"], team_id)].append(row)
            ordered = sorted(groups, key=lambda key: fixtures[key[0]]["kickoff_time"])
            for key in ordered:
                rows = groups[key]
                if sum(_float(r.get("minutes")) for r in rows) >= 990:
                    context.setdefault(key[1], []).append(sum(_float(r["tackles"]) for r in rows))
        except (injury_reports.InjuryFeedUnavailable, KeyError, TypeError):
            context = {}  # Missing team context cannot authorize the learned tackle model.
    candidates = []
    for player in eligible:
        summary = summaries[player["id"]]
        history = sorted(summary.get("history") or [], key=lambda row: row.get("round", 0))
        past = (summary.get("history_past") or [None])[-1]
        team = player["team"]
        own, opponent_rate, opponent = rates[team]
        team_avg = season[team]["xg"] / season[team]["matches"]
        opponent_avg = season[opponent]["xg"] / season[opponent]["matches"]
        if expected_minutes(history) < STARTER_MINUTES:
            continue
        markets = player_markets(player["element_type"], history, past, own, team_avg, opponent_rate, opponent_avg)
        if fitted and kickoff and len(context.get(team, [])) >= 3 and len(context.get(opponent, [])) >= 3:
            normalized = [{**row, "date": row["kickoff_time"], "starter": bool(row.get("starts", _float(row.get("minutes")) >= 60))}
                          for row in history if row.get("kickoff_time")]
            features = learned.inputs(normalized, "tackles", {1: "G", 2: "D", 3: "M", 4: "F"}[player["element_type"]],
                                      context[opponent], context[team], team == home, kickoff.isoformat())
            if features:
                markets = [learned.reprice(m, features, fitted) if m["market"] == "tackles" else m for m in markets]
        name = next(t["name"] for t in teams if t["id"] == team)
        candidates.append({"name": player.get("web_name") or "", "team": name, "player_id": str(player["id"]),
                           "match_side": "home" if team == home else "away",
                           "full_name": f"{player.get('first_name', '')} {player.get('second_name', '')}".strip(),
                           "position": {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}.get(player["element_type"], ""),
                           "markets": markets})
    return {"picks": best_picks(candidates), "candidates": candidates,
            "expected_goals": {"home": round(home_rate, 2), "away": round(away_rate, 2)}}
