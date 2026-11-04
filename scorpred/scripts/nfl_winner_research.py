"""Build/audit/backtest the disabled NFL winner research engine. Never deploys."""
import argparse
import csv
import json
from pathlib import Path

from services import nfl_winner_research as research
from services.nfl_winner_evaluation import evaluate
from services.nfl_winner_features import build_dataset, digest


def load_replay_dataset(path, evidence_class):
    source = Path(path)
    if source.stat().st_size > 250_000_000:
        raise ValueError("Oversized research replay dataset")
    dataset = json.loads(source.read_text(encoding="utf-8"))
    core = {k: v for k, v in dataset.items() if k not in {"generated_at", "dataset_fingerprint", "availability_manifest"}}
    if digest(core) != dataset["dataset_fingerprint"]:
        raise ValueError("Replay dataset fingerprint mismatch")
    if dataset.get("evidence_class", "A") != evidence_class:
        raise ValueError("Replay evidence class must be selected explicitly")
    if "availability_manifest" in dataset and dataset["availability_manifest"] != [
            {**m, "dataset_fingerprint": dataset["dataset_fingerprint"]} for m in dataset["audit"]]:
        raise ValueError("Replay availability manifest mismatch")
    return dataset


def write_summary(dataset, report, destination):
    lines = ["# NFL Phase 2 measured backtest results", "", f"Status: **{report['status']}**.", "",
             f"Evidence class: **{dataset.get('evidence_class', 'A')}**. "
             + ("Historically reconstructed under explicit assumptions; not historically observed." if dataset.get("evidence_class") == "B" else "Point-in-time observed only."), "",
             f"Dataset fingerprint: `{dataset['dataset_fingerprint']}`.", "",
             "| Season | Schedule games | Eligible games | Exclusion reasons |", "|---|---:|---:|---|"]
    for season, coverage in dataset["coverage"].items():
        reasons = "; ".join(f"{k}: {v}" for k, v in coverage["exclusions"].items()) or "None"
        lines.append(f"| {season} | {coverage['total']} | {coverage['eligible']} | {reasons} |")
    lines += ["", "| Model | Log loss | Brier | Accuracy | ECE | Games |", "|---|---:|---:|---:|---:|---:|"]
    for name in ("home_prior:selected", "elo:selected", "logistic_baseline:selected", "full_logistic:selected", "nonlinear_forest:selected"):
        m = report["models"].get(name)
        lines.append(f"| {name} | " + (f"{m['log_loss']:.6f} | {m['brier']:.6f} | {m['accuracy']:.6f} | {m['ece']:.6f} | {m['count']} |" if m else "N/A | N/A | N/A | N/A | 0 |"))
    lines += ["", f"Evaluated games: {report.get('evaluated_games', 0)}. No prediction model was enabled or deployed.", "",
              "Binary probabilities condition on no tie. Tied outcomes are excluded explicitly.", "",
              "Source publication history is unknown. First local observation cannot be backdated to the season of an archive.", "",
              "Calibration and ablation metrics are in evaluation.json when valid folds exist. N/A means no measured NFL result."]
    if not report["models"]:
        lines += ["", "No candidate model was fitted and no NFL improvement or calibration claim is supported.",
                  "A reliable multi-window evaluation requires prospective source vintages or a verified pregame publication archive."]
    Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
    with Path(destination).with_name("probability_buckets.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["evidence_class", "model", "low", "high", "count", "mean_probability", "home_win_rate"])
        for name, data in sorted(report["models"].items()):
            for bucket in data["reliability"]:
                writer.writerow([dataset.get("evidence_class", "A"), name] + [bucket[k] for k in
                    ("low", "high", "count", "mean_probability", "home_win_rate")])


def charts(report, output):
    if not report["models"]:
        return {"available": False, "reason": "No genuine out-of-sample NFL predictions to plot"}
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return {"available": False, "reason": "Optional matplotlib is absent; evaluation.json contains plot data"}
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    fig.suptitle(f"Class {report.get('evidence_class', 'A')} NFL backtest: {report.get('evaluated_games', 0)} games"
                 + (" — reconstructed under assumptions" if report.get("evidence_class") == "B" else ""))
    axes[0].plot([0, 1], [0, 1], "--", color="gray", label="Perfect calibration")
    for name in ("home_prior:selected", "elo:selected", "logistic_baseline:selected", "full_logistic:selected", "nonlinear_forest:selected"):
        data = report["models"].get(name)
        if not data:
            continue
        buckets = [b for b in data["reliability"] if b["count"]]
        axes[0].plot([b["mean_probability"] for b in buckets], [b["home_win_rate"] for b in buckets], marker="o", label=name)
        axes[1].step([(b["low"] + b["high"]) / 2 for b in data["confidence_distribution"]],
                     [b["count"] for b in data["confidence_distribution"]], label=name, where="mid")
    axes[0].set(xlabel="Predicted home win probability", ylabel="Observed home win frequency", xlim=(0, 1), ylim=(0, 1))
    axes[1].set(xlabel="Maximum binary probability", ylabel="Game count", xlim=(.5, 1))
    for axis in axes:
        axis.legend(fontsize=7)
        axis.grid(alpha=.2)
    path = Path(output) / "reliability_and_confidence.svg"
    fig.savefig(path)
    fig.savefig(path.with_suffix(".png"), dpi=160)
    plt.close(fig)
    compared = [n for n in ("logistic_baseline", "full_logistic", "nonlinear_forest") if f"{n}:selected" in report["models"]]
    comparison, panels = plt.subplots(1, len(compared), figsize=(5 * len(compared), 5), constrained_layout=True)
    if len(compared) == 1:
        panels = [panels]
    comparison.suptitle(f"Class {report.get('evidence_class', 'A')} calibration comparison — prior-data selection only")
    for axis, name in zip(panels, compared, strict=True):
        axis.plot([0, 1], [0, 1], "--", color="gray", label="Perfect calibration")
        for method in ("none", "sigmoid", "isotonic", "selected"):
            data = report["models"].get(f"{name}:{method}")
            if not data:
                continue
            buckets = [b for b in data["reliability"] if b["count"]]
            axis.plot([b["mean_probability"] for b in buckets], [b["home_win_rate"] for b in buckets],
                      marker="o", linestyle="--" if method == "selected" else "-", label=method)
        axis.set(title=name, xlabel="Predicted home win probability", ylabel="Observed home win frequency", xlim=(0, 1), ylim=(0, 1))
        axis.legend(fontsize=7)
        axis.grid(alpha=.2)
    comparison_path = Path(output) / "calibration_raw_vs_selected.svg"
    comparison.savefig(comparison_path)
    comparison.savefig(comparison_path.with_suffix(".png"), dpi=160)
    plt.close(comparison)
    return {"available": True, "path": str(path), "calibration_comparison": str(comparison_path), "matplotlib_version": matplotlib.__version__}


def run(seasons, *, path=None, output=None, lead_minutes=60, min_games=3, config=None, evidence_class="A", policy=None, replay_dataset=None):
    if replay_dataset is not None:
        if path is not None:
            raise ValueError("Choose a frozen dataset or a source database")
        dataset = load_replay_dataset(replay_dataset, evidence_class)
        lead_minutes = dataset["config"]["lead_minutes"]
    elif evidence_class == "B":
        from services.nfl_historical_recovery import build_reconstructed_dataset
        dataset = build_reconstructed_dataset(seasons, path=path, lead_minutes=lead_minutes, min_games=min_games, policy=policy)
    elif evidence_class == "A":
        dataset = build_dataset(seasons, path=path, lead_minutes=lead_minutes, min_games=min_games)
    else:
        raise ValueError("Evidence class must be A or B")
    report, predictions, artifacts = evaluate(dataset, config)
    report["prediction_lead_minutes"] = lead_minutes
    entry = research.save_run(dataset, report, predictions, artifacts, root=output)
    destination = Path(output or research.root_path()) / entry["run_id"]
    write_summary(dataset, report, destination / "results.md")
    chart = charts(report, destination)
    return {**entry, "coverage": dataset["coverage"], "summary": str(destination / "results.md"), "charts": chart}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", default="2022,2023,2024,2025,2026")
    parser.add_argument("--database")
    parser.add_argument("--replay-dataset", help="Replay a fingerprint-verified frozen dataset without reading the current source database")
    parser.add_argument("--output")
    parser.add_argument("--lead-minutes", type=int, default=60)
    parser.add_argument("--min-games", type=int, default=3)
    parser.add_argument("--evidence-class", choices=("A", "B"), default="A", help="B is explicitly reconstructed, never observed")
    parser.add_argument("--publication-lag-hours", type=int, default=168)
    parser.add_argument("--completion-bound-hours", type=int, default=12)
    parser.add_argument("--prepare-upcoming", action="store_true", help="Prepare compact prospective snapshots offline after the audit")
    parser.add_argument("--snapshots-only", action="store_true", help="Prepare upcoming snapshots without fitting or evaluating models")
    args = parser.parse_args()
    if args.replay_dataset and (args.database or args.prepare_upcoming or args.snapshots_only):
        parser.error("Frozen replay cannot be combined with database or prospective snapshot options")
    seasons = [int(v) for v in args.seasons.split(",")]
    if args.snapshots_only:
        if args.evidence_class != "A":
            parser.error("Prospective snapshots require Class A")
        print(json.dumps(research.prepare_snapshots(seasons, path=args.database, root=args.output,
                         lead_minutes=args.lead_minutes, min_games=args.min_games), indent=2))
        return
    from services.nfl_historical_recovery import RecoveryPolicy
    result = run(seasons, path=args.database, output=args.output,
                 lead_minutes=args.lead_minutes, min_games=args.min_games, evidence_class=args.evidence_class,
                 policy=RecoveryPolicy(args.publication_lag_hours, args.completion_bound_hours), replay_dataset=args.replay_dataset)
    if args.prepare_upcoming:
        result["snapshots"] = research.prepare_snapshots(seasons, path=args.database, root=args.output,
                              lead_minutes=args.lead_minutes, min_games=args.min_games)
    print(json.dumps(result, indent=2))
    if result["status"] != "evaluated_research_only":
        raise SystemExit(2)  # successful audit, insufficient evidence for training


if __name__ == "__main__":
    main()
