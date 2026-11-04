"""Deterministic candle annotations. No feed, broker, or order authority.

Candles must be ordered, unique and aligned to the interval. Missing bars are allowed (IEX has no bar for a
minute without an IEX trade) and are reported, but a three-candle fair value gap is only taken from three
consecutive bars. Swing levels end either swept (a wick through, close back inside) or broken (a close
beyond); a broken level is no longer liquidity. A gap is filled once price trades back through all of it.
"""
from datetime import datetime, timezone
from decimal import Decimal
from .trading_risk import number


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%MZ')


def analyze_candles(candles, *, now, interval_seconds=60, swing_width=2):
    if type(now) is not int or now <= 0:
        raise ValueError('now must be UTC Unix seconds.')
    if type(interval_seconds) is not int or not 1 <= interval_seconds <= 86400:
        raise ValueError('Invalid candle interval.')
    if type(swing_width) is not int or not 1 <= swing_width <= 10:
        raise ValueError('Swing width must be 1–10.')
    if not isinstance(candles, list) or not 3 <= len(candles) <= 500:
        raise ValueError('Supply 3–500 ordered OHLC candles, with UTC opening timestamps.')
    closed, previous, forming, missing = [], None, 0, 0
    for row in candles:
        t = row['t']
        if type(t) is not int or t <= 0 or t > now:
            raise ValueError('Invalid or future candle timestamp.')
        if previous is not None and (t <= previous or (t - previous) % interval_seconds):
            raise ValueError('Candles must be ordered, unique, and aligned to the interval.')
        if previous is not None:
            missing += (t - previous) // interval_seconds - 1
        previous = t
        values = {key: number(row[key]) for key in ('o', 'h', 'l', 'c')}
        if any(v <= 0 or v > Decimal('1e12') for v in values.values()):
            raise ValueError('Prices must be positive finite values at most 1e12.')
        if not values['l'] <= min(values['o'], values['c']) <= max(values['o'], values['c']) <= values['h']:
            raise ValueError('Invalid OHLC range.')
        if t + interval_seconds <= now:
            closed.append(dict(t=t, **values))
        else:
            forming += 1
    if len(closed) < 3:
        raise ValueError('At least three closed candles are required.')
    swings, gaps, sweeps = [], [], []
    n = swing_width
    for i, bar in enumerate(closed):
        closes_at = bar['t'] + interval_seconds
        # Only levels confirmed before this candle opened can be swept or broken, once.
        for level in swings:
            if level['confirmed_at'] > bar['t'] or level.get('swept_at') or level.get('broken_at'):
                continue
            price = Decimal(level['price'])
            beyond = bar['c'] > price if level['kind'] == 'high' else bar['c'] < price
            wick = bar['h'] > price if level['kind'] == 'high' else bar['l'] < price
            if beyond:
                level['broken_at'] = closes_at
            elif wick:
                level['swept_at'] = closes_at
                sweeps.append(dict(kind=level['kind'], price=level['price'], level_time=level['t'],
                                   confirmed_at=closes_at, at=iso(closes_at)))
        for gap in gaps:
            if gap.get('filled_at') or gap['confirmed_at'] > bar['t']:
                continue
            low, high = Decimal(gap['low']), Decimal(gap['high'])
            if (bar['l'] <= low) if gap['direction'] == 'bullish' else (bar['h'] >= high):
                gap['filled_at'] = closes_at
        center = i - n
        if center >= n:
            mid = closed[center]
            neighbors = closed[center-n:center] + closed[center+1:i+1]
            for kind, key, compare in [('high', 'h', max), ('low', 'l', min)]:
                extreme = compare(other[key] for other in neighbors)
                is_swing = mid[key] > extreme if kind == 'high' else mid[key] < extreme
                if is_swing:
                    swings.append(dict(kind=kind, price=str(mid[key]), t=mid['t'], at=iso(mid['t']),
                                       confirmed_at=closes_at))
        # A fair value gap needs three back-to-back bars; a missing minute in between is no gap.
        if i >= 2 and closed[i-2]['t'] + 2 * interval_seconds == bar['t']:
            first = closed[i-2]
            if bar['l'] > first['h']:
                gaps.append(dict(direction='bullish', low=str(first['h']), high=str(bar['l']),
                                 confirmed_at=closes_at, at=iso(closes_at)))
            if bar['h'] < first['l']:
                gaps.append(dict(direction='bearish', low=str(bar['h']), high=str(first['l']),
                                 confirmed_at=closes_at, at=iso(closes_at)))
    last_close = closed[-1]['t'] + interval_seconds
    open_levels = [s for s in swings if not s.get('swept_at') and not s.get('broken_at')]
    return dict(status='ANALYSIS ONLY', execution_authorized=False, source_verified=False,
                liquidity_definition='Confirmed swing levels are liquidity hypotheses, not observed resting orders.',
                strategy_check='Full multi-timeframe setup, news, account, instrument specs and monitoring are not verified.',
                stale=now-last_close > 2*interval_seconds, last_closed_at=last_close,
                window=dict(first=iso(closed[0]['t']), last_close=iso(last_close)),
                last_price=str(closed[-1]['c']), missing_bars=missing,
                forming_candles_ignored=forming, closed_candles=len(closed),
                swings=swings, open_levels=open_levels, fair_value_gaps=gaps,
                open_fair_value_gaps=[g for g in gaps if not g.get('filled_at')], sweeps=sweeps)
