"""Offline fixed-rule study. No downloads, fitting, production-model writes or NFL reads."""
import argparse
from collections import defaultdict
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import statistics

from runtime_paths import cache_dir, repo_root
from services import nba_early_season as research
from utils.atomic import atomic_write_json, file_transaction


def load_games(directory):
    games, sources = {}, []
    # Do not open files containing ending-2026/2027 outcomes for this study.
    for path in sorted(directory.glob("*.json")):
        if not path.stem.isdigit() or not 202201 <= int(path.stem) <= 202507:
            continue
        raw = path.read_bytes()
        sources.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
        for event in json.loads(raw).get("events", []):
            status = event.get("status", {}).get("type", {})
            if event.get("season", {}).get("type") != 2 or not status.get("completed") or status.get("state") != "post":
                continue
            season = event["season"].get("year")
            if not isinstance(season, int) or not 2022 <= season <= 2025:
                continue
            competition = event["competitions"][0]
            sides = {team["homeAway"]: team for team in competition["competitors"]}
            home, away = sides["home"], sides["away"]
            try:
                ids = [str(team["id"]) for team in (home, away)]
                scores = [int(team["score"]) for team in (home, away)]
                kickoff = datetime.fromisoformat(event["date"])
                if kickoff.tzinfo is None:
                    continue
                kickoff = kickoff.astimezone(UTC)
            except (KeyError, TypeError, ValueError):
                continue
            if not all(value.isdigit() and 1 <= int(value) <= 30 for value in ids) or ids[0] == ids[1] or min(scores) <= 0 or scores[0] == scores[1]:
                continue
            game = {"game_id": str(event["id"]), "season": season, "kickoff": kickoff.isoformat(), "neutral": competition.get("neutralSite"),
                    "home_team": ids[0], "away_team": ids[1], "actual": dict(zip(("home", "away"), scores, strict=True))}
            if game["game_id"] in games and games[game["game_id"]] != game:
                raise ValueError("Conflicting source observations; study aborted.")
            games[game["game_id"]] = game
    return sorted(games.values(), key=lambda game: (game["kickoff"], game["game_id"])), sources


def summarize(rows):
    rows = sorted(rows, key=lambda row: row["kickoff"], reverse=True)
    return {"games": len(rows), "points_for": round(statistics.mean(row["points_for"] for row in rows), 1) if rows else None,
            "points_against": round(statistics.mean(row["points_against"] for row in rows), 1) if rows else None,
            "recent_results": rows[:6]}


def candidates(games):
    history = defaultdict(list)
    result, coverage = [], defaultdict(lambda: {"early_games": 0, "winner_rule_gate": 0, "scoring_gate": 0, "research_available": 0})
    for game in sorted(games, key=lambda row: (row["kickoff"], row["game_id"])):
        season = game["season"]
        cutoff = datetime.fromisoformat(game["kickoff"]) - timedelta(hours=24)
        teams = []
        for side in ("home", "away"):
            team = game[f"{side}_team"]
            select = lambda year: [row for row in history[(year, team)] if datetime.fromisoformat(row["kickoff"]) < cutoff]
            teams.append({"previous": summarize(select(season - 1)), "current": summarize(select(season))})
        counts = [team["current"]["games"] for team in teams]
        if season >= 2023 and min(counts) < research.TRANSITION_GAMES:
            counts_report = coverage[str(season)]
            counts_report["early_games"] += 1
            counts_report["winner_rule_gate"] += int(min(counts) >= 2)
            counts_report["scoring_gate"] += int(min(counts) >= 3)
            try:
                blend = research.estimate(*teams, neutral=game["neutral"])
                prior = research.estimate(*teams, neutral=game["neutral"], prior_only=True)
            except ValueError:
                pass
            else:
                counts_report["research_available"] += 1
                result.append({**game, "statistics_cutoff": cutoff.isoformat(), "blend": blend, "previous_season_only": prior,
                               "current_games": counts, "input_end": max((row["kickoff"] for team in teams for period in ("previous", "current") for row in team[period]["recent_results"]), default=None)})
        # Insert target outcomes only after constructing this game's inputs.
        for side, other in (("home", "away"), ("away", "home")):
            history[(season, game[f"{side}_team"])].append({"kickoff": game["kickoff"], "game_id": game["game_id"],
                                                         "points_for": game["actual"][side], "points_against": game["actual"][other]})
    return result, dict(coverage)


def evaluate(games):
    games = [game for game in games if 2022 <= game["season"] <= 2025]
    rows, coverage = candidates(games)
    evaluated, folds = [], {}
    for season in (2024, 2025):
        past = [row for row in rows if row["season"] < season]
        calibrations = {name: research.calibration(past, name) for name in ("blend", "previous_season_only")}
        if not all(calibrations.values()):
            folds[str(season)] = {"reason": "Insufficient earlier calibration games", "games": 0}
            continue
        prior_results = [game for game in games if game["season"] < season]
        home_prior = (sum(game["actual"]["home"] > game["actual"]["away"] for game in prior_results) + 1) / (len(prior_results) + 2)
        selected = []
        for row in rows:
            if row["season"] != season:
                continue
            if any(value["end"] >= row["statistics_cutoff"] for value in calibrations.values()):
                raise ValueError("Calibration is not strictly before prediction inputs.")
            selected.append({**row, "calibration": calibrations, "probabilities": {name: research.probability(row[name], value) for name, value in calibrations.items()} | {"home_prior": home_prior}})
        folds[str(season)] = {"calibration_games": len(past), "calibration_end": calibrations["blend"]["end"],
                              "metrics": {method: research.metrics(selected, method) for method in ("blend", "previous_season_only", "home_prior")}}
        evaluated.extend(selected)
    if not evaluated:
        raise ValueError("No valid chronological evaluation games.")
    intervals = {method: research.paired_intervals(evaluated, method) for method in ("previous_season_only", "home_prior")}
    convincing = all(value["log_loss"]["high"] < 0 and value["brier"]["high"] < 0 for value in intervals.values()) and all(
        fold["metrics"]["blend"]["log_loss"] < min(fold["metrics"][name]["log_loss"] for name in ("previous_season_only", "home_prior")) for fold in folds.values())
    report = {"version": research.VERSION, "generated_at": datetime.now(UTC).isoformat(), "evaluated_games": len(evaluated),
              "development_games": sum(row["season"] == 2023 for row in rows), "coverage": coverage, "seasons": folds,
              "metrics": {method: research.metrics(evaluated, method) for method in ("blend", "previous_season_only", "home_prior")},
              "paired_intervals": intervals, "paired_intervals_favor_blend": convincing, "statistically_convincing": False, "production_eligible": False,
              "conclusion": f"Promising retrospective signal, not sufficient proof: only {len(folds)} evaluated seasons and {intervals['home_prior']['log_loss']['blocks']} weekly blocks, without prospective calibration evidence.",
              "sample_groups": {label: {method: research.metrics([row for row in evaluated if (min(row['current_games']) == n if n < 3 else min(row['current_games']) >= 3)], method) for method in ("blend", "previous_season_only", "home_prior")}
                                for n, label in ((0, "zero_current"), (1, "one_current"), (2, "two_current"), (3, "three_to_five_current"))},
              "gate_subsets": {label: {"metrics": {method: research.metrics([row for row in evaluated if min(row["current_games"]) < minimum], method) for method in ("blend", "previous_season_only", "home_prior")},
                                       "paired_intervals": research.paired_intervals([row for row in evaluated if min(row["current_games"]) < minimum], "previous_season_only")}
                               for label, minimum in (("below_winner_minimum", 2), ("below_scoring_minimum", 3))},
              "limitations": ["Retrospective source vintages; original historical pregame receipts unverified.",
                              "Only two evaluation seasons and a small number of seven-day blocks; long-range team dependence is not removed.",
                              "No roster, trade, lineup or injury adjustments; previous-season observations may describe a changed team.",
                              "Winner-rule gates are compared as availability only; the production weighted engine cannot be exactly reconstructed without historical point-in-time standings.",
                              "Ending-2026 outcomes excluded from this study, but previously used by older project scoring research; not a globally untouched holdout.",
                              "All post-freeze future games are reserved for genuinely prospective validation; no automatic production promotion."]}
    calibrated = research.calibration(rows, "blend")
    artifact = {"version": research.VERSION, "generated_at": report["generated_at"], "calibration": calibrated,
                "method_sha256": research.method_fingerprint(), "production_eligible": False}
    return report, artifact, evaluated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=repo_root() / "data/validation/nba_early_season_protocol_v1.json")
    args = parser.parse_args()
    protocol_bytes = args.protocol.read_bytes()
    protocol = json.loads(protocol_bytes)
    if (protocol["version"] != research.VERSION or protocol["transition_games"] != research.TRANSITION_GAMES
            or protocol["previous_minimum_observations"] != research.MIN_PREVIOUS or protocol["evaluation_seasons"] != [2024, 2025]):
        raise ValueError("Frozen research protocol mismatch")
    games, sources = load_games(cache_dir("score_models", "nba_raw"))
    report, artifact, rows = evaluate(games)
    report["protocol"] = protocol
    report["sources"] = sources
    report["observed_regular_games"] = len(games)
    artifact["protocol_sha256"] = hashlib.sha256(protocol_bytes).hexdigest()
    with file_transaction(research.DIRECTORY / "manifest.json"):
        for name, value in (("report", report), ("artifact", artifact), ("predictions", rows)):
            atomic_write_json(research.DIRECTORY / f"{name}.json", value)
        manifest = {f"{name}_sha256": hashlib.sha256((research.DIRECTORY / f"{name}.json").read_bytes()).hexdigest() for name in ("report", "artifact", "predictions")}
        manifest["protocol_sha256"] = artifact["protocol_sha256"]
        atomic_write_json(research.DIRECTORY / "manifest.json", manifest)
    print(json.dumps({name: report[name] for name in ("evaluated_games", "development_games", "coverage", "metrics", "paired_intervals", "statistically_convincing")}, indent=2))


if __name__ == "__main__":
    main()
