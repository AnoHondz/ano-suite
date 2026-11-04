import math

import numpy as np
import pytest

from services import soccer_ratings as sr


def _season(seed=7, days=500):
    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(10)]
    attack = {t: rng.normal(0, 0.3) for t in teams}
    defence = {t: rng.normal(0, 0.3) for t in teams}
    matches = []
    for day in range(days):
        home, away = rng.choice(teams, 2, replace=False)
        lam = math.exp(0.1 + 0.3 + attack[home] + defence[away])
        mu = math.exp(0.1 + attack[away] + defence[home])
        matches.append(sr.Match(day, str(home), str(away), int(rng.poisson(lam)), int(rng.poisson(mu))))
    return matches, attack


def test_fit_recovers_home_advantage_and_team_order():
    matches, attack = _season()
    model = sr.fit(matches, 500, half_life_days=1e9, window_days=10_000, ridge=0.01)
    assert model.home == pytest.approx(0.3, abs=0.1)
    fitted = [model.attack[t] for t in sorted(attack)]
    assert np.corrcoef(fitted, [attack[t] for t in sorted(attack)])[0, 1] > 0.8


def test_fit_uses_only_matches_before_the_cutoff():
    matches, _ = _season()
    model = sr.fit(matches, 200, half_life_days=1e9, window_days=10_000)
    late = [sr.Match(m.day, m.home, m.away, 9, 0) for m in matches if m.day >= 200]
    assert sr.fit(matches[:200] + late, 200, half_life_days=1e9, window_days=10_000).attack == pytest.approx(model.attack)
    assert sr.fit(matches[:10], 200) is None


def test_probabilities_sum_to_one_and_unknown_teams_use_the_new_team_prior():
    matches, _ = _season()
    model = sr.fit(matches, 500)
    probabilities = model.probabilities("T0", "T1")
    assert probabilities.sum() == pytest.approx(1.0)
    assert model.rates("Promoted", "T1")[0] < model.rates("T1", "Promoted")[0] * math.exp(model.home) * 1.5
    assert model._team("Promoted") == sr.NEW_TEAM_PRIOR


def test_negative_rho_raises_draw_probability():
    independent = sr.outcome_probabilities(sr.score_grid(1.3, 1.1, 0.0))
    corrected = sr.outcome_probabilities(sr.score_grid(1.3, 1.1, -0.1))
    assert corrected[1] > independent[1]


def test_elo_moves_more_for_bigger_wins_and_regresses_between_seasons():
    narrow, wide = sr.Elo(), sr.Elo()
    narrow.update("A", "B", 1, 0)
    wide.update("A", "B", 4, 0)
    assert wide.rating("A") - 1500 > narrow.rating("A") - 1500 > 0
    before = wide.rating("A")
    wide.new_season()
    assert 1500 < wide.rating("A") < before
    assert wide.rating("Newcomer") == wide.new_team


def test_dominant_team_does_not_stall_the_fit():
    # One side scoring heavily drives the 0-0 correction negative at the start; the fit must still move.
    matches = []
    for day in range(300):
        opponent = f"T{day % 9}"
        if day % 2:
            matches.append(sr.Match(day, "Giant", opponent, 4, 0))
        else:
            matches.append(sr.Match(day, opponent, "Giant", 0, 3))
        matches.append(sr.Match(day, f"T{(day + 1) % 9}", f"T{(day + 2) % 9}", day % 3, (day + 1) % 2))
    stale = sr.DixonColes(base=0.0, home=0.2, rho=0.3, attack={"Giant": 1.5}, defence={"Giant": -1.0})
    model = sr.fit(matches, 300, warm=stale)
    assert model.attack["Giant"] > 1.0
    assert model.rates("Giant", "T1") != stale.rates("Giant", "T1")
