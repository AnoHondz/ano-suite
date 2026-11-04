"""Pregame-only, rolling NFL features from the local nflverse store.

Only prior completed games are used. Source files contain later stat corrections;
this is not an archive of the versions that were available on each game day.
"""
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean
from bisect import bisect_right

from services import nflverse_provider as provider
from services import nflverse_store as store

TEAM_FEATURES = ["points_for", "points_against", "win_rate", "off_epa_per_play", "def_epa_per_play", "passing_cpoe", "giveaways"]
FEATURE_NAMES = [f"{side}_{name}" for side in ("home", "away") for name in TEAM_FEATURES] + ["neutral", "rest_difference", "postseason", "home_samples", "away_samples"]
SCHEMA = hashlib.sha256(json.dumps(FEATURE_NAMES).encode()).hexdigest()


def research_features(game_id, **kwargs):
    """Expanded descriptive research; never appended to FEATURE_NAMES."""
    from services.nfl_analytics import matchup_features
    return matchup_features(game_id, **kwargs)


def average(values):
    finite = [value for value in values if value is not None]
    return mean(finite) if finite else None


def efficiency(stats):
    fields = [provider.number(stats.get(name)) for name in ("passing_epa", "rushing_epa", "attempts", "carries", "sacks_suffered")]
    if any(value is None for value in fields):
        return None
    passing, rushing, attempts, carries, sacks = fields
    plays = attempts + carries + sacks
    return (passing + rushing) / plays if plays > 0 else None


def build_features(*, path=None, now=None, min_games=3, lookback=8):
    now = now or datetime.now(UTC)
    from services.nfl_holdout import protocol
    reserved = set((protocol() or {}).get("holdout", {}).get("game_ids", []))
    teams = {row["team_abbr"]: row for row in store.records("teams", path=path)}
    games = [provider.normalize_game(row, teams, now) for row in store.records("schedules", path=path)]
    games = sorted([game for game in games if game], key=lambda game: (game["kickoff"], game["id"]))
    # A stat correction observed after a historical target cannot enter it.
    # Importing a historical archive today does not establish earlier availability.
    observations = {}
    for dataset in ("schedules", "team_stats"):
        index = defaultdict(lambda: ([], []))
        for version in store.versions(dataset, path=path):
            row = version["payload"]
            key = row["game_id"] if dataset == "schedules" else (row["game_id"], row["team"])
            stamps, payloads = index[key]
            stamps.append(datetime.fromisoformat(version["observed_at"]))
            payloads.append(row)
        observations[dataset] = index

    def known(dataset, key, cutoff):
        stamps, payloads = observations[dataset].get(key, ([], []))
        position = bisect_right(stamps, cutoff) - 1
        return payloads[position] if position >= 0 else None

    def prior_row(candidate, team, cutoff):
        raw = known("schedules", candidate["game_id"], cutoff)
        prior = provider.normalize_game(raw, teams, cutoff) if raw else None
        if not prior or not prior["completed"]:
            return None
        is_home = prior["home_id"] == team
        opponent_id = prior["away_id"] if is_home else prior["home_id"]
        own = known("team_stats", (candidate["game_id"], team), cutoff) or {}
        opponent = known("team_stats", (candidate["game_id"], opponent_id), cutoff) or {}
        scores = (prior["home_score"], prior["away_score"]) if is_home else (prior["away_score"], prior["home_score"])
        interceptions, fumbles = provider.number(own.get("passing_interceptions")), provider.number(own.get("fumbles_lost_total"))
        return {"kickoff": datetime.fromisoformat(prior["kickoff"]), "points_for": scores[0], "points_against": scores[1],
                "win_rate": 1 if scores[0] > scores[1] else .5 if scores[0] == scores[1] else 0,
                "off_epa_per_play": efficiency(own), "def_epa_per_play": efficiency(opponent),
                "passing_cpoe": provider.number(own.get("passing_cpoe")),
                "giveaways": interceptions + fumbles if interceptions is not None and fumbles is not None else None}

    history, output = defaultdict(list), []
    for game in games:
        if game["nflverse_id"] in reserved:
            continue
        kickoff = datetime.fromisoformat(game["kickoff"])
        cutoff = min(kickoff, now) - timedelta(hours=24)
        availability_cutoff = min(now, kickoff - timedelta(microseconds=1))
        target_version = known("schedules", game["nflverse_id"], availability_cutoff)
        target_context = provider.normalize_game(target_version, teams, availability_cutoff) if target_version else None
        target_known = (target_context and target_context["kickoff"] == game["kickoff"]
                        and target_context["home_id"] == game["home_id"] and target_context["away_id"] == game["away_id"])
        recent = {side: [known_row for row in history[(game["season"], game[f"{side}_id"]) ] if row["kickoff"] < cutoff
                         and (known_row := prior_row(row, game[f"{side}_id"], availability_cutoff)) is not None][-lookback:]
                  for side in ("home", "away")}
        if target_known and min(len(recent["home"]), len(recent["away"])) >= min_games:
            features = {}
            for side in ("home", "away"):
                for name in TEAM_FEATURES:
                    features[f"{side}_{name}"] = average([row[name] for row in recent[side]])
            features.update(neutral=int(target_context["neutral"]), postseason=int(target_context["season_type"] == 3),
                            rest_difference=min(21, (kickoff - recent["home"][-1]["kickoff"]).total_seconds() / 86400)
                            - min(21, (kickoff - recent["away"][-1]["kickoff"]).total_seconds() / 86400),
                            home_samples=len(recent["home"]), away_samples=len(recent["away"]))
            hs, aws = game["home_score"], game["away_score"]
            target = (0 if hs > aws else 1 if hs == aws else 2) if game["completed"] else None
            output.append({"game_id": game["nflverse_id"], "season": game["season"], "date": game["date"],
                           "kickoff": game["kickoff"], "feature_cutoff": cutoff.isoformat(),
                           "history_max_kickoff": max(row["kickoff"] for rows in recent.values() for row in rows).isoformat(),
                           "home_team": game["home_id"], "away_team": game["away_id"],
                           "target": target, "home_score": hs, "away_score": aws, **features})
        if not game["completed"]:
            continue
        for side in ("home", "away"):
            history[(game["season"], game[f"{side}_id"])].append({"kickoff": kickoff, "game_id": game["nflverse_id"]})
    return output
