"""Local-only paper research tools; deliberately exposes no execution tool."""
import html
import json
import re
import time
import uuid

from ..paper_analysis import analyze_candles
from ..tools import ToolError


class PaperTrading:
    def __init__(self, workshop, clock=time.time, key_loader=None, feed_factory=None):
        self.workshop, self.clock = workshop, clock
        self.key_loader, self.feed_factory = key_loader, feed_factory

    def definitions(self):
        return [{'name':'smc_analysis',
                 'description':'Ano SMC v1 research: confirmed BOS/CHOCH, order blocks, breaker blocks, liquidity sweeps, FVGs, premium/discount and completed daily/4h/1h bias. QQQ/DIA IEX data; no orders or proven edge.',
                 'input_schema':{'type':'object','additionalProperties':False,'properties':{'symbol':{'type':'string','enum':['QQQ','DIA']}},'required':['symbol']}},
                {'name':'silver_bullet_setups',
                 'description':'Research Ano Silver Bullet v1 on closed QQQ/DIA IEX candles. NY 10–11/14–15 windows, named liquidity sweep, closed 15m direction, MSS and FVG, real 2R target. Analysis only; no orders or proven edge.',
                 'input_schema':{'type':'object','additionalProperties':False,'properties':{'symbol':{'type':'string','enum':['QQQ','DIA']}},'required':['symbol']}},
                {'name': 'analyze_paper_candles',
                 'description': 'Analyze supplied numeric OHLC candles and save an annotated SVG chart plus JSON evidence. '
                 'Detects confirmed swings, sweep candidates and 3-candle FVGs. No feed or order execution. '
                 'Do not invent candles or treat screenshot estimates as a verified feed.',
                 'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                     'symbol': {'type': 'string'}, 'source': {'type': 'string'},
                     'interval_seconds': {'type': 'integer'},
                     'candles': {'type': 'array', 'minItems': 3, 'maxItems': 500, 'items': {
                         'type': 'object', 'additionalProperties': False, 'properties': {
                             't': {'type': 'integer'}, **{k: {'type': 'number'} for k in ('o','h','l','c')}},
                         'required': ['t','o','h','l','c']}}},
                     'required': ['symbol','source','candles']}},
                {'name':'paper_connection_status', 'description':'Verify the configured Alpaca paper endpoint without exposing credentials or account identifiers. No orders.',
                 'input_schema':{'type':'object','additionalProperties':False,'properties':{}}},
                {'name':'analyze_paper_watchlist', 'description':'Fetch authorized Alpaca IEX one-minute ETF bars and save an annotated local chart. QQQ/DIA only. No orders or continuous monitoring. Missing/stale data does not authorize a trade.',
                 'input_schema':{'type':'object','additionalProperties':False,'properties':{'symbol':{'type':'string','enum':['QQQ','DIA']}},'required':['symbol']}},
                {'name':'ict_setups', 'description':'ICT 2022 model and AMD (power of three) for today on QQQ or DIA from Alpaca paper data: '
                 'liquidity sweeps in the kill zones, market structure shift with displacement, FVG entry, stop, target, '
                 '0–8 checklist grade and status. Saves an annotated chart. Analysis only; no orders.',
                 'input_schema':{'type':'object','additionalProperties':False,'properties':{'symbol':{'type':'string','enum':['QQQ','DIA']}},'required':['symbol']}},
                {'name':'ict_backtest', 'description':'Backtest the ICT 2022 model on the last 1–20 trading days of QQQ or DIA one-minute data: '
                 'win rate, R and profit factor by grade, and the best setups. Results exclude fees and slippage. No orders.',
                 'input_schema':{'type':'object','additionalProperties':False,'properties':{'symbol':{'type':'string','enum':['QQQ','DIA']},
                     'days':{'type':'integer','minimum':1,'maximum':20}},'required':['symbol']}},
                {'name':'ict_tradingview_indicator', 'description':'Save a TradingView Pine Script indicator and say how to add it: '
                 '"ano setups" (forex and stocks: Tokyo/London/New York sessions, kill zones, liquidity, sweeps, BOS/CHoCH/MSS, '
                 'FVGs, order blocks and labelled possible SMC/ICT entries with stop and target, matching the paper bots), '
                 'strategy "ict" (ICT 2022 / AMD: kill zones, accumulation range, PDH/PDL, sweeps, MSS, FVGs, entries) or '
                 '"gap fill" (the NY-morning gap fill with stop, target and result). Alerts included. No account access.',
                 'input_schema':{'type':'object','additionalProperties':False,'properties':{
                     'strategy':{'type':'string','enum':['ano setups','ict','gap fill','chart assistant']}}}},
                {'name':'forex_setups','description':'Read-only OANDA practice check of one forex pair with the exact native SMC/ICT rules the forex bot trades: '
                 'session and kill zone, H4/H1/daily bias, current SMC/ICT candidates or why there is none, and nearest untouched liquidity. No orders.',
                 'input_schema':{'type':'object','additionalProperties':False,'properties':{'pair':{'type':'string','enum':['EUR_USD','GBP_USD','USD_CAD','USD_JPY','AUD_USD','NZD_USD','USD_CHF','EUR_JPY']}},'required':['pair']}},
                {'name':'analyze_paper_indicators', 'description':'Calculate EMA20/50, Wilder RSI14/ATR14, RTH VWAP and opening-range context from actual closed QQQ/DIA IEX candles. Read-only, no orders. Reports missing volume and stale data.',
                 'input_schema':{'type':'object','additionalProperties':False,'properties':{'symbol':{'type':'string','enum':['QQQ','DIA']}},'required':['symbol']}}]

    def _feed(self):
        from ..credentials import load_paper_credentials
        from ..paper_feed import AlpacaPaperFeed
        self.workshop._check()
        try:
            keys = (self.key_loader or load_paper_credentials)()
        except Exception:
            raise ToolError('Could not read paper credentials from Windows Credential Manager.') from None
        if not keys:
            raise ToolError('Paper connection is not configured. Run .venv\\Scripts\\python.exe -m ano.configure paper locally; never paste credentials in chat.')
        return (self.feed_factory or AlpacaPaperFeed)(*keys)

    def run(self, name, arguments):
        if name == 'smc_analysis':
            from ..smc import analyze
            feed = self._feed()
            feed.verify()
            symbol = arguments.get('symbol')
            candles = feed.history(symbol, 2)['candles']
            # A missing higher-timeframe request is reported, never replaced
            # by minute-bar direction or an invented directional vote.
            try:
                higher = feed.higher_timeframes(symbol)
                error = ''
            except ToolError as exc:
                higher, error = {}, str(exc)
            report = analyze(candles, now=int(self.clock()), higher=higher)
            report.update(symbol=symbol, higher_timeframe_error=error)
            return json.dumps(report), 'SMC closed-candle research only; no orders'
        if name == 'silver_bullet_setups':
            from ..silver_bullet import analyze
            feed = self._feed()
            feed.verify()
            report = analyze(feed.history(arguments.get('symbol'), 2)['candles'], now=int(self.clock()))
            report['symbol'] = arguments.get('symbol')
            return json.dumps(report), 'Silver Bullet research only; no orders placed'
        if name == 'forex_setups':
            from ..forex_analysis import analyze as forex_analyze, describe as forex_describe
            try:
                return forex_describe(forex_analyze(arguments.get('pair'))), 'Read-only forex rule check; no orders'
            except ValueError as error:
                raise ToolError(str(error)) from None
        if name == 'analyze_paper_indicators':
            from ..chart_analysis import indicator_snapshot
            symbol=arguments.get('symbol')
            feed=self._feed()
            feed.verify()
            data=feed.candles(symbol)
            try:
                report=indicator_snapshot(data['candles'],now=int(self.clock()))
            except (ValueError,KeyError,TypeError) as error:
                raise ToolError('Indicator context unavailable: '+str(error)) from None
            report['symbol']=symbol
            return json.dumps(report), 'Calculated closed-candle indicator context; no orders'
        if name == 'paper_connection_status':
            return json.dumps(self._feed().verify()), 'Checked paper connection only; execution permissions not changed or inferred'
        if name == 'analyze_paper_watchlist':
            symbol = arguments.get('symbol')
            feed = self._feed()
            feed.verify()
            data = feed.candles(symbol)
            source = (f'Alpaca IEX 1-minute bars; latest session; {data.get("missing_minutes", 0)} minutes without an IEX bar; '
                      f'{data["omitted_before_last_gap"]} earlier bars excluded (previous session)')
            return self.analyze(symbol,source,data['candles'],feed_verified=True)
        if name == 'ict_setups':
            return self.ict_setups(arguments.get('symbol'))
        if name == 'ict_backtest':
            return self.ict_backtest(arguments.get('symbol'), arguments.get('days', 10))
        if name == 'ict_tradingview_indicator':
            return self.ict_indicator(arguments.get('strategy', 'ict'))
        if name != 'analyze_paper_candles':
            raise ToolError('No direct model-callable order tool is exposed. Use the fixed paper strategy controls.')
        arguments.pop('feed_verified', None)  # a model cannot mark its own candles as a verified feed
        return self.analyze(**arguments)

    # -------------------------------------------------- ICT 2022 model (ano/ict.py)
    @staticmethod
    def _public(setup):
        return {k: v for k, v in setup.items() if not k.startswith('_')}

    def ict_setups(self, symbol):
        from .. import ict
        feed = self._feed()
        feed.verify()
        history = feed.history(symbol, 2)
        now=int(self.clock())
        history['candles']=[b for b in history['candles'] if b['t']+60 <= now]
        days = ict.analyze(history['candles'])
        if not days:
            raise ToolError('No trading day in the returned data.')
        today = days[-1]
        ranked = sorted(today['setups'], key=lambda s: (s['score'], s['rr']), reverse=True)
        chart = self._ict_chart(symbol, history['candles'], today)
        report = dict(symbol=symbol, date=today['date'], source='Alpaca IEX 1-minute bars', amd=today['amd'],
                      setups=[self._public(s) for s in ranked], chart=chart, status='ANALYSIS ONLY',
                      execution_authorized=False,
                      last_closed_at=history['candles'][-1]['t']+60,
                      stale=now-history['candles'][-1]['t']-60>180,
                      note='Grades come from an ICT checklist; check them with ict_backtest before trusting a grade.')
        return json.dumps(report), f'Found {len(ranked)} ICT 2022 setup(s) on {symbol} today; no orders placed'

    def ict_backtest(self, symbol, days):
        from .. import ict
        if type(days) is not int or not 1 <= days <= 20:
            raise ToolError('Choose 1–20 trading days.')
        feed = self._feed()
        feed.verify()
        history = feed.history(symbol, days)
        results = ict.analyze(history['candles'])[1:]  # the first day only supplies PDH/PDL
        summary = dict(symbol=symbol, **ict.summarize(results))
        summary['ledger'] = self._update_ledger(symbol, results)
        evidence = self.workshop._path(f'ICT-backtest-{symbol}-{uuid.uuid4().hex[:8]}.json', suffixes=('.json',))
        self.workshop._check()
        with evidence.open('x', encoding='utf-8') as f:
            json.dump(dict(summary=summary, days=[dict(date=d['date'], amd=d['amd'],
                                                       setups=[self._public(s) for s in d['setups']]) for d in results]),
                      f, indent=2)
        summary['evidence'] = str(evidence)
        return json.dumps(summary), f'Backtested the ICT 2022 model on {symbol} over {len(results)} day(s); no orders placed'

    def _update_ledger(self, symbol, results):
        """One record per trading day, however many backtests cover it, so the growing sample never counts a day
        twice. Records from another engine version are dropped (their rules differed), and a day whose session
        has not closed yet is not stored."""
        from .. import ict
        path = self.workshop._path(f'ICT-ledger-{symbol}.json', suffixes=('.json',))
        ledger = dict(engine=ict.ENGINE_VERSION, symbol=symbol, days={})
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
                if saved.get('engine') == ict.ENGINE_VERSION:
                    ledger = saved
            except ValueError:
                pass
        now = ict.ny(int(self.clock()))
        for day in results:
            if day['date'] == now.date().isoformat() and now.time() < ict.CLOSE:
                continue
            ledger['days'][day['date']] = day
        self.workshop._check()
        path.write_text(json.dumps(ledger), encoding='utf-8')
        total = ict.summarize([ledger['days'][d] for d in sorted(ledger['days'])])
        return dict(path=str(path), unique_days=len(ledger['days']), traded=total['traded'],
                    overall=total['overall'], by_grade=total['by_grade'])

    def ict_indicator(self, strategy='ict'):
        from pathlib import Path
        if strategy == 'ano setups':
            source=Path(__file__).resolve().parents[1]/'assets'/'ano_setups.pine'
            target=self.workshop._path('Ano Setups SMC-ICT (TradingView).pine',suffixes=('.pine',))
            text=source.read_text(encoding='utf-8')
            if not target.exists() or target.read_text(encoding='utf-8')!=text:  # Ano's own copy: keep it current
                self.workshop._check()
                target.write_text(text,encoding='utf-8')
            return json.dumps(dict(path=str(target),how_to_add='TradingView > Pine Editor > paste the file > Save > Add to chart. '
                'Use a 1-minute chart of EURUSD, GBPUSD, USDCAD, USDJPY, AUDUSD, NZDUSD, USDCHF or EURJPY (OANDA) for the forex bot, or QQQ / DIA for the stock bot. '
                'Possible entries are labelled with entry, stop, target and R; add alerts on "Ano ICT/SMC long/short". '
                'The bots still apply news, spread and risk checks, so not every marking becomes a trade. No broker connection.')), 'Saved the Ano Setups TradingView indicator'
        if strategy == 'chart assistant':
            source=Path(__file__).resolve().parents[1]/'assets'/'chart_assistant.pine'
            target=self.workshop._path('Ano Chart Assistant (TradingView).pine',suffixes=('.pine',))
            text=source.read_text(encoding='utf-8')
            if not target.exists() or target.read_text(encoding='utf-8')!=text:  # Ano's own copy: keep it current
                target.write_text(text,encoding='utf-8')
            return json.dumps(dict(path=str(target),how_to_add='TradingView > Pine Editor > paste > Save privately > Add to chart. Use a 1-minute QQQ/DIA chart for FVGs. Analysis only; no broker connection.')), 'Saved Ano Chart Assistant indicator'
        asset, name = (('gap_fill.pine', 'Gap fill NY AM (TradingView).pine') if strategy == 'gap fill'
                       else ('ict_2022.pine', 'ICT 2022 model (TradingView).pine'))
        source = Path(__file__).resolve().parent.parent / 'assets' / asset
        target = self.workshop._path(name, suffixes=('.pine',))
        text = source.read_text(encoding='utf-8')
        if not target.exists() or target.read_text(encoding='utf-8') != text:  # Ano's own copy: keep it current
            self.workshop._check()
            target.write_text(text, encoding='utf-8')
        return json.dumps(dict(path=str(target), how_to_add='In TradingView open Pine Editor, paste the file, click '
                               '"Add to chart", then set alerts on "ICT 2022 long/short" if wanted. Use a 1–5 minute chart '
                               'of QQQ, DIA, NQ or ES; times are New York.')), 'Saved the ICT 2022 TradingView indicator'

    def _ict_chart(self, symbol, candles, today):
        from .. import ict
        day = [b for b in candles if ict.ny(b['t']).date().isoformat() == today['date']
               and ict.OPEN <= ict.ny(b['t']).time() < ict.CLOSE]
        if len(day) < 3:
            return None
        rows = [dict(t=b['t'], **{k: float(b[k]) for k in 'ohlc'}) for b in day]
        low, high = min(r['l'] for r in rows), max(r['h'] for r in rows)
        best = sorted(today['setups'], key=lambda s: (s['score'], s['rr']), reverse=True)[:3]
        for setup in best:
            low, high = min(low, setup['stop'], setup['target']), max(high, setup['stop'], setup['target'])
        span = max(high - low, high * .001)
        y = lambda p: 400 - (float(p) - low) / span * 320
        x = lambda i: 65 + i * 780 / max(1, len(rows) - 1)
        index_of = lambda seconds: next((i for i, r in enumerate(rows) if r['t'] >= seconds), len(rows) - 1)
        svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="520" viewBox="0 0 1000 520" font-family="M PLUS Rounded 1c, Arial, sans-serif">',
               '<rect width="1000" height="520" fill="#101826"/>',
               f'<text x="30" y="30" fill="white" font-size="18">{html.escape(symbol)} — ICT 2022 MODEL · {today["date"]}</text>',
               '<text x="30" y="52" fill="#fbbf24" font-size="12">Alpaca paper data (IEX). Analysis only: no trade signal, no orders.</text>']
        for name, start, end in ict.KILLZONES:  # kill zones shaded
            inside = [i for i, r in enumerate(rows) if start <= ict.ny(r['t']).time() < end]
            if inside:
                svg.append(f'<rect x="{x(inside[0])}" y="70" width="{max(1, x(inside[-1]) - x(inside[0]))}" height="340" fill="#a78bfa" opacity=".06"/>'
                           f'<text x="{x(inside[0]) + 4}" y="82" fill="#a78bfa" font-size="10">{name}</text>')
        amd = today.get('amd') or {}
        if amd.get('accumulation'):
            for edge in ('high', 'low'):
                price = amd['accumulation'][edge]
                svg.append(f'<path d="M65 {y(price)} H850" stroke="#a78bfa" stroke-dasharray="2 5"/>'
                           f'<text x="860" y="{y(price) + 4}" fill="#a78bfa" font-size="10">{amd["accumulation"]["source"]} {edge}</text>')
        for i, r in enumerate(rows):
            color = '#34d399' if r['c'] >= r['o'] else '#fb7185'
            svg += [f'<path d="M{x(i)} {y(r["h"])} V{y(r["l"])}" stroke="{color}" stroke-width=".8"/>',
                    f'<rect x="{x(i) - 1}" y="{min(y(r["o"]), y(r["c"]))}" width="2" height="{max(1, abs(y(r["o"]) - y(r["c"])))}" fill="{color}"/>']
        for setup in best:
            start = index_of(setup['_fvg_t'])
            fvg_low, fvg_high = setup['fvg']
            svg.append(f'<rect x="{x(start)}" y="{y(fvg_high)}" width="{max(1, 850 - x(start))}" height="{max(1, y(fvg_low) - y(fvg_high))}" fill="#38bdf8" opacity=".18"/>')
            for price, color, label in ((setup['entry'], '#e2e8f0', 'entry'), (setup['stop'], '#fb7185', 'stop'),
                                        (setup['target'], '#34d399', 'target')):
                svg.append(f'<path d="M{x(start)} {y(price)} H850" stroke="{color}" stroke-width="1"/>'
                           f'<text x="860" y="{y(price) + 4}" fill="{color}" font-size="10">{label} {price}</text>')
            svg.append(f'<text x="{x(start)}" y="{y(setup["extreme"]) + (-8 if setup["direction"] == "short" else 16)}" fill="#f472b6" font-size="11">'
                       f'{"▼" if setup["direction"] == "short" else "▲"} {setup["grade"]} {html.escape(setup["liquidity"])} · {html.escape(setup["status"])}</text>')
        manipulation = (amd.get('manipulation') or {}).get('direction')
        svg += [f'<text x="30" y="445" fill="white" font-size="12">AMD: accumulation {amd.get("accumulation", {}).get("source", "?")} → '
                f'manipulation {manipulation or "none yet"} → distribution {(amd.get("distribution") or {}).get("direction", "not yet")}</text>',
                '<text x="30" y="470" fill="#cbd5e1" font-size="12">Purple: kill zones and accumulation range. Blue: entry FVG. '
                'White / red / green: entry, stop, target. Up to 3 best setups shown.</text>',
                '<text x="30" y="495" fill="#94a3b8" font-size="11">ICT concepts as rules; grades are a checklist, not a forecast. Backtest before trusting them.</text>',
                '</svg>']
        path = self.workshop._path(f'ICT-{symbol}-{today["date"]}-{uuid.uuid4().hex[:6]}.svg', suffixes=('.svg',))
        self.workshop._check()
        with path.open('x', encoding='utf-8') as f:
            f.write('\n'.join(svg))
        return str(path)

    def analyze(self, symbol, source, candles, interval_seconds=60, feed_verified=False):
        """feed_verified is set only by the Alpaca watchlist path, never from model-supplied arguments."""
        self.workshop._check()
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Za-z0-9_:!.\-/]{1,70}', symbol):
            raise ToolError('Use an exact provider symbol.')
        if not isinstance(source, str) or not 1 <= len(source) <= 240:
            raise ToolError('Record the candle source, without credentials.')
        try:
            now = int(self.clock())
            report = analyze_candles(candles, now=now, interval_seconds=interval_seconds)
        except (ValueError, KeyError, TypeError) as error:
            raise ToolError('Invalid candle data: ' + str(error)) from None
        report.update(symbol=symbol, source=source, interval_seconds=interval_seconds, source_verified=bool(feed_verified))
        stem = 'Paper-analysis-' + uuid.uuid4().hex[:12]
        chart = self.workshop._path(stem+'.svg', suffixes=('.svg',))
        evidence = self.workshop._path(stem+'.json', suffixes=('.json',))
        closed = [r for r in candles if r['t']+interval_seconds <= now][-100:]
        low, high = min(float(r['l']) for r in closed), max(float(r['h']) for r in closed)
        span = max(high-low, abs(high)*.001)
        y = lambda p: 380-(float(p)-low)/span*300
        x = lambda i: 65+i*780/max(1,len(closed)-1)
        index_at = lambda seconds: next((i for i,r in enumerate(closed) if r['t']+interval_seconds >= seconds), None)
        heading = ('Alpaca paper data (IEX). Analysis only: no trade signal, no orders.' if feed_verified else
                   'Supplied candles; feed unverified. Analysis only: no trade signal, no orders.')
        svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="500" viewBox="0 0 1000 500" font-family="M PLUS Rounded 1c, Arial, sans-serif">',
               '<rect width="1000" height="500" fill="#101826"/>',
               f'<text x="30" y="30" fill="white" font-size="18">{html.escape(symbol)} — PAPER ANALYSIS</text>',
               f'<text x="30" y="54" fill="#fbbf24" font-size="12">{heading}</text>']
        for i, r in enumerate(closed):
            color = '#34d399' if float(r['c']) >= float(r['o']) else '#fb7185'
            svg += [f'<path d="M{x(i)} {y(r["h"])} V{y(r["l"])}" stroke="{color}"/>',
                    f'<rect x="{x(i)-2}" y="{min(y(r["o"]),y(r["c"]))}" width="4" height="{max(1,abs(y(r["o"])-y(r["c"])))}" fill="{color}"/>']
        # Only liquidity that is still there: open levels in amber; swept levels faded up to where they were taken.
        labeled = []
        for level in reversed([s for s in report['swings'] if not s.get('broken_at')][-10:]):
            start = index_at(level['confirmed_at'])
            if start is None:
                continue
            end = index_at(level['swept_at']) if level.get('swept_at') else None
            stop = x(end) if end is not None else 850
            faded = ' opacity=".35"' if end is not None else ''
            svg.append(f'<path d="M{x(start)} {y(level["price"])} H{stop}" stroke="#fbbf24" stroke-dasharray="4 4"{faded}/>')
            if end is None and all(abs(y(level['price'])-position) >= 14 for position in labeled):
                svg.append(f'<text x="860" y="{y(level["price"])+4}" fill="#fbbf24" font-size="11">{level["kind"]} {level["price"]}</text>')
                labeled.append(y(level['price']))
        for sweep in report['sweeps'][-8:]:
            index = index_at(sweep['confirmed_at'])
            if index is not None:
                tip = y(sweep['price']) + (-10 if sweep['kind'] == 'high' else 10)
                svg.append(f'<text x="{x(index)-4}" y="{tip}" fill="#f472b6" font-size="12">{"▼" if sweep["kind"] == "high" else "▲"}</text>')
        for gap in report['open_fair_value_gaps'][-5:]:
            index = index_at(gap['confirmed_at'])
            if index is not None:
                svg.append(f'<rect x="{x(index)}" y="{y(gap["high"])}" width="{max(1,850-x(index))}" height="{max(1,y(gap["low"])-y(gap["high"]))}" fill="#38bdf8" opacity=".18"/>')
        last = report['last_price']
        svg += [f'<path d="M65 {y(last)} H850" stroke="#e2e8f0" stroke-width=".6" opacity=".6"/>',
                f'<text x="860" y="{y(last)+4}" fill="#e2e8f0" font-size="11">last {last}</text>',
                f'<text x="30" y="430" fill="white" font-size="12">UTC {report["window"]["first"]} → {report["window"]["last_close"]} · '
                f'{report["closed_candles"]} bars, {report["missing_bars"]} missing · {"STALE" if report["stale"] else "current"}</text>',
                '<text x="30" y="455" fill="#cbd5e1" font-size="12">Amber dashed: open swing levels (faded = swept). ▼▲ sweeps. Blue: unfilled FVGs. '
                'Broken levels and filled gaps are hidden.</text>',
                '<text x="30" y="478" fill="#94a3b8" font-size="11">Swing levels are liquidity hypotheses, not order-book depth.</text>', '</svg>']
        self.workshop._check()
        with chart.open('x', encoding='utf-8') as f:
            f.write('\n'.join(svg))
        with evidence.open('x', encoding='utf-8') as f:
            json.dump(report, f, indent=2)
        return json.dumps(dict(report=report, chart=str(chart), evidence=str(evidence))), 'Saved paper-analysis chart and numeric evidence; no orders placed'
