"""Matchup engine: what each defense allows to each position, from ESPN box scores (free, cached on disk).

NBA box scores only say G / F / C, so the starting guard with the most assists in a game counts as that game's
point guard (PG); the other guards are G. Soccer box scores carry real positions, so wingers (LM, RM, LW, RW,
AM-L, AM-R), strikers, attacking and central midfielders are told apart.

For a defense: the average per player-game that opposing players in a group produced against it over its last
LAST_GAMES games, next to the league average for that group. The factor is shrunk halfway back to 1.0 because
15 games are a small sample. A factor of 1.15 means "this defense gives that position about 15% more".
"""
from __future__ import annotations

from collections import Counter

LAST_GAMES = 15
SHRINK = 0.2  # 0.5 pushed predictions too far; 0.2 was the most accurate strength on three seasons of replays (2026-11-03)
NBA_STATS = ("points", "rebounds", "assists", "threes", "pra")
SOCCER_STATS = ("shots", "shots_on_target", "goals", "assists")
NBA_GROUPS = {"PG": "point guards", "G": "guards", "F": "forwards", "C": "centers"}
SOCCER_GROUPS = {"W": "wingers", "ST": "strikers", "AM": "attacking mids", "CM": "central mids", "FB": "full-backs", "CB": "centre-backs"}
WINGER = {"LM", "RM", "LW", "RW", "AM-L", "AM-R", "LF", "RF"}
STRIKER = {"F", "CF", "CF-L", "CF-R", "ST"}
STAT_WORDS = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "3PM", "pra": "PRA",
              "shots": "shots", "shots_on_target": "shots on target", "goals": "goals"}


def value(row: dict, stat: str):
    if stat == "pra":
        parts = [row.get("points"), row.get("rebounds"), row.get("assists")]
        return None if any(p is None for p in parts) else sum(parts)
    return row.get(stat)


def nba_groups(players: list[dict]) -> dict[str, str]:
    """{player id: group} for one team in one game. Bench guards stay G; only starters define the PG."""
    guards = [p for p in players if p.get("position") == "G"]
    starters = [p for p in guards if p.get("starter")]
    lead = max(starters, key=lambda p: p.get("assists") or 0)["id"] if starters else None
    return {p["id"]: ("PG" if p["id"] == lead else p.get("position") if p.get("position") in ("G", "F", "C") else "F")
            for p in players}


def soccer_group(position: str) -> str | None:
    position = (position or "").upper()
    if position in WINGER:
        return "W"
    if position in STRIKER:
        return "ST"
    if position.startswith("AM"):
        return "AM"
    if position.startswith(("CM", "DM", "M")):
        return "CM"
    if position in ("LB", "RB", "LWB", "RWB"):
        return "FB"
    if position.startswith("CD") or position == "CB":
        return "CB"
    return None  # goalkeepers, substitutes with no position


def groups_for(sport: str, players: list[dict]) -> dict[str, str]:
    if sport == "nba":
        return nba_groups(players)
    return {p["id"]: g for p in players if (g := soccer_group(p.get("position"))) and (p.get("minutes") or 0) >= 45}


def allowed(sport: str, games: list[dict], team_id: str, last: int = LAST_GAMES) -> dict:
    """{group: {stat: [sum, count]}} produced by opponents against team_id in its newest `last` games."""
    stats = NBA_STATS if sport == "nba" else SOCCER_STATS
    found = {}
    mine = [g for g in games if str(team_id) in g["teams"]]
    for game in sorted(mine, key=lambda g: g.get("date") or "", reverse=True)[:last]:
        opponent = game["teams"].get(game["teams"][str(team_id)].get("opponent"), {}).get("players") or []
        roles = groups_for(sport, opponent)
        for player in opponent:
            group = roles.get(player["id"])
            if not group or (sport == "nba" and not player.get("starter") and group != "G"):
                continue  # NBA: starters by position, plus bench guards as G
            for stat in stats:
                v = value(player, stat)
                if v is not None:
                    cell = found.setdefault(group, {}).setdefault(stat, [0.0, 0])
                    cell[0] += v
                    cell[1] += 1
    return found


def produced(sport: str, games: list[dict], team_id: str, last: int = LAST_GAMES) -> dict:
    """{group: {stat: [sum, count]}} the team's OWN players produced, by position, in its newest `last` games:
    its attacking identity ('Real Madrid's wingers shoot a lot')."""
    stats = NBA_STATS if sport == "nba" else SOCCER_STATS
    found = {}
    mine = [g for g in games if str(team_id) in g["teams"]]
    for game in sorted(mine, key=lambda g: g.get("date") or "", reverse=True)[:last]:
        players = game["teams"][str(team_id)].get("players") or []
        roles = groups_for(sport, players)
        for player in players:
            group = roles.get(player["id"])
            if not group or (sport == "nba" and not player.get("starter") and group != "G"):
                continue
            for stat in stats:
                v = value(player, stat)
                if v is not None:
                    cell = found.setdefault(group, {}).setdefault(stat, [0.0, 0])
                    cell[0] += v
                    cell[1] += 1
    return found


def _league(summaries: dict) -> dict:
    league = {}
    for profile in summaries.values():
        for group, cells in profile.items():
            for stat, (total, count) in cells.items():
                cell = league.setdefault(group, {}).setdefault(stat, [0.0, 0])
                cell[0] += total
                cell[1] += count
    return league


class Profiles:
    """Defense-vs-position (and attack-by-position) profiles for a set of teams, with league averages and ranks."""

    def __init__(self, sport: str, games_by_team: dict[str, list[dict]] | None = None, names: dict[str, str] | None = None,
                 *, teams: dict | None = None, made: dict | None = None):
        """games_by_team: box scores per team; or teams / made: already-computed allowed() / produced() results (the cache)."""
        self.sport, self.names = sport, names or {}
        self.teams = teams if teams is not None else {str(team): allowed(sport, games, team) for team, games in (games_by_team or {}).items()}
        self.made = made if made is not None else {str(team): produced(sport, games, team) for team, games in (games_by_team or {}).items()}
        self.league, self.league_made = _league(self.teams), _league(self.made)

    def made_average(self, team, group, stat):
        cell = (self.made.get(str(team)) or {}).get(group, {}).get(stat)
        return cell[0] / cell[1] if cell and cell[1] >= 5 else None

    def made_league(self, group, stat):
        cell = self.league_made.get(group, {}).get(stat)
        return cell[0] / cell[1] if cell and cell[1] else None

    def made_rank(self, team, group, stat) -> tuple[int, int] | None:
        """(rank, out of): 1 = this team's players at the position produce the most."""
        values = {t: a for t in self.made if (a := self.made_average(t, group, stat)) is not None}
        if str(team) not in values:
            return None
        ordered = sorted(values, key=lambda t: -values[t])
        return ordered.index(str(team)) + 1, len(ordered)

    def average(self, team, group, stat):
        cell = (self.teams.get(str(team)) or {}).get(group, {}).get(stat)
        return cell[0] / cell[1] if cell and cell[1] >= 5 else None

    def league_average(self, group, stat):
        cell = self.league.get(group, {}).get(stat)
        return cell[0] / cell[1] if cell and cell[1] else None

    def factor(self, team, group, stat) -> float:
        """Shrunk ratio of what this defense allows to the group vs the league; 1.0 when unknown."""
        mine, base = self.average(team, group, stat), self.league_average(group, stat)
        if not mine or not base:
            return 1.0
        return 1 + SHRINK * (mine / base - 1)

    def rank(self, team, group, stat) -> tuple[int, int] | None:
        """(rank, out of): 1 = gives up the most to this group."""
        values = {t: a for t in self.teams if (a := self.average(t, group, stat)) is not None}
        if str(team) not in values:
            return None
        ordered = sorted(values, key=lambda t: -values[t])
        return ordered.index(str(team)) + 1, len(ordered)

    def note(self, team, group, stat) -> str | None:
        """'Jazz allow 9.1 ast to point guards (3rd-most of 30, +18%)' when the matchup clearly helps or hurts."""
        mine, base, rank = self.average(team, group, stat), self.league_average(group, stat), self.rank(team, group, stat)
        if not mine or not base or not rank or abs(mine / base - 1) < 0.08:
            return None
        band = max(3, rank[1] // 4)  # only the league's top or bottom quarter is a real matchup story
        if band < rank[0] <= rank[1] - band:
            return None
        labels = NBA_GROUPS if self.sport == "nba" else SOCCER_GROUPS
        where = (f"{ordinal(rank[0])}-most" if rank[0] <= rank[1] / 2 else f"{ordinal(rank[1] - rank[0] + 1)}-fewest")
        name = self.names.get(str(team)) or "This defense"
        return (f"{name} allow {mine:.1f} {STAT_WORDS.get(stat, stat)} a game to {labels.get(group, group)}, "
                f"the {where} of {rank[1]} teams ({mine / base - 1:+.0%} vs average)")


STORY_MIN = 0.15  # attack strength + defensive weakness together must move the position's numbers by 15%+


def where_words(rank: tuple[int, int]) -> str:
    return f"{ordinal(rank[0])}-most" if rank[0] <= rank[1] / 2 else f"{ordinal(rank[1] - rank[0] + 1)}-fewest"


def story(attack: "Profiles", team: str, defence: "Profiles", opponent: str, group: str, stat: str) -> dict | None:
    """The scouting story for one position and stat: the attack's strength there meeting the defence's weakness.

    Returns {'score': + favours overs / - unders, 'side', 'lines': [defence sentence, attack sentence]} when both point
    the same way, together move the numbers by STORY_MIN or more, and at least one is in its league's top or bottom
    quarter. Each team is judged against its own league (cross-league games stay fair)."""
    allowed_avg, allowed_base, allowed_rank = defence.average(opponent, group, stat), defence.league_average(group, stat), defence.rank(opponent, group, stat)
    made_avg, made_base, made_rank = attack.made_average(team, group, stat), attack.made_league(group, stat), attack.made_rank(team, group, stat)
    if not all((allowed_avg, allowed_base, allowed_rank, made_avg, made_base, made_rank)):
        return None
    weak, strong = allowed_avg / allowed_base - 1, made_avg / made_base - 1
    if weak * strong <= 0 or abs(weak + strong) < STORY_MIN:
        return None
    over = weak > 0
    # at least one side must be in its league's top quarter (an over story) or bottom quarter (an under story)
    band = lambda r: r[0] <= max(3, r[1] // 4) if over else r[0] > r[1] - max(3, r[1] // 4)
    if not (band(allowed_rank) or band(made_rank)):
        return None
    labels = NBA_GROUPS if attack.sport == "nba" else SOCCER_GROUPS
    who, word = labels.get(group, group), STAT_WORDS.get(stat, stat)
    opp_name, team_name = defence.names.get(str(opponent)) or "The opponent", attack.names.get(str(team)) or "Their team"
    owner = team_name + ("'" if team_name.endswith("s") else "'s")
    # a side that is not in its league's top / bottom quarter is described as about average, never overstated
    defence_line = (f"{opp_name} {'struggle to stop' if over else 'shut down'} opposing {who}" if band(allowed_rank)
                    else f"{opp_name} are about average against opposing {who}") + \
                   f": they give up {allowed_avg:.1f} {word} a game to them, the {where_words(allowed_rank)} of {allowed_rank[1]} teams"
    attack_line = (f"{owner} {who} {'are among the most productive' if over else 'produce little'} here" if band(made_rank)
                   else f"{owner} {who} are about average here") + \
                  f": {made_avg:.1f} {word} a game, the {where_words(made_rank)} of {made_rank[1]}"
    return {"score": round(weak + strong, 3), "side": "over" if over else "under", "lines": [defence_line, attack_line]}


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def player_group(sport: str, rows: list[dict]) -> str | None:
    """A player's usual group over his recent games (rows carry the per-game 'group')."""
    seen = Counter(r.get("group") for r in rows[:10] if r.get("group"))
    return seen.most_common(1)[0][0] if seen else None


def adjusted_hits(values: list[float], line: float, side: str, factor: float) -> int:
    """Hits at the line after scaling the player's games by the opponent factor."""
    over = side == "over"
    return sum(1 for v in values if (v * factor > line if over else v * factor < line))


def league_profiles(sport: str, league: str | None, season: int, before, *, ttl: int = 6 * 3600) -> Profiles:
    """Every defense in an ESPN league (NBA, or a soccer league slug). The small per-team summaries are cached
    for `ttl`; the box scores behind them are cached on disk for good, so later builds fetch only new games."""
    from services import cache_service, espn_box_scores as box, espn_props
    key = cache_service.make_key("dvp-v2", sport, league or "nba", season, str(before)[:10])  # one per day; v2 adds attack
    cached = cache_service.get_json(key, ttl=ttl)
    if cached:
        return Profiles(sport, names=cached["names"], teams=cached["teams"], made=cached["made"])
    espn_path = "basketball/nba" if sport == "nba" else f"soccer/{league}"
    listing = espn_props._get(f"https://site.api.espn.com/apis/site/v2/sports/{espn_path}/teams")
    teams = ((listing.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []
    names = {str(t["team"]["id"]): t["team"].get("name") or t["team"].get("displayName") for t in teams}
    summaries, made = {}, {}
    for team_id in names:
        try:
            games = box.team_games(sport, team_id, (season - 1, season), league=league, before=before)
        except Exception:
            continue
        summaries[team_id] = allowed(sport, games, team_id)
        made[team_id] = produced(sport, games, team_id)
    cache_service.set_json(key, {"names": names, "teams": summaries, "made": made}, ttl=ttl)
    return Profiles(sport, names=names, teams=summaries, made=made)
