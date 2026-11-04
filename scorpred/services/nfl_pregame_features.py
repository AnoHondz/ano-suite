"""Observed pregame inputs mapped to the existing Class B logistic semantics.

Training evidence stays B. New input evidence is A: actual immutable receipts,
not reconstructed publication dates. No target scores or sealed scores are read.
"""
from collections import defaultdict
from datetime import timedelta
from statistics import mean

import nfl_client
from services import nfl_analytics as analytics
from services import nflverse_store as store
from services.nfl_catalog import canonical_team
from services.nfl_historical_recovery import RecoveryPolicy, availability, event_values
from services.nfl_winner_baselines import elo_probability
from services.nfl_winner_features import aware, digest, outcome

VERSION = "nfl-pregame-matched-three-v1"
LOGISTIC_NAMES = ("home_field", "diff_win_rate", "diff_rest_days")
ELO_NAMES = ("home_field", "home_rating", "away_rating")
POLICY = {"publication_lag_hours": 168, "completion_bound_hours": 12, "min_games": 3,
          "lead_minutes": 60, "schedule_max_age_seconds": 300, "pbp_max_age_seconds": 21600,
          "rest_policy": "missing_if_a_more_recent_game_is_not_publication_eligible",
          "rest_uncertainty_guard": "mask_for_any_more_recent_potentially_played_schedule_including_sealed_games_without_reading_scores",
          "minimum_history": "three_same_season_pbp_games_with_measured_event_yards",
          "excluded_inputs": ["epa", "success", "quarterback", "injury", "odds", "weather"],
          "holdout_history": "exclude_every_reserved_game_even_after_its_result_is_known"}
SCHEDULE_FIELDS = ("game_id", "season", "week", "game_type", "gameday", "gametime", "home_team", "away_team",
                   "location", "status", "game_status", "state", "status_name", "game_end_timestamp", "source_published_at", "publication_timestamp", "published_at")


def observed_schedules(season, cutoff, sealed_ids, *, path=None, version_ceiling=None):
    """SQL projection omits every future/same-day/sealed result before decoding."""
    cutoff = aware(cutoff)
    placeholders = ",".join("?" for _ in sealed_ids) or "''"
    projection = ",".join(f"'{f}',json_extract(payload,'$.{f}')" for f in SCHEDULE_FIELDS)
    day = cutoff.astimezone(nfl_client.EASTERN).date().isoformat()
    score = f"CASE WHEN game_id NOT IN ({placeholders}) AND json_extract(payload,'$.gameday')<? THEN json_extract(payload,'$.{{}}') END"
    sql = ("SELECT json_object(" + projection + ",'home_score'," + score.format("home_score")
           + ",'away_score'," + score.format("away_score") + ") AS payload,observed_at FROM "
           "(SELECT record_key AS game_id,payload,observed_at,ROW_NUMBER() OVER (PARTITION BY record_key ORDER BY observed_at DESC) AS rank "
           "FROM record_versions WHERE dataset='schedules' AND season=? AND observed_at<=? AND rowid<=?) WHERE rank=1 ORDER BY game_id")
    import json
    params = [*sealed_ids, day, *sealed_ids, day, season, cutoff.isoformat(), version_ceiling if version_ceiling is not None else 2**63 - 1]
    with store.connection(path) as db:
        return [{**json.loads(r[0]), "_observed_at": r[1]} for r in db.execute(sql, params)]


def mapped_values(target, eligible, histories, played):
    """Exactly the three approved inputs; physical rest is not an older stats gap."""
    teams = [canonical_team(target[f"{s}_team"]) for s in ("home", "away")]
    parts = target["game_id"].split("_")
    if (teams[0] == teams[1] or len(parts) != 4
            or [canonical_team(parts[3]), canonical_team(parts[2])] != teams
            or parts[0] != str(target["season"]) or int(parts[1]) != int(target["week"])):
        raise ValueError("invalid_team_mapping")
    rates, rests, dependencies = [], [], set()
    for team in teams:
        history = sorted([g for g in eligible if team in histories[g["game_id"]]], key=lambda g: (analytics.kickoff(g), g["game_id"]))
        if len(history) < POLICY["min_games"]:
            raise ValueError("insufficient_observed_pbp_history")
        if not any(histories[g["game_id"]][team] for g in history):
            raise ValueError("missing_required_event_statistics")
        rates.append(mean(.5 if outcome(g) == "tie" else float((outcome(g) == "home") == (canonical_team(g["home_team"]) == team)) for g in history[-3:]))
        latest = analytics.kickoff(history[-1])
        intervening = any(team in {canonical_team(g["home_team"]), canonical_team(g["away_team"])} and analytics.kickoff(g) > latest for g in played)
        rests.append(None if intervening else (analytics.kickoff(target) - latest).total_seconds() / 86400)
        dependencies.update(g["game_id"] for g in history)
    return {"home_field": float(target.get("location") != "Neutral"), "diff_win_rate": rates[0] - rates[1],
            "diff_rest_days": rests[0] - rests[1] if None not in rests else None}, sorted(dependencies)


def receipt_status(season, cutoff, target_id, *, path=None, receipt_ceiling=None):
    fresh = {}
    with store.connection(path) as db:
        for dataset, age in (("schedules", POLICY["schedule_max_age_seconds"]), ("pbp", POLICY["pbp_max_age_seconds"])):
            r = db.execute("SELECT * FROM collection_receipts WHERE dataset=? AND season=? AND attempted_at<=? "
                           "AND (collected_at<=? OR status!='success') AND id<=? ORDER BY id DESC LIMIT 1",
                           (dataset, season, cutoff.isoformat(), cutoff.isoformat(), receipt_ceiling if receipt_ceiling is not None else 2**63 - 1)).fetchone()
            if r is None or r["status"] != "success" or not r["collected_at"]:
                raise ValueError(f"missing_successful_{dataset}_receipt")
            elapsed = (cutoff - aware(r["collected_at"])).total_seconds()
            if not 0 <= elapsed <= age:
                raise ValueError(f"stale_{dataset}_receipt")
            if dataset == "schedules" and not db.execute("SELECT 1 FROM collection_games WHERE receipt_id=? AND game_id=?", (r["id"], target_id)).fetchone():
                raise ValueError("target_not_in_schedule_receipt")
            fresh[dataset] = {"receipt_id": r["id"], "collected_at": r["collected_at"], "age_seconds": elapsed,
                              "maximum_age_seconds": age, "content_sha256": r["content_sha256"], "status": "fresh"}
    return fresh


def build_inputs(target, elo_artifact, sealed_ids, *, path=None, cutoff, source_ceiling=None):
    cutoff = aware(cutoff)
    start = analytics.kickoff(target)
    if start is None or cutoff > start - timedelta(minutes=POLICY["lead_minutes"]):
        raise ValueError("late_or_unknown_kickoff")
    blocked_states = {"in", "in_progress", "postponed", "suspended", "cancelled", "canceled", "final", "post"}
    if any(str(target.get(k, "")).lower().removeprefix("status_") in blocked_states for k in ("status", "game_status", "state", "status_name")):
        raise ValueError("unsupported_target_game_state")
    season = int(target["season"])
    if source_ceiling is None:
        # Pin the committed database snapshot as well as observation time. A pull
        # observed earlier but committed later cannot rewrite a saved replay.
        with store.connection(path) as db:
            db.execute("BEGIN")
            source_ceiling = {"versions": db.execute("SELECT COALESCE(MAX(rowid),0) FROM record_versions").fetchone()[0],
                              "receipts": db.execute("SELECT COALESCE(MAX(id),0) FROM collection_receipts").fetchone()[0]}
    if set(source_ceiling) != {"versions", "receipts"} or any(type(v) is not int or v < 1 for v in source_ceiling.values()):
        raise ValueError("invalid_source_snapshot_boundary")
    for dataset in ("schedules", "pbp"):
        if not store.observation_integrity(dataset, season, path=path)["tracked"]:
            raise ValueError("untracked_source_writer")
    freshness = receipt_status(season, cutoff, target["game_id"], path=path, receipt_ceiling=source_ceiling["receipts"])
    schedule = observed_schedules(season, cutoff, sealed_ids, path=path, version_ceiling=source_ceiling["versions"])
    known = next((g for g in schedule if g["game_id"] == target["game_id"]), None)
    if known is None or any(known.get(f) != target.get(f) for f in ("home_team", "away_team", "gameday", "gametime", "location")):
        raise ValueError("target_schedule_mapping_mismatch")
    played = [g for g in schedule if g["game_id"] not in sealed_ids and g["game_id"] != target["game_id"] and analytics.completed(g, cutoff)]
    rest_context = [g for g in schedule if g["game_id"] != target["game_id"] and analytics.kickoff(g) is not None
                    and analytics.kickoff(g) + timedelta(hours=24) < cutoff
                    and not any(str(g.get(k, "")).lower().removeprefix("status_") in {"cancelled", "canceled"}
                                for k in ("status", "game_status", "state", "status_name"))]
    eligible = []
    for g in played:
        detail, reason = availability(g, RecoveryPolicy())
        if not reason and aware(detail["available_after_assumed"]) < cutoff:
            eligible.append(g)
    prior_ids = {g["game_id"] for g in eligible}
    import json
    with store.connection(path) as db:
        marks = ",".join("?" for _ in prior_ids) or "''"
        observed = [{**json.loads(p[0]), "_observed_at": p[1]} for p in db.execute(
            "SELECT payload,observed_at FROM (SELECT record_key,payload,observed_at,ROW_NUMBER() OVER "
            "(PARTITION BY record_key ORDER BY observed_at DESC) AS rank FROM record_versions "
            f"WHERE dataset='pbp' AND season=? AND observed_at<=? AND rowid<=? AND json_extract(payload,'$.game_id') IN ({marks})) WHERE rank=1 ORDER BY record_key",
            (season, cutoff.isoformat(), source_ceiling["versions"], *sorted(prior_ids)))]
    sanitized = [{**event_values(p), "_observed_at": p["_observed_at"]} for p in observed]
    histories = defaultdict(dict)
    eligible_by_id = {g["game_id"]: g for g in eligible}
    for p in analytics.scrimmage_plays(sanitized):
        team = canonical_team(p["posteam"])
        g = eligible_by_id[p["game_id"]]
        if team not in {canonical_team(g["home_team"]), canonical_team(g["away_team"])}:
            raise ValueError("pbp_team_mapping_mismatch")
        histories[p["game_id"]][team] = histories[p["game_id"]].get(team, False) or p.get("yards_gained") is not None
    values, dependencies = mapped_values(known, eligible, histories, rest_context)
    from services.nfl_winner_baselines import EloConfig
    elo = elo_probability(known, elo_artifact["historical_schedule"] + eligible, cutoff, EloConfig(**elo_artifact["config"]))
    stamps = [known["_observed_at"]] + [g["_observed_at"] for g in eligible + rest_context] + [p["_observed_at"] for p in sanitized]
    if max(aware(s) for s in stamps) > cutoff:
        raise ValueError("future_source_observation")
    return {"game_id": target["game_id"], "season": season, "home_team": canonical_team(known["home_team"]),
            "away_team": canonical_team(known["away_team"]), "scheduled_kickoff": start.isoformat(),
            "evidence_cutoff": cutoff.isoformat(), "evidence_class": "A", "training_evidence_class": "B",
            "feature_version": VERSION, "feature_names": list(LOGISTIC_NAMES), "features": values,
            "elo_features": {"home_field": values["home_field"], "home_rating": elo["home_rating"], "away_rating": elo["away_rating"]},
            "freshness": freshness, "missing_features": [n for n in LOGISTIC_NAMES if values[n] is None],
            "source_observed_max": max(aware(s) for s in stamps).isoformat(), "source_game_ids": dependencies,
            "source_snapshot_boundary": source_ceiling,
            "rest_schedule_context_ids": sorted(g["game_id"] for g in rest_context),
            "elo_source_game_ids": sorted(g["game_id"] for g in eligible),
            "source_fingerprints": {"schedules": digest([known] + eligible + rest_context), "pbp": digest(sanitized)},
            "no_holdout_history": not (prior_ids & set(sealed_ids)), "policy": POLICY}
