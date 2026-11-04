"""Session strategy research on cached QQQ/DIA one-minute data (run tools/ict_research.py first to fetch it).

    .venv\\Scripts\\python.exe tools\\session_research.py --start 2026-03-02 --end 2026-10-27

Decided before looking at results:
  * Split by date: first 2/3 of trading days in-sample (IS), last 1/3 out-of-sample (OOS).
  * Costs: 2 cents per share round trip, charged in R on every trade.
  * Selection on IS only: among variants with at least 30 IS trades and a positive average net R, take the one
    with the highest win rate (the user asked for win rate; a losing variant never qualifies however often it wins).
  * OOS gate: at least 30 trades, average net R ≥ +0.05, profit factor ≥ 1.15, and the OOS win rate no more
    than 10 points below IS. Otherwise: fail, no paper execution.
"""
import argparse
from datetime import date, time as dtime
import itertools
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ano import session_strategies as ss  # noqa: E402

WORKSPACE = Path.home() / 'Documents' / 'AnoWorkspace'
DATA = WORKSPACE / 'ICT-data'
SYMBOLS = ('QQQ', 'DIA')
COST = 0.02
GATE = dict(trades=30, average_r=0.05, profit_factor=1.15, win_rate_drop=0.10)
GRIDS = {
    'gap fill': (ss.gap_fill, dict(max_gap=(0.003, 0.006, 0.01), stop_gaps=(0.75, 1.0, 1.5), confirm=(False, True))),
    'opening range breakout': (ss.opening_range, dict(minutes=(5, 15, 30), stop_at=('mid', 'far'), reward=(1.0, 1.5, 2.0))),
}


def load(symbol, start, end):
    rows = []
    for path in sorted(DATA.glob(f'{symbol}-*.json')):
        rows += json.loads(path.read_text(encoding='utf-8'))
    rows.sort(key=lambda r: r['t'])
    return [r for r in rows if start <= ss.ny(r['t']).date() <= end]


def stats(trades):
    if not trades:
        return dict(trades=0, wins=0, win_rate=None, total_r=0.0, average_r=None, profit_factor=None)
    total = sum(t['net_r'] for t in trades)
    gains = sum(t['net_r'] for t in trades if t['net_r'] > 0)
    losses = -sum(t['net_r'] for t in trades if t['net_r'] < 0)
    wins = sum(t['net_r'] > 0 for t in trades)
    return dict(trades=len(trades), wins=wins, win_rate=round(wins / len(trades), 3), total_r=round(total, 2),
                average_r=round(total / len(trades), 3), profit_factor=round(gains / losses, 2) if losses else None)


def run(days, rule, params):
    trades = []
    for day, reg, prior in days:
        trade = rule(reg, prior, **params) if rule is ss.gap_fill else rule(reg, **params)
        if trade and trade.get('r') is not None:
            trade['net_r'] = round(trade['r'] - COST / abs(trade['entry'] - trade['stop']), 3)
            trade['date'] = str(day)
            trades.append(trade)
    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', required=True, type=date.fromisoformat)
    parser.add_argument('--end', required=True, type=date.fromisoformat)
    args = parser.parse_args()
    sessions = {s: ss.sessions(load(s, args.start, args.end))[1:] for s in SYMBOLS}  # day 1 only gives a prior close
    trading_days = sorted({d for s in SYMBOLS for d, _r, _p in sessions[s]})
    cut = trading_days[len(trading_days) * 2 // 3]
    results = []
    for name, (rule, grid) in GRIDS.items():
        keys, values = zip(*grid.items())
        for combo in itertools.product(*values):
            params = dict(zip(keys, combo))
            row = dict(strategy=name, params=params)
            for part, keep in (('is', lambda d: d < cut), ('oos', lambda d: d >= cut)):
                per = {s: run([x for x in sessions[s] if keep(x[0])], rule, params) for s in SYMBOLS}
                row[part] = dict(both=stats(per['QQQ'] + per['DIA']), **{s: stats(per[s]) for s in SYMBOLS})
            results.append(row)
    eligible = [r for r in results if r['is']['both']['trades'] >= GATE['trades'] and (r['is']['both']['average_r'] or 0) > 0]
    best = max(eligible, key=lambda r: (r['is']['both']['win_rate'], r['is']['both']['average_r'])) if eligible else None
    passed = False
    if best:
        o, i = best['oos']['both'], best['is']['both']
        passed = (o['trades'] >= GATE['trades'] and (o['average_r'] or 0) >= GATE['average_r']
                  and (o['profit_factor'] or 0) >= GATE['profit_factor']
                  and (o['win_rate'] or 0) >= i['win_rate'] - GATE['win_rate_drop'])
    report = dict(start=str(args.start), end=str(args.end), split=str(cut), trading_days=len(trading_days), cost=COST,
                  gate=GATE, variants=len(results), eligible=len(eligible), best=best, passed=passed, all=results)
    path = WORKSPACE / f'Session-research-{args.start}-{args.end}.json'
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    for r in sorted(results, key=lambda r: (r['is']['both']['average_r'] or -9), reverse=True)[:12]:
        i, o = r['is']['both'], r['oos']['both']
        print(f"{r['strategy']:24} {json.dumps(r['params']):62} IS n={i['trades']:3} win={i['win_rate']} avg={i['average_r']:+} "
              f"| OOS n={o['trades']:3} win={o['win_rate']} avg={o['average_r']} pf={o['profit_factor']}")
    print(json.dumps(dict(passed=passed, eligible=f'{len(eligible)} of {len(results)}', best=best and dict(
        strategy=best['strategy'], params=best['params'], in_sample=best['is'], out_of_sample=best['oos']), report=str(path)), indent=2))


if __name__ == '__main__':
    main()
