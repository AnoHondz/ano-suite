from __future__ import annotations

import runtime_paths as rp


def test_ensure_runtime_dirs_seeds_bundled_artifacts(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    runtime_root = tmp_path / "runtime"

    (repo_root / "data" / "backtests").mkdir(parents=True)
    (repo_root / "cache" / "ml").mkdir(parents=True)
    (repo_root / "data" / "backtests" / "walk_forward_report.json").write_text("{}", encoding="utf-8")
    (repo_root / "cache" / "ml" / "model_comparison.json").write_text('{"available": true}', encoding="utf-8")

    monkeypatch.setattr(rp, "_REPO_ROOT", repo_root)
    monkeypatch.setenv("SCORPRED_DATA_ROOT", str(runtime_root))

    rp.ensure_runtime_dirs()

    assert (runtime_root / "data" / "backtests" / "walk_forward_report.json").exists()
    assert (runtime_root / "cache" / "ml" / "model_comparison.json").exists()


def test_seed_runtime_artifacts_does_not_overwrite_existing_files(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    runtime_root = tmp_path / "runtime"

    (repo_root / "cache" / "ml").mkdir(parents=True)
    (runtime_root / "cache" / "ml").mkdir(parents=True)
    (repo_root / "cache" / "ml" / "model_comparison.json").write_text('{"source": "repo"}', encoding="utf-8")
    target = runtime_root / "cache" / "ml" / "model_comparison.json"
    target.write_text('{"source": "runtime"}', encoding="utf-8")

    monkeypatch.setattr(rp, "_REPO_ROOT", repo_root)
    monkeypatch.setenv("SCORPRED_DATA_ROOT", str(runtime_root))

    rp.ensure_runtime_dirs()

    assert target.read_text(encoding="utf-8") == '{"source": "runtime"}'
