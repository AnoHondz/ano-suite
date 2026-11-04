"""Explicit Class B event reconstruction. Never backdate a source observation.

Archived event counts are a conditional reconstruction, not proof of historical
publication. Model-derived EPA/success and all QB selection are unsupported.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from services import nfl_analytics as analytics
from services import nflverse_store as store
from services.nfl_catalog import canonical_team
from services.nfl_winner_baselines import elo_probability
from services.nfl_winner_features import (
    FEATURE_NAMES,
    FeatureBuilder,
    aware,
    digest,
    outcome,
)
from services.nflverse_provider import number

FEATURE_VERSION = "nfl-winner-reconstructed-events-v1"
SCHEMA = digest([FEATURE_VERSION, FEATURE_NAMES])
SUPPORTED_NAMES = tuple(n for n in FEATURE_NAMES if not any(s in n for s in ("epa", "success", "qb")))
PUBLICATION_FIELDS = ("source_published_at", "publication_timestamp", "published_at")
EVENT_FIELDS = ("game_id", "season", "week", "posteam", "defteam", "play_type", "no_play",
                "yards_gained", "pass_attempt", "rush_attempt", "qb_kneel", "qb_spike", "qb_dropback",
                "sack", "qb_scramble", "interception", "fumble_lost", "third_down_converted",
                "third_down_failed", "down", "yardline_100", "fixed_drive", "drive", "pass_touchdown",
                "rush_touchdown", "two_point_attempt")
TEXT_FIELDS = {"game_id", "posteam", "defteam", "play_type"}
ASSUMPTIONS = [
    "Class B only: finalized event counts assumed stable after the configured correction delay; unobserved revisions remain possible.",
    "No verified finish timestamp: scheduled kickoff plus the completion bound is an assumption, not actual completion.",
    "Final archived schedule identity/venue assumed known pregame; detected identity/time/venue revisions are excluded.",
    "Historical EPA model provenance, EPA-based success and announced starters are unverified and excluded.",
]


@dataclass(frozen=True)
class RecoveryPolicy:
    publication_lag_hours: int = 168
    completion_bound_hours: int = 12

    def __post_init__(self):
        if (type(self.publication_lag_hours) is not int or self.publication_lag_hours < 96
                or type(self.completion_bound_hours) is not int or self.completion_bound_hours < 12):
            raise ValueError("Recovery requires at least a 12-hour completion bound and 96-hour publication delay")


def publication(row):
    values = [aware(row[k]) for k in PUBLICATION_FIELDS if row.get(k)]
    return max(values) if values else None


def availability(game, policy):
    start = analytics.kickoff(game)
    if start is None or outcome(game) is None or game.get("game_type") not in {"REG", "WC", "DIV", "CON", "SB"}:
        return None, "unsupported_or_unfinished_event"
    if any(str(game.get(k, "")).lower() in {"suspended", "postponed", "cancelled", "canceled", "in_progress"}
           for k in ("status", "game_status", "state")):
        return None, "ambiguous_completion"
    finish = game.get("game_end_timestamp")
    try:
        bound = aware(finish) if finish else start + timedelta(hours=policy.completion_bound_hours)
        published = publication(game)
    except (ValueError, TypeError):
        return None, "invalid_publication_or_completion_timestamp"
    if bound <= start:
        return None, "invalid_completion_timestamp"
    available = bound + timedelta(hours=policy.publication_lag_hours)
    return {"completion_time": aware(finish).isoformat() if finish else None,
            "completion_upper_bound_assumed": None if finish else bound.isoformat(),
            "completion_basis": "reported_game_end_timestamp" if finish else "scheduled_kickoff_plus_configured_bound_assumption",
            "publication_time": published.isoformat() if published else None,
            "available_after_assumed": max(available, published).isoformat() if published else available.isoformat()}, None


def event_values(row):
    return {k: row.get(k) if k in TEXT_FIELDS else number(row.get(k)) for k in EVENT_FIELDS}


def schedule_identity(row):
    return [analytics.kickoff(row).isoformat() if analytics.kickoff(row) else None,
            canonical_team(row.get("home_team")), canonical_team(row.get("away_team")), row.get("location") == "Neutral"]


class ReconstructedBuilder(FeatureBuilder):
    def __init__(self, season, *, policy=None, now=None, **kwargs):
        super().__init__(season, **kwargs)
        self.policy, self.now = policy or RecoveryPolicy(), aware(now or datetime.now(UTC))
        self.games, self.blocked, self.details, self.play_receipts = {}, {}, {}, {}
        self.revision_counts = Counter()
        for game in store.records("schedules", season, path=self.path):
            game_id = game["game_id"]
            stamps, variants = self.schedules.index[store.record_key("schedules", season, game)]
            observed = [(s, v) for s, v in zip(stamps, variants, strict=True) if s <= self.now]
            if not observed:
                self.blocked[game_id] = "no_observed_archive_at_extraction"
                continue
            identities = {digest(schedule_identity(v)) for _, v in observed}
            scores = {(number(v.get("home_score")), number(v.get("away_score"))) for _, v in observed if outcome(v) is not None}
            # Scores before kickoff are omitted schedule fields, not revisions.
            if len(identities) > 1 or len(scores) > 1:
                self.blocked[game_id] = "retrospectively_revised_schedule_or_result"
                continue
            details, reason = availability(game, self.policy)
            if reason:
                self.blocked[game_id] = reason
                continue
            result_receipt = min(s for s, v in observed if outcome(v) == outcome(game))
            self.games[game_id] = {**game, "_observed_at": result_receipt.isoformat()}
            details["schedule_first_observed_at"] = min(s for s, _ in observed).isoformat()
            details["final_result_first_observed_at"] = result_receipt.isoformat()
            self.details[game_id] = details
        versions = store.versions("pbp", season=season, path=self.path)
        self.pbp_fingerprint = digest(versions)
        first, signatures, invalid = {}, {}, set()
        publications = defaultdict(list)
        for version in versions:
            if aware(version["observed_at"]) > self.now:
                continue
            row = version["payload"]
            game_id = row["game_id"]
            key = store.record_key("pbp", season, row)
            values = event_values(row)
            signature = digest(values)
            if key in signatures and signature != signatures[key]:
                invalid.add(game_id)
            signatures.setdefault(key, signature)
            first.setdefault(key, {**values, "_observed_at": version["observed_at"]})
            try:
                stamp = publication(row)
                if stamp:
                    publications[game_id].append(stamp)
            except (ValueError, TypeError):
                invalid.add(game_id)
        for game_id in invalid:
            self.blocked[game_id] = "retrospectively_revised_or_invalid_pbp"
        first_receipts = defaultdict(set)
        for row in first.values():
            first_receipts[row["game_id"]].add(row["_observed_at"])
        for game_id, stamps in first_receipts.items():
            if len(stamps) > 1:
                self.blocked[game_id] = "retrospectively_extended_pbp_snapshot"
        plays = []
        for row in first.values():
            game_id = row["game_id"]
            if game_id not in self.games or game_id in self.blocked:
                continue
            if publications[game_id]:
                details = self.details[game_id]
                details["publication_time"] = max(publications[game_id]).isoformat()
                details["available_after_assumed"] = max(aware(details["available_after_assumed"]), max(publications[game_id])).isoformat()
            plays.append(row)
            self.play_receipts[game_id] = max(self.play_receipts.get(game_id, row["_observed_at"]), row["_observed_at"])
        safe = {g: v for g, v in self.games.items() if g not in self.blocked}
        self.cached_histories, _ = super().histories(safe, plays)
        self.revision_counts.update(self.blocked.values())

    def schedule_at(self, cutoff, target):
        return [g for game_id, g in self.games.items() if game_id not in self.blocked
                and (game_id == target["game_id"] or aware(self.details[game_id]["available_after_assumed"]) < cutoff)]

    def plays_at(self, cutoff, prior):
        # Common assembly only needs receipt provenance here; metrics were
        # calculated once from sanitized raw event fields, never season totals.
        return [{"game_id": g, "_observed_at": self.play_receipts[g]} for g in prior if g in self.play_receipts]

    def histories(self, prior, observed_plays):
        return defaultdict(list, {t: [r for r in rows if r["game_id"] in prior]
                                  for t, rows in self.cached_histories.items()}), defaultdict(list)

    def required_history(self, histories, sides):
        if any(len(histories[t]) < self.min_games for t in sides.values()):
            return "insufficient_prior_pbp_games_after_publication_guard"
        if any(not any(r["off"]["yards_per_play"]["value"] is not None for r in histories[t]) for t in sides.values()):
            return "missing_required_event_statistics"
        return None

    def build(self, target, *, as_of=None):
        if target["game_id"] in self.blocked:
            return None, self.blocked[target["game_id"]]
        start = analytics.kickoff(target)
        if start:
            try:
                stamp = publication(target)
            except (TypeError, ValueError):
                return None, "invalid_target_publication_timestamp"
            if stamp and stamp >= (aware(as_of) if as_of else start - timedelta(minutes=self.lead_minutes)):
                return None, "target_schedule_publication_after_cutoff"
        row, reason = super().build(target, as_of=as_of)
        if row is None:
            if reason == "insufficient_prior_pbp_games_after_publication_guard":
                cutoff = aware(as_of) if as_of else start - timedelta(minutes=self.lead_minutes)
                teams = [canonical_team(target[f"{s}_team"]) for s in ("home", "away")]
                counts = [sum(r["game_id"] != target["game_id"] and aware(r["kickoff"]) + timedelta(hours=24) < cutoff
                              for r in self.cached_histories[t]) for t in teams]
                if min(counts) >= self.min_games:
                    reason = "publication_delay_leaves_insufficient_history"
            return None, reason
        cutoff = aware(row["prediction_cutoff"])
        dependencies = [g for g in self.schedule_at(cutoff, target) if g["game_id"] != target["game_id"]]
        row["feature_version"], row["evidence_class"] = FEATURE_VERSION, "B"
        # A lagged statistics window must not pretend the team rested since
        # that older source game when a more recent game actually intervened.
        for side in ("home", "away"):
            team = row[f"{side}_team"]
            played = [g for g in self.games.values() if g["game_id"] != target["game_id"]
                      and team in {canonical_team(g["home_team"]), canonical_team(g["away_team"])}
                      and analytics.completed(g, cutoff)]
            eligible = [g for g in dependencies if team in {canonical_team(g["home_team"]), canonical_team(g["away_team"])}]
            if played and eligible and max(analytics.kickoff(g) for g in played) > max(analytics.kickoff(g) for g in eligible):
                for field in ("rest_days", "short_week", "bye_week"):
                    row["team_features"][side][field] = None
        for field in ("rest_days", "short_week", "bye_week"):
            h, a = (row["team_features"][s][field] for s in ("home", "away"))
            row["features"][f"diff_{field}"] = h - a if h is not None and a is not None else None
        for name in FEATURE_NAMES:
            if name not in SUPPORTED_NAMES:
                row["features"][name] = None
        row["quality"]["present"] = sum(v is not None for v in row["features"].values())
        row["quality"]["fraction"] = row["quality"]["present"] / len(FEATURE_NAMES)
        row["quality"]["supported_total"] = len(SUPPORTED_NAMES)
        row["quality"]["missing"] = len(FEATURE_NAMES) - row["quality"]["present"]
        row["provenance"].update(evidence_class="B", historically_reconstructed=True,
            publication_history_verified=False, availability_rule="class_b_event_reconstruction_assumptions",
            availability_assumed_max=max(self.details[g["game_id"]]["available_after_assumed"] for g in dependencies),
            publication_lag_config=asdict(self.policy), assumptions=ASSUMPTIONS,
            unsupported_feature_names=[n for n in FEATURE_NAMES if n not in SUPPORTED_NAMES],
            rest_policy="missing_if_a_more_recent_game_is_not_publication_eligible",
            source_game_dependencies=sorted(g["game_id"] for g in dependencies),
            known_revision_check=True)
        return row, None


def build_reconstructed_dataset(seasons, *, path=None, policy=None, lead_minutes=60, min_games=3, now=None):
    now, policy = aware(now or datetime.now(UTC)), policy or RecoveryPolicy()
    from services.nfl_reliability import sealed_protocol
    protocol = sealed_protocol()
    reserved = set(protocol["holdout"]["game_ids"]) if protocol else set()
    seasons = sorted(set(seasons))
    if not seasons or any(type(s) is not int or not 1999 <= s <= now.year for s in seasons):
        raise ValueError("Invalid reconstruction seasons")
    builders = {s: ReconstructedBuilder(s, path=path, policy=policy, now=now,
                                      lead_minutes=lead_minutes, min_games=min_games) for s in seasons}
    # Elo shares the same completion/publication/revision guard as team inputs.
    all_games = [(g, b.details[g["game_id"]]) for b in builders.values() for g in b.games.values() if g["game_id"] not in b.blocked and g["game_id"] not in reserved]
    rows, labels, manifest = [], {}, []
    for season, builder in builders.items():
        schedules = store.records("schedules", season, path=path)
        if len({g["game_id"] for g in schedules}) != len(schedules):
            raise ValueError("Duplicate schedule game IDs")
        for game in sorted(schedules, key=lambda g: (str(g.get("gameday")), g["game_id"])):
            if game["game_id"] in reserved:
                continue
            row, reason = None, None
            if not analytics.completed(game, now):
                reason = "not_completed_with_24h_confirmation"
            elif outcome(game) == "tie":
                reason = "tie_excluded_binary_conditional_on_no_tie"
            else:
                row, reason = builder.build(game)
            start = analytics.kickoff(game)
            item = {"game_id": game["game_id"], "season": season, "week": game.get("week"),
                    "kickoff": start.isoformat() if start else None,
                    "prediction_cutoff": (start - timedelta(minutes=lead_minutes)).isoformat() if start else None,
                    "evidence_class": "B", "feature_version": FEATURE_VERSION,
                    "eligible": row is not None, "reason": reason, "rejection_reasons": [reason] if reason else [],
                    "eligible_feature_count": row["quality"]["present"] if row else 0,
                    "missing_feature_count": len(FEATURE_NAMES) - row["quality"]["present"] if row else len(FEATURE_NAMES),
                    "source_game_dependencies": row["provenance"]["source_game_dependencies"] if row else [],
                    "availability_assumptions": ASSUMPTIONS, "publication_lag_config": asdict(policy),
                    "excluded_source_games": dict(sorted(builder.blocked.items()))}
            manifest.append(item)
            if row is None:
                continue
            cutoff = aware(row["prediction_cutoff"])
            safe_elo = [(g, d) for g, d in all_games if g["game_id"] != game["game_id"]
                        and int(g["season"]) <= season and aware(d["available_after_assumed"]) < cutoff]
            # Removing a receipt field here does not rewrite it: Elo receives
            # explicitly reconstructed event records after the policy guard.
            elo = elo_probability(game, [{k: v for k, v in g.items() if k != "_observed_at"} for g, _ in safe_elo], cutoff)
            elo.update(evidence_class="B", source_game_ids=sorted(g["game_id"] for g, _ in safe_elo))
            row["baselines"] = {"elo": elo}
            item["elo_source_game_ids"] = elo["source_game_ids"]
            versions = builder.schedules.index[store.record_key("schedules", season, game)]
            receipt = min(t for t, v in zip(*versions, strict=True) if outcome(v) == outcome(game) and t <= now)
            labels[game["game_id"]] = {"home_win": int(outcome(game) == "home"), "outcome": outcome(game),
                "home_score": number(game["home_score"]), "away_score": number(game["away_score"]),
                "first_outcome_observed_at": receipt.isoformat(), "evidence_class": "B",
                "usable_after": builder.details[game["game_id"]]["available_after_assumed"],
                "availability_basis": "reconstructed_after_completion_bound_and_publication_delay"}
            rows.append(row)
    rows.sort(key=lambda r: (r["prediction_cutoff"], r["game_id"]))
    coverage = {str(s): {"total": sum(m["season"] == s for m in manifest),
        "eligible": sum(m["season"] == s and m["eligible"] for m in manifest), "class_a_eligible": 0,
        "class_b_eligible": sum(m["season"] == s and m["eligible"] for m in manifest),
        "eligible_with_incomplete_features": sum(m["season"] == s and m["eligible"] and m["missing_feature_count"] > 0 for m in manifest),
        "excluded_publication_uncertainty": sum(m["season"] == s and m["reason"] in {
            "publication_delay_leaves_insufficient_history", "target_schedule_publication_after_cutoff",
            "invalid_publication_or_completion_timestamp", "retrospectively_revised_or_invalid_pbp",
            "retrospectively_extended_pbp_snapshot",
            "retrospectively_revised_schedule_or_result", "ambiguous_completion"} for m in manifest),
        "exclusions": dict(Counter(m["reason"] for m in manifest if m["season"] == s and not m["eligible"]))} for s in seasons}
    core = {"feature_version": FEATURE_VERSION, "schema": SCHEMA, "feature_names": list(FEATURE_NAMES),
            "supported_feature_names": list(SUPPORTED_NAMES), "evidence_class": "B", "historically_reconstructed": True,
            "config": {"lead_minutes": lead_minutes, "min_games": min_games, **asdict(policy),
                       "early_season": "same_season_minimum_no_carryover", "tie_policy": "exclude_condition_on_no_tie"},
            "rows": rows, "labels": labels, "audit": manifest, "coverage": coverage,
            "source_game_catalog": {g: {"game_id": g, "season": s, **b.details[g],
                "schedule_observed_at": v["_observed_at"], "pbp_observed_at": b.play_receipts.get(g),
                "excluded_reason": b.blocked.get(g)} for s, b in builders.items() for g, v in b.games.items()},
            "source_fingerprints": {str(s): {"schedules": b.schedules.fingerprint, "pbp": b.pbp_fingerprint,
                                           "observation_integrity": b.integrity} for s, b in builders.items()}}
    fingerprint = digest(core)
    return {**core, "dataset_fingerprint": fingerprint, "generated_at": now.isoformat(),
            "availability_manifest": [{**m, "dataset_fingerprint": fingerprint} for m in manifest]}
