from datetime import datetime, timedelta, timezone

import model_tracker as mt
from services import closing_lines

NOW = datetime(2030, 1, 1, 14, 50, tzinfo=timezone.utc)


def test_only_picks_starting_inside_the_window_are_priced(tmp_path, monkeypatch):
    monkeypatch.setattr(mt, "_TRACKING_FILE", str(tmp_path / "tracking.json"))
    soon = mt.save_prediction("soccer", "Home", "Away", "A", {"a": 50, "draw": 25, "b": 25}, "Medium",
                              "2030-01-01", kickoff="2030-01-01T15:00:00Z")
    later = mt.save_prediction("soccer", "Home", "Other", "A", {"a": 50, "draw": 25, "b": 25}, "Medium",
                               "2030-01-01", kickoff="2030-01-01T20:00:00Z")
    nba = mt.save_prediction("nba", "Suns", "Warriors", "A", {"a": 60, "b": 40}, "Medium",
                             "2030-01-01", kickoff="2030-01-01T15:05:00Z")
    monkeypatch.setattr(mt, "_utc_now", lambda: NOW)
    seen = []

    def soccer(pick):
        seen.append(pick["id"])
        return {"a": 2.0, "b": 4.0, "draw": 4.0}

    outcome = closing_lines.capture(now=NOW, sources={"soccer": soccer, "nba": lambda pick: None})
    assert seen == [soon]
    assert outcome == {"recorded": 1, "missing": 1}
    rows = {row["id"]: row for row in mt._load_predictions()}
    assert rows[soon]["closing_market"]["fair"]["a"] == 0.5
    assert "closing_market" not in rows[later] and "closing_market" not in rows[nba]


def test_due_ignores_started_and_completed_picks():
    rows = [{"kickoff_at": (NOW - timedelta(minutes=1)).isoformat(), "status": "pending"},
            {"kickoff_at": (NOW + timedelta(minutes=5)).isoformat(), "status": "completed"},
            {"kickoff_at": (NOW + timedelta(minutes=5)).isoformat(), "status": "pending"}]
    assert closing_lines.due(rows, NOW) == rows[2:]
