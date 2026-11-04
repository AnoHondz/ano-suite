"""NBA game projection: correlated scorelines, quarters, overtime, player box lines and matchups."""
import pytest

from services import nba_game_projection as game
from services import nba_pregame_model as model
from tests.test_nba_pregame_model import TIP, schedule


def player(player_id, position, minutes, pts, *, status="", team="1", gp=30):
    season = model.season_of(TIP)
    line = {"id": player_id, "name": player_id, "position": position, "team_ids": [team], "gp": gp, "min": minutes,
            "pts": pts, "fgm": pts * 0.37, "fga": pts * 0.8, "ftm": pts * 0.15, "fta": pts * 0.19, "reb": minutes / 5,
            "ast": minutes / 8, "fg3m": pts / 8, "stl": 1, "blk": 0.5, "tov": 2, "pf": 2}
    return {"id": player_id, "name": player_id, "position": position, "status": status}, (season, line)


def pool(team, rows):
    roster = [row[0] for row in rows]
    lines = {}
    for _, (season, line) in rows:
        lines.setdefault(season, []).append({**line, "team_ids": [team]})
    return model.player_pool(team, roster, lines)


ROTATION = [("PG", 34, 24), ("SG", 32, 18), ("SF", 31, 15), ("PF", 30, 13), ("C", 29, 12),
            ("G", 22, 9), ("F", 20, 8), ("C", 16, 6), ("SG", 14, 5), ("F", 12, 4)]


def team_pool(team, *, out=None):
    rows = [player(f"{team}{position}{index}", position, minutes, pts, status="Out" if index == out else "", team=team)
            for index, (position, minutes, pts) in enumerate(ROTATION)]
    return pool(team, rows)


def projection(**pools):
    return model.project(schedule("1", 4, 30), schedule("2", 0, 30), "1", "2", TIP,
                         home_players=pools.get("home", team_pool("1")), away_players=pools.get("away", team_pool("2")))


def test_scoreline_agrees_with_the_decision_model_and_never_ties():
    result = game.build(projection(), {"total": 224.5, "home_spread": -3.5}, "Home", "Away")
    assert result["win_probabilities"]["home"] > 50
    assert all(row["home"] != row["away"] for row in result["top_scorelines"])
    best = result["top_scorelines"][0]
    assert abs((best["home"] - best["away"]) - result["proj_margin"]) <= 2
    assert abs((best["home"] + best["away"]) - result["projected_score"]["total"]) <= 2
    assert any(row["line"] == 224.5 for row in result["over_under"]) and result["primary_ou_line"]["line"] == 224.5
    assert 0 < result["spread"]["home_cover_prob"] < 100
    low, high = result["intervals"]["home"]["low"], result["intervals"]["home"]["high"]
    assert low < result["projected_score"]["home"] < high


def test_quarters_add_up_and_close_games_go_to_overtime_more():
    result = game.build(projection())
    regulation = sum(row["home"] + row["away"] for row in result["quarters"][:4])
    expected = result["projected_score"]["total"] - result["overtime_prob"] / 100 * game.OVERTIME_POINTS
    assert regulation == pytest.approx(expected, abs=0.3)
    assert result["quarters"][3]["home"] < result["quarters"][2]["home"]
    assert game.overtime_probability(0, 12) > game.overtime_probability(10, 12)
    assert game.overtime_probability(0, 12) == pytest.approx(game.OVERTIME_RATE / 0.93)


def test_box_lines_fill_240_minutes_and_match_the_team_score():
    result = game.build(projection())
    lines = result["players"]["home"]
    assert sum(row["minutes"] for row in lines["players"]) + lines["unassigned_minutes"] == pytest.approx(240, abs=0.6)
    assert sum(row["pts"] for row in lines["players"]) == pytest.approx(result["projected_score"]["home"], abs=1.5)


def test_an_injured_starter_shifts_minutes_and_shots_to_teammates():
    healthy = game.build(projection())["players"]["home"]["players"]
    hurt = game.build(projection(home=team_pool("1", out=0)))["players"]["home"]
    assert "1PG0" not in [row["id"] for row in hurt["players"]]
    assert hurt["unavailable"][0]["id"] == "1PG0"
    before = {row["id"]: row for row in healthy}
    for row in hurt["players"]:
        assert row["minutes"] >= before[row["id"]]["minutes"] - 0.05
        assert row["minutes"] <= min(game.MAX_MINUTES, before[row["id"]]["minutes"] + game.MAX_MINUTE_BUMP) + 0.05
    assert max(row["pts"] for row in hurt["players"]) > before["1SG1"]["pts"]


def test_starters_are_paired_by_position_and_benches_compared():
    result = game.build(projection())
    pairs = result["matchups"]["starters"]
    assert [pair["home"]["position"] for pair in pairs] == ["PG", "SG", "SF", "PF", "C"]
    assert [pair["away"]["position"] for pair in pairs] == ["PG", "SG", "SF", "PF", "C"]
    assert all(pair["edge"] in {"home", "away", "even"} for pair in pairs)
    assert result["matchups"]["bench"]["home"]["players"] == 5


def test_without_rosters_the_scoreline_still_works():
    plain = model.project(schedule("1", 4, 30), schedule("2", 0, 30), "1", "2", TIP)
    result = game.build(plain)
    assert result["players"] == {"home": None, "away": None} and result["matchups"] is None
    assert result["data_completeness"]["factors"]["rosters"] is False


def test_a_crowded_rotation_loses_bench_minutes_not_star_minutes():
    crowded = ROTATION + [("G", 25, 10), ("F", 24, 9), ("C", 20, 7)]
    rows = [player(f"1x{index}", position, minutes, pts) for index, (position, minutes, pts) in enumerate(crowded)]
    result = game.build(projection(home=pool("1", rows)))
    lines = {row["id"]: row for row in result["players"]["home"]["players"]}
    assert lines["1x0"]["minutes"] == pytest.approx(34, abs=0.1)
    assert sum(row["minutes"] for row in lines.values()) == pytest.approx(240, abs=0.6)
    assert "1x9" not in lines
