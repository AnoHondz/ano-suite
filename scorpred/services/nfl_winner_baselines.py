"""Deterministic research baselines; no production rating state is modified."""
import math
from dataclasses import asdict, dataclass
from datetime import datetime

from services import nfl_analytics as analytics
from services.nfl_catalog import canonical_team


@dataclass(frozen=True)
class EloConfig:
    initial: float = 1500.0
    home_adjustment: float = 55.0
    k: float = 20.0
    season_retention: float = .75

    def __post_init__(self):
        if (not all(math.isfinite(v) for v in (self.initial, self.home_adjustment, self.k, self.season_retention))
                or not 0 <= self.season_retention <= 1 or not 0 < self.k <= 100):
            raise ValueError("Invalid Elo configuration")


def elo_probability(target, known_schedule, cutoff, config=None):
    config = config or EloConfig()
    games = [g for g in known_schedule if g["game_id"] != target["game_id"] and analytics.completed(g, cutoff)
             and (not g.get("_observed_at") or datetime.fromisoformat(g["_observed_at"]) <= cutoff)]
    games.sort(key=lambda g: (analytics.kickoff(g), g["game_id"]))
    ratings, previous_season = {}, None
    for game in games:
        season = int(game["season"])
        if previous_season is not None and season != previous_season:
            retention = config.season_retention ** (season - previous_season)
            ratings = {t: config.initial + retention * (v - config.initial) for t, v in ratings.items()}
        previous_season = season
        home, away = canonical_team(game["home_team"]), canonical_team(game["away_team"])
        h, a = ratings.get(home, config.initial), ratings.get(away, config.initial)
        advantage = config.home_adjustment if game.get("location") != "Neutral" else 0
        probability = 1 / (1 + 10 ** ((a - h - advantage) / 400))
        score = .5 if float(game["home_score"]) == float(game["away_score"]) else float(float(game["home_score"]) > float(game["away_score"]))
        delta = config.k * (score - probability)
        ratings[home], ratings[away] = h + delta, a - delta
    if previous_season is not None and previous_season < int(target["season"]):
        retention = config.season_retention ** (int(target["season"]) - previous_season)
        ratings = {t: config.initial + retention * (v - config.initial) for t, v in ratings.items()}
    home, away = canonical_team(target["home_team"]), canonical_team(target["away_team"])
    h, a = ratings.get(home, config.initial), ratings.get(away, config.initial)
    advantage = config.home_adjustment if target.get("location") != "Neutral" else 0
    return {"home_probability": 1 / (1 + 10 ** ((a - h - advantage) / 400)),
            "home_rating": h, "away_rating": a, "history_games": len(games), "config": asdict(config),
            "tie_update": "half_point_context_only_binary_labels_exclude_ties"}
