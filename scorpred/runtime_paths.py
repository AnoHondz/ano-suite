"""Shared runtime path helpers for local cache and generated data."""

from __future__ import annotations

import os
from pathlib import Path
from shutil import copy2


_REPO_ROOT = Path(__file__).resolve().parent
_DATA_ROOT_ENV = "SCORPRED_DATA_ROOT"
_PERSISTENT_ROOT_ENV = "SCORPRED_PERSISTENT_ROOT"


def repo_root() -> Path:
    return _REPO_ROOT


def data_root() -> Path:
    configured = os.getenv(_DATA_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return _REPO_ROOT


def persistent_root() -> Path:
    configured = os.getenv(_PERSISTENT_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    if os.getenv("RENDER"):
        render_candidates = (
            Path("/var/data/scorpred"),
            Path("/persistent/scorpred"),
        )
        for candidate in render_candidates:
            if candidate.exists():
                return candidate.resolve()

    return (data_root() / "persistent").resolve()


def cache_root() -> Path:
    return data_root() / "cache"


def data_dir() -> Path:
    # Keep generated/runtime data under SCORPRED_DATA_ROOT when configured.
    # When SCORPRED_DATA_ROOT is unset, data_root() falls back to repo root so
    # this remains <repo>/data for local development.
    return data_root() / "data"


def cache_dir(*parts: str) -> Path:
    return cache_root().joinpath(*parts)


def prediction_tracking_path() -> Path:
    return cache_root() / "prediction_tracking.json"


def prediction_history_path() -> Path:
    return cache_root() / "prediction_history.json"


def auth_db_path() -> Path:
    return persistent_root() / "auth.db"


def auth_storage_diagnostics() -> dict[str, str | bool]:
    if os.getenv("DATABASE_URL", "").strip():
        return {
            "mode": "database_url",
            "path": str(auth_db_path()),
            "durable": True,
        }

    configured_persistent_root = os.getenv(_PERSISTENT_ROOT_ENV, "").strip()
    if configured_persistent_root:
        return {
            "mode": "persistent_root_env",
            "path": str(auth_db_path()),
            "durable": True,
        }

    if os.getenv("RENDER"):
        render_candidates = (
            Path("/var/data/scorpred"),
            Path("/persistent/scorpred"),
        )
        resolved_path = auth_db_path().resolve()
        if any(resolved_path.is_relative_to(candidate) for candidate in render_candidates if candidate.exists()):
            return {
                "mode": "render_disk",
                "path": str(resolved_path),
                "durable": True,
            }
        return {
            "mode": "render_ephemeral",
            "path": str(resolved_path),
            "durable": False,
        }

    return {
        "mode": "local_sqlite",
        "path": str(auth_db_path()),
        "durable": True,
    }


def ml_report_path() -> Path:
    return cache_dir("ml") / "model_comparison.json"


def prediction_policy_path() -> Path:
    return cache_dir("ml") / "prediction_policy.json"


def matches_dataset_path() -> Path:
    return data_dir() / "matches.csv"


def historical_dataset_path() -> Path:
    return data_dir() / "historical_matches.csv"


def trained_model_path() -> Path:
    return data_dir() / "model.pkl"


def model_dir() -> Path:
    """Immutable deploy artifacts are separate from persistent runtime history."""
    configured = os.getenv("SCORPRED_MODEL_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else repo_root() / "data" / "models"


def clean_soccer_dataset_path() -> Path:
    """Clean pre-match feature dataset (no target leakage)."""
    return data_dir() / "processed" / "soccer_training_data_clean.csv"


def clean_soccer_model_path() -> Path:
    """Random Forest trained on clean pre-match features."""
    return model_dir() / "soccer_random_forest_clean.pkl"


def ensemble_soccer_model_path() -> Path:
    """Stacking ensemble (LR + RF + XGBoost + LightGBM) trained on clean pre-match features."""
    return model_dir() / "soccer_ensemble_stack.pkl"


def league_model_path(league_id: int | str) -> Path:
    """Per-league stacking ensemble, trained and evaluated on that league only."""
    return model_dir() / f"soccer_ensemble_league_{league_id}.pkl"


def nba_model_path() -> Path:
    """Calibrated Random Forest model for NBA home/away win prediction."""
    return model_dir() / "nba_random_forest.pkl"


def elo_state_path() -> Path:
    """Final ELO ratings for all teams, saved after training (used at runtime)."""
    return data_dir() / "processed" / "soccer_elo_state.json"


def walk_forward_report_path() -> Path:
    """Walk-forward backtest report JSON."""
    return data_dir() / "backtests" / "walk_forward_report.json"


def mistake_report_path() -> Path:
    """Mistake analysis report JSON."""
    return data_dir() / "analysis" / "mistake_report.json"


def policy_adjustments_path() -> Path:
    """Learned policy adjustments JSON."""
    return data_dir() / "analysis" / "policy_adjustments.json"


def _copy_tree_if_missing(source: Path, target: Path) -> None:
    if not source.exists():
        return
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy2(item, destination)


def seed_runtime_artifacts() -> None:
    runtime_root = data_root()
    if runtime_root == repo_root():
        return
    bundled_paths = (
        (repo_root() / "data", runtime_root / "data"),
        (repo_root() / "cache" / "ml", runtime_root / "cache" / "ml"),
    )
    for source, target in bundled_paths:
        _copy_tree_if_missing(source, target)


def ensure_runtime_dirs() -> None:
    data_root().mkdir(parents=True, exist_ok=True)
    persistent_root().mkdir(parents=True, exist_ok=True)
    data_dir().mkdir(parents=True, exist_ok=True)
    for folder in (
        cache_root(),
        cache_dir("football"),
        cache_dir("props"),
        cache_dir("nba"),
        cache_dir("nba_public"),
        cache_dir("ml"),
        data_dir() / "processed",
        data_dir() / "models",
        data_dir() / "backtests",
        data_dir() / "analysis",
        data_dir() / "logs",
    ):
        folder.mkdir(parents=True, exist_ok=True)
    seed_runtime_artifacts()
