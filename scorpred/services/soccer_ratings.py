"""Fitted soccer ratings: a time-weighted Dixon-Coles goals model and a goal-difference Elo.

Dixon-Coles gives each team an attack and a defence rating so that home goals are
Poisson with mean exp(base + home + attack_home + defence_away) and away goals with
exp(base + attack_away + defence_home); ``rho`` corrects the four low scores (0-0,
1-0, 0-1, 1-1) that independent Poisson draws misprice. Older matches count less
(exponential decay with ``half_life_days``), and a ridge penalty pulls each rating
toward a prior, so teams with few matches stay near it. Home, draw and away
probabilities all come from one score grid, so they always agree with the scoreline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

HALF_LIFE_DAYS = 365.0
WINDOW_DAYS = 730
RIDGE = 4.0
MAX_GOALS = 10
# A team new to the league is assumed weaker than average until its own results say otherwise.
NEW_TEAM_PRIOR = (-0.25, 0.20)
_TAU_FLOOR = 1e-6


@dataclass
class Match:
    day: int          # date.toordinal()
    home: str
    away: str
    home_goals: int
    away_goals: int


@dataclass
class DixonColes:
    base: float
    home: float
    rho: float
    attack: dict[str, float]
    defence: dict[str, float]
    prior: dict[str, tuple[float, float]] = field(default_factory=dict)

    def rates(self, home: str, away: str) -> tuple[float, float]:
        att_h, def_h = self._team(home)
        att_a, def_a = self._team(away)
        return math.exp(self.base + self.home + att_h + def_a), math.exp(self.base + att_a + def_h)

    def _team(self, team: str) -> tuple[float, float]:
        if team in self.attack:
            return self.attack[team], self.defence[team]
        return self.prior.get(team, NEW_TEAM_PRIOR)

    def score_grid(self, home: str, away: str, max_goals: int = MAX_GOALS) -> np.ndarray:
        lam, mu = self.rates(home, away)
        return score_grid(lam, mu, self.rho, max_goals)

    def probabilities(self, home: str, away: str) -> np.ndarray:
        """[home win, draw, away win]."""
        return outcome_probabilities(self.score_grid(home, away))


def score_grid(lam: float, mu: float, rho: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    goals = np.arange(max_goals + 1)
    grid = np.outer(poisson.pmf(goals, lam), poisson.pmf(goals, mu))
    grid[0, 0] *= 1 - lam * mu * rho
    grid[0, 1] *= 1 + lam * rho
    grid[1, 0] *= 1 + mu * rho
    grid[1, 1] *= 1 - rho
    grid = np.clip(grid, 0.0, None)
    return grid / grid.sum()


def outcome_probabilities(grid: np.ndarray) -> np.ndarray:
    return np.array([np.tril(grid, -1).sum(), np.trace(grid), np.triu(grid, 1).sum()])


def fit(matches: list[Match], as_of: int, *, half_life_days: float = HALF_LIFE_DAYS, window_days: int = WINDOW_DAYS,
        ridge: float = RIDGE, prior: dict[str, tuple[float, float]] | None = None,
        warm: DixonColes | None = None) -> DixonColes | None:
    """Fit on matches played before ``as_of`` (an ordinal day) within the window."""
    rows = [m for m in matches if as_of - window_days <= m.day < as_of]
    if len(rows) < 30:
        return None
    prior = prior or {}
    teams = sorted({m.home for m in rows} | {m.away for m in rows})
    index = {team: i for i, team in enumerate(teams)}
    n = len(teams)
    h = np.array([index[m.home] for m in rows])
    a = np.array([index[m.away] for m in rows])
    y = np.array([m.home_goals for m in rows], dtype=float)
    x = np.array([m.away_goals for m in rows], dtype=float)
    age = as_of - np.array([m.day for m in rows], dtype=float)
    w = np.power(0.5, age / half_life_days)
    centre_att = np.array([prior.get(t, (0.0, 0.0))[0] for t in teams])
    centre_def = np.array([prior.get(t, (0.0, 0.0))[1] for t in teams])
    is00, is01, is10, is11 = (y == 0) & (x == 0), (y == 0) & (x == 1), (y == 1) & (x == 0), (y == 1) & (x == 1)

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        base, home, rho = theta[0], theta[1], theta[2]
        att, dfn = theta[3:3 + n], theta[3 + n:]
        eta_h = base + home + att[h] + dfn[a]
        eta_a = base + att[a] + dfn[h]
        lam, mu = np.exp(eta_h), np.exp(eta_a)
        tau = np.ones_like(lam)
        tau = np.where(is00, 1 - lam * mu * rho, tau)
        tau = np.where(is01, 1 + lam * rho, tau)
        tau = np.where(is10, 1 + mu * rho, tau)
        tau = np.where(is11, 1 - rho, tau)
        # A very lopsided pairing can push the low-score correction below zero; clip it there
        # (and drop its gradient) instead of rejecting the step, which would stall the optimiser.
        feasible = tau > _TAU_FLOOR
        tau = np.where(feasible, tau, _TAU_FLOOR)
        loglik = w * (np.log(tau) + y * eta_h - lam + x * eta_a - mu)
        d_tau_lam = np.where(is00, -mu * rho, 0.0) + np.where(is01, rho, 0.0)
        d_tau_mu = np.where(is00, -lam * rho, 0.0) + np.where(is10, rho, 0.0)
        d_tau_rho = (np.where(is00, -lam * mu, 0.0) + np.where(is01, lam, 0.0)
                     + np.where(is10, mu, 0.0) + np.where(is11, -1.0, 0.0))
        d_tau_lam, d_tau_mu, d_tau_rho = d_tau_lam * feasible, d_tau_mu * feasible, d_tau_rho * feasible
        g_h = w * (y - lam + d_tau_lam * lam / tau)
        g_a = w * (x - mu + d_tau_mu * mu / tau)
        g_rho = (w * d_tau_rho / tau).sum()
        g_att = np.bincount(h, g_h, n) + np.bincount(a, g_a, n)
        g_def = np.bincount(a, g_h, n) + np.bincount(h, g_a, n)
        penalty = 0.5 * ridge * (np.square(att - centre_att).sum() + np.square(dfn - centre_def).sum())
        grad = np.concatenate([[g_h.sum() + g_a.sum(), g_h.sum(), g_rho],
                               g_att - ridge * (att - centre_att), g_def - ridge * (dfn - centre_def)])
        return -(loglik.sum() - penalty), -grad

    cold = np.zeros(3 + 2 * n)
    cold[0], cold[1], cold[2] = math.log(max(0.5, (y.sum() + x.sum()) / (2 * len(rows)))), 0.25, -0.05
    bounds = [(None, None), (None, None), (-0.3, 0.3)] + [(None, None)] * (2 * n)
    starts = [cold]
    if warm is not None:
        start = cold.copy()
        start[0], start[1], start[2] = warm.base, warm.home, warm.rho
        for team, i in index.items():
            start[3 + i], start[3 + n + i] = warm._team(team)
        starts.insert(0, start)
    for theta0 in starts:
        result = minimize(objective, theta0, jac=True, method="L-BFGS-B", bounds=bounds)
        if result.success:
            break
    theta = result.x
    return DixonColes(
        base=float(theta[0]), home=float(theta[1]), rho=float(theta[2]),
        attack={t: float(theta[3 + i]) for t, i in index.items()},
        defence={t: float(theta[3 + n + i]) for t, i in index.items()},
        prior=dict(prior),
    )


class Elo:
    """Goal-difference Elo (World Football Elo multiplier) with a fixed home advantage."""

    def __init__(self, k: float = 20.0, home_advantage: float = 60.0, new_team: float = 1400.0,
                 season_carry: float = 0.8):
        self.k, self.home_advantage, self.new_team, self.season_carry = k, home_advantage, new_team, season_carry
        self.ratings: dict[str, float] = {}

    def rating(self, team: str) -> float:
        return self.ratings.get(team, 1500.0 if not self.ratings else self.new_team)

    def difference(self, home: str, away: str) -> float:
        return self.rating(home) + self.home_advantage - self.rating(away)

    def new_season(self) -> None:
        """Regress toward the league mean between seasons; transfers and managers change teams."""
        if self.ratings:
            mean = sum(self.ratings.values()) / len(self.ratings)
            self.ratings = {t: mean + self.season_carry * (r - mean) for t, r in self.ratings.items()}

    def update(self, home: str, away: str, home_goals: int, away_goals: int) -> None:
        expected = 1 / (1 + 10 ** (-self.difference(home, away) / 400))
        actual = 1.0 if home_goals > away_goals else 0.5 if home_goals == away_goals else 0.0
        margin = abs(home_goals - away_goals)
        multiplier = 1.0 if margin <= 1 else 1.5 if margin == 2 else (11 + margin) / 8
        change = self.k * multiplier * (actual - expected)
        home_rating, away_rating = self.rating(home), self.rating(away)
        self.ratings[home] = home_rating + change
        self.ratings[away] = away_rating - change
