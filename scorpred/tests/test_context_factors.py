from services import context_factors as cf
from services.tiered_parlays import shot_profile


def nba_game(date, players):
    return {"date": date, "teams": {"1": {"players": players, "opponent": "2"}, "2": {"players": [], "opponent": "1"}}}


def row(name, minutes, points, assists=0):
    return {"id": name, "name": name, "minutes": minutes, "points": points, "assists": assists}


def test_recent_games_count_more_but_totals_keep_the_game_count():
    hits, n = cf.weighted_hits([10, 10, 10, 10, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 5.5, "over")
    assert n == 15 and round(hits, 2) == round(5 * 2 * 15 / (5 * 2 + 5 * 1.5 + 5), 2)   # 10 > 5/15 because the hits are recent
    assert cf.weighted_hits([6, 6], 5.5, "over", factor=.9) == (0.0, 2)                  # a factor can push values under the line


def test_star_out_boosts_the_teammate_who_absorbs_his_usage():
    with_star = [nba_game(f"2026-10-{d:02d}", [row("Star", 34, 28), row("Guard", 30, 14, 5)]) for d in range(1, 9)]
    without = [nba_game(f"2026-10-{d:02d}", [row("Guard", 34, 20, 8)]) for d in range(10, 14)]
    games = with_star + without
    stars = cf.stars(with_star, "1", str.lower)
    assert stars == {"star"}
    factor, note = cf.teammate_factor("guard", lambda r: r["points"], games, "1", {"star"}, str.lower)
    assert round(factor, 3) == round(1 + cf.TEAMMATE_STRENGTH * (20 / 14 - 1), 3) and note == "Star is out tonight; in 4 recent games without him, his average was 43% higher"
    assert cf.teammate_factor("guard", lambda r: r["points"], games, "1", set(), str.lower) == (1.0, None)


def test_back_to_back_and_blowout():
    day = 86400
    assert cf.back_to_back([1000 * day - day], 1000 * day) and not cf.back_to_back([1000 * day - 2 * day], 1000 * day)
    assert cf.blowout_factor(12.5, starter=True) < 1 and cf.blowout_factor(12.5, starter=False) == 1
    assert cf.blowout_factor(6, starter=True) == 1 and cf.blowout_factor(None, True) == 1


def test_shots_on_target_drive_team_goal_chances():
    strong = cf.shots_goal_chance(7, 6, .3, 1.5, "over")
    weak = cf.shots_goal_chance(3, 2.5, .3, 1.5, "over")
    assert strong > .55 > .25 > weak   # 1.95 expected goals vs 0.83
    assert round(cf.shots_goal_chance(3, 2.5, .3, 1.5, "under"), 6) == round(1 - weak, 6)
    game = {"teams": {"a": {"opponent": "b", "players": [{"shots_on_target": 5, "goals": 2}]},
                      "b": {"opponent": "a", "players": [{"shots_on_target": 2, "goals": 0}]}}}
    profile = shot_profile({"a": [game] * 8})
    assert profile["teams"]["a"] == (5, 2)
