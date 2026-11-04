"""Versioned, pregame-only winner research features over the existing store.

Recompute metrics from raw vintages: their later aggregation/ingestion time is
not evidence that a corrected historical source existed before kickoff.
"""
import hashlib
import json
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean, pstdev

from services import nfl_analytics as analytics
from services import nflverse_store as store
from services.nfl_catalog import canonical_team
from services.nflverse_provider import number

FEATURE_VERSION = "nfl-winner-features-v1"
WINDOWS = (3, 5, 8, "std")
OFFENSE = ("epa_per_play", "passing_epa_per_dropback", "rushing_epa_per_play", "success_rate",
           "yards_per_play", "explosive_play_rate", "third_down_conversion_rate", "redzone_td_rate",
           "turnover_play_rate", "sack_rate")
DEFENSE = tuple(name for name in OFFENSE if name not in {"yards_per_play", "turnover_play_rate"})
QB = ("passing_epa_per_dropback", "completion_percentage", "interception_rate", "sack_rate",
      "epa_stability")
CONTEXT = ("rest_days", "short_week", "bye_week", "win_rate", "strength_of_schedule", "adjusted_defensive_epa", "qb_observed_season_appearances")
DIFFERENCES = tuple(f"diff_w{w}_{kind}_{name}" for w in WINDOWS
                    for kind, names in (("off", OFFENSE), ("def", DEFENSE), ("qb", QB)) for name in names)
FEATURE_NAMES = ("home_field",) + tuple(f"diff_{name}" for name in CONTEXT) + DIFFERENCES
SCHEMA = hashlib.sha256(json.dumps([FEATURE_VERSION, FEATURE_NAMES]).encode()).hexdigest()
SOURCE_DATASETS = ("schedules", "pbp")


def aware(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timestamps require an explicit timezone")
    return parsed.astimezone(UTC)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class Vintages:
    """Index source observations once per season; no downloads or source writes."""
    def __init__(self, dataset, season, path=None):
        self.index = defaultdict(lambda: ([], []))
        versions = store.versions(dataset, season=season, path=path)
        self.fingerprint = digest(versions)
        for version in versions:
            row = version["payload"]
            key = store.record_key(dataset, season, row)
            stamps, rows = self.index[key]
            stamps.append(aware(version["observed_at"]))
            rows.append(row)

    def snapshot(self, cutoff):
        result = []
        for stamps, rows in self.index.values():
            position = bisect_right(stamps, cutoff) - 1
            if position >= 0:
                result.append({**rows[position], "_observed_at": stamps[position].isoformat()})
        return result


def outcome(game):
    home, away = number(game.get("home_score")), number(game.get("away_score"))
    if home is None or away is None or min(home, away) < 0:
        return None
    return "tie" if home == away else "home" if home > away else "away"


def pool(rows, side, names):
    metrics = analytics.aggregate(rows, side)
    return {name: metrics.get(name, {}).get("value") for name in names}


class FeatureBuilder:
    def __init__(self, season, *, path=None, lead_minutes=60, min_games=3):
        if type(lead_minutes) is not int or lead_minutes < 1 or type(min_games) is not int or min_games < 1:
            raise ValueError("Prediction lead and minimum history must be positive integers")
        self.season, self.path = season, path
        self.lead_minutes, self.min_games = lead_minutes, min_games
        self.schedules = Vintages("schedules", season, path)
        self.pbp = None  # Missing old schedule vintages do not require loading PBP.
        self.integrity = {d: store.observation_integrity(d, season, path=path) for d in SOURCE_DATASETS}

    def schedule_at(self, cutoff, target):
        return self.schedules.snapshot(cutoff)

    def plays_at(self, cutoff, prior):
        if self.pbp is None:
            self.pbp = Vintages("pbp", self.season, self.path)
        return [row for row in self.pbp.snapshot(cutoff) if row["game_id"] in prior]

    def required_history(self, histories, sides):
        if any(len(histories[team]) < self.min_games for team in sides.values()):
            return "insufficient_prior_pbp_games"
        if any(not any(r["off"]["epa_per_play"]["value"] is not None for r in histories[t]) for t in sides.values()):
            return "missing_required_epa"
        return None

    def histories(self, prior, observed_plays):
        plays = analytics.scrimmage_plays(observed_plays)
        groups, histories, quarterback_games = defaultdict(list), defaultdict(list), defaultdict(list)
        for row in plays:
            groups[(row["game_id"], canonical_team(row["posteam"]))].append(row)
        for game in sorted(prior.values(), key=lambda row: (analytics.kickoff(row), row["game_id"])):
            for side, other in (("home", "away"), ("away", "home")):
                team, opponent = canonical_team(game[f"{side}_team"]), canonical_team(game[f"{other}_team"])
                own, against = groups[(game["game_id"], team)], groups[(game["game_id"], opponent)]
                if not own:
                    continue
                quarterbacks = defaultdict(list)
                for row in own:
                    if row.get("passer_player_id") and analytics.is_dropback(row):
                        quarterbacks[row["passer_player_id"]].append(row)
                qb_stats = {player: {"metrics": analytics.quarterback_metrics(rows), "dropbacks": len(rows)}
                            for player, rows in quarterbacks.items()}
                record = {"game_id": game["game_id"], "kickoff": analytics.kickoff(game).isoformat(), "opponent": opponent,
                          "off": analytics.offense_metrics(own), "def": analytics.offense_metrics(against), "qb": qb_stats,
                          "win": 1 if outcome(game) == side else .5 if outcome(game) == "tie" else 0}
                histories[team].append(record)
                for player in qb_stats:
                    quarterback_games[player].append(record)
        return histories, quarterback_games

    def build(self, target, *, as_of=None):
        from services.nfl_holdout import reject
        reject([target["game_id"]])
        start = analytics.kickoff(target)
        if start is None:
            return None, "unknown_kickoff"
        scheduled_cutoff = start - timedelta(minutes=self.lead_minutes)
        cutoff = aware(as_of) if as_of is not None else scheduled_cutoff
        if cutoff > scheduled_cutoff:
            raise ValueError("Feature cutoff must respect the configured pregame lead")
        schedule = self.schedule_at(cutoff, target)
        known = next((row for row in schedule if row["game_id"] == target["game_id"]), None)
        if known is None:
            return None, "target_schedule_not_observed"
        if not all(v["tracked"] for v in self.integrity.values()):
            return None, "untracked_current_source_writer"
        if (analytics.kickoff(known) != start or canonical_team(known["home_team"]) != canonical_team(target["home_team"])
                or canonical_team(known["away_team"]) != canonical_team(target["away_team"])):
            return None, "schedule_changed_after_cutoff"
        prior = {row["game_id"]: row for row in schedule if row["game_id"] != target["game_id"] and analytics.completed(row, cutoff)}
        observed_plays = self.plays_at(cutoff, prior)
        histories, quarterback_games = self.histories(prior, observed_plays)
        sides = {side: canonical_team(known[f"{side}_team"]) for side in ("home", "away")}
        reason = self.required_history(histories, sides)
        if reason:
            return None, reason
        features = {"home_field": float(known.get("location") != "Neutral")}
        descriptive, used_games, qbs = {}, set(), {}
        for side, team in sides.items():
            history = histories[team]
            used_games.update(r["game_id"] for r in history)
            # A historical fallback, not an assertion about the upcoming starter.
            candidates = history[-1]["qb"]
            qb = max(sorted(candidates), key=lambda p: candidates[p]["dropbacks"]) if candidates else None
            qbs[side] = {"player_id": qb, "basis": "last_observed_game_primary_passer", "starter_confirmed": False}
            rest = (start - aware(history[-1]["kickoff"])).total_seconds() / 86400
            opponent_win_rates, adjusted = [], []
            for record in history[-8:]:
                other_games = [r for r in histories[record["opponent"]] if r["game_id"] != record["game_id"]]
                if other_games:
                    opponent_win_rates.append(mean(r["win"] for r in other_games))
                    baseline = pool(other_games, "off", ("epa_per_play",))["epa_per_play"]
                    allowed = record["def"]["epa_per_play"]["value"]
                    if baseline is not None and allowed is not None:
                        adjusted.append(allowed - baseline)
            values = {"rest_days": rest, "short_week": float(rest < 6), "bye_week": float(rest >= 12),
                      "win_rate": mean(r["win"] for r in history[-3:]),
                      "qb_observed_season_appearances": len(quarterback_games[qb]) if qb else None,
                      "strength_of_schedule": mean(opponent_win_rates) if opponent_win_rates else None,
                      "adjusted_defensive_epa": mean(adjusted) if adjusted else None}
            for window in WINDOWS:
                recent = history if window == "std" else history[-window:]
                for kind, names in (("off", OFFENSE), ("def", DEFENSE)):
                    values.update({f"w{window}_{kind}_{name}": value for name, value in pool(recent, kind, names).items()})
                qb_recent = [{"metrics": r["qb"][qb]["metrics"]} for r in recent if qb in r["qb"]] if qb else []
                measured = pool(qb_recent, "metrics", QB[:4])
                rates = [r["metrics"]["passing_epa_per_dropback"]["value"] for r in qb_recent
                         if r["metrics"]["passing_epa_per_dropback"]["value"] is not None]
                measured.update(epa_stability=pstdev(rates) if len(rates) >= 2 else None)
                values.update({f"w{window}_qb_{name}": value for name, value in measured.items()})
            descriptive[side] = values
        for name in CONTEXT + tuple(f"w{w}_{kind}_{name}" for w in WINDOWS for kind, names in (("off", OFFENSE), ("def", DEFENSE), ("qb", QB)) for name in names):
            home, away = descriptive["home"][name], descriptive["away"][name]
            features[f"diff_{name}"] = home - away if home is not None and away is not None else None
        matchup = {}
        for side, other in (("home", "away"), ("away", "home")):
            offense = descriptive[side]["w8_off_epa_per_play"]
            defense = descriptive[other]["w8_def_epa_per_play"]
            matchup[f"{side}_offense_minus_{other}_defense_epa"] = offense - defense if offense is not None and defense is not None else None
        # Only observations involved in allowed prior games or the target schedule
        # are provenance. Future outcomes never enter the predictors.
        observed = [aware(known["_observed_at"])] + [aware(r["_observed_at"]) for r in schedule if r["game_id"] in prior]
        observed += [aware(r["_observed_at"]) for r in observed_plays]
        observed_max = max(observed)
        present = sum(features[name] is not None for name in FEATURE_NAMES)
        return {"game_id": target["game_id"], "season": self.season, "week": int(target["week"]),
                "kickoff": start.isoformat(), "prediction_cutoff": cutoff.isoformat(),
                "home_team": sides["home"], "away_team": sides["away"], "feature_version": FEATURE_VERSION, "evidence_class": "A",
                "features": features, "team_features": descriptive, "matchup": matchup, "quarterbacks": qbs,
                "quality": {"present": present, "total": len(FEATURE_NAMES), "fraction": present / len(FEATURE_NAMES),
                            "home_games": len(histories[sides["home"]]), "away_games": len(histories[sides["away"]])},
                "provenance": {"evidence_class": "A", "historically_reconstructed": False,
                               "event_time": start.isoformat(), "publication_time": None,
                               "source_observed_max": observed_max.isoformat(), "ingestion_time_basis": "stored observation receipts",
                               "observation_and_source_ingestion_coincide": True, "publication_history_verified": False,
                               "availability_rule": "observed_by_cutoff_and_completed_24h_prior", "history_game_ids": sorted(used_games),
                               "history_max_kickoff": max(r["kickoff"] for t in sides.values() for r in histories[t]),
                               "no_injury_roster_travel_inputs": True}}, None


def build_dataset(seasons, *, path=None, lead_minutes=60, min_games=3, now=None):
    now = aware(now or datetime.now(UTC))
    seasons = sorted(set(seasons))
    if not seasons or any(type(s) is not int or not 1999 <= s <= now.year for s in seasons):
        raise ValueError("Select NFL seasons from 1999 through the current year")
    features, labels, audit, sources = [], {}, [], {}
    from services.nfl_reliability import sealed_protocol
    protocol = sealed_protocol()
    reserved = set(protocol["holdout"]["game_ids"]) if protocol else set()
    from services.nfl_winner_baselines import elo_probability
    schedule_archives = {year: Vintages("schedules", year, path) for year in sorted(
        {r["season"] for r in store.metadata(path=path) if r["dataset"] == "schedules" and r["season"] <= max(seasons)})}
    for season in sorted(set(seasons)):
        builder = FeatureBuilder(season, path=path, lead_minutes=lead_minutes, min_games=min_games)
        schedules = store.records("schedules", season, path=path)
        if len({r["game_id"] for r in schedules}) != len(schedules):
            raise ValueError("Duplicate game IDs")
        for game in sorted(schedules, key=lambda r: (str(r.get("gameday")), r["game_id"])):
            if game["game_id"] in reserved:
                continue
            reason, row = None, None
            result = outcome(game)
            if not analytics.completed(game, now):
                reason = "not_completed_with_24h_confirmation"
            elif result == "tie":
                reason = "tie_excluded_binary_conditional_on_no_tie"
            else:
                row, reason = builder.build(game)
            start = analytics.kickoff(game)
            cutoff = start - timedelta(minutes=lead_minutes) if start else None
            stamps, _ = builder.schedules.index[store.record_key("schedules", season, game)]
            audit.append({"game_id": game["game_id"], "season": season, "week": game.get("week"),
                          "kickoff": start.isoformat() if start else None,
                          "prediction_cutoff": cutoff.isoformat() if cutoff else None,
                          "evidence_class": "A", "eligible": row is not None, "reason": reason,
                          "rejection_reasons": [reason] if reason else [],
                          "target_schedule_first_observed_at": min(stamps).isoformat() if stamps else None,
                          "target_schedule_observed_by_cutoff": bool(stamps and cutoff and min(stamps) <= cutoff),
                          "source_publication_time": None, "publication_history_verified": False,
                          "first_failure_masks_later_guards": reason == "target_schedule_not_observed"})
            if row is not None:
                cutoff = aware(row["prediction_cutoff"])
                known_schedule = [g for year, index in schedule_archives.items() if year <= season for g in index.snapshot(cutoff)]
                elo_target = {**game, "location": "Home" if row["features"]["home_field"] else "Neutral"}
                row["baselines"] = {"elo": elo_probability(elo_target, known_schedule, cutoff)}
                versions = [v for v in builder.schedules.index[store.record_key("schedules", season, game)][1]]
                times = builder.schedules.index[store.record_key("schedules", season, game)][0]
                receipt = min(t for t, v in zip(times, versions) if outcome(v) == result)
                labels[game["game_id"]] = {"home_win": int(result == "home"), "outcome": result,
                                          "home_score": number(game["home_score"]), "away_score": number(game["away_score"]),
                                          "first_outcome_observed_at": receipt.isoformat(),
                                          "usable_after": max(receipt, analytics.kickoff(game) + timedelta(hours=24)).isoformat()}
                features.append(row)
        sources[str(season)] = {"schedules": builder.schedules.fingerprint,
                                "pbp": builder.pbp.fingerprint if builder.pbp else None,
                                "observation_integrity": builder.integrity}
    features.sort(key=lambda row: (row["prediction_cutoff"], row["game_id"]))
    counts = {str(s): {"total": sum(a["season"] == s for a in audit),
                       "eligible": sum(a["season"] == s and a["eligible"] for a in audit),
                       "exclusions": dict(Counter(a["reason"] for a in audit if a["season"] == s and not a["eligible"]))}
              for s in sorted(set(seasons))}
    sources["elo_schedule_archives"] = {str(year): index.fingerprint for year, index in schedule_archives.items()}
    core = {"feature_version": FEATURE_VERSION, "schema": SCHEMA, "feature_names": list(FEATURE_NAMES), "evidence_class": "A",
            "config": {"lead_minutes": lead_minutes, "min_games": min_games, "completion_lag_hours": 24,
                       "early_season": "minimum_three_same_season_pbp_games_no_carryover", "tie_policy": "exclude_condition_on_no_tie"},
            "rows": features, "labels": labels, "audit": audit, "coverage": counts, "source_fingerprints": sources}
    return {**core, "dataset_fingerprint": digest(core), "generated_at": now.isoformat()}
