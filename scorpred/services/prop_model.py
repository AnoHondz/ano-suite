"""Shared count-stat prop model for box-score sports (NBA, soccer).

For each player and market: a recency-weighted rate per minute, shrunk toward the
position's league rate; projected minutes from recent games; an opponent factor from
what the opponent has allowed per game, shrunk and capped; and a negative binomial whose
overdispersion blends the player's own history with a market default. The line stands
in for a sportsbook's: the median over the player's recent games in their current role.
A pick is the side where the model most exceeds the player's record at that line.
"""
import math
from collections import defaultdict
from statistics import median

import numpy as np
from scipy.stats import nbinom, poisson

HALF_LIFE_GAMES = 10.0
RECENT_MINUTES_GAMES = 5
LINE_GAMES = 15
ROLE_MINUTES = 0.7        # games below this share of projected minutes are a different role
OPPONENT_GAMES = 8
OPPONENT_LIMIT = 0.15
DISPERSION_GAMES = 10


def _weights(n):
    return [0.5 ** ((n - 1 - i) / HALF_LIFE_GAMES) for i in range(n)]


def value(row, fields):
    return sum(float(row.get(field) or 0.0) for field in fields)


def has_stats(row, fields):
    return all(isinstance(row.get(field), (int, float)) and not isinstance(row[field], bool)
               and math.isfinite(row[field]) for field in fields)


def projected_minutes(rows):
    recent = rows[-RECENT_MINUTES_GAMES:]
    weights = _weights(len(recent))
    return sum(w * r["minutes"] for w, r in zip(weights, recent)) / sum(weights) if recent else 0.0


def league_rates(games, markets, position_of):
    """Per-minute rate of each market's stat by position, over every player-game given."""
    totals = defaultdict(lambda: [0.0, 0.0])
    for game in games:
        for team in game["teams"].values():
            for row in team["players"]:
                for key, (fields, _alpha) in markets.items():
                    if not has_stats(row, fields):
                        continue
                    entry = totals[(key, position_of(row))]
                    entry[0] += value(row, fields)
                    entry[1] += row["minutes"]
    return {key: stat / minutes for key, (stat, minutes) in totals.items() if minutes}


def allowed(games, markets):
    """Per game, what each team's opponents produced in each market."""
    result = defaultdict(lambda: defaultdict(list))
    for game in games:
        for team_id, team in game["teams"].items():
            opponent = game["teams"][team["opponent"]]
            for key, (fields, _alpha) in markets.items():
                if not all(has_stats(row, fields) for row in opponent["players"]):
                    continue
                result[key][team_id].append(sum(value(row, fields) for row in opponent["players"]))
    return result


def opponent_factor(allowed_by_team, opponent, league_mean):
    games = allowed_by_team.get(opponent) or []
    if not games or not league_mean:
        return 1.0
    weight = len(games) / (len(games) + OPPONENT_GAMES)
    ratio = sum(games) / len(games) / league_mean
    return min(1 + OPPONENT_LIMIT, max(1 - OPPONENT_LIMIT, 1 + (ratio - 1) * weight))


def matchup_evidence(rows, fields, opponent, conceded, league_mean, factor):
    """Describe the EXISTING rule adjustment; never apply another multiplier."""
    sample = conceded.get(opponent) or []
    h2h = [r for r in rows if str(r.get('opponent')) == str(opponent) and has_stats(r, fields)]
    return {'opponent_id':str(opponent), 'opponent_sample_games':len(sample),
            'opponent_allowed_per_game':round(sum(sample)/len(sample),2) if sample else None,
            'sample_league_allowed_per_game':round(league_mean,2) if league_mean else None,
            'rule_opponent_multiplier':round(factor,4),
            'player_vs_opponent_games':len(h2h),
            'player_vs_opponent_average':round(sum(value(r,fields) for r in h2h)/len(h2h),2) if h2h else None,
            'scope':'Observed team totals allowed, not positional defense or a verified individual defender matchup.',
            'method':'Existing shrunk/capped rule baseline. Descriptive evidence; not an additional adjustment.',
            'final_projection_source':'rule baseline'}


def project_market(rows, fields, alpha_default, league_rate, prior_minutes, factor, min_games):
    """One market from a player's games (oldest first). None without enough games in role."""
    minutes = projected_minutes(rows)
    # Missing provider statistics are unavailable observations, never zero-count
    # games that can artificially inflate the probability of an under.
    rows = [row for row in rows if has_stats(row, fields)]
    in_role = [r for r in rows if r["minutes"] >= ROLE_MINUTES * minutes]
    if minutes <= 0 or len(in_role) < min_games:
        return None
    weights = _weights(len(rows))
    stat = sum(w * value(r, fields) for w, r in zip(weights, rows))
    played = sum(w * r["minutes"] for w, r in zip(weights, rows))
    rate = (stat + prior_minutes * league_rate) / (played + prior_minutes)
    mu = max(rate * minutes * factor, 0.02)
    recent = in_role[-LINE_GAMES:]
    values = [value(r, fields) for r in recent]
    scaled = [v * minutes / r["minutes"] for v, r in zip(values, recent)]
    m = sum(scaled) / len(scaled)
    observed = sum((v - m) ** 2 for v in scaled) / max(1, len(scaled) - 1)
    alpha_obs = max(0.0, (observed - m) / (m * m)) if m > 0 else alpha_default
    alpha = (len(scaled) * alpha_obs + DISPERSION_GAMES * alpha_default) / (len(scaled) + DISPERSION_GAMES)
    dist = poisson(mu) if alpha < 1e-6 else nbinom(1 / alpha, 1 / (1 + alpha * mu))
    line = math.floor(median(values)) + 0.5
    over = float(dist.sf(math.floor(line)))
    record = (sum(v > line for v in values) + 1) / (len(values) + 2)
    side = "over" if over >= record else "under"
    return {"projection": round(mu, 2), "minutes": round(minutes, 1), "line": line, "side": side,
            "probability": round(over if side == "over" else 1 - over, 4),
            "record": round(record if side == "over" else 1 - record, 4), "games": len(recent),
            "ladder": {"start": 0, "over": [round(float(p), 4) for p in dist.sf(np.arange(int(mu * 2.5) + 8))]}}


def best_picks(players, pick_markets, shrink, limit=5, sides=None):
    """One pick per player: the market beating their record by most, on the likelier side.

    ``sides`` limits a market to the sides books post (e.g. goals: over only).
    """
    picks = []
    for player in players:
        options = [m for m in player["markets"] if m["market"] in pick_markets
                   and m["line"] >= pick_markets[m["market"]] and m["probability"] > max(m["record"], 0.5)
                   and m["side"] in (sides or {}).get(m["market"], {"over", "under"})]
        if options:
            best = max(options, key=lambda m: m["probability"] - m["record"])
            picks.append({**best, "edge": best["probability"] - best["record"], "name": player["name"],
                          "team": player["team"], "position": player["position"], "player_id": player.get("id"),
                          "chance": round(0.5 + shrink * (best["probability"] - 0.5), 4)})
    return sorted(picks, key=lambda p: -p["edge"])[:limit]


def player_histories(games, team_id=None):
    """Each player's games, oldest first (``team_id`` limits to games for that team)."""
    by_player = defaultdict(list)
    for game in sorted(games, key=lambda g: g["date"] or ""):
        for tid, team in game["teams"].items():
            if team_id is not None and tid != str(team_id):
                continue
            for row in team["players"]:
                by_player[row["id"]].append({**row, "team": tid, "opponent": team["opponent"], "date": game["date"]})
    return by_player


def game_props(config, home_id, away_id, games, excluded=frozenset(), limit=5, *, eligible_players=None):
    """Markets for both teams' likely regulars and the game's best picks.

    ``games`` are box scores before tip-off involving either team (they also supply league
    rates and what each opponent allows). ``excluded`` holds normalized names of players on
    an injury report: a prop on a player who sits is void, and a listed one may be limited.
    """
    from services.injury_reports import normalize_name
    excluded_keys = {tuple(sorted(normalize_name(name).split())) for name in excluded}
    markets = config["markets"]
    histories = player_histories(games)
    positions = {}
    for player_id, rows in histories.items():
        listed = [config["position"](r["position"]) for r in rows if r["position"] not in ("SUB", "")]
        positions[player_id] = max(set(listed), key=listed.count) if listed else config["position"]("")
    position_of = lambda row: positions.get(row["id"]) or config["position"](row["position"])
    rates = league_rates(games, markets, position_of)
    conceded = allowed(games, markets)
    league_mean = {key: (sum(sum(v) for v in teams.values()) / max(1, sum(len(v) for v in teams.values())))
                   for key, teams in conceded.items()}
    players = []
    for team_id, opponent in ((str(home_id), str(away_id)), (str(away_id), str(home_id))):
        for player_id, rows in histories.items():
            if eligible_players is not None and player_id not in eligible_players.get(team_id, set()):
                continue
            if rows[-1]["team"] != team_id or tuple(sorted(normalize_name(rows[-1]["name"]).split())) in excluded_keys:
                continue
            if projected_minutes(rows) < config["min_minutes"]:
                continue
            position = positions[player_id]
            entry = {"id": player_id, "name": rows[-1]["name"], "team": team_id, "position": position, "markets": []}
            for key, (fields, alpha) in markets.items():
                if key in config.get("position_markets", {}) and position not in config["position_markets"][key]:
                    continue
                factor = opponent_factor(conceded[key], opponent, league_mean.get(key, 0.0))
                market = project_market(rows, fields, alpha, rates.get((key, position), 0.0), config["prior_minutes"],
                                        factor, config["min_games"])
                if market:
                    market = {**market, "market": key, "label": config["labels"][key]}
                    if config.get('sport') == 'nba':
                        market['matchup_context'] = matchup_evidence(rows, fields, opponent, conceded[key], league_mean.get(key,0), factor)
                    if config.get("sport"):
                        from services import prop_calibration
                        market = prop_calibration.apply(market, config["sport"])
                    entry["markets"].append(market)
            if entry["markets"]:
                players.append(entry)
    return {"players": players,
            "picks": best_picks(players, config["pick_markets"], config["shrink"], limit, config.get("pick_sides"))}
