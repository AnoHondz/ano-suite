"""ICT 2022 model research: months of Alpaca IEX data, rule variants, and an honest out-of-sample test.

    .venv\\Scripts\\python.exe tools\\ict_research.py --start 2026-03-02 --end 2026-10-28

Method (decided before looking at results):
  * Data: one-minute IEX bars for QQQ and DIA, cached by month in AnoWorkspace\\ICT-data (fetched once).
  * Split by date: the first 2/3 of trading days are in-sample (IS), the last 1/3 out-of-sample (OOS).
  * Variants: engine rules (swing size, minimum reward:risk, displacement, entry point) × filters
    (all setups, named liquidity only, AMD aligned, Silver Bullet, NY AM only, first trade of the day).
  * Costs: 2 cents per share round trip (spread and slippage), charged in R on every trade.
  * The single best variant on IS (most total net R with at least 30 IS trades) is then run once on OOS.
  * Pass gate on OOS, both symbols together: at least 30 trades, average net R ≥ +0.10, profit factor ≥ 1.2,
    and net R positive on each symbol. Anything else is a fail: no paper execution.
No orders, no AI: plain computation. Writes a JSON report next to the data.
"""
import argparse
from datetime import date, datetime, time as dtime, timedelta, timezone
import itertools
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ano import ict  # noqa: E402

WORKSPACE = Path.home() / 'Documents' / 'AnoWorkspace'
DATA = WORKSPACE / 'ICT-data'
SYMBOLS = ('QQQ', 'DIA')
COST_PER_SHARE = 0.02
ENGINE_GRID = dict(SWING=(3, 5), MIN_RR=(1.5, 2.0, 3.0), DISPLACEMENT=(1.5, 2.0), ENTRY=('ce', 'edge'))
FILTERS = {
    'all': lambda s: True,
    'named liquidity': lambda s: s['checks']['named_liquidity'],
    'AMD aligned': lambda s: s['amd_aligned'],
    'Silver Bullet': lambda s: s['checks']['silver_bullet'],
    'NY AM only': lambda s: s['killzone'] == 'NY AM',
    'first of day': None,  # handled in trades()
}
GATE = dict(trades=30, average_r=0.10, profit_factor=1.2)


def month_ranges(start, end):
    current = date(start.year, start.month, 1)
    while current <= end:
        following = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
        yield current, min(following, end + timedelta(days=1))
        current = following


def load(symbol, start, end, feed_factory):
    DATA.mkdir(parents=True, exist_ok=True)
    rows, feed = [], None
    today = datetime.now(timezone.utc).date()
    for first, stop in month_ranges(start, end):
        cache = DATA / f'{symbol}-{first:%Y-%m}.json'
        complete = stop <= today.replace(day=1)  # only finished months are cached
        if cache.exists() and complete:
            rows += json.loads(cache.read_text(encoding='utf-8'))
            continue
        feed = feed or feed_factory()
        bars = feed.between(symbol, datetime.combine(first, dtime(0), timezone.utc),
                            datetime.combine(stop, dtime(0), timezone.utc))
        if complete:
            cache.write_text(json.dumps(bars), encoding='utf-8')
        rows += bars
        print(f'  fetched {symbol} {first:%Y-%m}: {len(bars):,} bars', flush=True)
    low = datetime.combine(start, dtime(0), timezone.utc).timestamp()
    high = datetime.combine(end + timedelta(days=1), dtime(0), timezone.utc).timestamp()
    return [r for r in rows if low <= r['t'] < high]


def trades(days, keep):
    """Net-of-cost closed trades, one position at a time, after a filter."""
    if keep is None:  # first setup of each day that actually traded
        chosen = []
        for day in days:
            filled = [s for s in day['setups'] if s.get('r') is not None]
            if filled:
                chosen.append(min(filled, key=lambda s: s['_entered_t']))
    else:
        chosen = [s for day in days for s in day['setups'] if keep(s)]
    taken, _skipped = ict.one_at_a_time(chosen)
    result = []
    for s in taken:
        risk = abs(s['stop'] - s['entry'])
        result.append(dict(s, net_r=round(s['r'] - COST_PER_SHARE / risk, 3)))
    return result


def stats(rows):
    if not rows:
        return dict(trades=0, wins=0, win_rate=None, total_r=0.0, average_r=None, profit_factor=None)
    gains = sum(r['net_r'] for r in rows if r['net_r'] > 0)
    losses = -sum(r['net_r'] for r in rows if r['net_r'] < 0)
    total = sum(r['net_r'] for r in rows)
    return dict(trades=len(rows), wins=sum(r['status'] == 'win' for r in rows),
                win_rate=round(sum(r['status'] == 'win' for r in rows) / len(rows), 3),
                total_r=round(total, 2), average_r=round(total / len(rows), 3),
                profit_factor=round(gains / losses, 2) if losses else None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', required=True, type=date.fromisoformat)
    parser.add_argument('--end', required=True, type=date.fromisoformat)
    args = parser.parse_args()

    from ano.credentials import load_paper_credentials
    from ano.paper_feed import AlpacaPaperFeed
    factory = lambda: AlpacaPaperFeed(*load_paper_credentials())
    candles = {symbol: load(symbol, args.start, args.end, factory) for symbol in SYMBOLS}
    all_days = sorted({ict.ny(r['t']).date() for rows in candles.values() for r in rows})
    trading_days = [d for d in all_days if d.weekday() < 5]
    cut = trading_days[len(trading_days) * 2 // 3]
    print(f'{len(trading_days)} trading days; in-sample before {cut}, out-of-sample from {cut}', flush=True)

    names, values = zip(*ENGINE_GRID.items())
    results = []
    for combo in itertools.product(*values):
        rules = dict(zip(names, combo))
        with ict.rules(**rules):
            analyzed = {symbol: ict.analyze(candles[symbol])[1:] for symbol in SYMBOLS}
        for filter_name, keep in FILTERS.items():
            row = dict(rules=rules, filter=filter_name)
            for part, test in (('is', lambda d: date.fromisoformat(d['date']) < cut),
                               ('oos', lambda d: date.fromisoformat(d['date']) >= cut)):
                per_symbol = {symbol: trades([d for d in analyzed[symbol] if test(d)], keep) for symbol in SYMBOLS}
                row[part] = dict(both=stats(per_symbol['QQQ'] + per_symbol['DIA']),
                                 **{symbol: stats(per_symbol[symbol]) for symbol in SYMBOLS})
            results.append(row)
        print(f'  rules {rules}: done', flush=True)

    eligible = [r for r in results if r['is']['both']['trades'] >= 30]
    best = max(eligible, key=lambda r: r['is']['both']['total_r']) if eligible else None
    oos = best and best['oos']
    passed = bool(best) and oos['both']['trades'] >= GATE['trades'] and \
        (oos['both']['average_r'] or 0) >= GATE['average_r'] and (oos['both']['profit_factor'] or 0) >= GATE['profit_factor'] \
        and all(oos[s]['total_r'] > 0 for s in SYMBOLS)
    baseline = next(r for r in results if r['filter'] == 'all' and r['rules'] == dict(SWING=3, MIN_RR=2.0, DISPLACEMENT=1.5, ENTRY='ce'))
    report = dict(engine=ict.ENGINE_VERSION, start=str(args.start), end=str(args.end), trading_days=len(trading_days),
                  split=str(cut), cost_per_share=COST_PER_SHARE, gate=GATE, variants_tested=len(results),
                  baseline=baseline, best_in_sample=best, passed=passed,
                  top_in_sample=sorted(eligible, key=lambda r: r['is']['both']['total_r'], reverse=True)[:10],
                  positive_in_sample=sum(r['is']['both']['total_r'] > 0 for r in eligible), eligible=len(eligible))
    path = WORKSPACE / f'ICT-research-{args.start}-{args.end}.json'
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(dict(passed=passed, best=best and dict(rules=best['rules'], filter=best['filter'],
                                                           in_sample=best['is'], out_of_sample=best['oos']),
                          baseline=dict(in_sample=baseline['is']['both'], out_of_sample=baseline['oos']['both']),
                          positive_in_sample=f"{report['positive_in_sample']} of {len(eligible)} eligible variants",
                          report=str(path)), indent=2))


if __name__ == '__main__':
    main()
