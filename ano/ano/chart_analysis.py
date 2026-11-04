"""Numeric indicator context from closed bars; never an entry recommendation."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .paper_analysis import analyze_candles
from .trading_risk import number

NY = ZoneInfo('America/New_York')


def describe_snapshot(report):
    at=datetime.fromtimestamp(report['last_closed_at'],timezone.utc).astimezone(NY)
    price=lambda value: 'unavailable' if value is None else f'{value:.2f}'
    freshness=('Historical/stale data — not a current setup.' if report['stale'] else
               'Closed-candle context — not a trade signal.')
    return (f'{report["symbol"]} · As of {at:%Y-%m-%d %H:%M} New York\n{freshness}\n\n'
            f'EMA 20: {price(report["ema20"])} · EMA 50: {price(report["ema50"])}\n'
            f'RSI 14: {report["rsi14"]:.1f} · ATR 14: {price(report["atr14"])}\n'
            f'RTH VWAP: {price(report["rth_vwap"])}\n'
            f'Opening range: {price(report["opening_range_low"])}–{price(report["opening_range_high"])}\n\n'
            f'EMA context: {report["context"]}. IEX feed; values can differ from TradingView. No orders placed.')


def ema(values, period):
    value = values[0]
    alpha = 2/(period+1)
    for current in values[1:]:
        value = alpha*current+(1-alpha)*value
    return value


def wilder(values, period):
    value = sum(values[:period])/period
    for current in values[period:]:
        value = ((period-1)*value+current)/period
    return value


def indicator_snapshot(candles, *, now):
    structure = analyze_candles(candles, now=now)
    bars = [row for row in candles if row['t']+60 <= now]
    if len(bars) < 50:
        raise ValueError('At least 50 closed one-minute bars are needed for indicator context.')
    close = [float(number(row['c'])) for row in bars]
    tr = [max(float(row['h'])-float(row['l']),
              abs(float(row['h'])-close[i-1]),abs(float(row['l'])-close[i-1]))
          if i else float(row['h'])-float(row['l']) for i,row in enumerate(bars)]
    changes = [close[i]-close[i-1] for i in range(1,len(close))]
    gain = wilder([max(change,0) for change in changes],14)
    loss = wilder([max(-change,0) for change in changes],14)
    rsi = 50 if gain == loss == 0 else 100 if loss == 0 else 100-100/(1+gain/loss)
    fast,slow = ema(close,20),ema(close,50)
    day = datetime.fromtimestamp(bars[-1]['t'],timezone.utc).astimezone(NY).date()
    session = [row for row in bars if (local:=datetime.fromtimestamp(row['t'],timezone.utc).astimezone(NY)).date()==day
               and (9,30) <= (local.hour,local.minute) < (16,0)]
    pv = volume = 0.0
    volume_valid = bool(session)
    for row in session:
        if 'v' not in row:
            volume_valid = False
            break
        v = number(row['v'])
        if v < 0 or v > number('1e15'):
            raise ValueError('Invalid candle volume.')
        volume += float(v)
        pv += (float(row['h'])+float(row['l'])+float(row['c']))/3*float(v)
    start = datetime.fromtimestamp(session[0]['t'],timezone.utc).astimezone(NY) if session else None
    complete_open = bool(start and (start.hour,start.minute)==(9,30))
    opening = [row for row in session if datetime.fromtimestamp(row['t'],timezone.utc).astimezone(NY).hour==9
               and datetime.fromtimestamp(row['t'],timezone.utc).astimezone(NY).minute < 45]
    opening_complete = complete_open and len(opening)==15 and opening[-1]['t']-opening[0]['t']==14*60
    return dict(execution_authorized=False, source='Alpaca IEX supplied bars', closed_bars=len(bars),
                last_closed_at=structure['last_closed_at'], stale=structure['stale'],
                missing_minutes=structure['missing_bars'], close=close[-1],
                ema20=round(fast,6),ema50=round(slow,6),rsi14=round(rsi,4),atr14=round(wilder(tr,14),6),
                rth_vwap=round(pv/volume,6) if volume_valid and volume>0 and complete_open else None,
                opening_range_high=max(float(r['h']) for r in opening) if opening_complete else None,
                opening_range_low=min(float(r['l']) for r in opening) if opening_complete else None,
                context='upward' if close[-1]>fast>slow else 'downward' if close[-1]<fast<slow else 'mixed',
                note='Indicator context only, not a trade signal. IEX is not consolidated data. Finite history and missing IEX bars can differ from TradingView. VWAP requires observed session start and volume; opening range requires 15 contiguous bars.')
