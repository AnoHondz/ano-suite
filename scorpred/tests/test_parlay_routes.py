from __future__ import annotations

import json

import pytest

import app as flask_app_module
import parlay_engine as pe
import parlay_routes
from db_models import db


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SCORPRED_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("ODDS_API_KEY", "")          # never hit the real API in tests
    monkeypatch.setenv("PARLAY_ODDS_SOURCE", "odds_api")  # nor ESPN: with no key, nothing is fetched
    flask_app_module.app.config["TESTING"] = True
    flask_app_module.app.config["SECRET_KEY"] = "test-secret"
    flask_app_module.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    with flask_app_module.app.app_context():
        db.drop_all()
        db.create_all()
    return flask_app_module.app.test_client()


def _save_slate(sport="nba"):
    day = pe.today_et()
    pick = {
        "rank": 1, "event_id": "e1", "selection": "Boston Celtics to win", "matchup": "Brooklyn Nets @ Boston Celtics",
        "competition": "NBA", "commence_time": "2026-11-03T00:00:00Z", "bet_type": "Moneyline",
        "price": 1.4, "american": "-250", "book": "FanDuel", "prob": 0.74, "ev": 0.036,
        "tier": "value", "source": "model+market",
    }
    slate = {
        "sport": sport, "label": "NBA", "slate_date": day.isoformat(), "title": "NBA · Today",
        "generated_at": "2026-11-02T16:05:00+00:00", "status": "ready", "games": 1, "model_games": 1,
        "competitions": ["NBA"], "picks": [pick, {**pick, "rank": 2, "event_id": "e2", "selection": "Denver Nuggets to win"}],
        "parlay": {"legs": [1, 2], "selections": ["Boston Celtics to win (-250)", "Denver Nuggets to win (-250)"],
                   "decimal_odds": 1.96, "american": "-104", "hit_prob": 0.55, "ev": 0.08, "payout_per_10": 19.6, "min_odds": 2.0},
        "notes": [],
    }
    pe.SlateStore().save(slate)


def test_parlays_page_renders_saved_slate(client):
    _save_slate()
    resp = client.get("/parlays?sport=nba")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Boston Celtics to win" in body
    assert "Parlay of the day" in body


def test_parlays_page_without_slate(client):
    for sport in ("nba", "nfl", "soccer"):
        assert client.get(f"/parlays?sport={sport}").status_code == 200


def test_odds_credit_status_for_the_wallpaper(client, monkeypatch):
    from services import prop_markets
    monkeypatch.setattr(prop_markets, "credit_usage", lambda **kw: 3 if kw.get("share") else 445)
    _save_slate()
    data = client.get("/api/odds-credits").get_json()
    assert data["used"] == 445 and data["cap"] == prop_markets.MONTHLY_CREDIT_CAP and data["parlay"] == 3
    assert data["parlays_today"]["nba"] == "ready"


def test_parlays_api(client):
    _save_slate()
    data = client.get("/api/parlays/nba").get_json()
    assert data["status"] == "ready" and len(data["picks"]) == 2
    assert client.get("/api/parlays/cricket").status_code == 404
