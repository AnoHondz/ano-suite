"""Learned NBA player-prop means (LightGBM) over full ESPN box scores, priced as negative binomials.

The rule model in services.prop_model projects each stat from the player's own recent rate.
This model also sees shot volume (field-goal, three and free-throw attempts), minutes
stability, rest and back-to-backs, teammates and opponents listed out on ESPN's injury
report, team and opponent pace, what the opponent allows to the player's position, and the
pregame spread and total. Every feature comes from games before tip-off.

``History`` is shared by training (scripts/train_nba_props_ml.py) and the live endpoint so
both build features identically. The trained artifact names the markets that beat the rule
model in walk-forward tests; only those are replaced, and every guard of the rule model
(current rosters, injury exclusions, minimum games, lines and records) stays in force.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from datetime import datetime
from functools import lru_cache

import numpy as np
from scipy.stats import nbinom

from runtime_paths import model_dir

ARTIFACT = "nba_props_ml_v1.json"
VERSION = "nba-props-ml-v1"
MARKETS = {"points": ("points",), "rebounds": ("rebounds",), "assists": ("assists",), "threes": ("threes",),
           "pra": ("points", "rebounds", "assists")}
STATS = ("minutes", "points", "rebounds", "assists", "threes", "fga", "fg3a", "fta", "oreb", "dreb", "turnovers")
RATE_STATS = ("points", "rebounds", "assists", "threes", "fga", "fg3a", "fta", "oreb")
ALLOWED = ("points", "rebounds", "assists", "threes")
GROUPS = ("G", "F", "C")
HALF_LIVES = (3.0, 10.0)
WINDOW = 30
MIN_APPEARANCES = 3


def group(position: str) -> str:
    position = position or ""
    return "C" if "C" in position else ("F" if "F" in position else "G")


def _date(text) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def _ewma(values, half_life):
    if not values:
        return None
    weights = [0.5 ** ((len(values) - 1 - i) / half_life) for i in range(len(values))]
    return sum(w * v for w, v in zip(weights, values)) / sum(weights)


def _num(row, key):
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else 0.0


FEATURES = (
    [f"{stat}_ewm{int(h)}" for stat in STATS for h in HALF_LIVES]
    + [f"{stat}_pm10" for stat in RATE_STATS] + [f"{stat}_pm_prev" for stat in RATE_STATS]
    + [f"{stat}_season" for stat in ("minutes", "points", "rebounds", "assists", "threes")]
    + ["minutes_sd10", "minutes_last", "starter5", "games_season", "games_prev", "days_rest", "back_to_back",
       "games_last7", "team_pace", "team_points", "team_allowed", "opp_pace", "opp_points", "opp_allowed",
       "out_minutes", "out_points", "out_assists", "out_rebounds", "out_fga", "out_count", "opp_out_minutes",
       "home", "spread", "total", "team_total", "abs_spread", "playoffs", "pos_G", "pos_F", "pos_C"]
    + [f"opp_allows_{stat}" for stat in ALLOWED]
)


class History:
    """Chronological player and team state; call ``features`` before ``update`` for each game."""

    def __init__(self):
        self.players = defaultdict(list)   # player id -> appearances, oldest first
        self.positions = {}
        self.teams = defaultdict(list)     # team id -> per-game team summaries, oldest first
        self._contexts = {}                # (game id, team id) -> team-level features

    def update(self, game: dict) -> None:
        when, season = _date(game["date"]), game.get("season")
        summaries = {}
        for team_id, team in game["teams"].items():
            players = team["players"]
            pace = sum(_num(p, "fga") + 0.44 * _num(p, "fta") - _num(p, "oreb") + _num(p, "turnovers") for p in players)
            by_group = {g: {s: 0.0 for s in ALLOWED} for g in GROUPS}
            for row in players:
                bucket = by_group[group(row.get("position"))]
                for stat in ALLOWED:
                    bucket[stat] += _num(row, stat)
            summaries[team_id] = {"pace": pace, "points": sum(_num(p, "points") for p in players), "by_group": by_group}
        for team_id, team in game["teams"].items():
            opponent = summaries[team["opponent"]]
            self.teams[team_id].append({"date": when, "season": season, "pace": summaries[team_id]["pace"],
                                        "points": summaries[team_id]["points"], "allowed": opponent["points"],
                                        "allowed_by_group": opponent["by_group"]})
            for row in team["players"]:
                entry = {"date": when, "season": season, "team": team_id, "starter": float(bool(row.get("starter")))}
                entry.update({stat: _num(row, stat) for stat in STATS})
                self.players[row["id"]].append(entry)
                if row.get("position"):
                    self.positions[row["id"]] = row["position"]

    def _team(self, team_id, season):
        rows = self.teams.get(team_id, [])[-90:]
        return [r for r in rows if r["season"] == season][-10:] or [r for r in rows if r["season"] == season - 1][-10:]

    def _recent(self, player_id, season, team_id=None):
        rows = []
        for entry in reversed(self.players.get(player_id, [])):  # newest first; stop past last season
            if entry["season"] < season - 1:
                break
            if entry["season"] <= season:
                rows.append(entry)
        rows.reverse()
        if team_id is not None:
            rows = [e for e in rows if e["team"] == team_id and e["season"] == season]
        return rows

    def _context(self, team_id: str, game: dict) -> dict:
        """Team-level features, computed once per team and game."""
        key = (game["id"], team_id)
        if key in self._contexts:
            return self._contexts[key]
        season = game["season"]
        team = game["teams"][team_id]
        opponent_id = team["opponent"]
        opponent = game["teams"][opponent_id]
        f = {}
        for prefix, tid in (("team", team_id), ("opp", opponent_id)):
            rows = self._team(tid, season)
            f[f"{prefix}_pace"] = _ewma([r["pace"] for r in rows], 5.0)
            f[f"{prefix}_points"] = _ewma([r["points"] for r in rows], 5.0)
            f[f"{prefix}_allowed"] = _ewma([r["allowed"] for r in rows], 5.0)
        opp_rows = self._team(opponent_id, season)
        f["opp_allows"] = {g: {stat: _ewma([r["allowed_by_group"][g][stat] for r in opp_rows], 5.0) for stat in ALLOWED}
                           for g in GROUPS}
        out_totals = defaultdict(float)
        for out_id in team.get("out") or []:
            rows = self._recent(out_id, season, team_id)[-10:]
            if not rows:
                continue  # long-term absentees are already reflected in recent team games
            out_totals["count"] += 1
            for stat in ("minutes", "points", "assists", "rebounds", "fga"):
                out_totals[stat] += _ewma([r[stat] for r in rows], 3.0)
        for stat in ("minutes", "points", "assists", "rebounds", "fga"):
            f[f"out_{stat}"] = out_totals[stat]
        f["out_count"] = out_totals["count"]
        f["opp_out_minutes"] = sum(_ewma([r["minutes"] for r in rows], 3.0)
                                   for out_id in opponent.get("out") or []
                                   if (rows := self._recent(out_id, season, opponent_id)[-10:]))
        spread, total = team.get("spread"), game.get("total")
        f["home"] = float(bool(team.get("home")))
        f["spread"], f["total"] = spread, total
        f["team_total"] = total / 2 - spread / 2 if spread is not None and total is not None else None
        f["abs_spread"] = abs(spread) if spread is not None else None
        f["playoffs"] = float(game.get("season_type") == 3)
        if len(self._contexts) > 5000:
            self._contexts.clear()
        self._contexts[key] = f
        return f

    def features(self, player_id: str, team_id: str, game: dict) -> dict | None:
        """Pregame features for one player in ``game`` (which must not have been ``update``d yet)."""
        when, season = _date(game["date"]), game["season"]
        appearances = self._recent(player_id, season)
        if len(appearances) < MIN_APPEARANCES:
            return None
        window = appearances[-WINDOW:]
        current = [e for e in appearances if e["season"] == season]
        previous = [e for e in appearances if e["season"] == season - 1]
        f = {}
        for stat in STATS:
            values = [e[stat] for e in window]
            for h in HALF_LIVES:
                f[f"{stat}_ewm{int(h)}"] = _ewma(values, h)
        last10 = window[-10:]
        minutes10 = sum(e["minutes"] for e in last10)
        minutes_prev = sum(e["minutes"] for e in previous)
        for stat in RATE_STATS:
            f[f"{stat}_pm10"] = sum(e[stat] for e in last10) / minutes10 if minutes10 else None
            f[f"{stat}_pm_prev"] = sum(e[stat] for e in previous) / minutes_prev if minutes_prev else None
        for stat in ("minutes", "points", "rebounds", "assists", "threes"):
            f[f"{stat}_season"] = sum(e[stat] for e in current) / len(current) if current else None
        f["minutes_sd10"] = float(np.std([e["minutes"] for e in last10]))
        f["minutes_last"] = window[-1]["minutes"]
        f["starter5"] = sum(e["starter"] for e in window[-5:]) / len(window[-5:])
        f["games_season"], f["games_prev"] = len(current), len(previous)
        rest = (when - window[-1]["date"]).total_seconds() / 86400
        f["days_rest"] = min(rest, 10.0)
        f["back_to_back"] = float(rest < 1.6)
        f["games_last7"] = sum(1 for e in window if (when - e["date"]).total_seconds() < 7 * 86400)
        context = self._context(team_id, game)
        position = group(self.positions.get(player_id, ""))
        f.update({key: value for key, value in context.items() if key != "opp_allows"})
        for stat in ALLOWED:
            f[f"opp_allows_{stat}"] = context["opp_allows"][position][stat]
        for g in GROUPS:
            f[f"pos_{g}"] = float(position == g)
        return f


def vector(features: dict) -> list[float]:
    return [np.nan if features.get(name) is None else float(features[name]) for name in FEATURES]


def distribution(mu: float, alpha: float):
    mu = max(mu, 0.02)
    return nbinom(1 / alpha, 1 / (1 + alpha * mu))


def over_probability(mu: float, alpha: float, line: float) -> float:
    return float(distribution(mu, alpha).sf(math.floor(line)))


@lru_cache(maxsize=2)
def _load(path, modified):
    import lightgbm as lgb
    body = json.loads(path.read_text(encoding="utf-8"))
    if body.get("version") != VERSION:
        return None
    return {market: {**spec, "booster": lgb.Booster(model_str=spec["model"])}
            for market, spec in body["markets"].items() if spec.get("promoted")}


def promoted() -> dict:
    if os.getenv("SCORPRED_TESTING") == "1" or os.getenv("SCORPRED_PROPS_TRAINING") == "1":
        return {}
    path = model_dir() / ARTIFACT
    if not path.exists():
        return {}
    try:
        return _load(path, path.stat().st_mtime_ns) or {}
    except (OSError, ValueError, ImportError):
        return {}


def predict(models: dict, market: str, features: dict) -> float:
    return float(models[market]["booster"].predict(np.array([vector(features)]))[0])


def apply(result: dict, home_id, away_id, season: int, tipoff, event_id=None) -> dict:
    """Reprice the rule model's promoted markets with the learned model, in place.

    Players, lines and records stay the rule model's, so every eligibility guard still
    holds. Without a promoted artifact, or when its inputs cannot be read, the rule
    model's markets are returned unchanged.
    """
    models = promoted()
    if not models or not result.get("players"):
        return result
    from services import nba_history
    home_id, away_id = str(home_id), str(away_id)
    # Both teams' latest games are fetched; every other stored game supplies league-wide
    # history (as in training), so players who changed teams keep their full record.
    games = {}
    for team_id in (home_id, away_id):
        for game in nba_history.team_games(team_id, (season - 1, season), tipoff):
            games[game["id"]] = game
    for game in nba_history.cached_games((season - 1, season), tipoff):
        games.setdefault(game["id"], game)
    history = History()
    for game in sorted(games.values(), key=lambda g: g["date"]):
        history.update(game)
    context = nba_history.upcoming(event_id) if event_id else {"spreads": {}, "total": None, "out": {}}
    result.setdefault('_research_inputs',{})['learned_parameters']={key:{k:v for k,v in spec.items() if k!='booster'} for key,spec in models.items()}
    result['_research_inputs']['learned_features']={}
    result['game_environment']=context
    when = tipoff.isoformat() if hasattr(tipoff, "isoformat") else str(tipoff)
    upcoming = {"id": f"upcoming-{event_id or home_id + away_id}", "date": when, "season": season, "season_type": 2,
                "total": context["total"],
                "teams": {team_id: {"home": team_id == home_id, "opponent": other, "players": [],
                                    "spread": context["spreads"].get(team_id), "out": context["out"].get(team_id, [])}
                          for team_id, other in ((home_id, away_id), (away_id, home_id))}}
    for player in result["players"]:
        features = history.features(str(player["id"]), str(player["team"]), upcoming)
        if features is None:
            continue
        result['_research_inputs']['learned_features'][str(player['id'])]={key:(float(value) if isinstance(value,(int,float)) and math.isfinite(value) else None) for key,value in features.items()}
        for market in player["markets"]:
            spec = models.get(market["market"])
            if spec is None:
                continue
            mu = predict(models, market["market"], features)
            dist = distribution(mu, spec["alpha"])
            over = float(dist.sf(math.floor(market["line"])))
            record_over = market["record"] if market["side"] == "over" else 1 - market["record"]
            side = "over" if over >= record_over else "under"
            market.pop("calibration", None)  # fitted on the rule model's probabilities
            if market.get('matchup_context'):
                market['matchup_context']['final_projection_source'] = VERSION+'; rule multiplier shown for context only, not reapplied'
            market.update(projection=round(mu, 2), side=side, probability=round(over if side == "over" else 1 - over, 4),
                          record=round(record_over if side == "over" else 1 - record_over, 4), model=VERSION,
                          ladder={"start": 0, "over": [round(float(p), 4) for p in dist.sf(np.arange(int(mu * 2.5) + 8))]})
    return result


def warm() -> None:
    """Load the promoted models and stored box scores before the first props request."""
    try:
        if promoted():
            from datetime import UTC
            from services import nba_history
            nba_history.cached_games((), datetime.now(UTC))
    except Exception:  # warming is an optimisation; the request path loads on demand
        pass


def pick_shrink() -> float | None:
    path = model_dir() / ARTIFACT
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("pick_shrink") if path.exists() else None
    except (OSError, ValueError):
        return None
