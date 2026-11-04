"""Read-only forex check for one pair: the same native SMC / ICT rules the OANDA practice bot runs.

No orders, no account changes. The bot still applies news, spread, risk and session-budget checks
before trading, so a candidate here is not a promise of a trade.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from . import forex_v2
from .forex_sessions import SESSIONS, active, pricing

NAMES = {'EUR_USD': 'EUR/USD', 'GBP_USD': 'GBP/USD', 'USD_CAD': 'USD/CAD', 'USD_JPY': 'USD/JPY',
         'AUD_USD': 'AUD/USD', 'NZD_USD': 'NZD/USD', 'USD_CHF': 'USD/CHF', 'EUR_JPY': 'EUR/JPY'}
ALIASES = {'eurusd': 'EUR_USD', 'eur usd': 'EUR_USD', 'eur/usd': 'EUR_USD', 'euro': 'EUR_USD',
           'gbpusd': 'GBP_USD', 'gbp usd': 'GBP_USD', 'gbp/usd': 'GBP_USD', 'cable': 'GBP_USD', 'pound': 'GBP_USD',
           'usdcad': 'USD_CAD', 'usd cad': 'USD_CAD', 'usd/cad': 'USD_CAD', 'loonie': 'USD_CAD',
           'usdjpy': 'USD_JPY', 'usd jpy': 'USD_JPY', 'usd/jpy': 'USD_JPY', 'yen': 'USD_JPY',
           'audusd': 'AUD_USD', 'aud usd': 'AUD_USD', 'aud/usd': 'AUD_USD', 'aussie': 'AUD_USD',
           'nzdusd': 'NZD_USD', 'nzd usd': 'NZD_USD', 'nzd/usd': 'NZD_USD', 'kiwi': 'NZD_USD',
           'usdchf': 'USD_CHF', 'usd chf': 'USD_CHF', 'usd/chf': 'USD_CHF', 'swissy': 'USD_CHF', 'franc': 'USD_CHF',
           'eurjpy': 'EUR_JPY', 'eur jpy': 'EUR_JPY', 'eur/jpy': 'EUR_JPY'}


def pair_from(text):
    key = ' '.join(text.lower().split())
    return next((pair for alias, pair in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])) if alias in key), None)


def next_session(pair, now):
    """The next session that trades this pair, and when it opens (in New York time)."""
    best = None
    for name, spec in SESSIONS.items():
        if pair not in spec['pairs']:
            continue
        zone = ZoneInfo(spec['zone']);local = now.astimezone(zone)
        for days in range(0, 8):
            day = local.date().toordinal() + days
            start = datetime.combine(datetime.fromordinal(day).date(), spec['start'], zone)
            if start.weekday() < 5 and start > now:
                if best is None or start < best[1]:
                    best = (name, start)
                break
    return best


def analyze(pair, api=None, now=None):
    from .oanda_practice import PracticeAPI, PAIRS
    if pair not in PAIRS:
        raise ValueError('Supported pairs: ' + ', '.join(NAMES[p] for p in PAIRS) + '.')
    api = api or PracticeAPI()
    now = now or datetime.now(timezone.utc)
    rows = api.candles(pair, 'M1', 5000)
    higher = {name: api.candles(pair, g, 100) for name, g in (('daily', 'D'), ('4h', 'H4'), ('1h', 'H1'))}
    try:
        quote = api.quote(pair)
    except Exception:
        quote = None
    bias = forex_v2.bias_context(higher, now)
    sessions = {}
    for name in active(pair, now):
        report = forex_v2.scan(pair, rows, higher, now, name)
        c = forex_v2.session_context(name, now)
        levels = [p for p in forex_v2.known_levels(rows, c, now) if forex_v2.untaken(p, rows, now.timestamp())]
        sessions[name] = {'report': report, 'context': c, 'levels': levels}
    return {'pair': pair, 'now': now, 'quote': quote, 'last': rows[-1]['c'] if rows else None, 'bias': bias,
            'sessions': sessions, 'next': None if sessions else next_session(pair, now)}


def latest_reason(report, strategy):
    reasons = [d['reason'] for d in report.get('diagnostics', []) if d.get('strategy') in (strategy, 'all')]
    return reasons[-1] if reasons else 'no qualifying setup yet'


def describe(result):
    pair = result['pair'];tick = pricing(pair)[0]
    price = lambda v: str(type(tick)(str(v)).quantize(tick))
    q = result['quote']
    head = f'{NAMES[pair]} · ' + (f'bid {q["bid"]} / ask {q["ask"]}' if q else f'last {price(result["last"])}')
    states = result['bias']['timeframes']
    frames = ', '.join(f'{k} {v["state"]}' for k, v in states.items())
    direction = result['bias']['direction']
    lines = [head, f'Bias: {direction or "mixed"} ({frames})' + ('' if direction else ', so SMC waits')]
    if not result['sessions']:
        nxt = result['next']
        when = nxt[1].astimezone(ZoneInfo('America/New_York')).strftime('%a %I:%M %p ET') if nxt else 'unknown'
        lines.append(f'Session: none trading {NAMES[pair]} right now. Next: {nxt[0].replace("_", " ").title() if nxt else "?"} at {when}.')
    for name, s in result['sessions'].items():
        c, report = s['context'], s['report']
        lines.append(f'Session: {name.replace("_", " ").title()}' + (' · ICT kill zone' if c['ict_active'] else ''))
        plans = report.get('plans') or []
        for p in plans:
            lines.append(f'• {p["name"].upper()} {p["side"].upper()} candidate: entry {p["entry"]} ({p["entry_type"].lower()}), '
                         f'stop {p["stop"]}, target {p["target"]}')
        for strategy in ('ict', 'smc'):
            if not any(p['name'] == strategy for p in plans):
                lines.append(f'• {strategy.upper()}: {latest_reason(report, strategy)}')
        last = result['last']
        above = sorted((p for p in s['levels'] if p['price'] > last), key=lambda p: p['price'])[:2]
        below = sorted((p for p in s['levels'] if p['price'] <= last), key=lambda p: -p['price'])[:2]
        describe_level = lambda p: f'{price(p["price"])} ({p["source"]})'
        if above or below:
            lines.append('Liquidity: above ' + (', '.join(map(describe_level, above)) or 'none') +
                         ' · below ' + (', '.join(map(describe_level, below)) or 'none'))
    lines.append('Research only. The bot also checks news, spread and risk before any practice trade.')
    return '\n'.join(lines)
