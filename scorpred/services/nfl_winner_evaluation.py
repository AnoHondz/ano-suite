"""Chronological winner research: fixed baselines, bounded models and calibration.

Never invoked by production predictions. Models serialize as validated JSON,
without pickle/joblib loading. Label, tuning, calibration and test windows are
disjoint; all same-season/week rows stay together.
"""
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import numpy as np
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from services.nfl_winner_features import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    SCHEMA,
    aware,
)

MODEL_VERSION = "nfl-winner-research-v2"
BASELINE_NAMES = ("home_field", "diff_w8_off_epa_per_play", "diff_w8_def_epa_per_play", "diff_win_rate", "diff_rest_days")
SEED = 1729


@dataclass(frozen=True)
class EvaluationConfig:
    train_groups: int = 20
    tune_groups: int = 8
    calibration_groups: int = 8
    test_groups: int = 8
    min_train: int = 200
    min_tune: int = 50
    min_calibration: int = 80
    min_test: int = 30
    isotonic_min_fit: int = 1000
    seed: int = SEED
    bootstrap_samples: int = 500
    split_strategy: str = "fixed_groups"
    candidate_names: tuple | None = None

    def __post_init__(self):
        if min(self.train_groups, self.tune_groups, self.test_groups, self.min_train, self.min_tune,
               self.min_calibration, self.min_test, self.bootstrap_samples) < 1 or self.calibration_groups < 2:
            raise ValueError("Evaluation windows/sample thresholds must be positive; calibration needs two groups")
        if self.isotonic_min_fit < 1000:
            raise ValueError("Isotonic calibration requires at least 1000 fitting observations")
        if self.split_strategy not in {"fixed_groups", "adaptive_past_windows"}:
            raise ValueError("Unknown chronological split strategy")
        if self.candidate_names is not None and (not self.candidate_names or not set(self.candidate_names) <= set(model_specs())):
            raise ValueError("Unknown or empty candidate selection")


def metrics(labels, probabilities):
    y, p = np.asarray(labels, dtype=float), np.asarray(probabilities, dtype=float)
    if len(y) == 0 or y.shape != p.shape or not np.isin(y, [0, 1]).all() or not np.isfinite(p).all() or np.any((p < 0) | (p > 1)):
        raise ValueError("Expected nonempty binary labels and finite probabilities in [0,1]")
    clipped = p.clip(1e-12, 1 - 1e-12)
    buckets, confidence = [], []
    for i in range(10):
        mask = (p >= i / 10) & ((p < (i + 1) / 10) if i < 9 else p <= 1)
        selected = p[mask]
        buckets.append({"low": i / 10, "high": (i + 1) / 10, "count": int(mask.sum()),
                        "mean_probability": float(selected.mean()) if len(selected) else None,
                        "home_win_rate": float(y[mask].mean()) if len(selected) else None})
        confidence.append({"low": i / 10, "high": (i + 1) / 10, "count": int(((np.maximum(p, 1 - p) >= i / 10) &
                           ((np.maximum(p, 1 - p) < (i + 1) / 10) if i < 9 else np.maximum(p, 1 - p) <= 1)).sum())})
    ece = sum(b["count"] / len(y) * abs(b["mean_probability"] - b["home_win_rate"]) for b in buckets if b["count"])
    return {"count": len(y), "log_loss": float(-(y * np.log(clipped) + (1 - y) * np.log(1 - clipped)).mean()),
            "brier": float(((p - y) ** 2).mean()), "accuracy": float(((p >= .5) == y).mean()),
            "roc_auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
            "ece": float(ece), "reliability": buckets, "confidence_distribution": confidence}


def grouped_rows(dataset):
    rows, labels = dataset["rows"], dataset["labels"]
    from services.nfl_reliability import reject_sealed_games
    reject_sealed_games([r["game_id"] for r in rows] + list(labels))
    population = dataset.get("evidence_class", "A")
    if population not in {"A", "B"} or any(r.get("evidence_class", "A") != population for r in rows):
        raise ValueError("Evidence classes must be evaluated separately")
    if len({r["game_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate game IDs in dataset")
    groups = defaultdict(list)
    for row in rows:
        cutoff = aware(row["prediction_cutoff"])
        provenance = row["provenance"]
        available = provenance["source_observed_max"] if population == "A" else provenance["availability_assumed_max"]
        if population == "B" and (provenance.get("evidence_class") != "B"
                or not provenance.get("historically_reconstructed") or not provenance.get("known_revision_check")):
            raise ValueError("Class B requires explicit assumptions and revision checks")
        too_late = aware(available) >= cutoff if population == "B" else aware(available) > cutoff
        if cutoff >= aware(row["kickoff"]) or too_late:
            raise ValueError("Feature availability violates pregame cutoff")
        if row["game_id"] not in labels or labels[row["game_id"]]["home_win"] not in (0, 1):
            raise ValueError("Missing binary outcome")
        groups[(row["season"], row["week"])].append(row)
    ordered = sorted(groups.values(), key=lambda part: min(r["prediction_cutoff"] for r in part))
    # The whole earlier week must finish predicting before the next begins.
    for a, b in pairwise(ordered):
        if max(r["prediction_cutoff"] for r in a) >= min(r["prediction_cutoff"] for r in b):
            raise ValueError("Overlapping game-week prediction groups")
    return ordered


def folds(dataset, config):
    ordered, labels = grouped_rows(dataset), dataset["labels"]
    if config.split_strategy == "adaptive_past_windows":
        from services.nfl_reliability import adaptive_folds
        return adaptive_folds(dataset, config, ordered)
    initial = config.train_groups + config.tune_groups + config.calibration_groups
    accepted, exclusions = [], []
    for boundary in range(initial, len(ordered), config.test_groups):
        cal_start = boundary - config.calibration_groups
        train_end = cal_start - config.tune_groups
        cal_split = cal_start + config.calibration_groups // 2
        windows = {"train": ordered[:train_end], "tune": ordered[train_end:cal_start],
                   "calibration_fit": ordered[cal_start:cal_split], "calibration_select": ordered[cal_split:boundary],
                   "test": ordered[boundary:boundary + config.test_groups]}
        flat = {name: sorted([r for part in parts for r in part], key=lambda r: (r["prediction_cutoff"], r["game_id"])) for name, parts in windows.items()}
        # Purge whole groups if any outcome used for fitting/selection was only
        # observed after the next fitting stage. Never split a week to fill it.
        for name, next_name in (("train", "tune"), ("tune", "calibration_fit"),
                                ("calibration_fit", "calibration_select"), ("calibration_select", "test")):
            next_cutoff = min(r["prediction_cutoff"] for r in flat[next_name])
            unsafe = {(r["season"], r["week"]) for r in flat[name] if aware(labels[r["game_id"]]["usable_after"]) >= aware(next_cutoff)}
            flat[name] = [r for r in flat[name] if (r["season"], r["week"]) not in unsafe]
        sizes = {name: len(part) for name, part in flat.items()}
        sufficient = (sizes["train"] >= config.min_train and sizes["tune"] >= config.min_tune
                      and sizes["calibration_fit"] + sizes["calibration_select"] >= config.min_calibration
                      and min(sizes["calibration_fit"], sizes["calibration_select"]) >= 10 and sizes["test"] >= config.min_test)
        classes = all(len({labels[r["game_id"]]["home_win"] for r in flat[name]}) == 2 for name in ("train", "tune", "calibration_fit", "calibration_select"))
        if sufficient and classes:
            accepted.append(flat)
        else:
            exclusions.append({"boundary": boundary, "sizes": sizes, "reason": "sample_thresholds_or_single_class"})
    return accepted, exclusions


def matrix(rows, names):
    return np.asarray([[np.nan if r["features"].get(name) is None else r["features"][name] for name in names] for r in rows], dtype=float)


def train_classifier(rows, labels, names, kind, parameter, seed=SEED):
    from services.nfl_reliability import reject_sealed_games
    reject_sealed_games([r["game_id"] for r in rows])
    x, y = matrix(rows, names), np.array([labels[r["game_id"]]["home_win"] for r in rows])
    if set(y) != {0, 1}:
        raise ValueError("Classifier fitting requires both binary classes")
    imputer = SimpleImputer(strategy="median", keep_empty_features=True).fit(x)
    scale = StandardScaler().fit(imputer.transform(x))
    x = scale.transform(imputer.transform(x))
    if kind == "logistic":
        model = LogisticRegression(C=parameter, max_iter=3000, random_state=seed).fit(x, y)
        encoded = {"kind": "logistic", "coef": model.coef_[0].tolist(), "intercept": float(model.intercept_[0])}
    elif kind == "forest":
        model = RandomForestClassifier(n_estimators=80, max_depth=parameter, min_samples_leaf=20,
                                       max_features=.7, random_state=seed, n_jobs=1).fit(x, y)
        trees = []
        for estimator in model.estimators_:
            tree = estimator.tree_
            probability = tree.value[:, 0, 1] / tree.value[:, 0, :].sum(axis=1)
            trees.append({"left": tree.children_left.tolist(), "right": tree.children_right.tolist(),
                          "feature": tree.feature.tolist(), "threshold": tree.threshold.tolist(), "p": probability.tolist()})
        encoded = {"kind": "forest", "trees": trees}
    else:
        raise ValueError("Unknown candidate model")
    return {**encoded, "feature_names": list(names), "fill": imputer.statistics_.tolist(), "center": scale.mean_.tolist(),
            "scale": scale.scale_.tolist(), "parameter": parameter, "preprocessing_train_game_ids": [r["game_id"] for r in rows]}


def sigmoid(values):
    values = np.asarray(values, dtype=float).clip(-700, 700)
    return 1 / (1 + np.exp(-values))


def predict_portable(model, rows):
    """Bounded JSON inference; tree arrays cannot execute code or loop forever."""
    names = model["feature_names"]
    if not names or len(names) > 512 or len(set(names)) != len(names) or not set(names) <= set(FEATURE_NAMES):
        raise ValueError("Invalid portable feature schema")
    vectors = [np.asarray(model[key], dtype=float) for key in ("fill", "center", "scale")]
    if any(v.shape != (len(names),) or not np.isfinite(v).all() for v in vectors) or np.any(vectors[2] <= 0):
        raise ValueError("Invalid portable preprocessing")
    x = matrix(rows, names)
    x = (np.where(np.isnan(x), vectors[0], x) - vectors[1]) / vectors[2]
    if not np.isfinite(x).all():
        raise ValueError("Nonfinite input features")
    if model["kind"] == "logistic":
        coefficients = np.asarray(model["coef"], dtype=float)
        if coefficients.shape != (len(names),) or not np.isfinite(coefficients).all() or not math.isfinite(model["intercept"]):
            raise ValueError("Invalid portable logistic parameters")
        return sigmoid(x @ coefficients + model["intercept"])
    if model["kind"] != "forest" or not 1 <= len(model["trees"]) <= 200:
        raise ValueError("Invalid portable forest")
    # sklearn's tree inference converts predictors to float32 before traversal.
    x = x.astype(np.float32)
    if not np.isfinite(x).all():
        raise ValueError("Forest input exceeds float32 range")
    results = []
    for tree in model["trees"]:
        size = len(tree["left"])
        if not 1 <= size <= 4096 or any(len(tree[k]) != size for k in ("right", "feature", "threshold", "p")):
            raise ValueError("Invalid portable tree shape")
        if not all(math.isfinite(v) for k in ("threshold", "p") for v in tree[k]) or any(not 0 <= p <= 1 for p in tree["p"]):
            raise ValueError("Invalid portable tree numbers")
        probabilities = []
        for row in x:
            node = 0
            for _ in range(size):
                if type(node) is not int or not 0 <= node < size:
                    raise ValueError("Invalid portable tree child")
                if tree["left"][node] == -1 and tree["right"][node] == -1:
                    probabilities.append(tree["p"][node])
                    break
                feature = tree["feature"][node]
                if type(feature) is not int or not 0 <= feature < len(names):
                    raise ValueError("Invalid portable tree feature")
                node = tree["left"][node] if row[feature] <= tree["threshold"][node] else tree["right"][node]
            else:
                raise ValueError("Cyclic portable tree")
        results.append(probabilities)
    return np.mean(results, axis=0)


def calibrate_fit(probabilities, labels, method):
    probabilities, labels = np.asarray(probabilities), np.asarray(labels)
    if method == "none":
        return {"method": "none"}
    if len(set(labels)) != 2:
        raise ValueError("Calibration fitting requires both classes")
    if method == "sigmoid":
        x = np.log(probabilities.clip(1e-9, 1 - 1e-9) / (1 - probabilities.clip(1e-9, 1 - 1e-9))).reshape(-1, 1)
        fitted = LogisticRegression(C=1000, max_iter=2000, random_state=SEED).fit(x, labels)
        return {"method": method, "coefficient": float(fitted.coef_[0, 0]), "intercept": float(fitted.intercept_[0])}
    if method == "isotonic":
        if len(labels) < 1000:
            raise ValueError("Isotonic fitting requires 1000 or more observations")
        fitted = IsotonicRegression(out_of_bounds="clip").fit(probabilities, labels)
        return {"method": method, "x": fitted.X_thresholds_.tolist(), "y": fitted.y_thresholds_.tolist()}
    raise ValueError("Unknown calibration method")


def calibrate_predict(calibrator, probabilities):
    p = np.asarray(probabilities, dtype=float)
    if calibrator["method"] == "none":
        return p
    if calibrator["method"] == "sigmoid":
        if not all(math.isfinite(calibrator[key]) for key in ("coefficient", "intercept")):
            raise ValueError("Invalid calibration parameters")
        logits = np.log(p.clip(1e-9, 1 - 1e-9) / (1 - p.clip(1e-9, 1 - 1e-9)))
        return sigmoid(calibrator["coefficient"] * logits + calibrator["intercept"])
    if calibrator["method"] == "isotonic":
        x, y = np.asarray(calibrator["x"]), np.asarray(calibrator["y"])
        if len(x) != len(y) or not 1 <= len(x) <= 10000 or not np.isfinite(x).all() or not np.isfinite(y).all() or np.any(np.diff(x) < 0) or np.any(np.diff(y) < 0) or np.any((y < 0) | (y > 1)):
            raise ValueError("Invalid isotonic knots")
        return np.interp(p, x, y)
    raise ValueError("Unknown calibration")


def model_specs(allowed_names=None):
    groups = {
        "basic_strength": ("home_field", "diff_win_rate", "diff_strength_of_schedule"),
        "epa": tuple(n for n in FEATURE_NAMES if "epa" in n and "qb" not in n),
        "recent": tuple(n for n in FEATURE_NAMES if "_w3_" in n and "qb" not in n) + ("diff_win_rate",),
        "quarterback": tuple(n for n in FEATURE_NAMES if "qb" in n),
        "rest_home": ("home_field", "diff_rest_days", "diff_short_week", "diff_bye_week"),
    }
    specs = {"logistic_baseline": ("logistic", BASELINE_NAMES, (.1, 1.0)),
             "full_logistic": ("logistic", FEATURE_NAMES, (.1, 1.0)),
             "nonlinear_forest": ("forest", FEATURE_NAMES, (3, 4))}
    specs.update({f"ablation_{name}": ("logistic", names, (.1,)) for name, names in groups.items()})
    for group in ("quarterback", "epa", "rest_home"):
        specs[f"without_{group}"] = ("logistic", tuple(n for n in FEATURE_NAMES if n not in groups[group]), (.1,))
    if allowed_names is not None:
        allowed = set(allowed_names)
        specs = {name: (kind, tuple(n for n in names if n in allowed), parameters)
                 for name, (kind, names, parameters) in specs.items() if any(n in allowed for n in names)}
    return specs


def paired_intervals(predictions, reference="home_prior:selected", *, seed=SEED, samples=500, confidence=.95):
    """Paired season/week block bootstrap; negative deltas favor the candidate."""
    by_model = defaultdict(dict)
    for p in predictions:
        by_model[p["model_version"]][p["game_id"]] = p
    baseline = by_model.get(reference, {})
    result, random = {}, np.random.default_rng(seed)
    for model, values in sorted(by_model.items()):
        if model == reference:
            continue
        common = sorted(set(values) & set(baseline))
        blocks = defaultdict(list)
        for game_id in common:
            r = values[game_id]
            blocks[(r["season"], r["week"])].append(game_id)
        if len(blocks) < 5:
            result[model] = {"available": False, "reason": "Fewer than five paired week blocks"}
            continue
        # Resample paired week sums/counts. Recomputing AUC/reliability on every
        # bootstrap draw is unnecessary for the two proper scoring rules.
        groups = list(blocks.values())
        sums, counts = [], []
        for group in groups:
            y = np.array([values[g]["actual_home_win"] for g in group], dtype=float)
            a = np.array([values[g]["home_win_probability"] for g in group]).clip(1e-12, 1 - 1e-12)
            b = np.array([baseline[g]["home_win_probability"] for g in group]).clip(1e-12, 1 - 1e-12)
            delta = -(y * np.log(a) + (1 - y) * np.log(1 - a)) + y * np.log(b) + (1 - y) * np.log(1 - b)
            # Brier uses the original probabilities, including exact 0/1.
            raw_a = np.array([values[g]["home_win_probability"] for g in group])
            raw_b = np.array([baseline[g]["home_win_probability"] for g in group])
            sums.append([delta.sum(), (((raw_a - y) ** 2) - ((raw_b - y) ** 2)).sum()])
            counts.append(len(group))
        selections = random.integers(0, len(groups), (samples, len(groups)))
        deltas = np.asarray(sums)[selections].sum(axis=1) / np.asarray(counts)[selections].sum(axis=1)[:, None]
        tail = (1 - confidence) / 2
        bounds = np.quantile(deltas, [tail, 1 - tail], axis=0)
        result[model] = {"available": True, "games": len(common), "week_blocks": len(groups),
                         "confidence": confidence, "bootstrap_samples": samples,
                         "log_loss_delta_interval": bounds[:, 0].tolist(), "brier_delta_interval": bounds[:, 1].tolist(),
                         "log_loss_delta_95": bounds[:, 0].tolist(), "brier_delta_95": bounds[:, 1].tolist()}
        if confidence != .95:
            result[model].pop("log_loss_delta_95")
            result[model].pop("brier_delta_95")
    return result


def evaluate(dataset, config=None, *, now=None):
    config = config or EvaluationConfig()
    now = aware(now or datetime.now(UTC))
    from services.nfl_historical_recovery import FEATURE_VERSION as B_VERSION
    from services.nfl_historical_recovery import SCHEMA as B_SCHEMA
    version, schema = dataset.get("feature_version"), dataset.get("schema")
    population = dataset.get("evidence_class", "A")
    if (version, schema) != ((B_VERSION, B_SCHEMA) if population == "B" else (FEATURE_VERSION, SCHEMA)):
        raise ValueError("Incompatible research dataset")
    planned, skipped = folds(dataset, config)
    report = {"version": MODEL_VERSION, "feature_version": version, "schema": schema, "evidence_class": population,
              "historically_reconstructed": population == "B",
              "supported_feature_names": dataset.get("supported_feature_names", list(FEATURE_NAMES)),
              "unavailable_ablations": sorted(set(model_specs()) - set(model_specs(dataset.get("supported_feature_names")))),
              "generated_at": now.isoformat(), "research_only": True, "enabled": False, "production_active": False,
              "dataset_fingerprint": dataset["dataset_fingerprint"], "config": asdict(config), "sklearn_version": sklearn.__version__,
              "feature_config": dataset["config"],
              "eligible_games": len(dataset["rows"]), "coverage": dataset["coverage"], "folds": [], "skipped_folds": skipped,
              "production_comparison": {"available": False, "reason": "No verified archived historical production probabilities supplied"},
              "favorite_underdog_comparison": {"available": False, "reason": "No independently defined historical market favorites"},
              "tie_policy": "binary probabilities conditional on no tie; tied labels excluded",
              "status": "insufficient_safe_history", "models": {}, "recommended_model": None,
              "improvement_established": False}
    if not planned:
        return report, [], {}
    isotonic_supported = all(len(part["calibration_fit"]) >= config.isotonic_min_fit for part in planned)
    labels, predictions, artifacts = dataset["labels"], [], {}
    for fold_id, parts in enumerate(planned):
        y = {name: [labels[r["game_id"]]["home_win"] for r in rows] for name, rows in parts.items()}
        windows = {name: {"count": len(rows), "start": rows[0]["prediction_cutoff"], "end": rows[-1]["prediction_cutoff"],
                          "game_ids": [r["game_id"] for r in rows], "max_label_usable_after": max(labels[r["game_id"]]["usable_after"] for r in rows)}
                   for name, rows in parts.items()}
        report["folds"].append({"fold": fold_id, "windows": windows, "calibration": {}, "hyperparameter_selection": {}})
        current = report["folds"][-1]

        def record(name, method, probabilities, calibration_method=None, *, parts=parts, windows=windows, fold_id=fold_id):
            for row, probability in zip(parts["test"], probabilities, strict=True):
                p = float(probability)
                predictions.append({"game_id": row["game_id"], "season": row["season"], "week": row["week"],
                                    "prediction_cutoff": row["prediction_cutoff"], "kickoff": row["kickoff"],
                                    "home_team": row["home_team"], "away_team": row["away_team"],
                                    "home_win_probability": p, "away_win_probability": 1 - p,
                                    "actual_home_win": labels[row["game_id"]]["home_win"], "model_version": f"{name}:{method}",
                                    "feature_version": version, "model_definition_version": MODEL_VERSION,
                                    "evidence_class": population, "historically_reconstructed": population == "B",
                                    "dataset_fingerprint": dataset["dataset_fingerprint"],
                                    "training_cutoff": (aware(windows["tune"]["start"]) - timedelta(microseconds=1)).isoformat(),
                                    "training_period_end": windows["train"]["end"],
                                    "training_label_cutoff": windows["train"]["max_label_usable_after"],
                                    "calibration_version": f"{MODEL_VERSION}:fold{fold_id}:{method}",
                                    "calibration_method": calibration_method or ("none" if method == "selected" else method),
                                    "prediction_generated_at": now.isoformat(), "kind": "backtest", "fold": fold_id})

        prior = float(np.mean(y["train"]))
        record("home_prior", "selected", np.full(len(parts["test"]), prior))
        record("elo", "selected", [r["baselines"]["elo"]["home_probability"] for r in parts["test"]])
        allowed = dataset.get("supported_feature_names")
        for name, (kind, names, parameters) in model_specs(allowed).items():
            if config.candidate_names is not None and name not in config.candidate_names:
                continue
            candidates = []
            for parameter in parameters:
                model = train_classifier(parts["train"], labels, names, kind, parameter, config.seed)
                score = metrics(y["tune"], predict_portable(model, parts["tune"]))
                candidates.append((score["log_loss"], parameter, model, score))
            _, parameter, model, development = min(candidates, key=lambda candidate: (candidate[0], candidate[1]))
            current["hyperparameter_selection"][name] = {"parameter": parameter, "tuning_metrics": development,
                                                        "candidates": [{"parameter": c[1], "log_loss": c[0]} for c in candidates]}
            raw = {part: predict_portable(model, parts[part]) for part in ("calibration_fit", "calibration_select", "test")}
            methods = ["none", "sigmoid"] + (["isotonic"] if isotonic_supported else [])
            calibrators = {method: calibrate_fit(raw["calibration_fit"], y["calibration_fit"], method) for method in methods}
            scores = {method: metrics(y["calibration_select"], calibrate_predict(c, raw["calibration_select"])) for method, c in calibrators.items()}
            baseline = scores["none"]
            improved = [m for m in methods if scores[m]["log_loss"] < baseline["log_loss"] and scores[m]["brier"] <= baseline["brier"]]
            selected = min(improved, key=lambda m: (scores[m]["log_loss"], m)) if improved else "none"
            current["calibration"][name] = {"selected": selected, "selection_metrics": scores,
                                           "isotonic_available": "isotonic" in methods,
                                           "isotonic_reason": None if "isotonic" in methods else "Every evaluation fold needs at least 1000 calibration fitting rows"}
            for method, c in calibrators.items():
                record(name, method, calibrate_predict(c, raw["test"]))
            record(name, "selected", calibrate_predict(calibrators[selected], raw["test"]), selected)
            artifacts[f"fold{fold_id}_{name}"] = {"version": MODEL_VERSION, "model_name": name, "feature_version": version,
                "schema": schema, "evidence_class": population, "historically_reconstructed": population == "B",
                "research_only": True, "enabled": False, "production_active": False,
                "generated_at": now.isoformat(), "dataset_fingerprint": dataset["dataset_fingerprint"],
                "sklearn_version": sklearn.__version__, "windows": windows, "model": model,
                "calibration": calibrators[selected], "evaluation_fold": fold_id}
    grouped = defaultdict(list)
    for row in predictions:
        grouped[row["model_version"]].append(row)
    common_games = None
    for name, rows in sorted(grouped.items()):
        ids = {r["game_id"] for r in rows}
        if len(ids) != len(rows) or (common_games is not None and ids != common_games):
            raise ValueError("Models must evaluate identical games exactly once")
        common_games = ids
        report["models"][name] = metrics([r["actual_home_win"] for r in rows], [r["home_win_probability"] for r in rows])
        report["models"][name]["by_season"] = {}
        report["models"][name]["by_week"] = {}
        for key, label in (("season", "by_season"), ("week", "by_week")):
            for value in sorted({r[key] for r in rows}):
                subset = [r for r in rows if r[key] == value]
                report["models"][name][label][str(value)] = metrics([r["actual_home_win"] for r in subset], [r["home_win_probability"] for r in subset])
        report["models"][name]["by_fold"] = {str(f): metrics(
            [r["actual_home_win"] for r in rows if r["fold"] == f],
            [r["home_win_probability"] for r in rows if r["fold"] == f]) for f in sorted({r["fold"] for r in rows})}
    report.update(status="evaluated_research_only", evaluated_games=len(common_games or []))
    report["not_evaluated_game_ids"] = sorted({r["game_id"] for r in dataset["rows"]} - (common_games or set()))
    report["paired_comparisons_vs_home_prior"] = paired_intervals(predictions, seed=config.seed, samples=config.bootstrap_samples)
    report["paired_comparisons_vs_elo"] = paired_intervals(predictions, "elo:selected", seed=config.seed, samples=config.bootstrap_samples)
    # Predeclare the simple baseline as the research starting point. No
    # retrospective test ranking selects or promotes a deployed artifact.
    report["recommended_model"] = "logistic_baseline_pending_prospective_validation"
    report["selection_policy"] = "hyperparameters on tuning; calibration on later disjoint selection; no test-based activation"
    return report, predictions, artifacts
