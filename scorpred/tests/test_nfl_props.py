from datetime import UTC, datetime, timedelta

import pytest

from services import nfl_props
from services import nflverse_store as store

NOW = datetime.now(UTC)
SEASON = NOW.year - 1 if NOW.month <= 2 else NOW.year
# Weeks 1-4: AA and BB each play CC and DD twice. Week 5 is the target AA vs BB.
SCHEDULE = [(1, "AA", "CC"), (1, "BB", "DD"), (2, "AA", "DD"), (2, "BB", "CC"),
            (3, "AA", "CC"), (3, "BB", "DD"), (4, "AA", "DD"), (4, "BB", "CC")]
TARGET = {"id": "target", "nflverse_id": f"{SEASON}_05_BB_AA", "season": SEASON, "week": 5,
          "home_id": "AA", "away_id": "BB", "completed": False}


def records():
    return [{"id": f"g{i}", "nflverse_id": f"{SEASON}_{w:02d}_{a}_{h}", "season": SEASON, "week": w,
             "home_id": h, "away_id": a, "completed": True} for i, (w, h, a) in enumerate(SCHEDULE)] + [TARGET]


def stat(week, team, opponent, game_id, player, position, **values):
    row = {"season": SEASON, "season_type": "REG", "week": week, "game_id": game_id, "team": team,
           "opponent_team": opponent, "player_id": f"{team}-{player}", "player_display_name": f"{team} {player}",
           "position": position, "attempts": 0, "completions": 0, "passing_yards": 0, "passing_tds": 0,
           "passing_interceptions": 0, "carries": 0, "rushing_yards": 0, "rushing_tds": 0,
           "targets": 0, "receptions": 0, "receiving_yards": 0, "receiving_tds": 0}
    row.update(values)
    return row


def box_scores(skip=None, extra=()):
    rows = []
    for week, home, away in SCHEDULE:
        game_id = f"{SEASON}_{week:02d}_{away}_{home}"
        for team, opponent in ((home, away), (away, home)):
            if (team, week) == skip:
                continue
            rows += [stat(week, team, opponent, game_id, "qb", "QB", attempts=30 + week, completions=20,
                          passing_yards=220 + 10 * week, passing_tds=2, passing_interceptions=week % 2, carries=3,
                          rushing_yards=12),
                     stat(week, team, opponent, game_id, "rb", "RB", carries=15, rushing_yards=60 + week,
                          targets=3, receptions=2, receiving_yards=15, rushing_tds=week % 2),
                     stat(week, team, opponent, game_id, "wr", "WR", targets=8, receptions=5,
                          receiving_yards=60 + 5 * week, receiving_tds=int(week == 3))]
    return rows + list(extra)


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    path = tmp_path / "nfl.db"
    monkeypatch.setattr(store, "database_path", lambda: path)
    nfl_props._week_inputs.cache_clear()

    def seed(rows, injuries=()):
        rosters = {team: {row["player_id"]: {"displayName": row["player_display_name"]}
                          for row in rows if row.get("team") == team} for team in ("AA", "BB")}
        monkeypatch.setattr(nfl_props, "current_eligibility", lambda *args: (rosters, fresh()))
        store.replace_dataset("player_stats", SEASON, rows, path=path)
        if injuries:
            store.replace_dataset("injuries", SEASON, list(injuries), path=path)
        nfl_props._week_inputs.cache_clear()
    return seed


def market(result, team, player, key):
    group = next(t for t in result["teams"] if t["team"] == team)
    entry = next(p for p in group["players"] if p["player_id"] == f"{team}-{player}")
    return next(m for m in entry["markets"] if m["market"] == key)


def test_project_market_weights_recent_games_and_needs_a_sample():
    assert nfl_props.project_market("receptions", [5, 6], [], None, None) is None
    rising = nfl_props.project_market("receiving_yards", [20, 40, 60, 80, 100], [], None, None)
    assert 60 < rising["projection"] < 100  # recency-weighted above the plain mean
    low, high = rising["range"]
    assert low < rising["median"] < high
    over = rising["ladder"]["over"]
    assert all(a >= b for a, b in zip(over, over[1:]))
    assert rising["model_line"] == int(rising["median"]) + 0.5


def test_opponent_adjustment_is_shrunk_and_capped():
    base = nfl_props.project_market("rushing_yards", [70] * 6, [], None, None)["projection"]
    generous = nfl_props.project_market("rushing_yards", [70] * 6, [], [300] * 10, 100)
    stingy = nfl_props.project_market("rushing_yards", [70] * 6, [], [10] * 10, 100)
    assert generous["opponent_factor"] == pytest.approx(1 + nfl_props.OPPONENT_LIMIT)
    assert stingy["opponent_factor"] == pytest.approx(1 - nfl_props.OPPONENT_LIMIT)
    assert stingy["projection"] < base < generous["projection"]


def test_rate_prior_keeps_three_quiet_games_from_zeroing_a_starter():
    without = nfl_props.project_market("passing_tds", [0, 0, 0], [], None, None)
    with_prior = nfl_props.project_market("passing_tds", [0, 0, 0], [], None, None, rate_prior=1.4)
    assert without["projection"] < 0.1 < 0.5 < with_prior["projection"]


def test_anytime_td_returns_a_probability_not_a_line():
    td = nfl_props.project_market("anytime_td", [1, 0, 1, 0, 1], [], None, None)
    assert 0.3 < td["probability"] < 0.8
    assert "model_line" not in td and "ladder" not in td


def test_projection_uses_only_games_before_the_target_week(seeded):
    future = stat(5, "AA", "BB", TARGET["nflverse_id"], "wr", "WR", targets=20, receptions=15, receiving_yards=400)
    seeded(box_scores(extra=[future]))
    result = nfl_props.project(TARGET, records())
    wr = market(result, "AA", "wr", "receiving_yards")
    assert wr["recent"] == [65, 70, 75, 80]
    assert wr["projection"] < 100
    assert result["data_through_week"] == 4
    positions = {p["position"] for t in result["teams"] for p in t["players"]}
    assert positions == {"QB", "RB", "WR"}
    qb = next(p for p in next(t for t in result["teams"] if t["team"] == "AA")["players"] if p["position"] == "QB")
    assert {m["market"] for m in qb["markets"]} >= {"passing_yards", "passing_tds", "rushing_yards"}


def test_quarterback_is_the_latest_games_starter_not_the_busiest_recent_one():
    def qb(week, player, attempts):
        return stat(week, "AA", "CC", f"g{week}", player, "QB", attempts=attempts, completions=attempts // 2)
    # A fill-in started weeks 2-3; the starter returned in week 4.
    current = [qb(1, "starter", 35), qb(2, "fillin", 40), qb(3, "fillin", 38), qb(4, "starter", 33)]
    chosen = [player_id for player_id, _ in nfl_props._select("AA", current)]
    assert chosen == ["AA-starter"]


def test_missing_completed_game_withholds_props(seeded):
    seeded(box_scores(skip=("BB", 4)))
    with pytest.raises(nfl_props.PropsUnavailable, match="BB week 4"):
        nfl_props.project(TARGET, records())


def test_espn_fallback_games_are_not_projected():
    with pytest.raises(nfl_props.PropsUnavailable, match="nflverse"):
        nfl_props.project({**TARGET, "nflverse_id": None}, records())


def test_ruled_out_players_are_listed_not_projected(seeded):
    injury = {"season": SEASON, "season_type": "REG", "game_type": "REG", "week": 5, "team": "AA",
              "gsis_id": "AA-rb", "full_name": "AA rb", "position": "RB", "report_status": "Out",
              "report_primary_injury": "Ankle"}
    questionable = {**injury, "gsis_id": "AA-wr", "full_name": "AA wr", "position": "WR",
                    "report_status": "Questionable", "report_primary_injury": "Hamstring"}
    seeded(box_scores(), injuries=[injury, questionable])
    result = nfl_props.project(TARGET, records())
    aa = next(t for t in result["teams"] if t["team"] == "AA")
    assert [p["player_id"] for p in aa["ruled_out"]] == ["AA-rb"]
    assert "AA-rb" not in {p["player_id"] for p in aa["players"]}
    wr = next(p for p in aa["players"] if p["player_id"] == "AA-wr")
    assert wr["injury"] == {"status": "Questionable", "detail": "Hamstring"}
    assert result["injury_report"] == "synced"


def fresh():
    return {"state": "fresh", "observed_at": NOW.isoformat(), "expires_at": (NOW + timedelta(hours=1)).isoformat()}


def test_props_route_binds_a_real_slate_game(seeded, monkeypatch):
    from app import app
    from services import nfl_service
    seeded(box_scores())
    game = {**TARGET, "kickoff": (NOW + timedelta(days=2)).isoformat(), "status_name": "STATUS_SCHEDULED", "state": "pre"}
    monkeypatch.setattr(nfl_service, "load_slate", lambda selected=None: ({"slate": [{"matchId": "target"}]},
                                                                          records()[:-1] + [game]))
    monkeypatch.setattr(nfl_service, "prediction_freshness", lambda *_: fresh())
    client = app.test_client()
    response = client.get("/api/nfl/matches/target/props")
    assert response.status_code == 200
    assert {t["team"] for t in response.json["result"]["teams"]} == {"AA", "BB"}
    assert client.get("/api/nfl/matches/unknown/props").status_code == 404
    monkeypatch.setattr(nfl_service, "prediction_freshness", lambda *_: None)
    assert client.get("/api/nfl/matches/target/props").status_code == 422


def test_props_route_rejects_started_games_and_missing_stats(seeded, monkeypatch):
    from app import app
    from services import nfl_service
    seeded(box_scores(skip=("AA", 2)))
    game = {**TARGET, "kickoff": (NOW + timedelta(days=2)).isoformat(), "status_name": "STATUS_SCHEDULED", "state": "pre"}
    monkeypatch.setattr(nfl_service, "load_slate", lambda selected=None: ({"slate": [{"matchId": "target"}]},
                                                                          records()[:-1] + [game]))
    monkeypatch.setattr(nfl_service, "prediction_freshness", lambda *_: fresh())
    client = app.test_client()
    missing = client.get("/api/nfl/matches/target/props")
    assert missing.status_code == 422 and "AA week 2" in missing.json["error"]
    game["kickoff"] = (NOW - timedelta(minutes=5)).isoformat()
    assert client.get("/api/nfl/matches/target/props").status_code == 422


def test_best_picks_take_one_favoured_main_market_per_healthy_player():
    def market(key, probability, record, line):
        return {"market": key, "label": key, "projection": 1.0, "ladder": None,
                "pick": {"line": line, "side": "over", "probability": probability, "record": record}}
    players = [
        {"player_id": "a", "name": "A", "position": "WR", "injury": None,
         "markets": [market("receiving_yards", 0.70, 0.40, 55.5), market("receptions", 0.66, 0.30, 4.5)]},
        {"player_id": "b", "name": "B", "position": "WR", "injury": {"status": "Questionable"},
         "markets": [market("receiving_yards", 0.90, 0.20, 60.5)]},
        {"player_id": "c", "name": "C", "position": "WR", "injury": None,
         "markets": [market("receiving_yards", 0.45, 0.20, 40.5), market("receptions", 0.80, 0.30, 0.5)]},
        {"player_id": "d", "name": "D", "position": "QB", "injury": None,
         "markets": [market("passing_yards", 0.62, 0.50, 230.5), market("interceptions", 0.95, 0.10, 0.5)]},
    ]
    picks = nfl_props.best_picks([{"team": "AA", "players": players}])
    # A: largest edge (receptions 0.36 beats yards 0.30). B: injured. C: 45% is not a pick, and
    # 0.5 receptions is below a postable line. D: interceptions is not a pick market.
    assert [(p["name"], p["market"]) for p in picks] == [("A", "receptions"), ("D", "passing_yards")]
    assert picks[0]["chance"] == pytest.approx(0.5 + nfl_props.PICK_SHRINK * 0.16)


def test_projection_includes_at_most_five_picks(seeded):
    seeded(box_scores())
    result = nfl_props.project(TARGET, records())
    assert len(result["picks"]) <= nfl_props.PICKS
    assert len({p["player_id"] for p in result["picks"]}) == len(result["picks"])
    assert all(p["probability"] > 0.5 and p["chance"] <= p["probability"] for p in result["picks"])
