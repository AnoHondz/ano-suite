"""Ano SMC v1 research rules, closed candles only, no broker authority.

An order block is the last opposite candle in the ten bars preceding a close
through an already confirmed swing, with a displacement body >= 1.5 times
the preceding twenty-body mean. Its full wick range is the zone. A close
through its far edge invalidates it and confirms an opposite breaker. The
breaker is invalidated by a close through its own far edge. Retests can only
occur after confirmation. These are explicit research definitions, not a
claim that every interpretation of SMC is implemented.
"""
import math

from . import ict

VERSION = 'ano_smc_v1'


def closed_bars(candles, now, interval=60):
    rows, previous = [], None
    for raw in candles:
        t = raw['t']
        if type(t) is not int or t <= 0 or (previous is not None and t <= previous):
            raise ValueError('Candles must have unique ordered UTC timestamps.')
        previous = t
        values = {k: float(raw[k]) for k in 'ohlc'}
        if not all(math.isfinite(v) and 0 < v <= 1e12 for v in values.values()):
            raise ValueError('Invalid OHLC price.')
        if not values['l'] <= min(values['o'], values['c']) <= max(values['o'], values['c']) <= values['h']:
            raise ValueError('Invalid OHLC range.')
        if t + interval <= now:
            rows.append(dict(t=t, **values))
    return rows


def higher_bias(series, now):
    result = {}
    for name, interval in (('daily', 86400), ('4h', 14400), ('1h', 3600)):
        bars = closed_bars(series.get(name, []), now, interval)
        # The latest two completed bars define directional context; no vote
        # is inferred when the source is missing, stale, or contradictory.
        stale = not bars or now - bars[-1]['t'] - interval > 5*86400
        state = 'unavailable' if len(bars) < 2 or stale else (
            'bullish' if bars[-1]['c'] > bars[-2]['c'] else
            'bearish' if bars[-1]['c'] < bars[-2]['c'] else 'neutral')
        result[name] = dict(bias=state, last_closed_at=bars[-1]['t']+interval if bars else None,
                            closed_bars=len(bars), stale=stale)
    states = [r['bias'] for r in result.values()]
    result['alignment'] = (states[0] if all(s == states[0] for s in states)
                           and states[0] in ('bullish', 'bearish') else
                           'unavailable' if 'unavailable' in states else 'mixed')
    return result


def analyze(candles, *, now, higher=None, session_zone=None):
    bars = closed_bars(candles, now)
    from datetime import datetime
    def day(t):return datetime.fromtimestamp(t,session_zone).date() if session_zone else ict.ny(t).date()
    swings, blocks, breakers, events, gaps, sweeps = [], [], [], [], [], []
    trend = None
    for i, b in enumerate(bars):
        at = b['t']+60
        # Update existing zones before creating anything at this close.
        for block in blocks:
            if block['status'] == 'invalidated' or block['confirmed_at'] > b['t']:
                continue
            invalid = b['c'] < block['low'] if block['direction'] == 'bullish' else b['c'] > block['high']
            if invalid:
                block.update(status='invalidated', invalidated_at=at)
                breakers.append(dict(direction='bearish' if block['direction']=='bullish' else 'bullish',
                                     low=block['low'], high=block['high'], origin=block['origin'],
                                     confirmed_at=at, status='active'))
            elif b['l'] <= block['high'] and b['h'] >= block['low']:
                block.update(status='retested', retested_at=at)
        for zone in breakers:
            if zone['status'] == 'invalidated' or zone['confirmed_at'] > b['t']:
                continue
            invalid = b['c'] < zone['low'] if zone['direction']=='bullish' else b['c'] > zone['high']
            if invalid:
                zone.update(status='invalidated', invalidated_at=at)
            elif b['l'] <= zone['high'] and b['h'] >= zone['low']:
                zone.update(status='retested', retested_at=at)
        for gap in gaps:
            if gap.get('filled_at') or gap['confirmed_at'] > b['t']:
                continue
            if (b['l'] <= gap['low']) if gap['direction']=='bullish' else (b['h'] >= gap['high']):
                gap['filled_at'] = at
        for level in swings:
            if level.get('taken_at') or level['confirmed_at'] > b['t']:
                continue
            up = level['kind']=='high'
            cross = b['c'] > level['price'] if up else b['c'] < level['price']
            wick = b['h'] > level['price'] if up else b['l'] < level['price']
            if not wick:
                continue
            level['taken_at'] = at
            if not cross:
                sweeps.append(dict(kind=level['kind'], price=level['price'], confirmed_at=at))
                continue
            direction = 'bullish' if up else 'bearish'
            event = dict(kind='CHOCH' if trend and trend != direction else 'BOS',
                         direction=direction, price=level['price'], confirmed_at=at)
            events.append(event)
            trend = direction
            preceding = bars[max(0,i-20):i]
            mean = sum(abs(r['c']-r['o']) for r in preceding)/len(preceding) if preceding else 0
            contiguous = i > 0 and b['t']-bars[i-1]['t']==60
            if not contiguous or not mean or abs(b['c']-b['o']) < 1.5*mean:
                continue
            # Require a same-session, consecutive path from origin to break.
            for j in range(i-1,max(-1,i-11),-1):
                origin=bars[j]
                if bars[j+1]['t']-origin['t'] != 60 or day(origin['t'])!=day(b['t']):
                    break
                opposite=origin['c'] < origin['o'] if up else origin['c'] > origin['o']
                if opposite:
                    identity=(origin['t'],direction)
                    if not any((z['origin'],z['direction'])==identity for z in blocks):
                        blocks.append(dict(direction=direction,low=origin['l'],high=origin['h'],
                                           origin=origin['t'],confirmed_at=at,status='active',structure=event['kind']))
                    break
        # Swings become usable only AFTER their right-side candles close.
        n=2
        if i>=2*n and all(bars[k+1]['t']-bars[k]['t']==60 for k in range(i-2*n,i)):
            center=bars[i-n]; neighbors=bars[i-2*n:i-n]+bars[i-n+1:i+1]
            for kind,key,compare in (('high','h',max),('low','l',min)):
                extreme=compare(r[key] for r in neighbors)
                if center[key]>extreme if kind=='high' else center[key]<extreme:
                    swings.append(dict(kind=kind,price=center[key],t=center['t'],confirmed_at=at))
        if i>=2 and bars[i-2]['t']+120==b['t']:
            first=bars[i-2]
            if b['l']>first['h']:
                gaps.append(dict(direction='bullish',low=first['h'],high=b['l'],confirmed_at=at))
            if b['h']<first['l']:
                gaps.append(dict(direction='bearish',low=b['h'],high=first['l'],confirmed_at=at))
    high=next((s['price'] for s in reversed(swings) if s['kind']=='high'),None)
    low=next((s['price'] for s in reversed(swings) if s['kind']=='low'),None)
    midpoint=(high+low)/2 if high is not None and low is not None and high>low else None
    location=('unavailable' if midpoint is None or not bars else 'premium' if bars[-1]['c']>midpoint
              else 'discount' if bars[-1]['c']<midpoint else 'equilibrium')
    latest=bars[-1]['t']+60 if bars else None
    bias=higher_bias(higher or {},now)
    return dict(strategy=VERSION,status='ANALYSIS ONLY',execution_authorized=False,
                last_closed_at=latest,stale=latest is None or now-latest>180,
                structure=events,order_blocks=blocks,breaker_blocks=breakers,
                sweeps=sweeps,fair_value_gaps=gaps,location=location,range_midpoint=midpoint,
                higher_timeframes=bias,trend=trend or 'unconfirmed',
                note='Research definitions, IEX feed. Bias uses the last two completed closes per timeframe. '
                     'No synthetic missing bars, execution signal, fills or proven edge.')


def describe(report):
    lines=[f'{report.get("symbol", "")} · SMC v1 · ANALYSIS ONLY',
           'WAIT — stale or missing minute data.' if report['stale'] else 'Closed-candle research snapshot.',
           f'Structure: {report["trend"]}; location: {report["location"]}.']
    for tf in ('daily','4h','1h'):
        r=report['higher_timeframes'][tf]
        lines.append(f'{tf} bias: {r["bias"]}; last completed close: {r["last_closed_at"]}.')
    for label,key in (('Order block','order_blocks'),('Breaker block','breaker_blocks')):
        zones=[z for z in report[key] if z['status']!='invalidated']
        lines.append(f'{label}s: {len(zones)} active/retested.')
        for z in zones[-4:]:
            lines.append(f'  {z["direction"]} {z["low"]:.2f}–{z["high"]:.2f}: {z["status"]}; confirmed {z["confirmed_at"]}.')
    lines.append('No orders placed. '+report['note'])
    return '\n'.join(lines)
