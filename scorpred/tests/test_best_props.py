from datetime import datetime, timedelta, timezone

import pytest

from services import best_props

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)


def decision(match_id, kickoff, *, pregame=True, sport_label="Premier League"):
    return {"matchId": match_id, "matchup": f"Home{match_id} vs Away{match_id}", "matchDate": kickoff.isoformat(),
            "pregameAvailable": pregame, "support": sport_label}


def pick(name, chance, *, market="points", edge=0.2, team="T"):
    return {"name": name, "team": team, "market": market, "label": market.title(), "side": "under", "line": 9.5,
            "line_source": "historical", "projection": 7.0, "chance": chance, "record": 0.5, "edge": edge, "games": 10}


def test_worker_threads_inherit_deferred_odds_pricing():
    from services import prop_markets
    seen = []
    props_for = lambda *_: (seen.append(prop_markets._prices_deferred.get()) or ({"result": {"picks": []}}, 200))
    slates = {"nba": ("2026-10-23", [decision("n1", NOW + timedelta(hours=2)), decision("n2", NOW + timedelta(hours=3))])}
    with prop_markets.prices_deferred():
        best_props.build(slates, props_for, now=NOW)
    best_props.build(slates, props_for, now=NOW)
    assert seen == [True, True, False, False]


def test_board_ranks_by_edge_keeps_one_pick_per_player_and_keeps_nfl_separate():
    later = NOW + timedelta(hours=4)
    slates = {"soccer": ("2026-10-23", [decision("s1", later)]),
              "nba": ("2026-10-23", [decision("n1", later), decision("n2", NOW - timedelta(hours=1)),
                                     decision("n3", later, pregame=False)]),
              "nfl": ("2026-10-25", [decision("f1", later + timedelta(days=2))])}
    results = {"s1": [pick("Kane", 0.71, market="shots", edge=0.10), pick("Rice", 0.83, market="tackles", edge=0.30),
                      pick("Saliba", 0.97, market="shots_on_target", edge=0.05), pick("Odegaard", 0.55, edge=0.40)],
               "n1": [pick("Tatum", 0.77, edge=0.25), pick("Tatum", 0.69, market="assists", edge=0.12), pick("Brown", 0.74, edge=0.15)],
               "f1": [pick("Mahomes", 0.95, market="passing_yards")]}
    seen = []

    def props_for(sport, item, slate_date):
        seen.append((sport, item["matchId"], slate_date))
        return {"result": {"picks": results[item["matchId"]]}}, 200

    board = best_props.build(slates, props_for, now=NOW)
    assert sorted(seen) == [("nba", "n1", "2026-10-23"), ("nfl", "f1", "2026-10-25"), ("soccer", "s1", "2026-10-23")]
    # Saliba's near-certainty ranks last; Odegaard is under the chance floor.
    assert [p["name"] for p in board["overall"]] == ["Rice", "Tatum", "Brown", "Kane", "Saliba"]
    assert board["overall"][0]["validation"] == "backtested, small sample"
    assert board["overall"][1]["market"] == "points"  # Tatum's best pick only
    assert [p["name"] for p in board["by_sport"]["nfl"]] == ["Mahomes"]
    assert all(p["sport"] != "nfl" for p in board["overall"])
    assert board["slates"]["nfl"]["date"] == "2026-10-25"


def test_unavailable_games_are_reported_not_hidden():
    slates = {"nba": ("2026-10-23", [decision("n1", NOW + timedelta(hours=2))])}
    board = best_props.build(slates, lambda *_: ({"error": "Not enough games."}, 422), now=NOW)
    assert board["slates"]["nba"]["unavailable"] == [{"matchup": "Homen1 vs Awayn1", "reason": "Not enough games."}]
    assert board["overall"] == []


def test_started_games_drop_off_a_cached_board():
    board = {"overall": [{"kickoff": (NOW - timedelta(minutes=5)).isoformat()}, {"kickoff": (NOW + timedelta(hours=1)).isoformat()}],
             "by_sport": {"nba": [{"kickoff": (NOW - timedelta(minutes=5)).isoformat()}]}}
    trimmed = best_props.without_started(board, NOW)
    assert len(trimmed["overall"]) == 1 and trimmed["by_sport"]["nba"] == []


def test_cache_builds_once_per_window(monkeypatch):
    monkeypatch.setattr(best_props, "_cache", {})
    calls = []
    make = lambda: calls.append(1) or {"n": len(calls), "slates": {"nba": {"with_props": 1}}}  # noqa: E731
    assert best_props.cached("d", make, now=0)["n"] == 1
    assert best_props.cached("d", make, now=100)["n"] == 1
    assert best_props.cached("d", make, now=best_props.CACHE_SECONDS + 1)["n"] == 2


def test_endpoint_validates_the_date_and_serves_the_board(monkeypatch):
    import app as module
    monkeypatch.setattr(best_props, "_cache", {})
    monkeypatch.setattr(module, "build_best_props", lambda day: {"generated_at": "x", "slates": {}, "overall": [], "by_sport": {}, "notes": []})
    client = module.app.test_client()
    assert client.get("/api/props/best?date=2026-13-40").status_code == 400
    response = client.get("/api/props/best?date=2026-10-23")
    assert response.status_code == 200 and response.json["result"]["date"] == "2026-10-23"


def test_an_empty_board_is_rebuilt_after_a_minute(monkeypatch):
    monkeypatch.setattr(best_props, "_cache", {})
    boards = iter([{"slates": {"nba": {"with_props": 0}}}, {"slates": {"nba": {"with_props": 3}}}])
    assert best_props.cached("d", lambda: next(boards), now=0)["slates"]["nba"]["with_props"] == 0
    assert best_props.cached("d", lambda: next(boards), now=best_props.EMPTY_CACHE_SECONDS + 1)["slates"]["nba"]["with_props"] == 3


def test_expired_cached_fixtures_are_treated_as_a_cache_miss():
    from services import prediction_service as ps
    now = datetime.now(timezone.utc)
    fresh = {"state": "fresh", "observed_at": now.isoformat(), "expires_at": (now + timedelta(minutes=2)).isoformat(),
             "max_stale_at": (now + timedelta(minutes=10)).isoformat()}
    expired = dict(fresh, expires_at=(now - timedelta(minutes=1)).isoformat())
    assert ps._sources_fresh(([{"data_freshness": fresh}, {}], None, "espn", ""))
    assert not ps._sources_fresh(([{"data_freshness": fresh}, {"data_freshness": expired}], None, "espn", ""))
    assert not ps._sources_fresh(None)
