"""Phase 2.2 historical development comparison; prospective holdout stays sealed."""
import argparse
import json
from pathlib import Path

from scripts.nfl_winner_research import charts, load_replay_dataset, write_summary
from services import nfl_winner_research as registry
from services.nfl_reliability import STUDY_VERSION, coverage_audit, register_protocol
from services.nfl_winner_evaluation import EvaluationConfig, evaluate, paired_intervals
from utils.atomic import atomic_write_json

DEFAULT_RUN = Path("cache/nflverse/phase2_1/class_b/run_d51f0ec86b0dde870b81a606")


def run(source=DEFAULT_RUN, *, output=Path("cache/nflverse/phase2_2"), database=None):
    source, output = Path(source), Path(output)
    dataset = load_replay_dataset(source / "dataset.json", "B")
    protocol = register_protocol(output / "protocol.json", dataset["dataset_fingerprint"], database=database)
    ids = {r["game_id"] for r in dataset["rows"]}
    if ids & set(protocol["holdout"]["game_ids"]) or set(dataset["labels"]) != ids or any(r["season"] > 2025 for r in dataset["rows"]):
        raise ValueError("Prospective holdout or nondevelopment outcomes cannot enter Phase 2.2 fitting")
    config = EvaluationConfig(split_strategy="adaptive_past_windows", candidate_names=("logistic_baseline", "nonlinear_forest"),
                              bootstrap_samples=protocol["bootstrap_samples"])
    report, predictions, artifacts = evaluate(dataset, config)
    report.update(study_version=STUDY_VERSION, protocol_sha256=protocol["protocol_sha256"],
                  prediction_lead_minutes=dataset["config"]["lead_minutes"],
                  retrospective_development_only=True, final_holdout_evaluated=False, production_evidence_sufficient=False)
    confidence = 1 - .05 / protocol["primary_comparison_count"]
    report["familywise_paired_vs_home_prior"] = paired_intervals(predictions, samples=config.bootstrap_samples, confidence=confidence)
    report["familywise_paired_vs_elo"] = paired_intervals(predictions, "elo:selected", samples=config.bootstrap_samples, confidence=confidence)
    report["season_paired_comparisons"] = {}
    for season in sorted({p["season"] for p in predictions}):
        subset = [p for p in predictions if p["season"] == season]
        report["season_paired_comparisons"][str(season)] = {"vs_home_prior": paired_intervals(subset, samples=config.bootstrap_samples),
            "vs_elo": paired_intervals(subset, "elo:selected", samples=config.bootstrap_samples)}
    old_report = json.loads((source / "evaluation.json").read_text())
    old_predictions = json.loads((source / "backtest_predictions.json").read_text())["predictions"]
    report["coverage_audit"] = coverage_audit(dataset, old_report, old_predictions, report, predictions)
    report["consistency"] = {}
    for candidate in ("logistic_baseline:selected", "nonlinear_forest:selected"):
        measured = report["models"].get(candidate)
        if not measured:
            continue
        seasons = [s for s, m in measured["by_season"].items() if m["count"] >= 30]
        directions = {s: {reference: {metric: measured["by_season"][s][metric] - report["models"][reference]["by_season"][s][metric]
                                     for metric in ("log_loss", "brier")}
                          for reference in ("home_prior:selected", "elo:selected")} for s in seasons}
        intervals = [report[key].get(candidate, {}) for key in ("familywise_paired_vs_home_prior", "familywise_paired_vs_elo")]
        convincing = len(seasons) >= 2 and all(v < 0 for refs in directions.values() for delta in refs.values() for v in delta.values())
        convincing = convincing and all(i.get("available") and i["log_loss_delta_interval"][1] < 0 and i["brier_delta_interval"][1] < 0 for i in intervals)
        report["consistency"][candidate] = {"seasons_with_at_least_30_games": seasons, "deltas_by_season": directions,
                                           "consistent_historical_improvement_established": convincing,
                                           "prospective_confirmation_required": True}
    entry = registry.save_run(dataset, report, predictions, artifacts, root=output)
    destination = output / entry["run_id"]
    atomic_write_json(destination / "coverage_audit.json", report["coverage_audit"])
    write_summary(dataset, report, destination / "results.md")
    atomic_write_json(destination / "charts.json", charts(report, destination))
    return {**entry, "protocol_sha256": protocol["protocol_sha256"], "reserved_final_holdout_games": len(protocol["holdout"]["game_ids"]),
            "coverage_audit": {k: v for k, v in report["coverage_audit"].items() if k not in {"expanded_skipped_boundaries", "original_skipped_folds", "original_not_repeated"}},
            "models": {n: {k: m[k] for k in ("count", "log_loss", "brier", "accuracy", "ece")}
                       for n, m in report["models"].items() if n.endswith(":selected")}, "consistency": report["consistency"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", default=str(DEFAULT_RUN))
    parser.add_argument("--output", default="cache/nflverse/phase2_2")
    parser.add_argument("--database")
    args = parser.parse_args()
    print(json.dumps(run(args.source_run, output=Path(args.output), database=args.database), indent=2))


if __name__ == "__main__":
    main()
