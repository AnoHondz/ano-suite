"""Tests for fixture evidence loading cache and resilience behavior."""

import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services import evidence
from tests.source_contracts import fresh_source


class _DummyApi:
    FORCE_REFRESH = False
    DEFAULT_LEAGUE_ID = 39
    CURRENT_SEASON = 2026
    ESPN_SLUG_BY_LEAGUE = {39: "eng.1"}

    def __init__(self):
        self.calls = {
            "get_upcoming_fixtures": 0,
            "get_espn_fixtures": 0,
            "get_standings": 0,
            "get_h2h": 0,
            "get_team_fixtures": 0,
            "get_injuries": 0,
        }
        self.raise_primary = False
        self.upcoming_payload = [
            {
                "data_freshness": fresh_source(),
                "fixture": {"id": 1, "date": (datetime.now(UTC) + timedelta(days=1)).isoformat(), "status": {"short": "NS"}, "venue": {"name": "A"}},
                "teams": {
                    "home": {"id": 10, "name": "Team A", "logo": ""},
                    "away": {"id": 20, "name": "Team B", "logo": ""},
                },
                "league": {"id": 39, "name": "Premier League", "round": "Round 1"},
            }
        ]
        self.fallback_payload = []

    def get_upcoming_fixtures(self, league, season, next_n=20):
        self.calls["get_upcoming_fixtures"] += 1
        if self.raise_primary:
            raise RuntimeError("primary down")
        return self.upcoming_payload[:next_n]

    def get_espn_fixtures(self, slug, next_n=20):
        self.calls["get_espn_fixtures"] += 1
        return self.fallback_payload[:next_n]

    def get_standings(self, league, season):
        self.calls["get_standings"] += 1
        return []

    def get_h2h(self, home_id, away_id, last=10):
        self.calls["get_h2h"] += 1
        return []

    def get_team_fixtures(self, team_id, league, season, last=10):
        self.calls["get_team_fixtures"] += 1
        return []

    def get_injuries(self, league, season, team_id):
        self.calls["get_injuries"] += 1
        return []


class _DummyPredictor:
    @staticmethod
    def filter_recent_completed_fixtures(rows, current_season=None, seasons_back=None):
        return rows or []

    @staticmethod
    def extract_form(rows, team_id):
        return []


class _FormPredictor(_DummyPredictor):
    @staticmethod
    def extract_form(rows, team_id):
        # Returns one form row to avoid Limited-data branch.
        return [{"result": "W", "gf": 2, "ga": 1, "home": True}]


class _DummyEngine:
    @staticmethod
    def build_opp_strengths_from_standings(rows):
        return {}

    @staticmethod
    def scorpred_predict(**kwargs):
        return {
            "confidence": "Low",
            "best_pick": {"prediction": "Draw", "team": "draw", "confidence": "Low", "reasoning": "test"},
            "win_probabilities": {"a": 33.3, "draw": 33.4, "b": 33.3},
            "home_pct": 33.3,
            "draw_pct": 33.4,
            "away_pct": 33.3,
            "form_a": kwargs.get("form_a", []),
            "form_b": kwargs.get("form_b", []),
        }


class _DummyLogger:
    def error(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None


def test_load_upcoming_fixtures_uses_short_ttl_cache():
    evidence._UPCOMING_FIXTURE_CACHE.clear()
    evidence._TEAM_FORM_CACHE.clear()
    evidence._H2H_CACHE.clear()
    evidence._INJURY_CACHE.clear()

    api = _DummyApi()
    predictor = _DummyPredictor()
    engine = _DummyEngine()
    logger = _DummyLogger()

    first = evidence.load_upcoming_fixtures(
        api,
        predictor,
        engine,
        league=39,
        season=2026,
        logger=logger,
        football_data_source=lambda: "espn",
        next_n=6,
    )
    second = evidence.load_upcoming_fixtures(
        api,
        predictor,
        engine,
        league=39,
        season=2026,
        logger=logger,
        football_data_source=lambda: "espn",
        next_n=6,
    )

    assert first[0] and second[0]
    assert api.calls["get_upcoming_fixtures"] == 1
    assert api.calls["get_h2h"] == 1
    assert api.calls["get_team_fixtures"] == 2
    assert api.calls["get_injuries"] == 2
    assert api.calls["get_standings"] == 1


def test_load_upcoming_fixtures_can_skip_injuries_and_standings_for_lightweight_views():
    evidence._UPCOMING_FIXTURE_CACHE.clear()
    evidence._TEAM_FORM_CACHE.clear()
    evidence._H2H_CACHE.clear()
    evidence._INJURY_CACHE.clear()

    api = _DummyApi()

    fixtures, _, _, _ = evidence.load_upcoming_fixtures(
        api,
        _DummyPredictor(),
        _DummyEngine(),
        league=39,
        season=2026,
        logger=_DummyLogger(),
        football_data_source=lambda: "configured",
        next_n=6,
        max_deep_predictions=2,
        include_injuries=False,
        include_standings=False,
    )

    assert fixtures
    assert api.calls["get_upcoming_fixtures"] == 1
    assert api.calls["get_h2h"] == 1
    assert api.calls["get_team_fixtures"] == 2
    assert api.calls["get_injuries"] == 0
    assert api.calls["get_standings"] == 0


def test_load_upcoming_fixtures_bypasses_cache_on_force_refresh():
    evidence._UPCOMING_FIXTURE_CACHE.clear()
    api = _DummyApi()

    evidence.load_upcoming_fixtures(
        api,
        _DummyPredictor(),
        _DummyEngine(),
        league=39,
        season=2026,
        logger=_DummyLogger(),
        football_data_source=lambda: "espn",
        next_n=6,
    )

    api.FORCE_REFRESH = True
    evidence.load_upcoming_fixtures(
        api,
        _DummyPredictor(),
        _DummyEngine(),
        league=39,
        season=2026,
        logger=_DummyLogger(),
        football_data_source=lambda: "espn",
        next_n=6,
    )

    assert api.calls["get_upcoming_fixtures"] == 2


def test_load_upcoming_fixtures_supports_competition_filter():
    evidence._UPCOMING_FIXTURE_CACHE.clear()
    api = _DummyApi()
    api.upcoming_payload = [
        {
            "fixture": {"id": 2, "date": "2026-04-16T19:00:00+00:00", "status": {"short": "NS"}, "venue": {}},
            "teams": {"home": {"id": 11, "name": "Arsenal"}, "away": {"id": 12, "name": "Chelsea"}},
            "league": {"id": 39, "name": "Premier League"},
        },
        {
            "fixture": {"id": 3, "date": "2026-04-16T20:00:00+00:00", "status": {"short": "NS"}, "venue": {}},
            "teams": {"home": {"id": 13, "name": "Roma"}, "away": {"id": 14, "name": "Inter"}},
            "league": {"id": 135, "name": "Serie A"},
        },
    ]

    fixtures, _, _, _ = evidence.load_upcoming_fixtures(
        api,
        _DummyPredictor(),
        _DummyEngine(),
        league=39,
        season=2026,
        logger=_DummyLogger(),
        football_data_source=lambda: "configured",
        competition="premier",
    )
    assert len(fixtures) == 1
    assert fixtures[0]["league"]["name"] == "Premier League"


def test_load_upcoming_fixtures_accepts_legacy_league_name_alias_without_explicit_league():
    evidence._UPCOMING_FIXTURE_CACHE.clear()
    api = _DummyApi()
    api.upcoming_payload = [
        {
            "fixture": {"id": 9, "date": "2026-04-16T19:00:00+00:00", "status": {"short": "NS"}, "venue": {}},
            "teams": {"home": {"id": 101, "name": "Alpha FC"}, "away": {"id": 202, "name": "Beta FC"}},
            "league": {"id": 39, "name": "Premier League"},
        }
    ]

    fixtures, _, _, _ = evidence.load_upcoming_fixtures(
        api,
        _DummyPredictor(),
        _DummyEngine(),
        season=2026,
        logger=_DummyLogger(),
        football_data_source=lambda: "configured",
        league_name="premier",  # legacy alias -> canonical competition
    )
    assert len(fixtures) == 1


def test_load_upcoming_fixtures_handles_malformed_payload_and_fallback_source():
    evidence._UPCOMING_FIXTURE_CACHE.clear()
    api = _DummyApi()
    api.raise_primary = True
    api.fallback_payload = [
        {
            "fixture": {"id": 44, "date": "2026-04-18T18:30:00+00:00", "status": {"short": "NS"}, "venue": {}},
            "teams": {"home": {"id": "31", "name": "Home FC"}, "away": {"id": "32", "name": "Away FC"}},
            "league": {"id": 39, "name": "Premier League"},
        },
        # malformed row should be ignored
        {"fixture": {"id": 45, "date": "bad-date"}, "teams": {"home": {"id": 0}, "away": {"id": 0}}},
    ]

    fixtures, load_error, source, _ = evidence.load_upcoming_fixtures(
        api,
        _DummyPredictor(),
        _DummyEngine(),
        league=39,
        season=2026,
        logger=_DummyLogger(),
        football_data_source=lambda: "configured",
    )

    assert len(fixtures) == 1
    assert source == "espn"
    assert load_error is not None


def test_prediction_context_downgrades_when_form_is_empty():
    evidence._UPCOMING_FIXTURE_CACHE.clear()
    api = _DummyApi()

    fixtures, _, _, _ = evidence.load_upcoming_fixtures(
        api,
        _DummyPredictor(),
        _DummyEngine(),
        league=39,
        season=2026,
        logger=_DummyLogger(),
        football_data_source=lambda: "configured",
    )

    prediction = fixtures[0]["prediction"]
    assert prediction.get("data_quality") == "Limited"
    assert "fallback_reason" not in prediction


def test_prediction_context_non_limited_when_form_available():
    evidence._UPCOMING_FIXTURE_CACHE.clear()
    evidence._TEAM_FORM_CACHE.clear()
    api = _DummyApi()

    fixtures, _, _, _ = evidence.load_upcoming_fixtures(
        api,
        _FormPredictor(),
        _DummyEngine(),
        league=39,
        season=2026,
        logger=_DummyLogger(),
        football_data_source=lambda: "configured",
    )

    prediction = fixtures[0]["prediction"]
    assert prediction.get("form_a")
    assert prediction.get("form_b")
    assert prediction.get("data_quality") != "Limited"
