"""Player-prop parlays per sport: Ladder (rollover, ~2x), Easy (low-variance, 1.5-2x), Medium (2-5x),
Fly (high-variance, 5-10x) and a 5-leg player-prop parlay.

Every leg is a real sportsbook price. Its chance blends the player's record at that exact line over recent
games with the book's de-vigged chance (services.value_policy), and with the ScorPred model's chance where
the model covers the prop. Each tier is the slip with the best cautious expected value inside its payout band
(legs ranked by chance less half a standard error, so thin, extreme records cannot carry it): value legs
first ("+EV"), otherwise the best available legs ("Best available"), so every tier is still sent. One leg per
game and per player, so legs are not tied to the same result; chances multiply across separate games.
"""
from __future__ import annotations

import functools
import itertools
import math

from services import value_policy as vp

TIERS = {"ladder": (1.85, 2.3), "low": (1.5, 2.0), "medium": (2.0, 5.0), "fly": (5.0, 10.0), "five": (3.0, 60.0)}
TIER_LABELS = {"ladder": "Ladder · rollover (about 2x: $10 to $20)", "low": "Easy · low-variance parlay",
               "medium": "Medium · mid-variance parlay", "fly": "Fly · high-variance parlay", "five": "5-leg player-prop parlay"}
# Leg prices each tier draws from, never shorter than value_policy.PRICE_FLOOR (about -400): short favourites for
# Easy, up to about +220 for Fly. The ladder (user's double-up challenge, 2026-11-03) is one bet near 2x: one leg,
# or up to three from different games. The 5-leg parlay (user, 2026-11-04) is exactly five legs from five games.
LEG_PRICES = {"ladder": (vp.PRICE_FLOOR, 2.30), "low": (vp.PRICE_FLOOR, 1.45), "medium": (vp.PRICE_FLOOR, 2.30),
              "fly": (1.40, 3.20), "five": (vp.PRICE_FLOOR, 2.20)}
TIER_SHAPE = {"ladder": (1, 3, 1), "five": (5, 5, 1)}  # tier: (fewest legs, most legs, most legs per game)
MAX_LEGS = 4
MAX_PER_GAME = 1  # legs from one game share its result (a team total and the match total move together)
POOL = 16


def hit_record(values: list[float], line: float, side: str) -> tuple[int, int]:
    """(hits, games) of a stat against a half-point line; most recent games first."""
    hits = sum(1 for v in values if (v > line if side == "over" else v < line))
    return hits, len(values)


def smoothed(hits: int, games: int) -> float:
    """Hit rate pulled slightly toward 50% so a perfect small sample is never treated as certain."""
    return (hits + 1) / (games + 2)


MARKET_WEIGHT = 30  # the book's price counts as 30 games of evidence: books are sharp, records are short
SHOTS_WEIGHT = 10   # the soccer shots-on-target model counts as 10 games of evidence


def with_market(hits: float, games: int, price: float, weight: float = MARKET_WEIGHT, other: float | None = None) -> float:
    """A player's record blended with the sportsbook's de-vigged chance (`other` is the opposite side's price).

    A 15-for-15 record at a low line, picked out of hundreds of lines, overstates the true chance;
    the book's fair price is a strong prior. A leg only rates above its price when the record clearly beats it.
    """
    return (hits + weight * vp.no_vig(price, other)) / (games + weight)


def minutes_weight(minutes: list[float]) -> float:
    """Market weight for a player whose minutes swing: an unsteady role makes his record a weaker guide, so the
    book's price counts for more (up to double when his minutes vary by half their average or more)."""
    minutes = [m for m in minutes if m]
    if len(minutes) < 3:
        return MARKET_WEIGHT
    average = sum(minutes) / len(minutes)
    spread = math.sqrt(sum((m - average) ** 2 for m in minutes) / len(minutes)) / average
    return MARKET_WEIGHT * (1 + min(1.0, 2 * spread))


def leg_chance(record_chance: float | None, model_chance: float | None) -> float | None:
    values = [value for value in (record_chance, model_chance) if value is not None]
    return round(sum(values) / len(values), 4) if values else None


def priced(leg: dict, fair: float, evidence: float, parts: dict | None = None) -> dict:
    """A leg's value fields: the book's fair chance, our chance with only half its edge over that kept
    (value_policy.EDGE_SHRINK), how much evidence is behind it, its sources ('parts') and expected value."""
    raw = leg.get("chance")
    chance = round(vp.calibrated(raw, fair), 4) if raw is not None and leg.get("price") else raw
    return {**leg, "chance": chance, "raw_chance": raw, "fair": round(fair, 4), "evidence": round(evidence, 1),
            "parts": {k: round(v, 4) for k, v in (parts or {}).items() if v is not None},
            "ev": round(vp.expected_value(chance, leg["price"]), 4) if chance and leg.get("price") else None}


def _leg_rank(leg: dict):
    return (-vp.cautious(leg["chance"], leg.get("evidence") or 40) * leg["price"], -(leg.get("story") or 0))


def _search(legs: list[dict], low: float, high: float, max_legs: int, max_per_game: int, min_legs: int = 2) -> dict | None:
    """The slip with the best cautious expected value in the payout band (matchup stories only break ties)."""
    best = None
    for size in range(min_legs, max_legs + 1):
        for combo in itertools.combinations(legs, size):
            if len({leg["player"] for leg in combo}) < size:
                continue
            games = [leg["game_id"] for leg in combo]
            if max(games.count(g) for g in games) > max_per_game:
                continue
            odds = math.prod(leg["price"] for leg in combo)
            if not low <= odds <= high:
                continue
            careful, hit, _ = vp.slip_value(combo)
            key = (round(careful, 6), sum(leg.get("story") or 0 for leg in combo), hit)
            if best is None or key > best[0]:
                best = (key, combo, odds, hit)
    if not best:
        return None
    key, combo, odds, hit = best
    return {"legs": list(combo), "decimal_odds": round(odds, 2), "hit_prob": round(hit, 4),
            "ev": round(hit * odds - 1, 4), "cautious_ev": round(key[0], 4), "tag": vp.tag(combo),
            "payout_per_10": round(10 * odds, 2)}


STAT_LABELS = {"points": "Points", "rebounds": "Rebounds", "assists": "Assists", "pra": "Pts + Reb + Ast", "threes": "3-Pointers Made"}
MARKET_STATS = {"player_points": "points", "player_rebounds": "rebounds", "player_assists": "assists",
                "player_points_rebounds_assists": "pra", "player_threes": "threes"}
EDGE_MIN_CHANCE = 0.6  # a matchup edge also needs the player's matchup-adjusted record to clear this
MAX_EDGES = 6


# ── Plain-language reasons: every leg says why, in words, from the same numbers that picked it ─────────────────
def short_name(name: str) -> str:
    parts = str(name).split()
    return parts[-1] if len(parts) > 1 else str(name)


def target_words(line: float, side: str, unit: str) -> str:
    """'2+ shots' for over 1.5, '18 or fewer points' for under 18.5."""
    return f"{math.floor(line) + 1:g}+ {unit}" if side == "over" else f"{math.ceil(line) - 1:g} or fewer {unit}"


def record_sentence(name: str, line: float, side: str, unit: str, hits: int, n: int, average: float | None) -> str:
    did = "had" if side == "over" else "stayed at"
    tail = f", averaging {average:g}" if average is not None else ""
    return f"{short_name(name)} has {did} {target_words(line, side, unit)} in {hits} of his last {n} games{tail}."


def price_sentence(chance: float, price: float, fair: float | None = None) -> str:
    fair = fair if fair is not None else vp.no_vig(price)
    return f"We make it {chance:.0%} to hit; the bookmaker's price implies {fair:.0%} once its margin is removed."


def sentence(text: str | None) -> str | None:
    if not text:
        return None
    text = text[0].upper() + text[1:]
    return text if text.endswith(".") else text + "."


def edge_entry(leg: dict, adjusted: float, factor: float, note: str) -> dict:
    """One 'Matchup edges' row: the defense leans the same way as the pick and the adjusted record agrees."""
    return {**leg, "edge_note": note, "adjusted": round(adjusted, 3), "factor": round(factor, 3),
            "edge": round(adjusted - (leg.get("fair") or vp.no_vig(leg["price"])), 3) if leg.get("price") else None}


def leans_with(side: str, factor: float) -> bool:
    return factor > 1.04 if side == "over" else factor < 0.96


def _stat(row: dict, stat: str):
    if stat == "pra":
        parts = [row.get("points"), row.get("rebounds"), row.get("assists")]
        return None if any(p is None for p in parts) else sum(parts)
    return row.get(stat)


def build_nba(app, *, now=None, max_games: int = 15) -> dict:
    """Today's Low / Medium / Fly NBA player parlays from ESPN's free DraftKings board (no odds credits).

    Each leg is a real DraftKings line (main line either side, or an X+ alternate). Its chance blends the
    player's last-15 record at that exact line (ESPN box scores) with the book's price, and with the
    ScorPred model where the model covers that prop. Injured players and part-time players are skipped.
    """
    from datetime import UTC, datetime
    import parlay_engine as pe
    from services import daily_digest, espn_box_scores as box, espn_props, injury_reports, prop_markets
    from services.nba_pregame_model import season_of
    from services.prediction_lifecycle import is_pregame, parse_kickoff
    from zoneinfo import ZoneInfo
    now = now or datetime.now(UTC)
    day = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()  # the slate of the day `now` falls on (ET)
    api_get, props_for = daily_digest.flask_readers(app)
    slate, code = api_get("/api/dashboard/nba", {"date": day})
    cards = sorted((c for c in slate.get("slate") or [] if code == 200 and is_pregame(c.get("matchDate"), c.get("eventStatus"), now=now)),
                   key=lambda c: c["matchDate"])[:max_games]
    notes, legs, edges = [], [], []
    try:
        rows, _fresh = injury_reports.espn("nba")
        out = {prop_markets._name(r.get("name")) for r in rows}
    except Exception:
        out = set()
        notes.append("NBA injury feed unavailable; injured players were not excluded.")
    from services import context_factors as cf, matchup_engine as me
    try:
        profiles = me.league_profiles("nba", None, season_of(now), now) if cards else None
    except Exception:
        profiles = None
        notes.append("Defense-vs-position data unavailable; legs use player records only.")
    spreads = {}  # DraftKings point spread per game (ESPN scoreboard), for blowout risk
    try:
        board = espn_props._get(espn_props.SITE.format(sport="basketball", league="nba"), {"dates": day.replace("-", "")})
        for event in board.get("events") or []:
            shaped = pe.espn_event(event) or {}
            for market in ((shaped.get("bookmakers") or [{}])[0].get("markets") or []):
                if market["key"] == "spreads":
                    spreads[shaped["id"]] = abs(float(market["outcomes"][0]["point"]))
    except Exception:
        pass
    for card in cards:
        home, away, kickoff = card["teamA"], card["teamB"], parse_kickoff(card["matchDate"])
        feed = espn_props.quotes("nba", home, away, card["matchDate"])
        if feed["status"] != "available":
            continue
        offers = [dict(q, market=q["market"].removesuffix("_alternate")) for q in feed["alternates"]]
        for q in feed["quotes"]:
            offers += [{**q, "side": side, "price": q[f"{side}_decimal"], "book": q["bookmaker"],
                        "other": q["under_decimal" if side == "over" else "over_decimal"]} for side in ("over", "under")]
        with prop_markets.prices_deferred():
            payload, status = props_for("nba", card, day)
        ladders = {(prop_markets._name(p["name"]), p["market"]): p
                   for p in (payload.get("result") or {}).get("picks") or [] if status == 200 and p.get("ladder")}
        season, logs = season_of(kickoff), {}
        opponent = {str(card.get("homeId")): str(card.get("awayId")), str(card.get("awayId")): str(card.get("homeId"))}
        team_games, stars_out, rested = {}, {}, {}
        for team_id, team_name in ((card.get("homeId"), home), (card.get("awayId"), away)):
            try:
                games = box.team_games("nba", team_id, (season - 1, season), before=kickoff)
            except Exception:
                games = []
            team_games[str(team_id)] = games
            stars_out[str(team_id)] = cf.stars(games, str(team_id), prop_markets._name) & out
            rested[str(team_id)] = not cf.back_to_back([t.timestamp() for g in games if (t := parse_kickoff(g.get("date")))], kickoff.timestamp())
            for game in reversed(games):  # newest first
                players = game["teams"].get(str(team_id), {}).get("players", [])
                roles = me.nba_groups(players)  # PG = that game's starting guard with the most assists
                for player in players:
                    logs.setdefault(prop_markets._name(player["name"]), {"team": team_name, "team_id": str(team_id), "name": player["name"],
                                                                        "games": []})["games"].append({**player, "group": roles.get(player["id"])})
        best = {}
        for q in offers:
            stat, info = MARKET_STATS.get(q["market"]), logs.get(q["player"])
            if not stat or not info or q["player"] in out:
                continue
            recent = info["games"][:15]
            if len(recent) < 8 or sum(g["minutes"] for g in recent[:5]) / 5 < 18:
                continue  # part-time or barely-seen players
            values = [v for g in recent if (v := _stat(g, stat)) is not None]
            hits, n = hit_record(values, q["line"], q["side"])
            # Tonight's context scales his recent games before counting hits (recent games count more): the defense he
            # faces, star teammates out, a back-to-back, and blowout risk for starters. Each was tested on 3 seasons.
            tid = info["team_id"]
            group, against = me.player_group("nba", recent), opponent.get(tid)
            factor = profiles.factor(against, group, stat) if profiles and group else 1.0
            mate, mate_note = cf.teammate_factor(q["player"], lambda row: _stat(row, stat), team_games.get(tid, []), tid,
                                                 stars_out.get(tid, set()), prop_markets._name)
            starter = sum(1 for g in recent[:10] if g.get("starter")) >= 6
            context = mate * (1.0 if rested.get(tid, True) else cf.BACK_TO_BACK) * cf.blowout_factor(spreads.get(feed["event_id"]), starter)
            adjusted, _ = cf.weighted_hits(values, q["line"], q["side"], factor * context)
            note = profiles.note(against, group, stat) if profiles and group else None
            spread = spreads.get(feed["event_id"])
            reasons = [r for r in (note if note and leans_with(q["side"], factor) else None,
                                   mate_note if mate_note and leans_with(q["side"], mate) else None,
                                   "his team played yesterday, the second night of a back-to-back" if not rested.get(tid, True) and q["side"] == "under" else None,
                                   f"the spread is {spread:g}, so starters may sit the fourth quarter" if spread and starter and spread >= cf.BLOWOUT_SPREAD and q["side"] == "under" else None) if r]
            average = round(sum(values[:15]) / max(1, len(values[:15])), 1)
            # The scouting story: his team's attack at his position meeting this defence's weakness there. It leads the
            # explanation, and makes this a story pick, when it points his way and his own record does not disagree.
            told = me.story(profiles, tid, profiles, against, group, stat) if profiles and group and against else None
            agrees = bool(told and told["side"] == q["side"] and adjusted * 2 >= n)
            check = record_sentence(info["name"], q["line"], q["side"], STAT_LABELS[stat].lower(), hits, n, average)
            why = ([sentence(line) for line in told["lines"]] + [f"{short_name(info['name'])} is one of their {me.NBA_GROUPS.get(group, group)}. {check}"]
                   + [sentence(r) for r in reasons if r is not note] if agrees else [check, *[sentence(r) for r in reasons]])
            pick = ladders.get((q["player"], stat))
            model = prop_markets._model_at_line({**pick, "side": q["side"]}, q["line"]) if pick else None
            key = (q["player"], stat, q["side"], q["line"])
            weight = minutes_weight([g["minutes"] for g in recent[:10]])
            record_part = with_market(adjusted, n, q["price"], weight, q.get("other"))
            # say where the chance comes from when it is more than his plain record: tonight's context and the model
            why = why + [line for line in (
                f"Adjusted for tonight's matchup and lineup, his last {n} games count as {adjusted:.1f} at this line."
                if abs(adjusted - hits) >= 1 else None,
                f"ScorPred's projection model makes it {model:.0%}." if model is not None else None) if line]
            if key not in best or q["price"] > best[key]["price"]:
                best[key] = priced({"sport": "nba", "player": info["name"], "team": info["team"], "game_id": feed["event_id"],
                             "matchup": f"{away} @ {home}", "kickoff": card["matchDate"],
                             "selection": f'{info["name"]} {q["side"].capitalize()} {q["line"]:g} {STAT_LABELS[stat]}',
                             "price": q["price"], "american": pe.american(q["price"]), "book": q.get("book") or "DraftKings",
                             "chance": leg_chance(record_part, model), "model": model,
                             "record": f"{hits}/{n}", "last10": "%d/%d" % hit_record(values[:10], q["line"], q["side"]),
                             "average": round(sum(values[:10]) / max(1, len(values[:10])), 1), "expires_at": q["expires_at"],
                             "edge_note": " · ".join(reasons) or None,
                             "why": why, "story": abs(told["score"]) if agrees else 0,
                             "grade": {"kind": "player", "path": "basketball/nba", "event": feed["event_id"].removeprefix("espn-"),
                                       "player": info["name"], "stat": stat, "line": q["line"], "side": q["side"]}},
                                   vp.no_vig(q["price"], q.get("other")), n + weight + (n if model is not None else 0),
                                   {"record": record_part, "model": model})
                if agrees:
                    edges.append(edge_entry(best[key], best[key]["chance"], factor, " ".join(told["lines"])))
        legs += best.values()
    return {"generated_at": now.isoformat(), "games": len(cards), "legs_considered": len(legs),
            "tiers": build_tiers(legs), "edges": top_edges(edges), "notes": notes, "legs": legs}


def top_edges(edges: list[dict]) -> list[dict]:
    """Best matchup stories, one per player. Stories whose pick is value at its price come first; the rest follow,
    tagged, so a good story is never presented as a good bet on the story alone. No price shorter than the floor."""
    best = {}
    edges = [{**e, "tag": vp.TAG_VALUE if vp.is_value(e) else vp.TAG_BEST} for e in edges
             if not e.get("price") or e["price"] >= vp.PRICE_FLOOR]
    for edge in sorted(edges, key=lambda e: (e["tag"] != vp.TAG_VALUE, -(e.get("story") or 0),
                                             -(e["edge"] if e["edge"] is not None else e["adjusted"] - 0.5))):
        best.setdefault(edge["player"], edge)
    return list(best.values())[:MAX_EDGES]


SOCCER_SLUGS = ("eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "uefa.champions", "uefa.europa")
SOCCER_STATS = {"player_shots": ("shots", "Shots"), "player_shots_on_target": ("shots_on_target", "Shots on Target"),
                "player_assists": ("assists", "Assists"), "player_fouls": ("fouls", "Fouls"),
                "player_saves": ("saves", "Saves"), "player_goals": ("goals", "Goals")}


def _home_league(team_id: str, fallback: str) -> str:
    """A team's domestic league slug from ESPN (cached for a week); the game's own competition if unknown."""
    from services import cache_service, espn_props
    key = cache_service.make_key("espn-team-league-v1", team_id)
    slug = cache_service.get_json(key, ttl=7 * 86400)
    if not slug:
        try:
            team = espn_props._get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{fallback}/teams/{team_id}").get("team") or {}
            slug = (team.get("defaultLeague") or {}).get("slug") or fallback
        except Exception:
            slug = fallback
        cache_service.set_json(key, slug, ttl=7 * 86400)
    return slug


def team_results(team_id, seasons, leagues) -> list[tuple[float, float]]:
    """(scored, conceded) for a team's completed games in these competitions, newest first (ESPN schedules)."""
    from services import espn_box_scores as box, injury_reports
    games = {}
    for league in leagues:
        for season in seasons:
            url = box.BASE.format(path=f"soccer/{league}/teams/{team_id}/schedule") + f"?season={season}"
            try:
                payload, _fetched, _stale = injury_reports._fetch(f"schedule-soccer-{league}-{team_id}-{season}", url,
                                                                  fresh_seconds=box.SCHEDULE_FRESH_SECONDS)
            except Exception:
                continue
            for event in payload.get("events") or []:
                competition = (event.get("competitions") or [{}])[0]
                if not ((competition.get("status") or event.get("status") or {}).get("type") or {}).get("completed"):
                    continue
                sides = {str((c.get("team") or {}).get("id")): c for c in competition.get("competitors") or []}
                mine, other = sides.get(str(team_id)), next((c for k, c in sides.items() if k != str(team_id)), None)
                try:
                    goals = [float((c.get("score") or {}).get("value") if isinstance(c.get("score"), dict) else c.get("score"))
                             for c in (mine, other)]
                except (AttributeError, TypeError, ValueError):
                    continue
                games[str(event.get("id"))] = (event.get("date") or "", goals[0], goals[1])
    return [(scored, conceded) for _, scored, conceded in sorted(games.values(), reverse=True)]


def shot_profile(boxes: dict) -> dict:
    """{'teams': {team: (shots on target per game, shots on target allowed per game)} over the last 10, 'conversion':
    goals per shot on target across all these games}, from box scores newest first."""
    teams, goals, sot = {}, 0, 0
    for team, games in boxes.items():
        mine, against = [], []
        for game in games[:10]:
            side = game["teams"].get(team) or {}
            other = game["teams"].get(side.get("opponent")) or {}
            mine.append(sum(p.get("shots_on_target") or 0 for p in side.get("players", [])))
            against.append(sum(p.get("shots_on_target") or 0 for p in other.get("players", [])))
        for game in games:
            for side in game["teams"].values():
                goals += sum(p.get("goals") or 0 for p in side.get("players", []))
                sot += sum(p.get("shots_on_target") or 0 for p in side.get("players", []))
        if len(mine) >= 6:
            teams[team] = (sum(mine) / len(mine), sum(against) / len(against))
    return {"teams": teams, "conversion": goals / sot if sot >= 100 else 0.3}


# Settings chosen by scripts/validate_goal_model.py (2026-11-04) on 5,481 cached matches: picked on the selection
# period (Mar-May 2026) only, then measured once on the untouched final period (May 2026 on). Team-goals Brier 0.1889 vs
# 0.1915 for the old pooled 'own scored + opponent conceded' record (lower is better).
HOME_EDGE = 1.15        # home sides score x1.15 their usual rate, away sides /1.15
GOAL_PRIOR = 1.35       # goals per team per game across Europe's top leagues
GOAL_PRIOR_GAMES = 20   # each side's attack and defence are shrunk toward a typical side by this many games
GOAL_MODEL_WEIGHT = 15  # the attack-vs-defence goal model counts as 15 games of evidence


def expected_goals(scored: list[float], conceded: list[float], home: bool) -> float | None:
    """One team's expected goals in this game from a single attack-vs-defence model: its scoring rate times what
    this opponent concedes, relative to a typical side (both shrunk toward it), with home advantage. Team totals,
    match totals and both-teams-to-score all come from these two numbers, so the same evidence is never counted
    twice (audit, 2026-11-04: 'own scored + opponent conceded' had been treated as 30 separate games)."""
    if len(scored) < 6 or len(conceded) < 6:
        return None
    attack = (sum(scored) + GOAL_PRIOR * GOAL_PRIOR_GAMES) / (len(scored) + GOAL_PRIOR_GAMES)
    defence = (sum(conceded) + GOAL_PRIOR * GOAL_PRIOR_GAMES) / (len(conceded) + GOAL_PRIOR_GAMES)
    return attack * defence / GOAL_PRIOR * (HOME_EDGE if home else 1 / HOME_EDGE)


def goal_chance(kind: str, side: str, line: float | None, mine: float, theirs: float | None = None) -> float:
    """Poisson chance for a team total (mine), a match total (mine + theirs) or both teams to score."""
    from services import context_factors as cf
    if kind == "btts":
        yes = (1 - math.exp(-mine)) * (1 - math.exp(-theirs))
        return yes if side == "yes" else 1 - yes
    over = cf.poisson_over(mine + (theirs or 0), line)
    return over if side == "over" else 1 - over


def _game_legs(event, slug, items, home, away, recent, matchup, shots=None) -> list[dict]:
    """BTTS, match over/under goals and team total goals for one game. Each chance blends the teams' recent
    record, the attack-vs-defence goal model (and shots on target for team goals) and the book's fair price."""
    import parlay_engine as pe
    from services import context_factors as cf, espn_props
    game_id, legs = f"espn-{event['id']}", []
    hid, aid = str(home.get("id")), str(away.get("id"))
    h, a = recent.get(hid) or [], recent.get(aid) or []
    both = h[:10] + a[:10]
    xg = {hid: expected_goals([s for s, _ in h[:15]], [c for _, c in a[:15]], True),
          aid: expected_goals([s for s, _ in a[:15]], [c for _, c in h[:15]], False)}
    model_ok = None not in xg.values()

    def leg(key, selection, price, hits, n, detail, grade, model=None, why=(), other=None):
        if not price or n < 8:
            return
        # the goal model counts as GOAL_MODEL_WEIGHT games of evidence next to the record and the book's fair price,
        # so it can tip a close call but never overrule a sharp market (cross-league games are where it is weakest)
        fair = vp.no_vig(price, other)
        weight = GOAL_MODEL_WEIGHT if model is not None else 0
        chance = (hits + weight * (model or 0) + MARKET_WEIGHT * fair) / (n + weight + MARKET_WEIGHT)
        legs.append(priced({"sport": "soccer", "player": key, "team": detail, "game_id": game_id, "matchup": matchup,
                            "kickoff": event.get("date"), "selection": selection, "price": price, "american": pe.american(price),
                            "book": espn_props.BOOK, "chance": round(chance, 4), "model": None if model is None else round(model, 4),
                            "why": [w for w in why if w], "record": f"{hits}/{n}", "record_label": detail, "last10": None,
                            "average": None, "expires_at": None,
                            "grade": {"path": f"soccer/{slug}", "event": str(event["id"]), **grade}}, fair, n + weight + MARKET_WEIGHT,
                           {"record": (hits + MARKET_WEIGHT * fair) / (n + MARKET_WEIGHT), "model": model}))

    names = {hid: home.get("displayName"), aid: away.get("displayName")}
    xg_line = (f"Our goal model expects {names[hid]} {xg[hid]:.1f} and {names[aid]} {xg[aid]:.1f} goals "
               f"(each attack against this opponent's defence, with home advantage)." if model_ok else None)
    # Both teams to score: DraftKings lists Yes, then No.
    btts = [espn_props._decimal(i) for i in items if (i.get("type") or {}).get("name") == "Both Teams To Score"]
    if len(btts) == 2:
        hits = sum(1 for s, c in both if s > 0 and c > 0)
        for side, price, other, count, text in (
                ("yes", btts[0], btts[1], hits, f"Both sides scored in {hits} of these two teams' last {len(both)} games."),
                ("no", btts[1], btts[0], len(both) - hits,
                 f"At least one side failed to score in {len(both) - hits} of these two teams' last {len(both)} games.")):
            leg(f"btts:{game_id}", f"Both teams to score: {side.capitalize()}", price, count, len(both), "BTTS in these teams' last games",
                {"kind": "btts", "side": side}, model=goal_chance("btts", side, None, xg[hid], xg[aid]) if model_ok else None,
                why=[text, xg_line], other=other)
    # Match goals over / under at DraftKings' main line (fresh on the scoreboard, both sides).
    shaped = pe.espn_event(event) or {}
    for market in ((shaped.get("bookmakers") or [{}])[0].get("markets") or []):
        if market["key"] != "totals":
            continue
        for outcome in market["outcomes"]:
            line, over = outcome["point"], outcome["name"] == "Over"
            side = "over" if over else "under"
            other = next((o["price"] for o in market["outcomes"] if o is not outcome and o.get("point") == line), None)
            hits = sum(1 for s, c in both if (s + c > line if over else s + c < line))
            leg(f"total:{game_id}", f'{outcome["name"]} {line:g} goals', outcome["price"], hits, len(both), "goals in these teams' last games",
                {"kind": "total", "line": line, "side": side},
                model=goal_chance("total", side, line, xg[hid], xg[aid]) if model_ok else None,
                why=[f"{hits} of these two teams' last {len(both)} games had {target_words(line, side, 'goals')}.", xg_line], other=other)
    # Team total goals: per team and line, over then under; the over ladder must rise with the line.
    teams = {str(home.get("id")): home, str(away.get("id")): away}
    ladders = {}
    for item in items:
        if (item.get("type") or {}).get("name") != "Team Total Goals":
            continue
        team = str((item.get("team") or {}).get("$ref", "")).split("/teams/")[-1].split("?")[0]
        line = espn_props._line(item)
        if team in teams and line is not None and line % 1 == 0.5:
            ladders.setdefault(team, {}).setdefault(line, []).append(espn_props._decimal(item))
    for team, lines in ladders.items():
        pairs = {line: prices for line, prices in lines.items() if len(prices) == 2 and all(prices)}
        overs = [pairs[line][0] for line in sorted(pairs)]
        if len(pairs) < 2 or any(lo >= hi for lo, hi in zip(overs, overs[1:])):
            continue  # cannot tell the sides apart reliably
        # A team's goals depend on who it plays: its own scoring AND what this opponent concedes, so a strong attack
        # facing a tight defence is not backed for over 2.5 on its own record (user, 2026-11-03).
        scored = [s for s, _ in (recent.get(team) or [])[:15]]
        rival = next((k for k in teams if k != team), None)
        conceded = [c for _, c in (recent.get(rival) or [])[:15]]
        name, rival_name = teams[team].get("displayName"), (teams.get(rival) or {}).get("displayName", "opponent")
        detail = f"{name} goals, last {len(scored)}"
        mine, theirs = ((shots or {}).get("teams") or {}).get(team), ((shots or {}).get("teams") or {}).get(rival)
        conversion = (shots or {}).get("conversion", 0.3)
        if mine and theirs:
            detail += f" · shots on target {mine[0]:.1f} vs {theirs[1]:.1f} allowed"
        shots_line = (f"{name} average {mine[0]:.1f} shots on target a game; {rival_name} allow {theirs[1]:.1f}."
                      if mine and theirs else None)
        for line, (over, under) in pairs.items():
            for side, price, other in (("over", over, under), ("under", under, over)):
                own = sum(1 for g in scored if (g > line if side == "over" else g < line))
                allowed = sum(1 for g in conceded if (g > line if side == "over" else g < line))
                if own * 2 < len(scored) or allowed * 2 < len(conceded):
                    continue  # both the team's own record and the opponent's defence must back it (no one-sided picks)
                # the team's own record is the sample (its games only); the opponent's defence enters through the goal
                # model, averaged with the shots-on-target model where both teams' shot data exists
                models = [m for m in (goal_chance("team", side, line, xg[team]) if xg.get(team) is not None else None,
                                      cf.shots_goal_chance(mine and mine[0], theirs and theirs[1], conversion, line, side)) if m is not None]
                target = target_words(line, side, "goals")
                why = [f"{name} scored {target} in {own} of their last {len(scored)} games, and {rival_name} conceded {target} "
                       f"in {allowed} of their last {len(conceded)}.", f"Our goal model expects {name} to score {xg[team]:.1f}." if xg.get(team) else None,
                       shots_line]
                leg(f"team:{team}:{line}", f"{name} {side} {line:g} goals", price, own, len(scored), detail,
                    {"kind": "team_total", "team_id": team, "line": line, "side": side},
                    model=sum(models) / len(models) if models else None, why=why, other=other)
    return legs


def build_soccer(app=None, *, now=None, unpriced: bool = False) -> dict:
    """Today's Low / Medium / Fly soccer player parlays from ESPN's DraftKings board (no odds credits).

    ESPN often lists soccer prop lines without prices; a line with no price never reaches a parlay, so this
    returns no parlays until DraftKings prices are posted. unpriced=True instead returns record-only 'picks'
    at the posted lines (no payouts). Records come from each team's domestic league plus the game's own
    competition, for regular starters only (45+ minutes a game on average)."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo
    import parlay_engine as pe
    from services import espn_box_scores as box, espn_props, prop_markets
    from services.prediction_lifecycle import parse_kickoff
    from services.soccer_props import season_of
    now = now or datetime.now(UTC)
    day = now.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    from services import matchup_engine as me
    legs, games, notes, edges = [], 0, [], []
    for slug in SOCCER_SLUGS:
        try:
            events = espn_props._get(espn_props.SITE.format(sport="soccer", league=slug), {"dates": day}).get("events") or []
        except Exception:
            continue
        for event in events:
            kickoff = parse_kickoff(event.get("date"))
            if ((event.get("status") or {}).get("type") or {}).get("state") != "pre" or not kickoff or kickoff <= now:
                continue
            sides = {c.get("homeAway"): c.get("team") or {} for c in (event.get("competitions") or [{}])[0].get("competitors") or []}
            home, away = sides.get("home") or {}, sides.get("away") or {}
            try:
                board = espn_props._board(("soccer", slug), str(event["id"]))
                names = espn_props._names({(i.get("athlete") or {}).get("$ref") for i in board["items"]} - {None})
            except Exception:
                continue
            _pairs, offers = espn_props.shape("soccer", board["items"], names, board["fetched_at"], event.get("date"), unpriced=unpriced)
            if unpriced:
                offers = [q for q in offers if q["price"] is None]
            season, logs = season_of(kickoff), {}
            matchup = f'{away.get("displayName")} @ {home.get("displayName")}'
            # each team's box scores (domestic league + this competition), newest first: player logs and shots on target
            boxes = {}
            for team in (home, away):
                rows = {}
                for league in {_home_league(str(team.get("id")), slug), slug}:
                    try:
                        for game in box.team_games("soccer", team.get("id"), (season - 1, season), league=league, before=kickoff):
                            rows[game["id"]] = game
                    except Exception:
                        continue
                boxes[str(team.get("id"))] = sorted(rows.values(), key=lambda g: g.get("date") or "", reverse=True)
            game_legs = []
            if not unpriced:  # BTTS, goals over / under and team goals, from the teams' recent scores and shots
                recent = {str(t.get("id")): team_results(t.get("id"), (season - 1, season),
                                                         {_home_league(str(t.get("id")), slug), slug}) for t in (home, away)}
                game_legs = _game_legs(event, slug, board["items"], home, away, recent, matchup, shots=shot_profile(boxes))
            if not offers and not game_legs:
                continue  # no priced props for this game yet
            games += 1
            legs += game_legs
            opponent = {str(home.get("id")): str(away.get("id")), str(away.get("id")): str(home.get("id"))}
            for team in (home, away):
                for game in boxes[str(team.get("id"))]:
                    for player in game["teams"].get(str(team.get("id")), {}).get("players", []):
                        logs.setdefault(prop_markets._name(player["name"]), {"team": team.get("displayName"), "team_id": str(team.get("id")),
                                                                            "name": player["name"], "games": []})["games"].append(
                            {**player, "group": me.soccer_group(player.get("position"))})
            best = {}
            for q in offers:
                stat = SOCCER_STATS.get(q["market"].removesuffix("_alternate"))
                info = logs.get(q["player"])
                if not stat or not info:
                    continue
                recent = info["games"][:15]
                if len(recent) < 8 or sum(g["minutes"] for g in recent[:5]) / 5 < 45:
                    continue  # rotation and bench players
                values = [g.get(stat[0]) for g in recent if g.get(stat[0]) is not None]
                hits, n = hit_record(values, q["line"], q["side"])
                # The defense he faces: judged against its own domestic league (wingers vs leaky flanks, and so on).
                against, group = opponent.get(info["team_id"]), me.player_group("soccer", recent)
                profiles = soccer_profiles(_home_league(against, slug), season, now) if against and group else None
                factor = profiles.factor(against, group, stat[0]) if profiles else 1.0
                adjusted = me.adjusted_hits(values, q["line"], q["side"], factor)
                note = profiles.note(against, group, stat[0]) if profiles else None
                # the scouting story: his side's attack at his position (vs its own league) meeting this defence's weakness
                attack = soccer_profiles(_home_league(info["team_id"], slug), season, now) if profiles else None
                told = me.story(attack, info["team_id"], profiles, against, group, stat[0]) if attack else None
                agrees = bool(told and told["side"] == q["side"] and adjusted * 2 >= n)
                check = record_sentence(info["name"], q["line"], q["side"], stat[1].lower(), hits, n, round(sum(values) / max(1, len(values)), 1))
                why = ([sentence(line) for line in told["lines"]] + [f"{short_name(info['name'])} is one of their {me.SOCCER_GROUPS.get(group, group)}. {check}"]
                       if agrees else [check, sentence(note) if note and leans_with(q["side"], factor) else None])
                key = (q["player"], stat[0], q["line"])
                if key not in best or (q["price"] or 0) > (best[key]["price"] or 0):
                    weight = minutes_weight([g["minutes"] for g in recent[:10]])
                    chance = smoothed(adjusted, n) if q["price"] is None else leg_chance(with_market(adjusted, n, q["price"], weight), None)
                    best[key] = priced({"sport": "soccer", "player": info["name"], "team": info["team"], "game_id": f"espn-{event['id']}",
                                 "matchup": matchup, "kickoff": event.get("date"),
                                 "selection": f'{info["name"]} {q["line"] + 0.5:g}+ {stat[1]}',
                                 "price": q["price"], "american": pe.american(q["price"]) if q["price"] else "—", "book": q["book"],
                                 "chance": chance, "model": None,
                                 "record": f"{hits}/{n}", "last10": "%d/%d" % hit_record(values[:10], q["line"], q["side"]),
                                 "average": round(sum(values[:10]) / max(1, len(values[:10])), 2), "expires_at": q["expires_at"],
                                 "edge_note": note if note and leans_with(q["side"], factor) else None,
                                 "why": [w for w in why if w], "story": abs(told["score"]) if agrees else 0,
                                 "grade": {"kind": "player", "path": f"soccer/{slug}", "event": str(event["id"]),
                                           "player": info["name"], "stat": stat[0], "line": q["line"], "side": q["side"]}},
                                       vp.no_vig(q["price"]) if q["price"] else smoothed(adjusted, n), n + weight)
                    if agrees:
                        edges.append(edge_entry(best[key], best[key]["chance"], factor, " ".join(told["lines"])))
            legs += best.values()
    if unpriced:
        picks = {}
        for leg in legs:  # each player's single strongest line
            if leg["player"] not in picks or leg["chance"] > picks[leg["player"]]["chance"]:
                picks[leg["player"]] = leg
        ranked = sorted((p for p in picks.values() if p["chance"] >= 0.62), key=lambda p: -p["chance"])
        return {"generated_at": now.isoformat(), "games": games, "picks": ranked, "tiers": {}, "edges": top_edges(edges), "notes": notes}
    if not games:
        notes.append("No soccer player props with DraftKings prices yet.")
    return {"generated_at": now.isoformat(), "games": games, "legs_considered": len(legs), "tiers": build_tiers(legs),
            "edges": top_edges(edges), "notes": notes, "legs": legs}


NFL_STATS = {"player_pass_yds": (("passing_yards",), "Passing Yards", "passing yards"),
             "player_rush_yds": (("rushing_yards",), "Rushing Yards", "rushing yards"),
             "player_reception_yds": (("receiving_yards",), "Receiving Yards", "receiving yards"),
             "player_receptions": (("receptions",), "Receptions", "receptions"),
             "player_rush_reception_yds": (("rushing_yards", "receiving_yards"), "Rush + Rec Yards", "rushing plus receiving yards"),
             "player_pass_completions": (("completions",), "Completions", "completions"),
             "player_pass_attempts": (("attempts",), "Pass Attempts", "pass attempts"),
             "player_rush_attempts": (("carries",), "Rush Attempts", "rushing attempts")}
NFL_ABBR = {"WSH": "WAS", "LAR": "LA"}  # ESPN abbreviations that nflverse spells differently
NFL_MIN_GAMES = 5
NFL_GROUPS = {"QB": "quarterbacks", "RB": "running backs", "WR": "wide receivers", "TE": "tight ends"}


def _nfl_value(row: dict, fields) -> float | None:
    values = [row.get(field) for field in fields]
    return None if any(v is None or isinstance(v, (bool, str)) for v in values) else float(sum(values))


def nfl_logs(season: int, week: int) -> dict:
    """{normalised name: rows newest first} from nflverse weekly stats before `week`, plus the previous season."""
    from services import nflverse_store as store
    from services.prop_markets import _name
    rows = [r for r in store.records("player_stats", season) if r.get("week") is not None and r["week"] < week]
    rows += list(store.records("player_stats", season - 1))
    logs = {}
    for row in sorted(rows, key=lambda r: (r.get("season") or 0, r.get("week") or 0), reverse=True):
        if row.get("season_type") in ("REG", "POST") and row.get("player_display_name"):
            logs.setdefault(_name(row["player_display_name"]), []).append(row)
    return logs


def nfl_allowed(season: int, week: int, fields, position: str) -> dict:
    """{team: (per-game amount allowed to this position, rank 1 = most, teams)} this season before `week`."""
    from services import nflverse_store as store
    totals, games = {}, {}
    for row in store.records("player_stats", season):
        if row.get("week") is None or row["week"] >= week or row.get("position") != position or not row.get("opponent_team"):
            continue
        value = _nfl_value(row, fields)
        if value is None:
            continue
        team = row["opponent_team"]
        totals[team] = totals.get(team, 0.0) + value
        games.setdefault(team, set()).add(row["week"])
    averages = {team: totals[team] / len(games[team]) for team in totals if len(games[team]) >= 3}
    ranked = sorted(averages, key=lambda t: -averages[t])
    return {team: (averages[team], ranked.index(team) + 1, len(ranked)) for team in averages}


def build_nfl(app=None, *, now=None) -> dict:
    """Today's NFL player parlays from ESPN's DraftKings board (no odds credits) and nflverse game logs.

    Each leg's chance blends the player's record at that exact line (this season and last) with the book's de-vigged
    price. The opponent's defence against his position is named when it ranks in the top or bottom quarter."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo
    import parlay_engine as pe
    from services import espn_props, injury_reports, prop_markets
    from services.prediction_lifecycle import parse_kickoff
    now = now or datetime.now(UTC)
    day = now.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    legs, games, notes = [], 0, []
    try:
        events = espn_props._get(espn_props.SITE.format(sport="football", league="nfl"), {"dates": day}).get("events") or []
    except Exception:
        events = []
    try:
        out = {prop_markets._name(r.get("name")) for r in injury_reports.espn("nfl")[0]}
    except Exception:
        out = set()
    logs_cache, allowed_cache = {}, {}
    for event in events:
        kickoff = parse_kickoff(event.get("date"))
        if ((event.get("status") or {}).get("type") or {}).get("state") != "pre" or not kickoff or kickoff <= now:
            continue
        season, week = (event.get("season") or {}).get("year"), (event.get("week") or {}).get("number")
        if not season or not week:
            continue
        competitors = (event.get("competitions") or [{}])[0].get("competitors") or []
        abbrs = [NFL_ABBR.get(a, a) for a in ((c.get("team") or {}).get("abbreviation") for c in competitors) if a]
        names = {NFL_ABBR.get(a, a): (c.get("team") or {}).get("displayName")
                 for c in competitors if (a := (c.get("team") or {}).get("abbreviation"))}
        sides = {c.get("homeAway"): (c.get("team") or {}).get("displayName") for c in competitors}
        try:
            board = espn_props._board(("football", "nfl"), str(event["id"]))
            athletes = espn_props._names({(i.get("athlete") or {}).get("$ref") for i in board["items"]} - {None})
        except Exception:
            continue
        pairs, alternates = espn_props.shape("nfl", board["items"], athletes, board["fetched_at"], event.get("date"))
        offers = [dict(q, market=q["market"].removesuffix("_alternate")) for q in alternates]
        for q in pairs:
            offers += [{**q, "side": side, "price": q[f"{side}_decimal"], "book": q["bookmaker"],
                        "other": q["under_decimal" if side == "over" else "over_decimal"]} for side in ("over", "under")]
        if not offers:
            continue
        games += 1
        logs = logs_cache.setdefault((season, week), nfl_logs(season, week))
        matchup = f'{sides.get("away")} @ {sides.get("home")}'
        best = {}
        for q in offers:
            spec, rows = NFL_STATS.get(q["market"]), logs.get(q["player"])
            if not spec or not rows or q["player"] in out or not q.get("price"):
                continue
            fields, label, words = spec
            values = [v for r in rows[:15] if (v := _nfl_value(r, fields)) is not None]
            if len(values) < NFL_MIN_GAMES:
                continue
            hits, n = hit_record(values, q["line"], q["side"])
            team, position = rows[0].get("team"), rows[0].get("position")
            against = next((a for a in abbrs if a != team), None)
            allowed = allowed_cache.setdefault((season, week, fields, position), nfl_allowed(season, week, fields, position))
            rank = allowed.get(against) if against else None
            check = record_sentence(rows[0]["player_display_name"], q["line"], q["side"], words, hits, n,
                                    round(sum(values) / len(values), 1))
            note = None
            if rank and position in NFL_GROUPS:
                average, place, count = rank
                weak, strong = place <= count / 4, place > count * 3 / 4
                if (weak and q["side"] == "over") or (strong and q["side"] == "under"):
                    from services.matchup_engine import where_words
                    note = (f"{names.get(against, against)} give up {average:.0f} {words} a game to opposing {NFL_GROUPS[position]}, "
                            f"the {where_words((place, count))} of {count} teams")
            key = (q["player"], q["market"], q["side"], q["line"])
            if key in best and q["price"] <= best[key]["price"]:
                continue
            chance = with_market(hits, n, q["price"], MARKET_WEIGHT, q.get("other"))
            best[key] = priced({"sport": "nfl", "player": rows[0]["player_display_name"], "team": names.get(team, team),
                                "game_id": f"espn-{event['id']}", "matchup": matchup, "kickoff": event.get("date"),
                                "selection": f'{rows[0]["player_display_name"]} {q["side"].capitalize()} {q["line"]:g} {label}',
                                "price": q["price"], "american": pe.american(q["price"]), "book": q.get("book") or espn_props.BOOK,
                                "chance": round(chance, 4), "model": None, "record": f"{hits}/{n}",
                                "last10": "%d/%d" % hit_record(values[:10], q["line"], q["side"]),
                                "average": round(sum(values[:10]) / len(values[:10]), 1), "expires_at": q.get("expires_at"),
                                "edge_note": note, "why": [w for w in (sentence(note), check) if w], "story": 0,
                                "grade": {"kind": "player", "path": "football/nfl", "event": str(event["id"]),
                                          "player": rows[0]["player_display_name"], "stat": "+".join(fields),
                                          "line": q["line"], "side": q["side"]}},
                               vp.no_vig(q["price"], q.get("other")), n + MARKET_WEIGHT)
        legs += best.values()
    if not games:
        notes.append("No NFL player props posted for today's games.")
    return {"generated_at": now.isoformat(), "games": games, "legs_considered": len(legs), "tiers": build_tiers(legs) if legs else {},
            "edges": [], "notes": notes, "legs": legs}


def soccer_profiles(league: str, season: int, now):
    """A domestic league's defense-vs-position profiles (12-hour cache); None when ESPN cannot supply them."""
    return _soccer_profiles(league, season, now.date().isoformat())


@functools.lru_cache(maxsize=16)
def _soccer_profiles(league: str, season: int, day: str):
    from datetime import UTC, datetime
    from services import matchup_engine as me
    try:  # games before the start of the day, so every build that day shares one profile
        return me.league_profiles("soccer", league, season, datetime.fromisoformat(day).replace(tzinfo=UTC), ttl=12 * 3600)
    except Exception:
        return None


def record_groups(picks: list[dict]) -> list[tuple[str, float, list[dict]]]:
    """Safest / Medium / Bold sets from record-only picks (different games), for when no prices exist."""
    out = []
    for name, floor, size in (("Safest", 0.8, 2), ("Medium", 0.7, 3), ("Bold", 0.0, 4)):
        pool = [p for p in picks if p["chance"] >= floor][:14]
        best = None
        for combo in itertools.combinations(pool, size):
            if len({p["game_id"] for p in combo}) < size:
                continue
            hit = math.prod(p["chance"] for p in combo)
            if best is None or hit > best[0]:
                best = (hit, list(combo))
        if best:
            out.append((name, best[0], best[1]))
    return out


def _fill(legs: list[dict], low: float, high: float, most: int, per_game: int, fewest: int) -> dict | None:
    """Value legs first (a "+EV" slip); when they cannot fill the band, the best available legs (tagged as such)."""
    for candidates in ([leg for leg in legs if vp.is_value(leg)], legs):
        pool = sorted(candidates, key=_leg_rank)[:POOL]
        found = _search(pool, low, high, most, per_game, fewest) if len(pool) >= fewest else None
        if found:
            return found
    return None


def build_tiers(legs: list[dict], *, max_legs: int = MAX_LEGS, max_per_game: int = MAX_PER_GAME) -> dict:
    """{tier: parlay or None}. Legs need player, game_id, price and chance.

    Every tier is filled whenever its price range has enough legs (user, 2026-11-04): from value legs when they
    can fill it, otherwise from the best available legs, and the slip's tag says which. None only when the
    board has too few priced legs in range.
    """
    priced_legs = [leg for leg in legs if leg.get("chance") and leg.get("price")]
    result = {}
    for tier, (low, high) in TIERS.items():
        cheap, dear = LEG_PRICES[tier]
        fewest, most, per_game = TIER_SHAPE.get(tier, (2, max_legs, max_per_game))
        in_range = [leg for leg in priced_legs if cheap <= leg["price"] <= dear]
        result[tier] = _fill(in_range, low, high if tier != "low" else high - 1e-9, most, per_game, fewest)
    return result


def ladder_bets(legs: list[dict], count: int = 3) -> list[dict]:
    """Up to `count` separate ~2x bets for a split stake ($30 as 3 x $10): each the best-value bet paying 1.85-2.3x,
    and no two bets share a game, so one bad result cannot sink two of them."""
    low, high = TIERS["ladder"]
    cheap, dear = LEG_PRICES["ladder"]
    fair = [leg for leg in legs if leg.get("chance") and leg.get("price")]
    bets, used = [], set()
    for _ in range(count):
        open_legs = [leg for leg in fair if cheap <= leg["price"] <= dear and leg["game_id"] not in used]
        bet = _fill(open_legs, low, high, 3, 1, 1)
        if not bet:
            break
        bets.append(bet)
        used |= {leg["game_id"] for leg in bet["legs"]}
    return bets
