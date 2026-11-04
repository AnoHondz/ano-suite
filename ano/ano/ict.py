"""ICT 2022 model and AMD (power of three) on one-minute bars, in New York time. Analysis only: no orders.

The 2022 model, bearish (bullish is the mirror image):
  1. In a kill zone, price takes buy-side liquidity: it trades above a named pool (previous day high, the
     accumulation-range high) or a confirmed swing high.
  2. Market structure shifts: within MSS_WINDOW bars a candle closes below the swing low that led to the high.
  3. The move down is a displacement (a large-bodied candle) and leaves a fair value gap.
  4. Entry is the gap's consequent encroachment (midpoint) on the retrace; the stop goes above the swept high;
     the target is the nearest untouched sell-side liquidity at least MIN_RR away. Without such liquidity there
     is no setup (it is counted under rejected['no_liquidity_target']); no target is ever invented.
Timing: a setup exists only once both the shift and the gap have closed (the decision bar). Entries, and the
liquidity a target may use, are judged from the bar after that, so nothing is filled with hindsight.
AMD: the accumulation range is the pre-market (or, without pre-market bars, the first 15 minutes); the
manipulation is the first raid beyond it in the NY AM kill zone (the Judas swing); distribution is the move
the other way. A setup that trades the distribution side of a Judas swing is "AMD aligned".

Every setup gets a 0–7 checklist score (A ≥ 5, B 3–4, C below) and, when later bars exist, its outcome in R:
a stop and target touched in the same bar count as a loss; an open trade is closed at 15:55. Backtest
statistics take one position at a time per symbol: a setup that would enter while the previous trade is still
open is listed but not counted. These are hypotheses about price behaviour, not resting orders or advice, and
results exclude fees and slippage.
"""
# 2: no entries before the decision bar, liquidity targets only, one position at a time.
# 3: a pool is taken only by trading through it after it formed, in or out of a kill zone.
ENGINE_VERSION = 3
from contextlib import contextmanager
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
KILLZONES = (('NY AM', dtime(9, 30), dtime(11, 0)), ('NY PM', dtime(13, 30), dtime(15, 0)))
SILVER_BULLETS = ((dtime(10, 0), dtime(11, 0)), (dtime(14, 0), dtime(15, 0)))
OPEN, CLOSE, LAST_EXIT = dtime(9, 30), dtime(16, 0), dtime(15, 55)
SWING = 3            # bars each side of a swing point
MSS_WINDOW = 30      # bars after the sweep for structure to shift
ENTRY_WINDOW = 30    # bars after the gap for price to come back to the entry
MIN_RR = 1.5        # = trading_risk.TARGET_R (realistic take-profits, 2026-11-02)
DISPLACEMENT = 1.5   # the strongest candle of the move must have a body at least this × the recent average
ENTRY = 'ce'         # 'ce': the gap's midpoint (consequent encroachment); 'edge': the gap's near edge (first touch)
TICK = 0.01
TUNABLE = ('SWING', 'MSS_WINDOW', 'ENTRY_WINDOW', 'MIN_RR', 'DISPLACEMENT', 'ENTRY')


@contextmanager
def rules(**changes):
    """Research only: run the engine with other rule values, e.g. with rules(SWING=5, MIN_RR=3): ...
    Restores the defaults afterwards. Not for concurrent use; the live tools always use the defaults."""
    unknown = set(changes) - set(TUNABLE)
    if unknown:
        raise ValueError(f'Unknown rule(s): {", ".join(sorted(unknown))}')
    saved = {name: globals()[name] for name in changes}
    globals().update(changes)
    try:
        yield
    finally:
        globals().update(saved)
NAMED = ('PDH', 'PDL', 'pre-market high', 'pre-market low', 'opening range high', 'opening range low')


def ny(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone(NY)


def stamp(seconds):
    return ny(seconds).strftime('%Y-%m-%d %H:%M ET')


def in_window(seconds, windows):
    clock = ny(seconds).time()
    return any(start <= clock < end for start, end in windows)


def killzone(seconds):
    clock = ny(seconds).time()
    return next((name for name, start, end in KILLZONES if start <= clock < end), None)


def by_day(candles):
    days = {}
    for bar in candles:
        days.setdefault(ny(bar['t']).date(), []).append(bar)
    return days


def swings(bars):
    """Confirmed swing points: (kind, price, index, confirmed_index)."""
    found = []
    for center in range(SWING, len(bars) - SWING):
        around = bars[center - SWING:center] + bars[center + 1:center + SWING + 1]
        if bars[center]['h'] > max(b['h'] for b in around):
            found.append(('high', bars[center]['h'], center, center + SWING))
        if bars[center]['l'] < min(b['l'] for b in around):
            found.append(('low', bars[center]['l'], center, center + SWING))
    return found


def analyze_day(day_bars, previous=None):
    """Setups and AMD for one New York trading day. previous: (high, low) of the prior regular session."""
    bars = [dict(t=b['t'], o=float(b['o']), h=float(b['h']), l=float(b['l']), c=float(b['c'])) for b in day_bars]
    pre = [b for b in bars if ny(b['t']).time() < OPEN]
    reg = [b for b in bars if OPEN <= ny(b['t']).time() < CLOSE]
    if len(reg) < 20:
        return dict(setups=[], amd=None, regular_bars=len(reg))
    pools = []  # (name, side, price, confirmed_index in reg)
    if previous:
        pools += [('PDH', 'high', previous[0], 0), ('PDL', 'low', previous[1], 0)]
    if len(pre) >= 10:
        accumulation = ('pre-market', max(b['h'] for b in pre), min(b['l'] for b in pre), 0)
    else:
        accumulation = ('opening range', max(b['h'] for b in reg[:15]), min(b['l'] for b in reg[:15]), 15)
    name, acc_high, acc_low, ready = accumulation
    pools += [(f'{name} high', 'high', acc_high, ready), (f'{name} low', 'low', acc_low, ready)]
    points = swings(reg)
    pools += [(f'swing {kind}', kind, price, confirmed) for kind, price, _index, confirmed in points]
    taken = set()

    # AMD: the first raid beyond the accumulation range in the NY AM kill zone is the manipulation (Judas swing).
    judas = None
    for index in range(ready, len(reg)):
        bar = reg[index]
        if killzone(bar['t']) != 'NY AM':
            continue
        if bar['h'] > acc_high or bar['l'] < acc_low:
            judas = dict(direction='up' if bar['h'] > acc_high else 'down', at=stamp(bar['t']), index=index)
            break
    amd = dict(accumulation=dict(source=name, high=round(acc_high, 2), low=round(acc_low, 2)),
               manipulation=judas and {k: v for k, v in judas.items() if k != 'index'},
               distribution=None)

    average_body = lambda index: (sum(abs(b['c'] - b['o']) for b in reg[max(0, index - 20):index]) /
                                  max(1, len(reg[max(0, index - 20):index]))) or TICK
    setups, rejected = [], {}
    for index, bar in enumerate(reg):
        zone = killzone(bar['t'])
        for pool in pools:
            label, side, price, confirmed = pool
            if confirmed > index - 1 or pool in taken:
                continue
            if (side == 'high' and bar['h'] > price) or (side == 'low' and bar['l'] < price):
                taken.add(pool)  # liquidity taken outside a kill zone is gone too; it just makes no setup
                if not zone:
                    continue
                setup = _model(reg, index, label, side, price, points, pools, average_body, previous, judas)
                if isinstance(setup, str):
                    rejected[setup] = rejected.get(setup, 0) + 1
                elif setup:
                    setup.update(killzone=zone)
                    setups.append(setup)
    # Several sweeps can lead to the same shift and gap: keep the best-scored one.
    best = {}
    for setup in setups:
        key = (setup['direction'], setup['fvg_at'])
        if key not in best or setup['score'] > best[key]['score']:
            best[key] = setup
    setups = sorted(best.values(), key=lambda s: s['fvg_at'])
    distribution = next((s for s in setups if s['amd_aligned']), None)
    if distribution:
        amd['distribution'] = dict(direction='down' if distribution['direction'] == 'short' else 'up',
                                   shift_at=distribution['mss_at'])
    return dict(setups=setups, amd=amd, regular_bars=len(reg), rejected=rejected)


def untouched(reg, kind, price, start, end):
    """Is a liquidity pool still resting at the end bar? Only trading through it after it formed (from start)
    takes it: a lower high made after the day's high is fresh liquidity."""
    return not any((b['l'] < price) if kind == 'low' else (b['h'] > price) for b in reg[start:end + 1])


def _model(reg, index, label, side, level, points, pools, average_body, previous, judas):
    """A setup dict, or the reason there is none (a string, counted in rejected)."""
    short = side == 'high'  # buy-side liquidity taken: look for a bearish shift
    sign = -1 if short else 1
    before = [p for p in points if p[0] == ('low' if short else 'high') and p[3] <= index and p[2] < index]
    if not before:
        return 'no_structure_to_shift'
    shift_level = before[-1][1]
    extreme, extreme_index, shift = reg[index]['h'] if short else reg[index]['l'], index, None
    for j in range(index, min(len(reg), index + MSS_WINDOW + 1)):
        bar = reg[j]
        if (short and bar['h'] > extreme) or (not short and bar['l'] < extreme):
            extreme, extreme_index = (bar['h'] if short else bar['l']), j
        if (short and bar['c'] < shift_level) or (not short and bar['c'] > shift_level):
            shift = j
            break
    if shift is None:
        return 'no_shift'
    leg = reg[extreme_index:shift + 1]
    strongest = max(abs(b['c'] - b['o']) for b in leg if (b['c'] - b['o']) * sign > 0) if any(
        (b['c'] - b['o']) * sign > 0 for b in leg) else 0
    displacement = strongest / average_body(index)
    if displacement < DISPLACEMENT:
        return 'no_displacement'
    gap = None
    for k in range(extreme_index + 2, min(len(reg), shift + 2)):
        first, third = reg[k - 2], reg[k]
        if third['t'] - first['t'] != 120:  # three back-to-back minutes
            continue
        if short and third['h'] < first['l']:
            gap = (third['h'], first['l'], k)
        if not short and third['l'] > first['h']:
            gap = (first['h'], third['l'], k)
    if not gap:
        return 'no_gap'
    low_edge, high_edge, gap_index = gap
    decision = max(shift, gap_index)  # the setup exists only once the shift and the gap have both closed
    # A short waits for price to rise back into the gap, so its near edge is the gap's low; a long's is the high.
    entry = round((low_edge + high_edge) / 2 if ENTRY == 'ce' else (low_edge if short else high_edge), 2)
    stop = round(extreme + TICK if short else extreme - TICK, 2)
    risk = abs(stop - entry)
    if risk <= 0:
        return 'no_risk'
    leg_far = min(b['l'] for b in reg[extreme_index:decision + 1]) if short else max(b['h'] for b in reg[extreme_index:decision + 1])
    span = abs(extreme - leg_far)
    ote = sorted((leg_far + span * 0.62 * (1 if short else -1), leg_far + span * 0.79 * (1 if short else -1)))
    ote_overlap = low_edge <= ote[1] and high_edge >= ote[0]
    # Draw on liquidity: the nearest opposing pool, known by the decision bar, that price has not traded through
    # since it formed (a later lower high is fresh liquidity even below the day's high), at least MIN_RR away.
    opposite = [(name, price) for name, kind, price, confirmed in pools
                if kind == ('low' if short else 'high') and confirmed <= decision
                and (price < entry if short else price > entry) and untouched(reg, kind, price, confirmed, decision)]
    far_enough = sorted(((name, price) for name, price in opposite if abs(entry - price) / risk >= MIN_RR),
                        key=lambda item: abs(entry - item[1]))
    if not far_enough:
        return 'no_liquidity_target'
    target_name, target = far_enough[0]
    target = round(target, 2)
    rr = round(abs(target - entry) / risk, 2)
    discount_side = None
    if previous:
        equilibrium = (previous[0] + previous[1]) / 2
        discount_side = entry > equilibrium if short else entry < equilibrium
    aligned = bool(judas) and judas['index'] <= index and judas['direction'] == ('up' if short else 'down')
    checks = dict(named_liquidity=label in NAMED, strong_displacement=displacement >= 2,
                  fvg_in_ote=ote_overlap, premium_discount=bool(discount_side),
                  silver_bullet=in_window(reg[decision]['t'], SILVER_BULLETS), amd_aligned=aligned)
    score = sum(checks.values()) + 1  # +1: the sweep happened inside a kill zone (only kill-zone sweeps get here)
    setup = dict(direction='short' if short else 'long', liquidity=label, liquidity_price=round(level, 2),
                 swept_at=stamp(reg[index]['t']), extreme=round(extreme, 2), mss_level=round(shift_level, 2),
                 mss_at=stamp(reg[shift]['t']), displacement=round(displacement, 1),
                 fvg=[round(low_edge, 2), round(high_edge, 2)], fvg_at=stamp(reg[gap_index]['t']),
                 ote=[round(ote[0], 2), round(ote[1], 2)], entry=entry, stop=stop, target=target,
                 target_liquidity=target_name, rr=rr, checks=checks, score=score, amd_aligned=aligned,
                 decided_at=stamp(reg[decision]['t']), grade='A' if score >= 5 else 'B' if score >= 3 else 'C')
    setup.update(_outcome(reg, decision, short, entry, stop, target, risk))
    setup['_fvg_t'] = reg[gap_index]['t']
    return setup


def _outcome(reg, decision, short, entry, stop, target, risk):
    """What the bars after the decision bar did: waiting, missed, expired, win, loss or time exit (R before costs).

    _entered_t and _exited_t are bar times kept for the one-position-at-a-time rule in summarize().
    """
    filled = None
    def done(status, r, exit_bar=None):
        result = dict(status=status, r=r)
        if filled is not None:
            result.update(entered_at=stamp(reg[filled]['t']), _entered_t=reg[filled]['t'])
        if exit_bar is not None:
            result.update(exited_at=stamp(exit_bar['t']), _exited_t=exit_bar['t'])
        return result
    for j in range(decision + 1, len(reg)):
        bar = reg[j]
        if filled is None:
            if j > decision + ENTRY_WINDOW or ny(bar['t']).time() >= LAST_EXIT:
                return done('expired', None)
            if (short and bar['l'] <= target) or (not short and bar['h'] >= target):
                return done('missed (ran without a retrace)', None)
            if (short and bar['h'] >= entry) or (not short and bar['l'] <= entry):
                filled = j
                if (short and bar['h'] >= stop) or (not short and bar['l'] <= stop):
                    return done('loss', -1.0, bar)
            continue
        if (short and bar['h'] >= stop) or (not short and bar['l'] <= stop):
            return done('loss', -1.0, bar)
        if (short and bar['l'] <= target) or (not short and bar['h'] >= target):
            return done('win', round(abs(target - entry) / risk, 2), bar)
        if ny(bar['t']).time() >= LAST_EXIT:
            move = (entry - bar['c']) if short else (bar['c'] - entry)
            return done('time exit', round(move / risk, 2), bar)
    return done('in trade' if filled is not None else 'waiting for entry', None)


def analyze(candles, previous=None):
    """Every New York day in the candles, each using the prior day in the data (or previous) for PDH/PDL."""
    days = by_day(candles)
    results, prior = [], previous
    for day in sorted(days):
        result = analyze_day(days[day], prior)
        result['date'] = day.isoformat()
        results.append(result)
        regular = [b for b in days[day] if OPEN <= ny(b['t']).time() < CLOSE]
        if regular:
            prior = (max(float(b['h']) for b in regular), min(float(b['l']) for b in regular))
    return results


def one_at_a_time(setups):
    """Closed trades a single account could actually take: a setup that would enter while the previous trade is
    still open is skipped. Returns (taken, skipped)."""
    taken, skipped, free_at = [], [], 0
    for setup in sorted((s for s in setups if s.get('r') is not None), key=lambda s: s['_entered_t']):
        if setup['_entered_t'] < free_at:
            skipped.append(setup)
            continue
        taken.append(setup)
        free_at = setup.get('_exited_t', setup['_entered_t'])
    return taken, skipped


def summarize(days):
    """Backtest statistics by grade (one position at a time), rejection reasons, and the best setups."""
    setups = [s for day in days for s in day['setups']]
    closed, overlapping = one_at_a_time(setups)
    rejected = {}
    for day in days:
        for reason, count in day.get('rejected', {}).items():
            rejected[reason] = rejected.get(reason, 0) + count
    def stats(rows):
        wins = [s for s in rows if s['status'] == 'win']
        gains = sum(s['r'] for s in rows if s['r'] > 0)
        losses = -sum(s['r'] for s in rows if s['r'] < 0)
        return dict(trades=len(rows), wins=len(wins), win_rate=round(len(wins) / len(rows), 2) if rows else None,
                    total_r=round(sum(s['r'] for s in rows), 2), average_r=round(sum(s['r'] for s in rows) / len(rows), 2) if rows else None,
                    profit_factor=round(gains / losses, 2) if losses else None)
    by_grade = {grade: stats([s for s in closed if s['grade'] == grade]) for grade in 'ABC'}
    best = sorted(setups, key=lambda s: (s['score'], s['rr']), reverse=True)[:5]
    return dict(engine=ENGINE_VERSION, days=len(days), setups=len(setups), traded=len(closed),
                skipped_overlapping=len(overlapping), overall=stats(closed), by_grade=by_grade,
                not_filled=len([s for s in setups if s['status'].startswith(('missed', 'expired'))]),
                rejected=rejected, best=[{k: v for k, v in s.items() if not k.startswith('_')} for s in best],
                note='One position at a time; R multiples before fees and slippage; same-bar stop and target counted '
                     'as a loss. Past results do not predict future ones.')
