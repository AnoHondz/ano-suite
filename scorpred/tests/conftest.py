"""Hermetic defaults: never use local accounts, provider credentials, or background jobs."""
import os
from pathlib import Path
import tempfile

import pytest
import requests

_runtime = tempfile.TemporaryDirectory(prefix="scorpred-tests-", ignore_cleanup_errors=True)
os.environ["SCORPRED_TESTING"] = "1"
os.environ["SCORPRED_DATA_ROOT"] = _runtime.name
os.environ["SCORPRED_PERSISTENT_ROOT"] = str(Path(_runtime.name) / "persistent")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_runtime.name) / "tests.db").replace("\\", "/")
os.environ["SECRET_KEY"] = "isolated-tests-only"
os.environ["SCORPRED_SCHEDULER_ENABLED"] = "0"


@pytest.fixture(autouse=True)
def isolate_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("NFL_DATA_SOURCE", "espn")  # nflverse integration tests opt in explicitly.
    # Sportsbook tests mock The Odds API; the free ESPN prop / parlay feeds are tested with their own fakes.
    monkeypatch.setenv("PROP_ODDS_SOURCE", "odds_api")
    monkeypatch.setenv("PARLAY_ODDS_SOURCE", "odds_api")
    import app as module
    from services import cache_service
    from services import prediction_service
    import model_tracker
    from db_models import db

    monkeypatch.setitem(module.app.config, "TESTING", True)
    monkeypatch.setitem(module.app.config, "SERVE_SPA", False)
    monkeypatch.setitem(module.app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(prediction_service, "_deps", dict(prediction_service._deps))
    monkeypatch.setattr(model_tracker, "_TRACKING_FILE", str(tmp_path / "prediction_tracking.json"))
    monkeypatch.setattr(module.limiter, "enabled", False)
    monkeypatch.setattr(cache_service, "_DISK_CACHE_DIR", tmp_path / "service-cache")
    cache_service._local_cache.clear()
    module._FIXTURE_INDEX.clear()
    module._local_fixture_cache.clear()
    module._local_match_analysis_cache.clear()
    module._RATE_LIMIT_BUCKETS.clear()
    module._local_scoreline_cache.clear()
    with module.app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
    monkeypatch.setattr(module, "_schedule_auto_refresh", lambda *args, **kwargs: None)

    def offline(*args, **kwargs):
        raise requests.ConnectionError("Live network disabled in tests")

    monkeypatch.setattr(requests.sessions.Session, "request", offline)
    yield
    cache_service._local_cache.clear()


def pytest_sessionfinish(session, exitstatus):
    import app as module
    from db_models import db
    with module.app.app_context():
        db.session.remove()
        db.engine.dispose()
