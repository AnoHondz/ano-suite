"""How listed injuries move each team's win chance, for NBA, NFL and soccer.

Each sport answers the same question: what is this team's chance of winning with its
players as reported, and how much would it rise if each listed player were fit? NBA and
NFL work in points of margin (the final margin is normal around the projection); soccer
works in expected goals (independent Poisson scores, rates implied by the forecast).

A player moves the projection by value x (chance of playing - share of the recent games
behind the base forecast that they played), so an absence the form already reflects is
not counted twice. The NBA pregame model applies this itself; NFL and soccer forecasts
do not, so it is applied here. Player values: services.nba_pregame_model (box score),
services.nfl_player_value (EPA, backtested) and services.soccer_player_value (FPL).
"""
import math
from collections import defaultdict
from datetime import UTC, datetime
from statistics import NormalDist

from scipy.optimize import least_squares
from scipy.stats import poisson

from services import nfl_player_value as nfl_value
from services import nflverse_store as store
from services import soccer_player_value as soccer_value
from services.injury_reports import normalize_name

NFL_SIGMA = 13.5
# Share of players with each status who were active, 2022 and 2024 official reports
# against weekly roster status (Questionable 0.67-0.68, Doubtful 0.00-0.03, Out 0.00).
NFL_AVAILABILITY = {"out": 0.0, "injured reserve": 0.0, "reserve": 0.0, "suspension": 0.0, "pup": 0.0,
                    "doubtful": 0.02, "questionable": 0.67}
ESPN_NFL_TEAMS = {"WSH": "WAS", "LAR": "LA"}  # ESPN abbreviation -> nflverse
MAX_GOALS = 12
_NORMAL = NormalDist()


class ImpactUnavailable(ValueError):
    """The base forecast or player data needed to price injuries is missing."""


def nfl_availability(status):
    text = str(status or "").strip().lower()
    return next((value for key, value in NFL_AVAILABILITY.items() if key in text), 1.0)


def _sorted(players):
    return sorted(players, key=lambda p: -(p["impact"] if p.get("impact") is not None else -1))


def margin_report(base_margin, sigma, players, *, priced):
    """``players`` maps "home"/"away" to rows with value (points or None), availability, played_share.

    ``priced`` means ``base_margin`` already includes availability (the NBA model).
    Returns each side's win chance now, if every listed player were fit, and per-player impact
    (win chance gained if that player were fit).
    """
    shift = lambda rows: sum(p["value"] * (p["availability"] - p["played_share"]) for p in rows if p["value"] is not None)
    margin = base_margin if priced else base_margin + shift(players["home"]) - shift(players["away"])
    teams = {}
    for side, sign in (("home", 1), ("away", -1)):
        win = lambda m: _NORMAL.cdf(sign * m / sigma)
        now = win(margin)
        listed = [dict(p) for p in players[side] if p["availability"] < 1]
        # A fit player is never worth less than the replacement who would play instead.
        gain = lambda p: max(p["value"], 0.0) * (1 - p["availability"])
        for p in listed:
            p["impact"] = None if p["value"] is None else win(margin + sign * gain(p)) - now
        healthy = margin + sign * sum(gain(p) for p in listed if p["value"] is not None)
        teams[side] = {"win_now": now, "win_if_fit": win(healthy), "players": _sorted(listed)}
    return {"margin": margin, "sigma": sigma, "teams": teams}


def poisson_outcomes(home_rate, away_rate):
    goals = range(MAX_GOALS + 1)
    home, away = poisson.pmf(goals, home_rate), poisson.pmf(goals, away_rate)
    win = sum(home[i] * away[j] for i in goals for j in goals if i > j)
    draw = sum(home[i] * away[i] for i in goals)
    return win, draw, 1 - win - draw


def implied_rates(p_home, p_away):
    """Scoring rates whose independent Poisson scores give these win probabilities."""
    def residual(log_rates):
        win, _draw, loss = poisson_outcomes(*map(math.exp, log_rates))
        return [win - p_home, loss - p_away]
    fit = least_squares(residual, x0=[math.log(1.4), math.log(1.1)], bounds=([-3, -3], [1.6, 1.6]))
    return tuple(map(math.exp, fit.x))


def goals_report(p_home, p_draw, p_away, players):
    """Soccer: ``players`` rows carry attack/defence (goals per match), availability, played_share."""
    total = p_home + p_draw + p_away
    home_rate, away_rate = implied_rates(p_home / total, p_away / total)

    def rates(changes):
        """``changes`` maps side -> {player index: chance of playing}."""
        rate = {"home": home_rate, "away": away_rate}
        for side, other in (("home", "away"), ("away", "home")):
            for index, p in enumerate(players[side]):
                played = changes.get(side, {}).get(index, p["availability"]) - p["played_share"]
                rate[side] += p["attack"] * played
                rate[other] -= p["defence"] * played
        return max(rate["home"], 0.05), max(rate["away"], 0.05)

    def outcome(side, changes):
        win, draw, loss = poisson_outcomes(*rates(changes))
        return (win if side == "home" else loss), draw

    teams = {}
    for side in ("home", "away"):
        now, draw = outcome(side, {})
        listed = []
        for index, p in enumerate(players[side]):
            if p["availability"] < 1:
                listed.append({**p, "impact": outcome(side, {side: {index: 1.0}})[0] - now})
        fit_all = {side: {i: 1.0 for i, p in enumerate(players[side]) if p["availability"] < 1}}
        teams[side] = {"win_now": now, "draw_now": draw, "win_if_fit": outcome(side, fit_all)[0], "players": _sorted(listed)}
    return {"rates": rates({}), "base_rates": (home_rate, away_rate), "teams": teams}


# -- NBA -------------------------------------------------------------------

def nba_players(projection, espn_rows):
    """Rotation players from the model's lineup adjustment plus anyone else ESPN lists."""
    by_id = {row["espn_id"]: row for row in espn_rows if row.get("espn_id")}
    by_name = {normalize_name(row["name"]): row for row in espn_rows}
    sides = {}
    for side in ("home", "away"):
        lineup = (projection.get("availability") or {}).get(side)
        if lineup is None:
            raise ImpactUnavailable("NBA rosters or player averages are unavailable for this game.")
        rows = []
        for player in lineup["players"]:
            if not player["on_roster"]:
                continue
            report = by_id.get(str(player["id"])) or by_name.get(normalize_name(player["name"])) or {}
            rows.append({"name": player["name"], "position": player.get("position") or report.get("position") or "",
                         "status": player["status"] or report.get("status") or "", "availability": player["availability"],
                         "played_share": player["played_share"], "value": player["value"],
                         "injury": report.get("injury") or "", "updated": report.get("updated"),
                         "sources": ["ESPN"] if player["status"] else []})
        sides[side] = rows
    return sides


# -- NFL -------------------------------------------------------------------

def _team_games(team, game, records, now):
    from services import nfl_service
    return {prior["nflverse_id"] for prior in nfl_service._recent_games(team, game, records, now) if prior.get("nflverse_id")}


def nfl_players(game, records, espn_rows, now=None):
    """Listed players for both teams from ESPN and the official weekly report, valued by EPA."""
    from services import nfl_props
    now = now or datetime.now(UTC)
    season, week = game["season"], game["week"]
    current, previous, _context = nfl_props._week_inputs(season, week, nfl_props._fingerprint(season),
                                                         nfl_props._fingerprint(season - 1))
    replacement = nfl_value.replacement_levels([r for r in current + previous if r.get("position") in nfl_value.POSITIONS])
    by_player, previous_by_player = defaultdict(list), defaultdict(list)
    for row in current:
        by_player[row["player_id"]].append(row)
    for row in previous:
        previous_by_player[row["player_id"]].append(row)
    espn_to_gsis = {str(r["espn_id"]): r["gsis_id"] for r in store.records("weekly_rosters", season)
                    if r.get("espn_id") and r.get("gsis_id")}
    official = [r for r in store.records("injuries", season) if r.get("week") == week and r.get("report_status")]
    sides = {}
    for side, team in (("home", game["home_id"]), ("away", game["away_id"])):
        names = {normalize_name(r.get("player_display_name")): r["player_id"] for r in current if r.get("team") == team}
        listed = {}
        for row in official:
            if row.get("team") == team:
                listed[row["gsis_id"]] = {"gsis": row["gsis_id"], "name": row.get("full_name") or "", "position": row.get("position") or "",
                                          "status": row["report_status"], "injury": row.get("report_primary_injury") or "",
                                          "updated": None, "sources": ["NFL report"]}
        for row in espn_rows:
            if ESPN_NFL_TEAMS.get(row["team"], row["team"]) != team or row["status"].lower() == "active":
                continue
            gsis = espn_to_gsis.get(str(row["espn_id"])) or names.get(normalize_name(row["name"]))
            key = gsis or f'espn:{row["espn_id"] or row["name"]}'
            entry = listed.setdefault(key, {"gsis": gsis, "name": row["name"], "position": row["position"], "sources": []})
            # ESPN is updated through the week; the official report is the latest synced one.
            entry.update(status=row["status"], injury=row["injury"] or entry.get("injury", ""), updated=row["updated"])
            entry["sources"] = ["ESPN", *[s for s in entry["sources"] if s != "ESPN"]]
        # Unlisted players on the team count as available, so a starter back from injury
        # adds what the recent form lacks (as in scripts/backtest_nfl_injuries.py).
        for player_id, history in by_player.items():
            latest = max(history, key=lambda r: r["week"])
            if latest.get("team") == team and latest.get("position") in nfl_value.POSITIONS and player_id not in listed:
                listed[player_id] = {"gsis": player_id, "name": latest.get("player_display_name") or "", "status": "",
                                     "position": latest["position"], "injury": "", "updated": None, "sources": []}
        window = _team_games(team, game, records, now)
        rows = []
        for entry in listed.values():
            history = [r for r in by_player.get(entry["gsis"], []) if r.get("team") == team]
            position = history[-1]["position"] if history else entry["position"]
            value = share = None
            if position in nfl_value.POSITIONS and (history or previous_by_player.get(entry["gsis"])):
                value = nfl_value.player_value(history, previous_by_player.get(entry["gsis"], []), position, replacement)["value"]
                share = nfl_value.played_share(history, window, position)
            rows.append({**{k: entry.get(k) for k in ("name", "status", "injury", "updated", "sources")}, "position": position,
                         "availability": nfl_availability(entry["status"]), "value": value, "played_share": share or 0.0})
        sides[side] = rows
    return sides


# -- Soccer ----------------------------------------------------------------

FPL_ALIASES = {"man city": "manchester city", "man utd": "manchester united", "spurs": "tottenham hotspur",
               "nottm forest": "nottingham forest", "wolves": "wolverhampton wanderers"}


def _club(name):
    words = [w for w in normalize_name(name).split() if w not in {"afc", "fc"}]
    return " ".join(words)


def fpl_team(teams, name):
    target = _club(name)
    for team in teams:
        short = _club(team["name"])
        full = FPL_ALIASES.get(short, short)
        if target == full or target.startswith(full + " ") or full.startswith(target + " "):
            return team
    return None


def soccer_players(payload, home_name, away_name):
    players, teams = payload["elements"], payload["teams"]
    matches = sum(1 for event in payload.get("events") or [] if event.get("finished"))
    replacement = soccer_value.replacement_levels(players)
    sides = {}
    for side, name in (("home", home_name), ("away", away_name)):
        team = fpl_team(teams, name)
        if team is None:
            raise ImpactUnavailable(f"{name} was not found in the Fantasy Premier League feed.")
        rows = []
        for player in players:
            if player.get("team") != team["id"] or player.get("status") == "a":
                continue
            if player.get("status") == "u" and not player.get("minutes"):
                continue  # left the club or on loan without playing: nothing to take out of the form
            value = soccer_value.player_value(player, players, replacement)
            rows.append({"name": player.get("web_name") or "", "position": soccer_value.POSITION_NAMES.get(player.get("element_type"), ""),
                         "status": {"d": "Doubtful", "i": "Injured", "s": "Suspended", "u": "Unavailable", "n": "Not eligible"}.get(player.get("status"), ""),
                         "injury": player.get("news") or "", "updated": player.get("news_added"), "sources": ["Fantasy Premier League"],
                         "availability": soccer_value.availability(player), "played_share": soccer_value.played_share(player, matches),
                         "attack": value["attack"], "defence": value["defence"]})
        sides[side] = rows
    return sides


def summarize(report, names, *, unit):
    """JSON-ready output: rounded win chances and players, impact in percentage points."""
    teams = []
    for side in ("home", "away"):
        team = report["teams"][side]
        players = []
        for p in team["players"]:
            row = {k: p.get(k) for k in ("name", "position", "status", "injury", "updated", "sources")}
            row["chance_of_playing"] = round(p["availability"], 2)
            row["impact"] = None if p.get("impact") is None else round(100 * p["impact"], 1)
            players.append(row)
        teams.append({"side": side, "team": names[side], "win_now": round(100 * team["win_now"], 1),
                      "win_if_fit": round(100 * team["win_if_fit"], 1), "players": players})
    result = {"teams": teams, "unit": unit}
    if "draw_now" in report["teams"]["home"]:
        result["draw_now"] = round(100 * report["teams"]["home"]["draw_now"], 1)
    return result
