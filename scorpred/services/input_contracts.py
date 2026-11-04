"""Validate declared inputs without constructing replacement predictions."""
import math


def three_way_probabilities(value):
    if not isinstance(value, dict):
        return False
    keys = ("a", "draw", "b") if "a" in value else ("home", "draw", "away")
    values = [value.get(k) for k in keys]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values):
        return False
    total = sum(values)
    return (max(values) <= 1 and abs(total - 1) <= .02) or (max(values) <= 100 and abs(total - 100) <= 1)
