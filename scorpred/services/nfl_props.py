"""NFL player prop projections from local nflverse weekly player statistics.

Descriptive research only. Each market blends a recency-weighted current-season
mean with the player's previous season, scales it by what the opponent has
allowed to that position, and converts it to a full distribution so any line
can be priced. Only games played before the target week are used. Sportsbook
comparisons are attached separately by services.prop_markets, without changing
this model or its historical-target ranking.
"""
import math
from collections import defaultdict
from functools import lru_cache
from statistics import mean, median, pvariance

import numpy as np
from scipy import stats as sps

from services import nflverse_store as store

POSITIONS = ("QB", "RB", "WR", "TE")
# key: (label, source fields summed, positions, distribution, prior coefficient of variation)
MARKETS = {
    "passing_yards": ("Passing yards", ("passing_yards",), {"QB"}, "symmetric", 0.30),
    "completions": ("Completions", ("completions",), {"QB"}, "count", 0.22),
    "passing_attempts": ("Pass attempts", ("attempts",), {"QB"}, "count", 0.18),
    "passing_tds": ("Passing TDs", ("passing_tds",), {"QB"}, "count", 0.0),
    "interceptions": ("Interceptions", ("passing_interceptions",), {"QB"}, "count", 0.0),
    "rushing_yards": ("Rushing yards", ("rushing_yards",), {"QB", "RB"}, "yards", 0.55),
    "carries": ("Carries", ("carries",), {"RB"}, "count", 0.30),
    "receptions": ("Receptions", ("receptions",), {"RB", "WR", "TE"}, "count", 0.40),
    "receiving_yards": ("Receiving yards", ("receiving_yards",), {"RB", "WR", "TE"}, "yards", 0.65),
    "rush_rec_yards": ("Rush + rec yards", ("rushing_yards", "receiving_yards"), {"RB"}, "yards", 0.45),
    "anytime_td": ("Anytime TD", ("rushing_tds", "receiving_tds"), {"RB", "WR", "TE"}, "count", 0.0),
}
# Opportunity fields: a league per-opportunity rate times the player's own volume
# gives a role-aware prior, so three quiet games cannot zero out a starter.
OPPORTUNITY = {
    "passing_yards": ("attempts",), "completions": ("attempts",), "passing_tds": ("attempts",),
    "interceptions": ("attempts",), "rushing_yards": ("carries",), "receptions": ("targets",),
    "receiving_yards": ("targets",), "rush_rec_yards": ("carries", "targets"), "anytime_td": ("carries", "targets"),
}
RARE = {"passing_tds", "interceptions", "anytime_td"}
DECAY = 0.8          # weight of each older current-season game relative to the next
PRIOR_GAMES = 3      # previous-season mean counts as this many current games
RATE_GAMES = 2       # league-rate prior; touchdowns and interceptions lean on it harder
RARE_RATE_GAMES = 3
VARIANCE_GAMES = 4   # prior dispersion counts as this many observed games
# Yardage spread multiplier, fit on 2024 and checked on 2025. Wider gamma tails
# lower its median and miscalibrate the over price, so ranges stay a little narrow.
YARDS_DISPERSION = 1.2
OPPONENT_GAMES = 6   # opponent allowance shrinks toward league average
OPPONENT_LIMIT = 0.15
MIN_GAMES = 3
ROLE_SHARE = 0.4    # QB games below this share of the current workload were a different role
LIMITS = {"QB": 1, "RB": 2, "WR/TE": 5}
PICKS = 5
# Markets books post for each role, with the smallest line they post: a season median of
# 0.5 receiving yards is a role, not a prop.
PICK_MARKETS = {"QB": {"passing_yards": 149.5, "completions": 11.5, "passing_tds": 0.5},
                "RB": {"rushing_yards": 19.5, "receptions": 1.5},
                "WR": {"receiving_yards": 19.5, "receptions": 1.5},
                "TE": {"receiving_yards": 14.5, "receptions": 1.5}}
# Picking the largest disagreements selects overstated chances: the five best per game
# (favoured side only) said 69.8% and hit 65.1% in 2024 (1,185 picks). Shrinking toward even
# by 0.76 (fit 2024) is slightly conservative on 2025: 64.3% shown, 66.2% hit. Lines are
# season medians, softer than a sportsbook's, so these rates overstate results against a book.
PICK_SHRINK = 0.76
NOTICE = ("Experimental projections from nflverse box scores. Not validated against sportsbook closing lines; "
          "weather, game script and snap counts are not modeled. Sportsbook comparisons, when available, are experimental.")


class PropsUnavailable(ValueError):
    """The local store cannot support pregame projections for this game."""


@lru_cache(maxsize=4)
def _season_rows(season, fingerprint):
    return [row for row in store.records("player_stats", season)
            if row.get("player_id") and row.get("season_type") in {"REG", "POST"}]


def _fingerprint(season):
    return next((m["sha256"] for m in store.metadata() if m["dataset"] == "player_stats" and m["season"] == season), None)


def _rows(season):
    fingerprint = _fingerprint(season)
    return _season_rows(season, fingerprint) if fingerprint else []


@lru_cache(maxsize=16)
def _week_inputs(season, week, current_fingerprint, previous_fingerprint):
    """Rows before the target week plus league context, cached per synced snapshot."""
    current = [row for row in _season_rows(season, current_fingerprint) if row["week"] < week]         if current_fingerprint else []
    previous = _season_rows(season - 1, previous_fingerprint) if previous_fingerprint else []
    return current, previous, Context(current, current + previous)


def _value(row, fields):
    total = 0.0
    for field in fields:
        value = row.get(field)
        # The store already maps non-finite floats to None.
        if value is None or isinstance(value, (bool, str)):
            return None
        total += value
    return total


def _distribution(kind, mu, variance):
    mu = max(mu, 0.05)
    if kind == "symmetric":  # passing yards are close to normal; a gamma median sits too low
        return sps.norm(mu, math.sqrt(variance))
    if kind == "yards":
        shape = mu * mu / variance
        return sps.gamma(shape, scale=variance / mu)
    if variance <= mu * 1.02:
        return sps.poisson(mu)
    r = mu * mu / (variance - mu)
    return sps.nbinom(r, r / (r + mu))


def _ladder(dist, kind):
    """over[i] = P(stat >= start + i + 1), i.e. the over price of line start + i + 0.5."""
    start = max(0, int(math.floor(dist.ppf(0.005))))
    stop = int(math.ceil(dist.ppf(0.995))) + 1
    points = np.arange(start, stop) + (0.5 if kind != "count" else 0)  # integer yardage; continuity-corrected
    return {"start": start, "over": [round(float(p), 4) for p in dist.sf(points)]}


def _allowance(rows, fields, group):
    """Per-team-game totals allowed by each defense to one position group."""
    totals = defaultdict(float)
    for row in rows:
        if row.get("position") in group and row.get("opponent_team") and (value := _value(row, fields)) is not None:
            totals[(row["opponent_team"], row["game_id"])] += value
    by_team = defaultdict(list)
    for (team, _game), total in totals.items():
        by_team[team].append(total)
    league = mean(totals.values()) if totals else None
    return by_team, league


def _league_rates(rows):
    """Per-opportunity rate of every market's stat, by position."""
    totals = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        if row.get("position") not in POSITIONS:
            continue
        for key, opportunity in OPPORTUNITY.items():
            stat, volume = _value(row, MARKETS[key][1]), _value(row, opportunity)
            if stat is not None and volume:
                totals[(key, row.get("position"))][0] += stat
                totals[(key, row.get("position"))][1] += volume
    return {key: stat / volume for key, (stat, volume) in totals.items() if volume}


def _recency_mean(values):
    weights = [DECAY ** age for age in range(len(values) - 1, -1, -1)]
    return sum(w * v for w, v in zip(weights, values, strict=True)) / sum(weights), weights


def project_market(key, current, previous, opponent_allowed, league_allowed, rate_prior=None, *, include_distribution=True):
    """Pure model step: chronological values (oldest first) -> distribution summary.

    rate_prior is the league per-opportunity rate times the player's recent volume.
    """
    label, _fields, _positions, kind, cv = MARKETS[key]
    n = len(current)
    if n < MIN_GAMES and not (n >= 1 and len(previous) >= 6):
        return None
    recent, weights = _recency_mean(current) if n else (0.0, [])
    effective = sum(weights) ** 2 / sum(w * w for w in weights) if n else 0.0
    parts = [(effective, recent)]
    if len(previous) >= 4:
        parts.append((PRIOR_GAMES, mean(previous)))
    if rate_prior is not None:
        parts.append((RARE_RATE_GAMES if key in RARE else RATE_GAMES, rate_prior))
    base = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    factor = 1.0
    if opponent_allowed and league_allowed:
        ratio = mean(opponent_allowed) / league_allowed
        weight = len(opponent_allowed) / (len(opponent_allowed) + OPPONENT_GAMES)
        factor = min(1 + OPPONENT_LIMIT, max(1 - OPPONENT_LIMIT, 1 + (ratio - 1) * weight))
    mu = max(base * factor, 0.05)
    sample = current + previous[-8:]
    observed = pvariance(sample) if len(sample) >= 2 else 0.0
    prior = mu + (cv * mu) ** 2 if kind == "count" else max((cv * mu) ** 2, 25.0)
    variance = (len(sample) * observed + VARIANCE_GAMES * prior) / (len(sample) + VARIANCE_GAMES)
    # Predictive spread includes uncertainty in the mean itself, not just game-to-game noise.
    variance *= 1 + 1 / sum(w for w, _ in parts)
    if kind != "count":
        variance *= YARDS_DISPERSION
    variance = max(variance, mu * 1.0001 if kind == "count" else 1.0)
    dist = _distribution(kind, mu, variance)
    result = {"market": key, "label": label, "kind": kind, "projection": round(mu, 2),
              "games": n, "previous_games": len(previous), "opponent_factor": round(factor, 3),
              "recent": [round(v, 1) for v in current[-5:]]}
    if include_distribution:
        middle = float(dist.median())
        result.update(median=round(middle, 1), range=[round(float(dist.ppf(0.2)), 1), round(float(dist.ppf(0.8)), 1)])
    if key == "anytime_td":
        result["probability"] = round(float(dist.sf(0)), 4)
        if n >= MIN_GAMES:
            record = (sum(v >= 1 for v in current) + 1) / (n + 2)
            result["pick"] = {"line": None, "side": "yes", "probability": result["probability"], "record": round(record, 4)}
        return result
    if include_distribution:
        line = math.floor(middle) + 0.5
        result["model_line"] = line
        result["hit_rate"] = round(sum(v > line for v in current) / n, 3) if n else None
        result["ladder"] = _ladder(dist, kind)
    if n >= MIN_GAMES:
        # Stand-in for a sportsbook line: the player's own season median. "record" is the
        # smoothed share of this season's games over it, roughly what a book's price reflects.
        season_line = math.floor(median(current)) + 0.5
        over = float(dist.sf(math.floor(season_line) if kind == "count" else season_line))
        record = (sum(v > season_line for v in current) + 1) / (n + 2)
        side = "over" if over >= record else "under"
        result["pick"] = {"line": season_line, "side": side, "probability": round(over if side == "over" else 1 - over, 4),
                          "record": round(record if side == "over" else 1 - record, 4)}
    return result


def best_picks(teams, limit=PICKS):
    """Where the model most disagrees with each player's season record, one pick per healthy player.

    Ranked by edge: model chance minus the player's own hit rate at that line, in the
    markets books post for the player's role (``PICK_MARKETS``). Listed players are skipped: a prop on a player
    who sits is void, and a questionable tag changes their role.
    """
    candidates = []
    for team in teams:
        for player in team["players"]:
            if player.get("injury"):
                continue
            allowed = PICK_MARKETS.get(player["position"], {})
            options = [{**market["pick"], "edge": market["pick"]["probability"] - market["pick"]["record"],
                        "market": market["market"], "label": market["label"], "projection": market["projection"],
                        "calibration": market["pick"].get("calibration"),
                        "ladder": market.get("ladder")}
                       for market in player["markets"] if market.get("pick") and market["market"] in allowed
                       and market["pick"]["line"] >= allowed[market["market"]]]
            # A pick must beat the player's record and be the likelier side.
            options = [option for option in options if option["edge"] > 0 and option["probability"] > 0.5]
            if options:
                best = max(options, key=lambda option: option["edge"])
                candidates.append({**best, "player_id": player["player_id"], "name": player["name"],
                                   "position": player["position"], "team": team["team"],
                                   "chance": round(0.5 + PICK_SHRINK * (best["probability"] - 0.5), 4)})
    return sorted(candidates, key=lambda c: -c["edge"])[:limit]


class Context:
    """League-wide inputs computed once per game: defensive allowances and rates."""

    def __init__(self, current, rate_rows):
        self.current = current
        self.rates = _league_rates(rate_rows)
        self.allowances = {}

    def allowance(self, key, position):
        if (key, position) not in self.allowances:
            self.allowances[(key, position)] = _allowance(self.current, MARKETS[key][1], {position})
        return self.allowances[(key, position)]


def player_markets(position, rows, previous_rows, opponent, context, *, include_distribution=True):
    """Every market for one player, from rows before the target game (oldest first)."""
    # Quarterbacks are starters or not: a backup spell or partial game would drag
    # every mean down. Skill-player workloads swing weekly, so theirs are kept.
    if position == "QB" and rows:
        role = median(_usage([row]) for row in rows[-3:])
        rows = [row for row in rows if _usage([row]) >= ROLE_SHARE * role]
        previous_rows = [row for row in previous_rows if _usage([row]) >= ROLE_SHARE * role]
    markets = []
    for key, (_label, fields, positions, _kind, _cv) in MARKETS.items():
        if position not in positions:
            continue
        by_team, league = context.allowance(key, position)
        values = [v for row in rows if (v := _value(row, fields)) is not None]
        prior = [v for row in previous_rows if (v := _value(row, fields)) is not None]
        rate_prior = None
        if key in OPPORTUNITY and (rate := context.rates.get((key, position))) is not None:
            volume = [v for row in rows if (v := _value(row, OPPORTUNITY[key])) is not None]
            rate_prior = rate * _recency_mean(volume)[0] if volume else None
        if (market := project_market(key, values, prior, by_team.get(opponent), league, rate_prior,
                                     include_distribution=include_distribution)):
            from services import prop_calibration
            market = prop_calibration.apply(market, "nfl")
            markets.append(market)
    return markets


def _usage(rows):
    return sum((row.get("attempts") or 0) + (row.get("carries") or 0) + (row.get("targets") or 0) for row in rows)


def _select(team, current, eligible=None):
    """Likely starters on the player's latest team.

    The quarterback is whoever led the team's latest game: summed usage would
    keep a fill-in starter ahead of a returning one. Skill players rank by
    recency-weighted usage over the team's three latest games.
    """
    ordered = sorted(current, key=lambda r: r["week"])
    latest_team = {row["player_id"]: row.get("team") for row in ordered}
    weeks = sorted({row["week"] for row in ordered if row.get("team") == team})[-3:]
    usage, latest = defaultdict(float), {}
    for row in ordered:
        if row.get("team") != team or row["week"] not in weeks or latest_team[row["player_id"]] != team \
                or row.get("position") not in POSITIONS or (eligible is not None and row["player_id"] not in eligible):
            continue
        age = len(weeks) - 1 - weeks.index(row["week"])
        if row["position"] != "QB" or age == 0:
            usage[row["player_id"]] += DECAY ** age * _usage([row])
        latest[row["player_id"]] = row
    chosen, counts = [], defaultdict(int)
    for player_id in sorted(usage, key=lambda pid: -usage[pid]):
        position = latest[player_id]["position"]
        group = "WR/TE" if position in {"WR", "TE"} else position
        if counts[group] < LIMITS[group] and usage[player_id] > 0:
            counts[group] += 1
            chosen.append((player_id, latest[player_id]))
    return chosen


def _injuries(season, week):
    rows = [row for row in store.records("injuries", season) if row.get("week") == week]
    return {row["gsis_id"]: row for row in rows if row.get("gsis_id")}, bool(rows)


def check_coverage(game, records, rows):
    """Every completed game of both teams before this one must have player rows."""
    present = {(row["game_id"], row.get("team")) for row in rows}
    missing = []
    for prior in records:
        if (prior.get("completed") and prior.get("season") == game["season"] and prior.get("week", 0) < game["week"]
                and prior.get("nflverse_id")):
            for team in (game["home_id"], game["away_id"]):
                if team in {prior["home_id"], prior["away_id"]} and (prior["nflverse_id"], team) not in present:
                    missing.append(f'{team} week {prior["week"]}')
    if missing:
        raise PropsUnavailable("Player statistics are not available yet for " + ", ".join(sorted(set(missing)))
                               + ". Props need every completed game; run the nflverse sync.")


def current_eligibility(home, away):
    from services import current_rosters
    from services.data_freshness import worst
    from services.prediction_lifecycle import freshness_state
    crosswalk = defaultdict(set)
    for row in store.records("players"):
        if row.get("espn_id") and row.get("gsis_id"):
            crosswalk[str(row["espn_id"])].add(row["gsis_id"])
    directory = {("espn_id", pid): next(iter(ids)) for pid, ids in crosswalk.items() if len(ids) == 1}
    rosters, sources = {}, []
    for team in (home, away):
        players, source = current_rosters.current("nfl", current_rosters.nfl_team_id(team))
        rosters[team] = {directory[("espn_id", pid)]: athlete for pid, athlete in players.items()
                         if ("espn_id", pid) in directory}
        sources.append(source)
        if not rosters[team]:
            raise current_rosters.RosterUnavailable("No verified NFL player ID crosswalk is available for this team.")
    ambiguous = set(rosters[home]) & set(rosters[away])
    for team in rosters:
        rosters[team] = {pid: athlete for pid, athlete in rosters[team].items() if pid not in ambiguous}
    verification = worst(sources)
    if freshness_state(verification) != "fresh":
        raise current_rosters.RosterUnavailable("NFL current roster verification expired.")
    return rosters, {**verification, "status": "verified", "team_ids": [home, away],
                     "method": "Current ESPN roster athlete ID joined to nflverse GSIS player ID"}


def project(game, records):
    if not game.get("nflverse_id"):
        raise PropsUnavailable("Player props need the local nflverse store; this game came from the ESPN fallback.")
    season, week = game["season"], game["week"]
    current, previous_rows, context = _week_inputs(season, week, _fingerprint(season), _fingerprint(season - 1))
    check_coverage(game, records, current)
    if not current:
        raise PropsUnavailable("No current-season player statistics precede this game.")
    from services import current_rosters
    try:
        rosters, verification = current_eligibility(game["home_id"], game["away_id"])
    except current_rosters.RosterUnavailable as exc:
        raise PropsUnavailable(str(exc)) from exc
    injuries, report_synced = _injuries(season, week)
    by_player = defaultdict(list)
    for row in sorted(current, key=lambda r: r["week"]):
        by_player[row["player_id"]].append(row)
    previous_by_player = defaultdict(list)
    for row in sorted(previous_rows, key=lambda r: r["week"]):
        previous_by_player[row["player_id"]].append(row)
    teams = []
    for team, opponent, home in ((game["home_id"], game["away_id"], True), (game["away_id"], game["home_id"], False)):
        players, ruled_out, limited = [], [], []
        for player_id, latest in _select(team, current, set(rosters[team])):
            injury = injuries.get(player_id) or {}
            status = injury.get("report_status")
            entry = {"player_id": player_id, "name": rosters[team][player_id]["displayName"],
                     "position": latest["position"], "headshot": latest.get("headshot_url"),
                     "injury": {"status": status, "detail": injury.get("report_primary_injury")} if status else None}
            if status == "Out":
                ruled_out.append(entry)
                continue
            markets = player_markets(latest["position"], by_player[player_id], previous_by_player[player_id],
                                     opponent, context)
            if markets:
                entry["markets"] = markets
                players.append(entry)
            else:  # selected by recent usage but too few games in this role to project
                limited.append(entry)
        teams.append({"team": team, "opponent": opponent, "home": home, "players": players,
                      "ruled_out": ruled_out, "limited": limited})
    return {"game_id": game["nflverse_id"], "season": season, "week": week, "teams": teams, "picks": best_picks(teams),
            "roster_verification": verification,
            "injury_report": "synced" if report_synced else "not_synced",
            "data_through_week": max(row["week"] for row in current), "notice": NOTICE, "data_source": "nflverse"}
