"""Monotone, versioned probability calibration; no unvalidated market is promoted."""
from functools import lru_cache
import json
import math
import os
from runtime_paths import model_dir

ARTIFACT = "player_props_calibration_v1.json"


@lru_cache(maxsize=4)
def _load(path, modified):
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        return body if body.get("version") == "player-props-calibration-v1" else {}
    except (OSError, ValueError):
        return {}


def parameters(sport, market):
    if os.getenv("SCORPRED_TESTING") == "1" or os.getenv("SCORPRED_PROPS_TRAINING") == "1":
        return None
    path = model_dir() / ARTIFACT
    if not path.exists():
        return None
    row = _load(path, path.stat().st_mtime_ns).get("markets", {}).get(f"{sport}/{market}")
    if not row or not (.1 <= row.get("slope", 0) <= 3 and -2 <= row.get("intercept", 100) <= 2):
        return None
    return row


def adjusted(probability, params):
    p = min(1 - 1e-5, max(1e-5, probability))
    z = params["slope"] * math.log(p / (1 - p)) + params["intercept"]
    return 1 / (1 + math.exp(-z))


def apply(market, sport):
    params = parameters(sport, market["market"])
    if params and sport == 'nfl' and market['market'] == 'anytime_td':
        result = {**market, 'probability': round(adjusted(market['probability'], params), 4),
                  'calibration': 'player-props-calibration-v1'}
        if market.get('pick'):
            result['pick'] = {**market['pick'], 'probability': result['probability'],
                              'calibration': result['calibration']}
        return result
    if not params or not market.get("ladder"):
        return market
    result = dict(market)
    result["ladder"] = {**market["ladder"], "over": [round(adjusted(p, params), 4) for p in market["ladder"]["over"]]}
    pick = market.get("pick") if sport == "nfl" else market
    if not pick or pick.get("line") is None:
        return result
    raw = pick["probability"] if pick["side"] == "over" else 1 - pick["probability"]
    record = pick["record"] if pick["side"] == "over" else 1 - pick["record"]
    p = adjusted(raw, params)
    side = "over" if p >= record else "under"
    target = {**pick, "side": side, "probability": round(p if side == "over" else 1 - p, 4),
              "record": round(record if side == "over" else 1 - record, 4),
              "calibration": "player-props-calibration-v1"}
    if sport == "nfl":
        result["pick"] = target
    else:
        result.update({key: target[key] for key in ("side", "probability", "record", "calibration")})
    return result
