"""One value policy for every emailed pick and parlay (audit fixes, 2026-11-04).

- The bookmaker's price carries its margin (vig). Its fair chance is de-vigged: both sides of the market
  when both are priced, otherwise the price shrunk by a typical two-way prop margin.
- A leg is value (+EV) only when our chance beats its price by VALUE_EDGE at the price actually offered.
- Picking the biggest edges from hundreds of lines overstates them (winner's curse), so parlays are ranked by
  a cautious chance: each leg's estimate minus half its standard error, given how much evidence is behind it.
- Every tier is still sent every day (user, 2026-11-04). A slip whose legs are all value is tagged "+EV";
  otherwise it is the best available slip, tagged so the email never presents it as a value bet.
"""
from __future__ import annotations

import math

VALUE_EDGE = 0.04       # +4% expected value at the offered price; a screening value, not a proven optimum
PROP_MARGIN = 0.045     # typical two-way prop overround when only one side is priced
PRICE_FLOOR = 1.25      # no leg shorter than about -400: one miss wipes out many small wins
CAUTION = 0.5           # standard errors taken off each chance before ranking parlays
# Until the pick tracker shows our edges over the market are real (closing-line value and calibration by band),
# only half of each estimated edge over the fair price counts (review of the 2026-11-04 email: a 7-of-15 record shown
# at 61% vs a fair 49%, and five-leg slips claiming +90% value). Revisit with the weekly scorecard.
EDGE_SHRINK = 0.5
TAG_VALUE, TAG_BEST = "+EV", "Best available"


def calibrated(chance: float, fair: float) -> float:
    """Our chance with only EDGE_SHRINK of its disagreement with the fair price kept."""
    return fair + EDGE_SHRINK * (chance - fair)


def no_vig(price: float, other: float | None = None, *, margin: float = PROP_MARGIN) -> float:
    """The fair chance behind a decimal price, with the bookmaker's margin removed."""
    raw = 1 / price
    if other and other > 1:
        return raw / (raw + 1 / other)
    return raw / (1 + margin)


def expected_value(chance: float, price: float) -> float:
    return chance * price - 1


def split(leg: dict) -> bool:
    """True when one source of the chance (the player's record vs the projection model) sits below the fair price:
    a leg is value only when every source agrees, so no single source can carry it."""
    fair = leg.get("fair")
    return fair is not None and any(part is not None and part < fair for part in (leg.get("parts") or {}).values())


def is_value(leg: dict) -> bool:
    return bool(leg.get("chance") and leg.get("price") and expected_value(leg["chance"], leg["price"]) >= VALUE_EDGE
                and not split(leg))


def value_sentence(leg: dict) -> str | None:
    """Each leg's value in words, so a 'Best available' slip shows which legs fall short and by how much."""
    if leg.get("ev") is None:
        return None
    ev = leg["ev"]
    if split(leg):
        return f"Not counted as value ({ev:+.1%}): its record and ScorPred's projection model disagree about it."
    if ev >= VALUE_EDGE:
        return f"Value: {ev:+.1%} at this price."
    if ev >= 0:
        return f"Value: {ev:+.1%}, below the 4% bar."
    return f"Negative value ({ev:+.1%}) at this price: here only to complete the slip."


def cautious(chance: float, evidence: float) -> float:
    """The chance less half a standard error: thin evidence counts for less when choosing between slips."""
    evidence = max(evidence or 0, 1)
    return max(0.0, chance - CAUTION * math.sqrt(chance * (1 - chance) / evidence))


def slip_value(legs) -> tuple[float, float, float]:
    """(cautious expected value, estimated hit chance, decimal odds) for a slip, legs treated as separate games."""
    odds = math.prod(leg["price"] for leg in legs)
    hit = math.prod(leg["chance"] for leg in legs)
    careful = math.prod(cautious(leg["chance"], leg.get("evidence") or 40) for leg in legs)
    return careful * odds - 1, hit, odds


def tag(legs) -> str:
    return TAG_VALUE if legs and all(is_value(leg) for leg in legs) else TAG_BEST
