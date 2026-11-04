"""Deterministic market comparison; probabilities must come from a model.

No network, AI calls, wagers, or assumption that a historical hit rate is a
calibrated forecast. Integer-line pushes are represented explicitly.
"""
import math


def decimal_odds(american):
    if isinstance(american, bool) or not isinstance(american, (int, float)) or not math.isfinite(american) or abs(american) < 100:
        raise ValueError('American odds must be finite and at least +100 or at most -100')
    return 1 + (american / 100 if american > 0 else 100 / -american)


def compare_market(over_odds, under_odds, p_over, p_under, p_push=0):
    return compare_decimal_market(decimal_odds(over_odds), decimal_odds(under_odds), p_over, p_under, p_push)


def compare_decimal_market(over, under, p_over, p_under, p_push=0):
    if any(isinstance(price, bool) or not isinstance(price, (int,float)) or not math.isfinite(price) or price <= 1 for price in (over, under)):
        raise ValueError('Both decimal prices must be finite and greater than one')
    probabilities = (p_over, p_under, p_push)
    if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError('Probabilities must be finite numbers from zero to one')
    if not math.isclose(sum(probabilities), 1, abs_tol=1e-9):
        raise ValueError('Over, under and push probabilities must sum to one')
    raw_over, raw_under = 1 / over, 1 / under
    margin = raw_over + raw_under
    return {
        'market_overround': margin - 1,
        'over': {'decimal_odds': over, 'break_even_conditional_on_no_push': raw_over,
                 'market_no_vig_probability': raw_over / margin,
                 'model_win_probability': p_over, 'expected_profit_per_unit': p_over * (over - 1) - p_under},
        'under': {'decimal_odds': under, 'break_even_conditional_on_no_push': raw_under,
                  'market_no_vig_probability': raw_under / margin,
                  'model_win_probability': p_under, 'expected_profit_per_unit': p_under * (under - 1) - p_over},
        'push_probability': p_push,
        'scope': 'Market arithmetic only; freshness, model calibration and evidence gates must pass before any recommendation.'}
