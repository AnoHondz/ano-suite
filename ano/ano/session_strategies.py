"""Session strategies for QQQ/DIA on one-minute bars, New York time. Deterministic rules; analysis only, no orders.

Every strategy trades only inside a session: it may enter only within its entry window and is flat by the
session's exit time. Each returns at most one trade per day as a dict (side, entry, stop, target, entered_t)
plus its outcome, found by simulate(). Market entries fill at the next bar's open; a bar that touches both the
stop and the target counts as a loss; an open trade is closed at the exit time's bar close.

  gap_fill        The regular session opens away from yesterday's close by a small gap: fade the open toward
                  yesterday's close (the target). Stop: the gap size × stop_gaps beyond the entry.
  opening_range   The first `minutes` of the session make a range; the first close outside it (by 11:00)
                  is entered at the next open. Stop: the range midpoint or its far side; target: R × risk.
"""
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
OPEN, CLOSE = dtime(9, 30), dtime(16, 0)


def ny(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone(NY)


def at_or_after(reg, clock):
    return next((i for i, b in enumerate(reg) if ny(b['t']).time() >= clock), None)


def simulate(reg, start, side, entry, stop, target, exit_clock):
    """Outcome of a trade filled at bar `start` (from its open): win, loss or time exit, in R before costs."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    for bar in reg[start:]:
        clock = ny(bar['t']).time()
        hit_stop = bar['h'] >= stop if side == 'short' else bar['l'] <= stop
        hit_target = bar['l'] <= target if side == 'short' else bar['h'] >= target
        if hit_stop:
            return dict(status='loss', r=-1.0, exited_t=bar['t'])
        if hit_target:
            return dict(status='win', r=round(abs(target - entry) / risk, 3), exited_t=bar['t'])
        if clock >= exit_clock:
            move = (entry - bar['c']) if side == 'short' else (bar['c'] - entry)
            return dict(status='time exit', r=round(move / risk, 3), exited_t=bar['t'])
    return dict(status='open', r=None, exited_t=None)


def gap_fill(reg, prior, min_gap=0.0005, max_gap=0.006, stop_gaps=1.0, confirm=False, exit_clock=dtime(11, 0)):
    """prior: the previous regular session's close."""
    if not reg or prior is None or ny(reg[0]['t']).time() != OPEN:
        return None
    open_price = reg[0]['o']
    gap = (open_price - prior) / prior
    if not min_gap <= abs(gap) <= max_gap:
        return None
    side = 'short' if gap > 0 else 'long'
    start = 0
    if confirm:  # wait for the first five minutes to close toward the fill
        five = at_or_after(reg, dtime(9, 35))
        if five is None or (reg[five - 1]['c'] >= open_price if side == 'short' else reg[five - 1]['c'] <= open_price):
            return None
        start = five
    entry = reg[start]['o']
    target = prior
    if (side == 'short' and entry <= target) or (side == 'long' and entry >= target):
        return None  # already filled before entry
    stop = entry + (1 if side == 'short' else -1) * stop_gaps * abs(open_price - prior)
    outcome = simulate(reg, start, side, entry, stop, target, exit_clock)
    return outcome and dict(side=side, entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2),
                            entered_t=reg[start]['t'], gap_pct=round(gap * 100, 3), **outcome)


def opening_range(reg, minutes=15, stop_at='mid', reward=1.5, last_entry=dtime(11, 0), exit_clock=dtime(12, 0)):
    if not reg or ny(reg[0]['t']).time() != OPEN:
        return None
    end = at_or_after(reg, dtime(9, 30 + minutes) if minutes < 30 else dtime(10, 0))
    if end is None or end < 3:
        return None
    high, low = max(b['h'] for b in reg[:end]), min(b['l'] for b in reg[:end])
    for i in range(end, len(reg) - 1):
        bar = reg[i]
        if ny(bar['t']).time() >= last_entry:
            return None
        side = 'long' if bar['c'] > high else 'short' if bar['c'] < low else None
        if not side:
            continue
        entry = reg[i + 1]['o']
        stop = (high + low) / 2 if stop_at == 'mid' else (low if side == 'long' else high)
        risk = (entry - stop) if side == 'long' else (stop - entry)
        if risk <= 0:
            return None
        target = entry + reward * risk if side == 'long' else entry - reward * risk
        outcome = simulate(reg, i + 1, side, entry, stop, target, exit_clock)
        return outcome and dict(side=side, entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2),
                                entered_t=reg[i + 1]['t'], range=[round(low, 2), round(high, 2)], **outcome)
    return None


def sessions(candles):
    """[(date, regular bars, previous regular close)] in order."""
    days, ordered = {}, []
    for bar in candles:
        day = ny(bar['t']).date()
        if day not in days:
            days[day] = []
            ordered.append(day)
        if OPEN <= ny(bar['t']).time() < CLOSE:
            days[day].append(dict(t=bar['t'], **{k: float(bar[k]) for k in 'ohlc'}))
    result, prior = [], None
    for day in ordered:
        reg = days[day]
        if reg:
            result.append((day, reg, prior))
            prior = reg[-1]['c']
    return result
