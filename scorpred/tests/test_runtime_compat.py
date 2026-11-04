"""Runtime compatibility regression tests."""

from pathlib import Path
import re


def test_no_datetime_utc_constant_imports_in_runtime_modules():
    files = [
        Path("app.py"),
        Path("api_client.py"),
        Path("nba_routes.py"),
        Path("model_tracker.py"),
        Path("services/evidence.py"),
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "datetime.UTC" not in text
        assert re.search(r"from datetime import .*\\bUTC\\b", text) is None
