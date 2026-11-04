"""Versioned soccer research semantics; never imported by production predictors.

All transformations keep the source object and definition identity. A known
numeric value is not proof of compatible units, period or xG model definition.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
from collections.abc import Mapping

VERSION = "soccer-stats-v1"
COUNT_PATHS = {
    "goals": ("goals", "goals.total"), "assists": ("assists", "goals.assists"),
    "shots": ("shots", "shots.total"), "shots_on_target": ("shots_on_target", "shots.on"),
    "corners": ("corners",), "fouls_committed": ("fouls_committed", "committed"),
    "fouls_drawn": ("fouls_drawn", "drawn"), "yellow_cards": ("yellow_cards", "cards.yellow", "yellow"),
    "red_cards": ("red_cards", "cards.red"), "second_yellow_red": ("second_yellow_red",),
    "direct_red": ("direct_red",), "minutes": ("minutes", "games.minutes"),
    "clearances": ("clearances",), "aerial_duels_won": ("aerial_duels_won",),
    "passes_completed": ("passes_completed",), "chances_created": ("chances_created",),
}
UNSUPPORTED_ALIASES = ("clearances", "aerial_duels_won", "passes_completed", "chances_created")
XG_PATHS = {"xg": ("expected_goals", "expected.goals", "Expected Goals", "xG"),
            "home_xg": ("home_xg",), "away_xg": ("away_xg",)}


def require_version(version):
    if version != VERSION:
        raise ValueError("Incompatible soccer statistic contract version")


def number(value, *, integer=True):
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, (Mapping, list, tuple)):
        raise ValueError("Expected a scalar statistic, not a boolean/object")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError("Invalid statistic number/unit") from None
    if not math.isfinite(parsed) or parsed < 0 or (integer and not parsed.is_integer()):
        raise ValueError("Statistic must be finite, nonnegative and integral for counts")
    return int(parsed) if integer else parsed


def lookup(raw, path):
    if path in raw:
        return raw[path]
    current = raw
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def normalize(raw, definition, *, version=VERSION, prediction_cutoff=None):
    """Normalize declared match-level source counts into an isolated v1 record.

    definition: source, definition_id, period, unit, aggregation, verified,
    optionally xg_model_version and verified_dedicated_fields. No implicit
    field acquisition or inference from final scores exists.
    """
    require_version(version)
    if not isinstance(raw, Mapping) or not isinstance(definition, Mapping):
        raise ValueError("Expected source values and a source definition")
    values, issues, transforms = {}, [], []
    compatible = (definition.get("verified") is True and bool(definition.get("source"))
                  and bool(definition.get("definition_id")) and definition.get("unit") == "count"
                  and definition.get("aggregation") in {"match", "season"}
                  and definition.get("period") in {"regulation", "full_time_source", "extra_time", "shootout", "season"})
    for field, paths in COUNT_PATHS.items():
        found = [(path, lookup(raw, path)) for path in paths if lookup(raw, path) is not None
                 and not isinstance(lookup(raw, path), Mapping)]
        values[field] = None
        if not found:
            continue
        if not compatible:
            issues.append({"field": field, "reason": "unverified_or_incompatible_source_definition"})
            continue
        if field == "minutes" and definition.get("minute_unit") != "decimal_minutes":
            issues.append({"field": field, "reason": "minute_format_or_unit_not_declared"})
            continue
        if field in UNSUPPORTED_ALIASES and field not in definition.get("verified_dedicated_fields", []):
            issues.append({"field": field, "reason": "dedicated_statistic_definition_not_verified"})
            continue
        try:
            parsed = [number(value, integer=field != "minutes") for _, value in found]
            if len(set(parsed)) != 1:
                raise ValueError("Conflicting source aliases")
            values[field] = parsed[0]
            transforms.append({"field": field, "source_paths": [path for path, _ in found],
                               "operation": "validate_count" if field != "minutes" else "validate_decimal_minutes"})
        except ValueError as exc:
            issues.append({"field": field, "reason": str(exc)})
    values["goal_or_assist"] = (values["goals"] + values["assists"]
                                 if values["goals"] is not None and values["assists"] is not None else None)
    values["corner_probability"] = None  # No validated corner forecasting method is supplied.
    if values["shots"] is not None and values["shots_on_target"] is not None and values["shots_on_target"] > values["shots"]:
        values["shots"] = values["shots_on_target"] = None
        issues.append({"field": "shots/shots_on_target", "reason": "on_target_exceeds_total; pair_quarantined"})
    for field, paths in XG_PATHS.items():
        found = [(path, lookup(raw, path)) for path in paths if lookup(raw, path) is not None]
        values[field] = None
        if not found:
            continue
        if not compatible or not definition.get("xg_model_version"):
            issues.append({"field": field, "reason": "xg_provider_model_period_definition_not_verified"})
            continue
        try:
            parsed = [number(value, integer=False) for _, value in found]
            if len(set(parsed)) != 1:
                raise ValueError("Conflicting xG aliases")
            values[field] = parsed[0]
            transforms.append({"field": field, "source_paths": [path for path, _ in found],
                               "operation": "validate_explicit_provider_xg"})
        except ValueError as exc:
            issues.append({"field": field, "reason": str(exc)})
    # Model shares retain their name/unit; never become observed expected goals.
    proxies = {}
    for source, field in (("xg_a", "model_attack_share_home"), ("xg_b", "model_attack_share_away")):
        if source in raw:
            try:
                value = number(raw[source], integer=False)
                if value is None or value > 1:
                    raise ValueError("Attack share outside [0,1]")
                proxies[field] = {"value": value, "unit": "fraction", "validated_for_prediction": False}
            except ValueError:
                issues.append({"field": field, "reason": "invalid_model_attack_share"})
    allowed = False
    if prediction_cutoff is not None:
        cutoff, observed, ended = [_time(v) for v in (prediction_cutoff, definition.get("observed_at"), definition.get("event_ended_at"))]
        allowed = (compatible and definition.get("aggregation") == "match"
                   and definition.get("stat_scope") == "prior_game_final_box"
                   and cutoff is not None and observed is not None and ended is not None
                   and observed < cutoff and ended < cutoff)
        if not allowed:
            issues.append({"field": "availability", "reason": "pregame_receipt_or_prior_game_scope_not_proven"})
    return {"contract_version": VERSION, "research_only": True, "source_values": deepcopy(dict(raw)),
            "definition": deepcopy(dict(definition)), "values": values, "labeled_model_quantities": proxies,
            "transformations": transforms, "issues": issues, "available_for_prediction": allowed}


def player_overview(payload, *, league_slug, definition, version=VERSION):
    """Descriptive season totals only, from one explicitly matched split."""
    root = payload.get("statistics") or {}
    splits = [s for s in root.get("splits", []) if s.get("leagueSlug") == league_slug]
    if len(splits) != 1:
        raise ValueError("Overview requires one explicit competition split")
    split = splits[0]
    supplied = dict(zip(root.get("names", []), split.get("stats", [])))
    names = {"goals": "totalGoals", "assists": "goalAssists", "shots": "totalShots",
             "shots_on_target": "shotsOnTarget", "yellow_cards": "yellowCards", "red_cards": "redCards"}
    scope = dict(definition, aggregation="season", period="season")
    result = normalize({k: supplied.get(v) for k, v in names.items()}, scope, version=version)
    result["source_values"] = deepcopy(payload)
    result["source_column_mapping"] = names
    return result


def team_stat_table(rows, team_id, *, definition, version=VERSION):
    """Aliases are confined to this declared provider table contract."""
    aliases = {"Total Shots": "shots", "Shots": "shots", "Shots on Goal": "shots_on_target",
               "Shots on Target": "shots_on_target", "Corner Kicks": "corners", "Corners": "corners",
               "Fouls": "fouls_committed", "Fouls Committed": "fouls_committed",
               "Yellow Cards": "yellow_cards", "Red Cards": "red_cards",
               "Expected Goals": "expected_goals", "xG": "expected_goals", "expected_goals": "expected_goals"}
    matches = [r for r in rows if str((r.get("team") or {}).get("id")) == str(team_id)]
    if len(matches) > 1:
        raise ValueError("Ambiguous team statistic table")
    raw = {}
    for stat in (matches[0].get("statistics", []) if matches else []):
        field = aliases.get(stat.get("type"))
        if field:
            if field in raw and raw[field] != stat.get("value"):
                raise ValueError("Conflicting provider statistic aliases")
            raw[field] = stat.get("value")
    result = normalize(raw, definition, version=version)
    result["source_values"] = deepcopy(rows)
    return result


def source_row(raw, *, side, definition, version=VERSION):
    """Preserve a raw CSV row; translate only explicitly named source columns."""
    if side not in {"home", "away"}:
        raise ValueError("Expected oriented home/away side")
    prefix = "H" if side == "home" else "A"
    mapping = {"goals": "FTHG" if side == "home" else "FTAG", "shots": prefix + "S",
               "shots_on_target": prefix + "ST", "corners": prefix + "C", "fouls_committed": prefix + "F",
               "yellow_cards": prefix + "Y", "red_cards": prefix + "R"}
    normalized = normalize({field: raw.get(column) for field, column in mapping.items()}, definition, version=version)
    normalized["source_values"] = deepcopy(raw)
    normalized["source_column_mapping"] = mapping
    return normalized


def nullable_mean(values, *, version=VERSION):
    """Complete-window research means: unknown is not a zero observation."""
    require_version(version)
    parsed = [number(v, integer=False) for v in values]
    return sum(parsed) / len(parsed) if parsed and all(v is not None for v in parsed) else None


def rate(count, denominator, *, scale=1, version=VERSION):
    require_version(version)
    count, denominator = number(count), number(denominator, integer=False)
    factor = number(scale, integer=False)
    return count / denominator * factor if count is not None and denominator and factor is not None else None


def regulation_result(periods, *, version=VERSION):
    require_version(version)
    value = periods.get("regulation")
    if value is None:
        return {"source_periods": deepcopy(periods), "regulation_result": None}
    try:
        home, away = [number(v) for v in value.split("-")]
    except (ValueError, AttributeError):
        raise ValueError("Invalid regulation score pair") from None
    return {"source_periods": deepcopy(periods), "regulation_result": "HomeWin" if home > away else "AwayWin" if away > home else "Draw"}
