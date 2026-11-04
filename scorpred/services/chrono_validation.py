"""Nested chronological validation (audit, 2026-11-04): pick settings on one period, measure once on a later one.

A replay walks history in date order and scores every prediction made from earlier games only. Its timeline is cut
into three periods: the earliest (history the replay learns from), a selection period (where candidate settings are
compared and one is chosen) and the final untouched test period (where only the chosen setting's score is the result).
Choosing on the same seasons that are reported is what overstates backtests; this keeps the two apart.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable


def cuts(times: list[float], select: float = 0.2, test: float = 0.2) -> tuple[float, float]:
    """(start of the selection period, start of the test period) as quantiles of the prediction times."""
    times = sorted(times)
    if len(times) < 10:
        raise ValueError("too few predictions to split")
    return times[int(len(times) * (1 - select - test))], times[int(len(times) * (1 - test))]


def mean_loss(run: Iterable[tuple[float, float]], start: float, end: float = float("inf")) -> tuple[float | None, int]:
    losses = [loss for when, loss in run if start <= when < end]
    return (sum(losses) / len(losses) if losses else None), len(losses)


def choose_then_test(candidates: dict, replay: Callable[[object], list[tuple[float, float]]], *,
                     select: float = 0.2, test: float = 0.2) -> dict:
    """candidates: {name: settings}; replay(settings) -> [(time, loss)], lower loss is better.

    Returns the chosen name (best on the selection period only), every candidate's selection score, and the test
    score: the chosen candidate's is the result; the others are shown for reference, never used to choose."""
    runs = {name: replay(settings) for name, settings in candidates.items()}
    start, test_start = cuts([when for run in runs.values() for when, _ in run], select, test)
    selection = {name: mean_loss(run, start, test_start) for name, run in runs.items()}
    chosen = min((name for name in runs if selection[name][0] is not None), key=lambda name: selection[name][0])
    tested = {name: mean_loss(run, test_start) for name, run in runs.items()}
    return {"chosen": chosen, "selection": selection, "test": tested, "result": tested[chosen],
            "selection_starts": start, "test_starts": test_start}
