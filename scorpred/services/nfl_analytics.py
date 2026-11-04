"""Descriptive NFL research, computed offline and isolated from model schemas.

Every rate carries an actual observed denominator. Missing flags are excluded,
never treated as zero. Research reads recorded vintages at/before its cutoff.
"""
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean

import nfl_client
from services import nflverse_store as store
from services.nfl_catalog import canonical_team, player_ids
from services.nflverse_provider import number

METRIC_VERSION = 1
DEPENDENCIES = ("schedules", "pbp", "player_stats", "pfr_pass_week", "participation", "players")


def rate(numerator, denominator, eligible=None):
    return {"value": numerator / denominator if denominator else None,
            "numerator": numerator if denominator else None, "denominator": denominator,
            "eligible": denominator if eligible is None else eligible}


def measured(rows, field, transform=lambda value: value):
    values = [transform(value) for row in rows if (value := number(row.get(field))) is not None]
    return rate(sum(values), len(values), len(rows))


def binary_rate(rows, field):
    values = [flag(row, field) for row in rows if flag(row, field) is not None]
    return rate(sum(values), len(values), len(rows))


def flag(row, field):
    value = number(row.get(field))
    return value if value in (0, 1) else None


def is_dropback(row):
    return (flag(row, "qb_dropback") == 1 or flag(row, "pass_attempt") == 1 or flag(row, "sack") == 1)


def scrimmage_plays(rows):
    return [row for row in rows if row.get("posteam") and row.get("defteam")
            and row.get("play_type") in {"pass", "run"} and flag(row, "no_play") != 1
            and flag(row, "qb_kneel") == 0 and flag(row, "qb_spike") == 0
            and flag(row, "two_point_attempt") != 1]


def offense_metrics(rows):
    passing = [row for row in rows if is_dropback(row)]
    rushing = [row for row in rows if flag(row, "rush_attempt") == 1 and not is_dropback(row)]
    explosive = [int(yards >= (20 if is_dropback(row) else 10)) for row in passing + rushing
                 if (yards := number(row.get("yards_gained"))) is not None]
    third = [flag(row, "third_down_converted") for row in rows if number(row.get("down")) == 3
             and flag(row, "third_down_converted") is not None and flag(row, "third_down_failed") is not None
             and flag(row, "third_down_converted") + flag(row, "third_down_failed") == 1]
    turnovers = [max(flag(row, "interception"), flag(row, "fumble_lost")) for row in rows
                 if flag(row, "interception") is not None and flag(row, "fumble_lost") is not None]
    drives = defaultdict(list)
    for row in rows:
        drive = row.get("fixed_drive", row.get("drive"))
        if drive is not None:
            drives[drive].append(row)
    redzone = [plays for plays in drives.values() if any(
        (yard := number(row.get("yardline_100"))) is not None and 0 < yard <= 20 for row in plays)]
    touchdowns = []
    for plays in redzone:
        flags = [max(flag(row, "pass_touchdown"), flag(row, "rush_touchdown")) for row in plays
                 if flag(row, "pass_touchdown") is not None and flag(row, "rush_touchdown") is not None]
        # An observed TD suffices; otherwise every play needs TD coverage.
        if flags and (max(flags) == 1 or len(flags) == len(plays)):
            touchdowns.append(max(flags))
    sacks = [flag(row, "sack") for row in passing if flag(row, "sack") is not None]
    return {
        "epa_per_play": measured(rows, "epa"),
        "success_rate": rate(sum(flag(r, "success") for r in rows if flag(r, "success") is not None),
                             sum(flag(r, "success") is not None for r in rows), len(rows)),
        "passing_epa_per_dropback": measured(passing, "epa"),
        "rushing_epa_per_play": measured(rushing, "epa"),
        "explosive_play_rate": rate(sum(explosive), len(explosive), len(passing + rushing)),
        "third_down_conversion_rate": rate(sum(third), len(third)),
        "redzone_td_rate": rate(sum(touchdowns), len(touchdowns), len(redzone)),
        "yards_per_play": measured(rows, "yards_gained"),
        "turnover_play_rate": rate(sum(turnovers), len(turnovers), len(rows)),
        "sack_rate": rate(sum(sacks), len(sacks), len(passing)),
        "pressure_rate": rate(sum(flag(r, "was_pressure") for r in passing if flag(r, "was_pressure") is not None),
                              sum(flag(r, "was_pressure") is not None for r in passing), len(passing)),
    }


def quarterback_metrics(rows):
    dropbacks = [row for row in rows if is_dropback(row)]
    attempts = [row for row in dropbacks if flag(row, "pass_attempt") == 1 and flag(row, "sack") == 0]
    sacks = [row for row in dropbacks if flag(row, "sack") == 1]
    ay = []
    for row in attempts:
        fields = [number(row.get(key)) for key in ("passing_yards", "pass_touchdown", "interception")]
        if fields[0] is None and flag(row, "complete_pass") == 0:
            fields[0] = 0  # An explicitly incomplete pass has zero passing yards.
        if all(value is not None for value in fields):
            ay.append(fields[0] + 20 * fields[1] - 45 * fields[2])
    pressured = [row for row in dropbacks if flag(row, "was_pressure") == 1]
    return {
        "completion_percentage": binary_rate(attempts, "complete_pass"),
        "passing_epa_per_dropback": measured(dropbacks, "epa"),
        "adjusted_yards_per_attempt": rate(sum(ay), len(ay), len(attempts)),
        "interception_rate": binary_rate(attempts, "interception"),
        "sack_rate": rate(len(sacks), len(attempts) + len(sacks)) if all(flag(r, "sack") is not None for r in dropbacks) else rate(0, 0),
        "epa_under_pressure": measured(pressured, "epa"),
        "pressure_rate": binary_rate(dropbacks, "was_pressure"),
    }


def kickoff(row):
    try:
        return datetime.fromisoformat(f'{row["gameday"]}T{row["gametime"]}').replace(tzinfo=nfl_client.EASTERN).astimezone(UTC)
    except (KeyError, TypeError, ValueError):
        return None


def completed(row, now):
    start = kickoff(row)
    return (start is not None and start + timedelta(hours=24) < now
            and row.get("game_type") in {"REG", "WC", "DIV", "CON", "SB"}
            and all((score := number(row.get(f"{side}_score"))) is not None and score >= 0 for side in ("home", "away")))


def refresh_analytics(season, *, path=None, now=None, force=False):
    now = now or datetime.now(UTC)
    metadata = store.metadata(path=path)
    if now.tzinfo is None or any(datetime.fromisoformat(entry["synced_at"]) > now for entry in metadata
                               if entry["dataset"] in DEPENDENCIES and entry["season"] in {season, 0}):
        raise ValueError("Analytics observation timestamp cannot precede its source receipts")
    fingerprints = {entry["dataset"]: entry["sha256"] for entry in metadata
                    if entry["dataset"] in DEPENDENCIES and entry["season"] in {season, 0}}
    # Games only become eligible after the completion lag, even when a cached
    # schedule file's content is unchanged since the last run.
    schedule = {row["game_id"]: row for row in store.records("schedules", season, path=path) if completed(row, now)}
    fingerprint = hashlib.sha256(json.dumps([METRIC_VERSION, fingerprints, sorted(schedule)], sort_keys=True).encode()).hexdigest()
    previous = store.records("advanced_team_games", season, path=path)
    if not force and previous and all(row.get("source_fingerprint") == fingerprint for row in previous):
        return [{"dataset": "advanced_team_games", "season": season, "status": "cached"}]
    plays = scrimmage_plays(store.records("pbp", season, path=path))
    participation = {(r.get("game_id") or r.get("nflverse_game_id"), number(r.get("play_id"))): r
                     for r in store.records("participation", season, path=path)}
    for row in plays:
        extra = participation.get((row["game_id"], number(row["play_id"])), {})
        if "was_pressure" in extra:
            row["was_pressure"] = extra["was_pressure"]
    groups = defaultdict(list)
    for row in plays:
        if row["game_id"] in schedule:
            groups[(row["game_id"], canonical_team(row["posteam"]))].append(row)
    pfr_ids = player_ids(store.records("players", 0, path=path))
    pressures = {(row["game_id"], canonical_team(row["team"]), pfr_ids.get(("pfr_id", row["pfr_player_id"]))) : row
                 for row in store.records("pfr_pass_week", season, path=path)}
    team_rows, qb_rows = [], []
    for game_id, game in schedule.items():
        start = kickoff(game)
        for side, other in (("home", "away"), ("away", "home")):
            team, opponent = canonical_team(game[f"{side}_team"]), canonical_team(game[f"{other}_team"])
            own, against = groups[(game_id, team)], groups[(game_id, opponent)]
            if not own and not against:
                continue
            common = {"game_id": game_id, "season": season, "week": int(game["week"]), "team": team,
                      "kickoff": start.isoformat(), "source_fingerprint": fingerprint, "metric_version": METRIC_VERSION}
            team_rows.append({**common, "opponent": opponent, "home": side == "home", "neutral": game.get("location") == "Neutral",
                              "points_for": number(game[f"{side}_score"]), "points_against": number(game[f"{other}_score"]),
                              "offense": offense_metrics(own), "defense": offense_metrics(against)})
            quarterbacks = defaultdict(list)
            for row in own:
                if row.get("passer_player_id") and is_dropback(row):
                    quarterbacks[row["passer_player_id"]].append(row)
            for player, qb_plays in quarterbacks.items():
                metrics = quarterback_metrics(qb_plays)
                pfr = pressures.get((game_id, team, player), {})
                pressure_count = number(pfr.get("times_pressured"))
                attempts = sum(flag(row, "pass_attempt") == 1 and flag(row, "sack") == 0 for row in qb_plays)
                sacks = sum(flag(row, "sack") == 1 for row in qb_plays)
                if not metrics["pressure_rate"]["denominator"]:
                    metrics["pressure_rate"] = rate(pressure_count, attempts + sacks) if pressure_count is not None and pressure_count <= attempts + sacks else rate(0, 0)
                qb_rows.append({**common, "player_id": player, "metrics": metrics})
    # PFR pressure totals apply only to explicitly linked quarterbacks. Keep
    # their real coverage denominator rather than treating missing QBs as zero.
    team_index = {(r["game_id"], r["team"]): r for r in team_rows}
    for row in team_rows:
        linked = [qb["metrics"]["pressure_rate"] for qb in qb_rows
                  if qb["game_id"] == row["game_id"] and qb["team"] == row["team"]
                  and qb["metrics"]["pressure_rate"]["denominator"] > 0]
        if not row["offense"]["pressure_rate"]["denominator"] and linked:
            row["offense"]["pressure_rate"] = rate(sum(r["numerator"] for r in linked), sum(r["denominator"] for r in linked))
    for row in team_rows:
        opponent = team_index.get((row["game_id"], row["opponent"]))
        if opponent:
            row["defense"]["pressure_rate"] = opponent["offense"]["pressure_rate"]
    reports = []
    for dataset, rows in (("advanced_team_games", team_rows), ("advanced_qb_games", qb_rows)):
        if rows:
            reports.append(store.replace_dataset(dataset, season, rows, path=path, synced_at=now))
        else:
            reports.append({"dataset": dataset, "season": season, "status": "unavailable", "reason": "No completed games with usable PBP"})
    return reports


def aggregate(rows, side):
    result = {}
    names = {name for row in rows for name in row.get(side, {})}
    for name in sorted(names):
        values = [row[side][name] for row in rows if name in row.get(side, {})]
        result[name] = rate(sum(value["numerator"] or 0 for value in values), sum(value["denominator"] for value in values),
                            sum(value.get("eligible", value["denominator"]) for value in values))
    return result


def matchup_features(game_id, *, path=None, as_of=None, now=None, lookback=8):
    """Strict research bundle; current rosters and season summaries never enter.

    Old imports cannot establish historical availability. No approximation mode
    silently relaxes this requirement. No scoring or action is produced here.
    """
    now = now or datetime.now(UTC)
    target = next((row for row in store.records("schedules", path=path) if row["game_id"] == game_id), None)
    if target is None or kickoff(target) is None:
        raise ValueError("Unknown game or kickoff")
    start = kickoff(target)
    cutoff = as_of if as_of is not None else min(now, start - timedelta(microseconds=1))
    if cutoff.tzinfo is None or cutoff >= start or cutoff > now:
        raise ValueError("Research cutoff must be timezone-aware, before kickoff and not in the future")
    season = int(target["season"])
    schedule_vintage = store.records_as_of("schedules", season, cutoff, path=path)
    known_target = next((row for row in schedule_vintage if row["game_id"] == game_id), None)
    if known_target is None:
        return {"game_id": game_id, "season": season, "feature_cutoff": cutoff.isoformat(),
                "research_only": True, "available": False,
                "reason": "Target schedule had not been observed by this cutoff"}
    target = known_target
    start = kickoff(target)
    if start is None or cutoff >= start:
        raise ValueError("Target was not scheduled after this cutoff in its known schedule version")
    rows = [row for row in store.records_as_of("advanced_team_games", season, cutoff, path=path)
            if datetime.fromisoformat(row["kickoff"]) + timedelta(hours=24) < cutoff]
    qb_rows = [row for row in store.records_as_of("advanced_qb_games", season, cutoff, path=path)
               if datetime.fromisoformat(row["kickoff"]) + timedelta(hours=24) < cutoff]
    prior_schedule = [row for row in schedule_vintage if completed(row, cutoff)]
    prior_ids = {r["game_id"] for r in prior_schedule}
    player_rows = [r for r in store.records_as_of("player_stats", season, cutoff, path=path)
                   if r.get("game_id") in prior_ids and r.get("player_id")]
    output = {"game_id": game_id, "season": season, "feature_cutoff": cutoff.isoformat(), "research_only": True,
              "available": bool(rows), "metric_version": METRIC_VERSION, "home_field": int(target.get("location") != "Neutral"),
              "roster_availability": {"available": False, "reason": "No verified injury/inactive publication-time archive; no availability adjustment"}}
    by_team = defaultdict(list)
    for row in sorted(rows, key=lambda row: row["kickoff"]):
        by_team[row["team"]].append(row)
    for side in ("home", "away"):
        team = canonical_team(target[f"{side}_team"])
        history = by_team[team][-lookback:]
        scored = [r for r in prior_schedule if team in {canonical_team(r["home_team"]), canonical_team(r["away_team"])}]
        last = max((kickoff(r) for r in scored), default=None)
        rest = (start - last).total_seconds() / 86400 if last else None
        opponents = []
        adjusted = []
        for row in history:
            opponent_history = [r for r in by_team[row["opponent"]] if r["game_id"] != row["game_id"]]
            # All values are known at this target's cutoff, including baselines.
            baseline = aggregate(opponent_history, "offense").get("epa_per_play", {}).get("value")
            allowed = row["defense"]["epa_per_play"]["value"]
            if baseline is not None and allowed is not None:
                adjusted.append(allowed - baseline)
            opponent_scores = [r for r in prior_schedule if row["opponent"] in {canonical_team(r["home_team"]), canonical_team(r["away_team"])}]
            results = []
            for game in opponent_scores:
                if game["game_id"] == row["game_id"]:
                    continue
                h = canonical_team(game["home_team"]) == row["opponent"]
                pf, pa = number(game["home_score" if h else "away_score"]), number(game["away_score" if h else "home_score"])
                results.append(1 if pf > pa else .5 if pf == pa else 0)
            if results:
                opponents.append(mean(results))
        players = defaultdict(list)
        for row in qb_rows:
            if row["team"] == team and row["game_id"] in {r["game_id"] for r in history}:
                players[row["player_id"]].append(row)
        qbs = []
        for player, games in sorted(players.items()):
            ordered = sorted(games, key=lambda row: row["kickoff"])
            recent = aggregate(ordered[-3:], "metrics")
            earlier = aggregate(ordered[:-3], "metrics")
            current_epa = recent.get("passing_epa_per_dropback", {}).get("value")
            prior_epa = earlier.get("passing_epa_per_dropback", {}).get("value")
            qbs.append({"player_id": player, "games": len(games), "metrics": aggregate(games, "metrics"),
                        "recent_metrics": recent, "epa_trend": current_epa - prior_epa if current_epa is not None and prior_epa is not None else None})
        recent = aggregate(history[-3:], "offense").get("epa_per_play", {}).get("value")
        earlier = aggregate(history[:-3], "offense").get("epa_per_play", {}).get("value")
        player_groups = defaultdict(list)
        recent_ids = {r["game_id"] for r in sorted(scored, key=kickoff)[-lookback:]}
        for row in player_rows:
            if canonical_team(row.get("team")) == team and row["game_id"] in recent_ids:
                player_groups[row["player_id"]].append(row)
        player_research = []
        for player, games in sorted(player_groups.items()):
            totals = {}
            for field in ("attempts", "completions", "passing_yards", "passing_tds", "passing_interceptions",
                          "carries", "rushing_yards", "rushing_tds", "targets", "receptions", "receiving_yards", "receiving_tds"):
                observed = [number(r.get(field)) for r in games]
                totals[field] = sum(observed) if all(v is not None for v in observed) else None
            player_research.append({"player_id": player, "games": len(games), "totals": totals})
        output[side] = {"team": team, "games": len(history), "offense": aggregate(history, "offense"),
                        "defense": aggregate(history, "defense"), "rest_days": rest,
                        "short_week": rest < 6 if rest is not None else None, "recent_offense": aggregate(history[-3:], "offense"),
                        "offensive_epa_trend": recent - earlier if recent is not None and earlier is not None else None,
                        "strength_of_schedule": mean(opponents) if opponents else None,
                        "opponent_adjusted_defensive_epa": mean(adjusted) if adjusted else None,
                        "quarterbacks": qbs, "players": player_research, "history_game_ids": [r["game_id"] for r in history]}
    output["available"] = all(output[side]["games"] > 0 for side in ("home", "away"))
    output["rest_difference"] = (output["home"]["rest_days"] - output["away"]["rest_days"]
                                 if all(output[s]["rest_days"] is not None for s in ("home", "away")) else None)
    output["matchup"] = {}
    for side, other in (("home", "away"), ("away", "home")):
        off = output[side]["offense"].get("epa_per_play", {}).get("value")
        defense = output[other]["defense"].get("epa_per_play", {}).get("value")
        output["matchup"][f"{side}_offense_vs_defense_epa_gap"] = off - defense if off is not None and defense is not None else None
    output["head_to_head"] = [{"game_id": r["game_id"], "home_score": number(r["home_score"]), "away_score": number(r["away_score"])}
                              for r in prior_schedule if {canonical_team(r["home_team"]), canonical_team(r["away_team"])}
                              == {output["home"]["team"], output["away"]["team"]}]
    if not output["available"]:
        output["reason"] = "Insufficient completed-game analytics observed before this cutoff"
    return output
