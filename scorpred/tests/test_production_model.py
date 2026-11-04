import json
from datetime import date, timedelta

import joblib
import pytest

import ml_service
import production_model as production
from runtime_paths import clean_soccer_model_path, historical_dataset_path
from scripts.model_artifacts import create_manifest, ensure_models


def test_packaged_model_predicts_using_current_runtime_schema():
    rows, _ = production.build_clean_features(historical_dataset_path())
    row = rows[-1]
    result = ml_service.predict_match(row)
    assert result["available"] is True, result.get("error")
    bundle = joblib.load(clean_soccer_model_path())
    expected = bundle["model"].predict_proba(production.feature_matrix([row]))[0]
    assert result["probabilities"] == pytest.approx(expected, abs=1e-6)
    assert sum(result["probabilities"]) == pytest.approx(1, abs=2e-6)


def test_preprocessing_fits_only_training_and_calibration_keeps_the_fitted_forest(tmp_path, monkeypatch):
    rows = []
    for i in range(120):
        row = {name: 0.0 for name in production.FEATURE_COLUMNS}
        row.update(date=(date(2020, 1, 1) + timedelta(days=i)).isoformat(), target=i % 3)
        row["home_implied_prob"] = 0.4 if i < 72 else 0.9
        rows.append(row)
    source = tmp_path / "source.csv"
    source.write_text("controlled fixture", encoding="utf-8")
    monkeypatch.setattr(production, "build_clean_features", lambda _: (rows, {}))
    model_path, report_path = tmp_path / "model.pkl", tmp_path / "report.json"
    report = production.train_production_model(source, model_path, report_path)
    bundle = joblib.load(model_path)
    assert bundle["feature_fill_values"]["home_implied_prob"] == 0.4
    assert report["windows"]["train"]["end"] < report["windows"]["calibration"]["start"]
    assert report["windows"]["calibration"]["end"] < report["windows"]["test"]["start"]
    assert bundle["model"].estimator.estimator.named_steps["classifier"].n_features_in_ == len(production.FEATURE_COLUMNS)
    assert report["profitability"]["evaluated"] is False


def test_splits_keep_matches_from_the_same_date_together():
    rows = [{"date": f"2020-01-{i // 4 + 1:02d}"} for i in range(80)]
    train, cal, test = production.chronological_splits(rows)
    assert train[-1]["date"] < cal[0]["date"]
    assert cal[-1]["date"] < test[0]["date"]


def test_manifest_rejects_corruption_before_unpickling(tmp_path):
    directory = tmp_path / "models"
    directory.mkdir()
    model = directory / "soccer_random_forest_clean.pkl"
    model.write_bytes(clean_soccer_model_path().read_bytes())
    create_manifest(directory)
    ensure_models(directory / "manifest.json", download=False)
    model.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        ensure_models(directory / "manifest.json", download=False)


def test_manifest_rejects_path_traversal(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"sklearn_version": production.sklearn.__version__, "files": {"../bad.pkl": {"sha256": "bad"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe"):
        ensure_models(path)
