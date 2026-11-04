from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime, timedelta

import pytest

import app as flask_app_module
import decision_ui as dui
import security
from services.prediction_contract import validate_analysis_contract
from tests.source_contracts import fresh_source


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(monkeypatch):
    flask_app_module.app.config["TESTING"] = True
    flask_app_module.app.config["WTF_CSRF_ENABLED"] = False
    flask_app_module.app.config["TRACKING_LAST_BOOTSTRAP"] = "2099-01-01"
    security.reset_chat_rate_limits()

    fixture = {
        "data_freshness": fresh_source(),
        "fixture": {"id": 42, "date": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        "teams": {
            "home": {"id": 1, "name": "AFC Bournemouth", "logo": ""},
            "away": {"id": 2, "name": "Leeds", "logo": ""},
        },
        "league": {"id": 39, "name": "Premier League"},
    }
    analysis = {
        "match_id": "42",
        "matchup": "AFC Bournemouth vs Leeds",
        "confidence": 72,
        "probabilities": {"a": 52, "draw": 24, "b": 24},
        "action": "BET",
        "recommended_side": "AFC Bournemouth",
        "reason": "Mock canonical reason",
        "data_quality": "Strong Data",
        "metric_breakdown": {
            "form": {"home": 64, "away": 41},
            "attack": {"home": 70, "away": 52},
            "defense": {"home": 58, "away": 44},
            "venue": {"home": 61, "away": 39},
        },
    }

    monkeypatch.setattr(flask_app_module, "load_fixtures_cached", lambda _league: ([fixture], None, "mock", ""))
    monkeypatch.setattr(flask_app_module, "analyze_match", lambda _match_id: analysis)
    flask_app_module.prediction_service.configure(
        analyze_match=flask_app_module.analyze_match,
        load_fixtures=flask_app_module.load_fixtures_cached,
        card_from_fixture=flask_app_module._soccer_card_from_fixture_analysis,
        top_opportunities=lambda cards, limit: dui.top_opportunities(cards, limit=limit),
        plan_summary=dui.plan_summary,
    )
    monkeypatch.setattr(flask_app_module.ac, "get_teams", lambda *_a, **_k: [])
    monkeypatch.setattr(flask_app_module, "_load_grouped_upcoming_fixtures_all_leagues", lambda **_kwargs: ([fixture], [{"fixtures": [fixture], "league_id": 39, "league_name": "Premier League", "league_flag": ""}], None, "mock"))

    with flask_app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["team_a_id"] = 1
            sess["team_a_name"] = "AFC Bournemouth"
            sess["team_a_logo"] = ""
            sess["team_b_id"] = 2
            sess["team_b_name"] = "Leeds"
            sess["team_b_logo"] = ""
            sess["selected_fixture"] = {
                "fixture_id": "42",
                "date": fixture["fixture"]["date"],
            }
        yield c


def test_contract_blocks_forbidden_patterns():
    errors = validate_analysis_contract(
        {
            "match_id": "x",
            "action": "BET",
            "recommended_side": "A",
            "confidence": 53,
            "probabilities": {"a": 38, "draw": 26, "b": 36},
            "reason": "x",
            "data_quality": "Strong Data",
            "metric_breakdown": {"form": {"home": 50, "away": 50}},
        }
    )
    assert errors
    assert any("forbidden confidence" in err for err in errors)
    assert any("forbidden probability fallback" in err for err in errors)


def test_build_decision_card_is_pure_formatter():
    analysis = {
        "match_id": "42",
        "matchup": "AFC Bournemouth vs Leeds",
        "confidence": 72,
        "probabilities": {"a": 52, "draw": 24, "b": 24},
        "action": "BET",
        "recommended_side": "AFC Bournemouth",
        "reason": "Mock canonical reason",
        "data_quality": "Strong Data",
        "metric_breakdown": {"form": {"home": 64, "away": 41}},
    }
    card = dui.build_decision_card(analysis=analysis)
    assert card["confidence"] == 72
    assert card["probabilities"] == {"a": 52, "draw": 24, "b": 24}
    assert card["reason"] == "Mock canonical reason"
    assert card["metric_breakdown"] == {"form": {"home": 64, "away": 41}}


def test_route_parity_and_visual_read_values(client):
    soccer = client.get("/soccer")
    prediction = client.get("/prediction")
    soccer_text = soccer.get_data(as_text=True)
    prediction_text = prediction.get_data(as_text=True)
    assert soccer.status_code == 200
    assert prediction.status_code == 200

    for text in (soccer_text, prediction_text):
        assert "AFC Bournemouth" in text
        assert "72%" in text
        assert "H 52 /" in text
        assert "D 24 /" in text
        assert "A 24" in text
        assert "53%" not in text
        assert "38 / 26 / 36" not in text
        assert "50/50" not in text
        assert "64" in text
        assert "41" in text
        assert "70" in text
        assert "52" in text
        assert "58" in text
        assert "44" in text
        assert "61" in text
        assert "39" in text


def test_visual_read_unavailable_when_missing():
    analysis = {
        "match_id": "1",
        "matchup": "A vs B",
        "confidence": 70,
        "probabilities": {"a": 50, "draw": 10, "b": 40},
        "action": "BET",
        "recommended_side": "A",
        "reason": "ok",
        "data_quality": "Strong Data",
        "metric_breakdown": None,
    }
    card = dui.build_decision_card(analysis=analysis)
    assert card["metric_breakdown"] is None


def test_codebase_guard_forbidden_patterns():
    forbidden = [
        "_normalise_probs",
        "_probabilities_look_placeholder",
        "confidence = 53",
        "38 / 26 / 36",
    ]
    source_files = list(REPO_ROOT.glob("*.py"))
    for directory in ("services", "utils", "scripts"):
        source_files.extend((REPO_ROOT / directory).rglob("*.py"))
    source_files.extend((REPO_ROOT / "templates").rglob("*.html"))
    violations = []
    for path in source_files:
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            if pattern in text:
                violations.append(f"{path}: {pattern}")
    assert not violations, "Forbidden fallback patterns found:\n" + "\n".join(violations)


def test_pipeline_usage_guard():
    app_source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    assert 'fixture["prediction"]' not in app_source
