"""Ano Silver Bullet v1 research variant. Deterministic, closed bars only; no orders.

15-minute direction is the change between the last two complete RTH closes before
the sweep. Sweep, MSS, FVG confirmation and entry must all fit one NY window.
This is an explicit research definition, not a claim of canonical ICT rules or edge.
"""
from datetime import datetime, timezone
from . import ict

VERSION = 'ano_silver_bullet_v1'
WINDOWS = ((10, 11), (14, 15))


def seconds(text):
    return int(datetime.strptime(text, '%Y-%m-%d %H:%M ET').replace(tzinfo=ict.NY).timestamp())


def window(t):
    local = ict.ny(t)
    for start, end in WINDOWS:
        if start <= local.hour < end:
            return (int(local.replace(hour=start, minute=0, second=0).timestamp()),
                    int(local.replace(hour=end, minute=0, second=0).timestamp()))


def direction(bars, before):
    buckets = {}
    for b in bars:
        if not ict.OPEN <= ict.ny(b['t']).time() < ict.CLOSE:
            continue
        key = b['t'] // 900 * 900
        if key + 900 <= before:
            buckets.setdefault(key, {})[b['t']] = b
    complete = [(t, rows[t + 840]['c']) for t, rows in sorted(buckets.items())
                if set(rows) == set(range(t, t + 900, 60))]
    if len(complete) < 2 or complete[-1][0] - complete[-2][0] != 900:
        return None
    a, b = complete[-2][1], complete[-1][1]
    return 'long' if b > a else 'short' if b < a else None


def outcome(s, bars, end, now):
    """Conservative bar simulation, bounded by elapsed minutes and window end."""
    decision = seconds(s['decided_at'])
    expiry = min(end, decision + 60 + 30 * 60)
    short = s['direction'] == 'short'
    entry, stop, target = (s[k] for k in ('entry', 'stop', 'target'))
    risk = abs(stop - entry)
    filled = None
    result = dict(status='waiting for entry', r=None)
    for b in bars:
        t = b['t']
        if t <= decision or t >= end:
            continue
        stop_hit = b['h'] >= stop if short else b['l'] <= stop
        target_hit = b['l'] <= target if short else b['h'] >= target
        if filled is None:
            if t >= expiry:
                break
            if b['l'] <= entry <= b['h']:
                filled = t
                result.update(entered_at=ict.stamp(t), _entered_t=t)
                if not stop_hit:
                    continue  # OHLC cannot prove target occurred after entry in this bar.
            else:
                if stop_hit or target_hit:
                    return dict(status='invalidated before entry', r=None)
                continue
        if stop_hit or target_hit:
            result.update(status='loss' if stop_hit else 'win',
                          r=-1.0 if stop_hit else round(abs(target-entry)/risk, 2),
                          exited_at=ict.stamp(t), _exited_t=t)
            return result
    if filled is None:
        result['status'] = 'expired' if now >= expiry else 'waiting for entry'
    elif now >= end:
        # Never invent a flatten fill from a missing last minute.
        last = next((b for b in bars if b['t'] == end-60), None)
        if last:
            move = entry-last['c'] if short else last['c']-entry
            result.update(status='time exit (bar simulation)', r=round(move/risk, 2),
                          exited_at=ict.stamp(last['t']), _exited_t=end)
        else:
            result['status'] = 'exit unverified (missing window-close bar)'
    else:
        result['status'] = 'in trade (bar simulation)'
    return result


def analyze(candles, *, now):
    bars = sorted((dict(b) for b in candles if b['t']+60 <= now), key=lambda b:b['t'])
    days = ict.analyze(bars)
    for day in days:
        selected, rejected = [], {}
        day_bars = [b for b in bars if str(ict.ny(b['t']).date()) == day['date']]
        for candidate in day['setups']:
            s = {k:v for k,v in candidate.items() if k not in
                 ('status','r','entered_at','exited_at','_entered_t','_exited_t')}
            sweep, decision, gap = (seconds(s[k]) for k in ('swept_at','decided_at','fvg_at'))
            w = window(sweep)
            reason = None
            if not w or not all(w[0] <= t and t+60 < w[1]
                                for t in (decision, gap, seconds(s['mss_at']))):
                reason = 'outside_same_silver_bullet_window'
            elif s['liquidity'] not in ict.NAMED:
                reason = 'not_named_liquidity'
            elif direction(day_bars, sweep) != s['direction']:
                reason = '15m_bias_missing_or_not_aligned'
            if reason:
                rejected[reason] = rejected.get(reason,0)+1
                continue
            s.update(strategy=VERSION, bias_15m=s['direction'], window_end=ict.stamp(w[1]))
            s.update(outcome(s, day_bars, w[1], now))
            selected.append(s)
        day['base_candidate_count'] = len(day['setups'])
        day['setups'] = selected
        day['silver_rejected'] = rejected
    latest = bars[-1]['t']+60 if bars else None
    return dict(strategy=VERSION, status='ANALYSIS ONLY', execution_authorized=False,
                as_of=datetime.fromtimestamp(now,timezone.utc).isoformat(),
                last_closed_at=latest, stale=latest is None or now-latest>180,
                days=days, note='Sparse IEX feed; no bar does not prove no market trading. '
                '15m bias requires all 15 minute bars. Outcomes are simulations before costs, not broker fills. '
                'No proven edge. Daily/1h bias is not implemented in this variant.')


def describe(report):
    today = str(datetime.fromisoformat(report['as_of']).astimezone(ict.NY).date())
    day = next((d for d in report['days'] if d['date'] == today), None)
    head = f'{report["symbol"]} · Silver Bullet v1 · ANALYSIS ONLY\n'
    if report['stale']:
        return head + 'WAIT — stale or missing IEX data. No orders placed.'
    setups = day['setups'] if day else []
    lines = [head, f'{len(setups)} confirmed research setup(s) today.']
    for s in setups:
        lines.append(f'{s["direction"]}: entry {s["entry"]:.2f}, stop {s["stop"]:.2f}, '
                     f'target {s["target"]:.2f} ({s["rr"]}R); {s["status"]}; confirmed after {s["decided_at"]}.')
    if not setups:
        lines.append('WAIT — no qualifying named sweep / 15m bias / MSS / FVG sequence.')
    lines.append('NY windows 10–11 and 14–15. IEX feed; results are bar simulations, not fills. No proven edge or orders.')
    return '\n'.join(lines)
