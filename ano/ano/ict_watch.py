"""Live ICT 2022 setup watcher for QQQ and DIA: plain code, no AI, no cost, alerts only by default.

"watch ict setups" starts it. During market days it reads today's one-minute Alpaca IEX bars every minute from
9:25 to 15:10 New York time, runs ano.ict on them, and reports each new setup once (grade at or above the
chosen minimum) when it forms and again if it fills, wins, loses or exits. Reading is one request per symbol a
minute (Alpaca allows 200).

Paper orders are not wired in here. If a strategy passes tools/ict_research.py's out-of-sample gate, orders
would go through paper_orders.PaperOrderTransport.submit_once, which refuses while PAPER_EXECUTION_HOLD is set:
a code constant only the user lifts. No command, setting or model tool here places or enables an order.

Commands arrive on the reply worker thread; settings are saved by poll() on the main thread (Memory's
database belongs to it).
"""
from datetime import datetime, time as dtime, timedelta, timezone
import queue
import re
import threading
from copy import deepcopy

from . import ict
from . import session_strategies as ss
from .strategy_signals import STRATEGIES,candidates

WATCH_FROM, WATCH_UNTIL = dtime(9, 25), dtime(15, 10)
# The gap fill variant chosen on in-sample data by tools/session_research.py (2026-03-02 – 2026-10-27).
# It narrowly failed the out-of-sample gate (61% wins, +0.04R net), so it is watched forward, never traded.
GAP_RULES = dict(max_gap=0.006, stop_gaps=1.5, confirm=True)
INTERVAL = 60
SYMBOLS = ('QQQ', 'DIA')


class IctWatch:
    def __init__(self, feed_factory, memory_get, memory_set, clock=lambda: datetime.now(timezone.utc)):
        self.feed_factory, self.clock = feed_factory, clock
        self._get, self._set = memory_get, memory_set
        self.events = queue.Queue()  # (text) for the main thread to post
        self.seen = dict(memory_get('trading_alert_seen', None) or {})
        self._seen_saved = dict(self.seen) # durable deduplication across restarts
        self.previous = {}           # symbol -> (date, (high, low)) of the prior session
        self._stop = threading.Event()
        self._thread = None
        self.last_error = ''
        self._unsaved = False
        settings = memory_get('ict_watch', None) or {}  # read here, on the main thread
        self.symbols = tuple(s for s in settings.get('symbols', ()) if s in SYMBOLS)
        self.min_grade = settings.get('min_grade', 'B')
        self.gap_symbols = tuple(s for s in settings.get('gap_symbols', ()) if s in SYMBOLS)
        self.research = {name: tuple(s for s in symbols if s in SYMBOLS)
                         for name, symbols in settings.get('research', {}).items()
                         if name in ('smc','silver bullet','turtle soup','amd','opening range','technicals')}
        self.snapshots = memory_get('trading_model_snapshots', None) or {}
        self._snapshot_lock = threading.RLock()
        self._snapshots_dirty = False
        self._last_health = None
        self._higher_cache = {}
        self._history_cache = {}
        self.executor = None  # paper_execution.GapFillExecutor, set by app.py
        self._paper_saved = settings.get('gap_paper', False)
        self._strategy_saved=tuple(name for name in settings.get('strategy_paper',[]) if name in STRATEGIES)
        self._paper_symbols = tuple(s for s in settings.get('paper_symbols', self.gap_symbols) if s in SYMBOLS)

    # ---------------------------------------------------- commands (LocalRoutines, worker thread)
    def command(self, text):
        key = ' '.join(text.lower().split()).rstrip('.!?')
        # Complete user commands only; generic model launches never enable orders.
        prefix = (r'(?:(?:hey )?ano[, ]+)?(?:please )?'
                  r'(?:(?:can|could|would|will) you (?:please )?|'
                  r'(?:tell|ask) (?:chatgpt|ano) to )?')
        if re.fullmatch(prefix+r'(?:paper strategy|strategy paper|paper execution) (?:status|statud|stauts)',key):
            if self.executor and hasattr(self.executor,'execution_status'):return self.executor.execution_status()
            return 'Multi-strategy execution status is unavailable; use trading bot readiness for the installed runner.'
        if key in ('which other strategies do you have','which trades can you take','are you gonna take more trades','keep taking more trades when opportunities come'):
            status=self.executor.execution_status() if self.executor and hasattr(self.executor,'execution_status') else 'Executor status unavailable.'
            return 'Available paper strategies: gap fill, ICT, SMC, Silver Bullet, Turtle Soup, AMD, opening range and technicals.\n'+status
        alias=re.fullmatch(prefix+r'(?:start|enable|activate) paper trading (?:the )?(ict(?: 2022)?|smc|silver bullet|turtle soup|amd|opening range|technicals)',key)
        if alias:return self.command('start '+alias[1].replace(' 2022','')+' paper strategy')
        if re.fullmatch(prefix+r'enable ict 2022 model',key):return self.command('start ict paper strategy')
        named=re.fullmatch(prefix+r'(?:start|enable|activate) (?:the )?'
            r'(ict|smc|smart money concepts|silver bullet|turtle soup|amd|opening range|technicals) paper strategy',key)
        if named:
            name={'smart money concepts':'smc'}.get(named[1],named[1])
            if not self.executor or not hasattr(self.executor,'enable_strategy'):
                return 'The paper strategy executor is unavailable; check trading bot readiness.'
            previous=getattr(self.executor,'strategy_enabled',())
            already=isinstance(previous,(tuple,list)) and name in previous
            self.executor.enable_strategy(name)
            if name=='ict':self.symbols=SYMBOLS
            else:self.research[name]=SYMBOLS
            self._unsaved=True;self.start()
            return (f'{name.upper()} paper execution '+('was already ENABLED' if already else 'is now ENABLED')+
                    ' on QQQ/DIA. Other strategy permissions are preserved. '
                    'Fresh setups and shared risk checks determine orders; this command itself submits no order. '
                    'Live trading remains disabled. Type paper strategy status for actual permissions and recent decisions.')
        if re.fullmatch(prefix+r'(?:allow (?:them|all strategies) to place (?:paper )?trades|'
                r'(?:start|enable|activate) (?:all paper strategies|paper trading for all strategies|strategy paper trading))',key):
            if not self.executor or not hasattr(self.executor,'process_candidates'):
                return 'Multi-strategy paper execution is not loaded. Check paper credentials and reload the updated Ano runner.'
            self.symbols=SYMBOLS;self.gap_symbols=SYMBOLS
            self.research={name:SYMBOLS for name in STRATEGIES if name!='ict'}
            self.executor.resume_entries();self.executor.strategy_enabled=tuple(STRATEGIES)
            self.executor.enabled=True;self.executor.symbols=SYMBOLS;self._unsaved=True;self.start()
            return ('Experimental PAPER execution enabled for ICT, SMC retests, Silver Bullet, Turtle Soup intraday, '
                    'AMD-aligned ICT, opening range, EMA-crossover technicals and gap fill on QQQ/DIA. '
                    'One shared position at a time, no practical daily entry cap (40/day runaway guard), 0.25% planned risk/entry, '
                    'two consecutive losses and 10% daily-loss stops. Non-gap plans require 2:1 after the configured cost allowance. '
                    'Fresh quotes, closed bars, news/calendar/account and bracket checks remain mandatory. '
                    'No immediate order was submitted by this command. Live trading remains disabled. '
                    'Say stop paper trading to disable all entries and manage existing positions to close.')
        if re.fullmatch(prefix+r'(?:enable|activate|start|turn on) (?:the )?(?:live trading|live trading mode|real money trading)',key):
            paper='ENABLED' if self.executor and self.executor.enabled else 'OFF'
            modes=getattr(self.executor,'strategy_enabled',()) if self.executor else ()
            modes=modes if isinstance(modes,(tuple,list)) else ()
            return ('Live execution is not implemented in this installation. The configured executor uses the Alpaca '
                    'PAPER endpoint and simulated funds. It supports a paper broker connection and qualifying gap-fill '
                    f'paper orders; current gap-fill paper execution is {paper}. This command changed no execution setting. '
                    f'Additional enabled paper adapters: {", ".join(modes) or "none"}. '
                    'and submitted no real-money order. Type "trading bot readiness" for a fresh paper broker check.')
        if re.fullmatch(prefix+r'(?:what is|what\'s|whats|explain)(?: this| the)? (?:gap fill(?:er)?|gap_fill_v1_2)(?: strategy| bot)?',key):
            return ('Gap fill is an experimental strategy inside Ano, not a separate application or .exe. '
                    'If QQQ/DIA opens above the previous session close, it looks for a confirmed move down toward that close; '
                    'an opening gap down looks for a move up. Example: previous close $100, open $100.30: '
                    'the strategy may short after the first five closed minutes if its rules pass, targeting $100. '
                    'It decides only at 09:35–09:37 New York time, checks data/news/calendar/account/risk, '
                    'and uses a stop and target with an 11:00 exit. Gaps can continue away from the close. '
                    'This is paper experimentation, not a proven profitable model. Type trading model status for actual runner state.')
        if re.fullmatch(prefix+r'(?:(?:let|have) (?:the )?best (?:research |reach |trading )?model (?:make|place) a (?:paper )?trade|'
                        r'(?:use|trade with|activate) (?:the )?best (?:research |reach |trading )?model|best (?:research |trading )?model(?: status)?)',key):
            from .trading_selection import evidence_status
            return evidence_status()
        if re.fullmatch(prefix+r'(?:activate|enable) (?:(?:my|the) )?(?:paper )?trading ?bot',key) or re.fullmatch(
                prefix+r'(?:start|activate|run|enable) (?:the )?gap_fill_v1_2(?: paper bot| bot)?',key):
            if not self.gap_symbols:self.gap_symbols=SYMBOLS
            return self.command('start paper trading the gap fill')
        target = r'(?:(?:the |my )?(?:trading model|trading bot|trading models)|all (?:trading models|trading strategies))'
        launch = re.fullmatch(prefix + r'(?:start|run|open|launch) ' + target +
                              r'(?: (?:for me|now|please|from ano))?', key)
        if launch:
            self.symbols = SYMBOLS
            self.gap_symbols = SYMBOLS
            self.min_grade = 'B'
            self.research = {name: SYMBOLS for name in ('smc','silver bullet','turtle soup','amd','opening range','technicals')}
            self._unsaved = True
            self.start()
            paper_state=('ENABLED on '+', '.join(self.executor.symbols)) if self.executor and self.executor.enabled else 'OFF'
            modes=getattr(self.executor,'strategy_enabled',()) if self.executor else ()
            modes=modes if isinstance(modes,(tuple,list)) else ()
            return ('Watching ICT 2022 setups and forward-testing gap fills on QQQ and DIA, every minute '
                    'from 9:25 to 15:10 New York time on weekdays. Alerts only for these research scans.\n\n'
                    f'Existing gap-fill paper execution: {paper_state}. This command leaves that setting unchanged. '
                    f'Additional paper adapters enabled: {", ".join(modes) or "none"}. '
                    'Live trading: disabled. Paper entries still require their session, data, news and risk checks.\n\n'
                    'Also monitoring SMC (BOS/CHOCH, order blocks, breakers, daily/4h/1h bias), '
                    'Silver Bullet, Turtle Soup (intraday variant), AMD / Power of Three, opening-range research and technical indicators. '
                    'Each strategy reports missing/stale data separately. Type "trading model status" '
                    'for the latest evidence. Research/alerts only; no new order authorization.')
        if re.fullmatch(prefix + r'(?:stop|pause) ' + target, key):
            self.symbols = ()
            self.gap_symbols = ()
            self.research = {}
            self._unsaved = True
            return ('Stopped trading model analysis and alerts for all strategies. '
                    'Paper execution has separate controls; say "stop paper trading" to stop it.')
        if (re.fullmatch(target + r' status', key) or
                re.fullmatch(prefix + r'(?:check|show) ' + target + r'(?: status)?', key)):
            ict_state = ' and '.join(self.symbols) if self.symbols else 'off'
            gap_state = ' and '.join(self.gap_symbols) if self.gap_symbols else 'off'
            worker = bool(self._thread and self._thread.is_alive())
            paper_state=('ENABLED on '+', '.join(self.executor.symbols)) if self.executor and self.executor.enabled else 'OFF'
            modes=getattr(self.executor,'strategy_enabled',()) if self.executor else ()
            modes=modes if isinstance(modes,(tuple,list)) else ()
            note = f' Last problem: {self.last_error}' if self.last_error else ''
            details = '\n'.join(f'{name}: {" and ".join(symbols)}' for name,symbols in self.research.items())
            with self._snapshot_lock:
                snapshots = deepcopy(self.snapshots)
            evidence = '\n'.join(f'{symbol} / {name}: {item["summary"]} (checked {item["at"]})'
                                 for symbol,items in snapshots.items() for name,item in items.items())
            return (f'Trading model: ICT alerts {ict_state}; gap-fill forward test {gap_state}. '
                    f'Gap-fill paper execution: {paper_state}. Live trading: disabled. '
                    f'Other paper adapters: {", ".join(modes) or "none"}. '
                    f'Watcher thread running: {worker}. Analysis window: 9:25–15:10 New York time '
                    f'on weekdays. No orders enabled by this command.{note}\n{details}\n'
                    'Latest observations (timestamps may be historical):\n' + (evidence or 'No verified scan yet.'))
        individual = re.fullmatch(prefix + r'(?:run|start) (?:the )?(ict(?: 2022)?|gap fills?)'
                                  r'(?: model)?(?: on (qqq(?: and dia)?|dia(?: and qqq)?))?',key)
        if individual:
            name='ict setups' if individual[1].startswith('ict') else 'gap fills'
            return self.command('watch '+name+' on '+(individual[2] or 'qqq and dia'))
        stopped = re.fullmatch(prefix + r'(?:stop|pause) (?:watching )?(?:the )?'
                               r'(smc|smart money concepts|silver bullet|turtle soup|amd|power of three|opening range|technicals)'
                               r'(?: (?:model|setups))?',key)
        if stopped:
            name={'smart money concepts':'smc','power of three':'amd'}.get(stopped[1],stopped[1])
            self.research={k:v for k,v in self.research.items() if k!=name}
            self._unsaved=True
            return f'Stopped {name} research monitoring. Other strategies keep their existing settings.'
        research = re.fullmatch(prefix + r'(?:watch|monitor|run|start) (?:the )?'
                                r'(smc|smart money concepts|silver bullet|turtle soup|amd|power of three|opening range|technicals)'
                                r'(?: (?:model|setups))?(?: on (qqq(?: and dia)?|dia(?: and qqq)?))?', key)
        if research:
            name={'smart money concepts':'smc','power of three':'amd'}.get(research[1],research[1])
            self.research={**self.research,name:tuple(s.upper() for s in re.findall(r'qqq|dia',research[2] or 'qqq and dia'))}
            self._unsaved=True
            self.start()
            return f'Watching {name} on {" and ".join(self.research[name])}. This changes research scanning only. '+self._execution_note(name)
        match = re.fullmatch(r'(?:watch|monitor|alert me (?:to|about)) (?:for )?(?:the )?ict(?: 2022)? setups?'
                             r'(?: on (?P<symbols>qqq(?: and dia)?|dia(?: and qqq)?))?(?: grade (?P<grade>[abc]) or better)?', key)
        if match:
            symbols = tuple(s.upper() for s in re.findall(r'qqq|dia', match['symbols'] or 'qqq and dia'))
            self.min_grade = (match['grade'] or 'b').upper()
            self.symbols = symbols
            self._unsaved = True
            self.start()
            return (f'Watching {" and ".join(symbols)} for ICT 2022 setups, grade {self.min_grade} or better, every minute '
                    'from 9:25 to 15:10 New York time on market days. Plain code: no AI or credits. Alerts from this scanner; '+
                    self._execution_note('ict')+' Say “stop watching ict setups” to stop research; stop paper trading controls execution.')
        gap = re.fullmatch(r'(?:watch|monitor|forward test) (?:the )?gap fills?(?: setups?)?(?: on (?P<symbols>qqq(?: and dia)?|dia(?: and qqq)?))?', key)
        if gap:
            self.gap_symbols = tuple(s.upper() for s in re.findall(r'qqq|dia', gap['symbols'] or 'qqq and dia'))
            if self.executor:
                self.executor.symbols = self.gap_symbols
            self._unsaved = True
            self.start()
            execution=('Independent gap-fill paper execution is ENABLED; broker receipts determine actual orders/fills. '
                       if self.executor and self.executor.enabled else 'Alerts only; no orders. ')
            return (f'Forward-testing the gap fill on {" and ".join(self.gap_symbols)}: each market morning Ano reports the '
                    f'signal (gaps up to {GAP_RULES["max_gap"] * 100:.1f}%, confirmed after 5 minutes, stop {GAP_RULES["stop_gaps"]}× '
                    'the gap, target yesterday\'s close, flat by 11:00) and its bar-simulated result. '+execution+
                    'Say “stop watching gap fills” to stop.')
        if key in ('start paper trading the gap fill', 'start paper trading gap fill', 'paper trade the gap fill'):
            if not self.executor:
                return 'Paper trading is not available: Alpaca paper keys are not set up.'
            if not self.gap_symbols:
                return 'First say “watch gap fills on qqq and dia”, then start paper trading.'
            self.executor.symbols = self.gap_symbols
            self.executor.enabled = True
            if hasattr(self.executor,'resume_entries'):self.executor.resume_entries()
            self._unsaved = True
            self.start()
            return (f'Gap-fill paper execution is ENABLED on {" and ".join(self.gap_symbols)} inside Ano; no separate .exe is needed. '
                    'Rule sheet v1.2: Alpaca PAPER account only, decisions at 09:35–09:37 New York time, flat by 11:00, '
                    '0.25% risk budget per trade, news and calendar checks. No immediate order was submitted by this command. '
                    'Every decision goes in Trading\\JOURNAL.md. Ano must be running at 9:35 and 11:00. '
                    'Say “stop paper trading” to stop and close what Ano opened.')
        if key in ('stop paper trading', 'stop paper trading the gap fill', 'stop all paper trading'):
            if not self.executor:
                return 'Paper trading was not on.'
            self._unsaved = True
            try:
                return self.executor.stop_everything()
            except Exception as error:
                return (f'Paper trading is off (no new entries), but closing failed: {error}. Check the Alpaca paper '
                        'account in TradingView now.')
        if key in ('stop watching gap fills', 'stop the gap fill watch', 'stop gap fill alerts'):
            self.gap_symbols = ()
            if self.executor:
                self.executor.symbols = ()  # no new entries; anything open is still managed to the close
            self._unsaved = True
            return 'Stopped the gap fill forward test (no new paper entries; anything open is still managed).'
        if key in ('stop watching ict setups', 'stop the ict watch', 'stop ict alerts', 'stop watching ict'):
            self.symbols = ()
            self._unsaved = True
            return 'Stopped watching for ICT setups.'
        if key in ('ict watch status', 'is the ict watch on', 'ict status'):
            if not self.symbols:
                return 'The ICT watch is off. Say “watch ict setups on qqq and dia” to start it.'
            note = f' Last problem: {self.last_error}' if self.last_error else ''
            return f'Watching {" and ".join(self.symbols)} for grade {self.min_grade}+ ICT 2022 setups. Paper orders on hold.{note}'
        return None

    # ---------------------------------------------------- the loop (its own thread)
    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name='ano-ict-watch', daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()

    def _run(self):
        # Alerts only. Paper orders are handled by paper_execution.ExecutionWorker on its own thread.
        while not self._stop.wait(0 if not self.seen and self._due() else INTERVAL):
            if not (self.symbols or self.gap_symbols or any(self.research.values())) or not self._due():
                continue
            try:
                self.check()
            except Exception as error:  # a failed read is retried next minute, never acted on
                self.last_error = str(error)[:160]

    def _due(self):
        now = ict.ny(int(self.clock().timestamp()))
        return now.weekday() < 5 and WATCH_FROM <= now.time() <= WATCH_UNTIL

    def check(self):
        now = self.clock()
        self.last_error = ''
        today = ict.ny(int(now.timestamp())).date()
        feed = self.feed_factory()
        wanted = set(self.symbols) | set(self.gap_symbols)
        for symbols in self.research.values():
            wanted.update(symbols)
        for symbol in sorted(wanted):
            try:
                self._check_symbol(feed, symbol, now, today)
            except Exception as error:
                self.last_error = f'{symbol}: {str(error)[:140]}'
                self._snapshot(symbol, 'feed', 'WAIT — '+str(error)[:140], now)

    def _snapshot(self, symbol, name, summary, now):
        with self._snapshot_lock:
            self.snapshots.setdefault(symbol,{})[name]={'summary':summary,'at':now.isoformat()}
            self._snapshots_dirty=True

    def _check_symbol(self, feed, symbol, now, today):
        previous = self.previous.get(symbol)
        if not previous or previous[0] != today:
            previous = (today, self._prior_session(feed, symbol, today, now))
            self.previous[symbol] = previous
        start = datetime.combine(today, dtime(4, 0), ict.NY).astimezone(timezone.utc)
        bars = feed.between(symbol, start, now, pages=2)
        bars = [b for b in bars if b['t']+60 <= now.timestamp()]
        self._session_bars = {**getattr(self, '_session_bars', {}), symbol: bars}  # with volume, for the trend filter
        stale = not bars or now.timestamp()-bars[-1]['t']-60 > 180
        if stale:
            for name in (['ICT'] if symbol in self.symbols else []) + (['gap fill'] if symbol in self.gap_symbols else []) + [k for k,v in self.research.items() if symbol in v]:
                self._snapshot(symbol,name,'WAIT — stale or missing minute data',now)
            if not bars:
                return
        self._snapshot(symbol,'feed','WAIT — stale minute data' if stale else 'Fresh closed IEX minute data',now)
        prior = previous[1]
        day = None
        if symbol in self.symbols:
            day = ict.analyze_day(bars, prior and prior[:2])
            self._snapshot(symbol,'ICT',('WAIT — stale data; historical outcomes only' if stale else f'{len(day["setups"])} confirmed setup(s)'),now)
            for setup in day['setups']:
                identity=f'{symbol}|{setup["direction"]}|{setup["fvg_at"]}'
                if not stale or identity in self.seen:
                    self._report(symbol, setup)
        closed = [b for b in bars if b['t'] + 60 <= now.timestamp()]
        regular = [dict(t=b['t'], **{k: float(b[k]) for k in 'ohlc'}) for b in closed
                   if ict.OPEN <= ict.ny(b['t']).time() < ict.CLOSE]
        if not stale and day:self._offer('ict',symbol,regular,now,day)
        if symbol in self.gap_symbols and prior:
            trade = ss.gap_fill(regular, prior[2], **GAP_RULES)
            self._snapshot(symbol,'gap fill','WAIT — stale data; historical outcomes only' if stale else trade['status'] if trade else 'WAIT — no qualifying gap',now)
            if trade and (not stale or f'{symbol}|gap|{today}' in self.seen):
                self._report_gap(symbol, today, trade)
        elif symbol in self.gap_symbols:
            self._snapshot(symbol,'gap fill','WAIT — previous session unavailable',now)
        if stale:
            return
        for name,symbols in self.research.items():
            if symbol not in symbols:
                continue
            try:
                self._research_scan(name,symbol,feed,bars,regular,prior,day,now,today)
            except Exception as error:
                self._snapshot(symbol,name,'WAIT — '+str(error)[:160],now)

    def _research_scan(self,name,symbol,feed,bars,regular,prior,day,now,today):
        if name == 'smc':
            from .smc import analyze
            cached=self._higher_cache.get(symbol)
            if not cached or (now-cached[0]).total_seconds()>=900:
                higher=feed.higher_timeframes(symbol,now=now)
                self._higher_cache[symbol]=(now,higher)
            else:
                higher=cached[1]
            report=analyze(bars,now=int(now.timestamp()),higher=higher)
            self._offer(name,symbol,regular,now,report)
            active=[z for z in report['order_blocks'] if z['status']!='invalidated']
            breakers=[z for z in report['breaker_blocks'] if z['status']!='invalidated']
            summary=(f'{report["trend"]}; {report["location"]}; {len(active)} order blocks; '
                     f'{len(breakers)} breakers; '+', '.join(
                         f'{tf} {report["higher_timeframes"][tf]["bias"]}' for tf in ('daily','4h','1h')))
            self._snapshot(symbol,name,summary,now)
            # Only new confirmed state changes generate alerts, not every tick.
            latest=report['structure'][-1:] + report['order_blocks'][-1:] + report['breaker_blocks'][-1:]
            for item in latest:
                identity=f'{symbol}|smc|{item.get("kind", "zone")}|{item["confirmed_at"]}|{item["direction"]}'
                state=item.get('status','confirmed')
                if self.seen.get(identity)!=state:
                    self.seen[identity]=state
                    self.events.put(f'SMC research · {symbol}: {summary}. '+self._execution_note(name))
        elif name == 'turtle soup':
            from .turtle_soup import analyze
            report=analyze(regular,int(now.timestamp()))
            self._offer(name,symbol,regular,now,report)
            self._snapshot(symbol,name,report['summary'],now)
            for setup in report['setups']:
                key=f'{symbol}|turtle-soup|{setup["confirmed_at"]}|{setup["direction"]}'
                if key not in self.seen:
                    self.seen[key]='confirmed'
                    self.events.put(f'Turtle Soup intraday research · {symbol} {setup["direction"]}: '
                        f'entry reference {setup["entry"]}, stop reference {setup["stop"]}, target reference {setup["target"]}. '
                        '20-minute variant. '+self._execution_note(name))
        elif name == 'silver bullet':
            from .silver_bullet import analyze
            cache=self._history_cache.get(symbol)
            if not cache or cache[0]!=today:
                past=feed.history(symbol,2,now=now)['candles']
                past=[b for b in past if ict.ny(b['t']).date()<today]
                self._history_cache[symbol]=(today,past)
            else:
                past=cache[1]
            report=analyze(past+bars,now=int(now.timestamp()))
            current=next((d for d in report['days'] if d['date']==str(today)),None)
            self._offer(name,symbol,regular,now,{**(current or {}),'stale':report['stale']})
            setups=current['setups'] if current else []
            self._snapshot(symbol,name,f'{len(setups)} confirmed setup(s); NY windows 10–11 / 14–15',now)
            for setup in setups:
                key=f'{symbol}|silver|{setup["direction"]}|{setup["fvg_at"]}'
                if self.seen.get(key)!=setup['status']:
                    self.seen[key]=setup['status']
                    self.events.put(f'Silver Bullet · {symbol} {setup["direction"]}: entry {setup["entry"]}, '
                                    f'stop {setup["stop"]}, target {setup["target"]}; {setup["status"]}. '
                                    'Bar simulation. '+self._execution_note(name))
        elif name == 'amd':
            day=day or ict.analyze_day(bars,prior and prior[:2])
            self._offer(name,symbol,regular,now,day)
            amd=day.get('amd') or {}
            phase='distribution' if amd.get('distribution') else 'manipulation' if amd.get('manipulation') else 'accumulation / waiting'
            self._snapshot(symbol,name,phase,now)
            key=f'{symbol}|amd|{today}'
            if amd.get('manipulation') and self.seen.get(key)!=phase:
                self.seen[key]=phase
                self.events.put(f'AMD / Power of Three · {symbol}: {phase}. '+self._execution_note(name))
        elif name == 'opening range':
            self._offer(name,symbol,regular,now,{})
            opening=[b for b in regular if ict.ny(b['t']).time()<dtime(10,0)]
            complete=(len(opening)==30 and ict.ny(opening[0]['t']).time()==dtime(9,30)
                      and all(b['t']-a['t']==60 for a,b in zip(regular,regular[1:])))
            trade=ss.opening_range(regular, minutes=30) if complete else None
            self._snapshot(symbol,name,trade['status'] if trade else 'WAIT — no breakout or complete 30-minute opening range',now)
            if trade:
                key=f'{symbol}|opening-range|{today}'
                if self.seen.get(key)!=trade['status']:
                    self.seen[key]=trade['status']
                    self.events.put(f'Opening range · {symbol} {trade["side"]}: {trade["status"]}. Bar simulation. '+self._execution_note(name))
        elif name == 'technicals':
            self._offer(name,symbol,regular,now,{})
            from .chart_analysis import indicator_snapshot
            report=indicator_snapshot(bars[-500:],now=int(now.timestamp()))
            self._snapshot(symbol,name,f'{report["context"]}; RSI {report["rsi14"]:.1f}; EMA20 {report["ema20"]:.2f}; EMA50 {report["ema50"]:.2f}',now)

    def _offer(self,name,symbol,regular,now,report):
        modes=getattr(self.executor,'strategy_enabled',()) if self.executor else ()
        if not isinstance(modes,(tuple,list)) or name not in modes:return
        from .strategy_signals import trend_allows
        found=candidates(name,symbol,regular,now,report)
        plans=[p for p in found if trend_allows(p,getattr(self,'_session_bars',{}).get(symbol,[]),now)]
        self._snapshot(symbol,name+' paper',f'{len(plans)} fresh complete plan(s) offered to shared risk checks'
                       +(f' ({len(found)-len(plans)} against the day\'s trend skipped)' if len(found)>len(plans) else '')+'; no fill assumed',now)
        for plan in plans:self.executor.offer(plan)

    def _execution_note(self,name):
        modes=getattr(self.executor,'strategy_enabled',()) if self.executor else ()
        return ('Independent paper execution is enabled; only broker receipts establish orders/fills.'
                if isinstance(modes,(tuple,list)) and name in modes else 'Research only; no order was placed by this scanner.')

    @staticmethod
    def _prior_session(feed, symbol, today, now):
        """(high, low, close) of the previous regular session."""
        for back in range(1, 6):  # skip weekends and holidays
            day = today - timedelta(days=back)
            start = datetime.combine(day, ict.OPEN, ict.NY).astimezone(timezone.utc)
            end = datetime.combine(day, ict.CLOSE, ict.NY).astimezone(timezone.utc)
            bars = feed.between(symbol, start, end, pages=2)
            if bars:
                return (max(float(b['h']) for b in bars), min(float(b['l']) for b in bars), float(bars[-1]['c']))
        return None

    def _report_gap(self, symbol, today, trade):
        key = f'{symbol}|gap|{today}'
        status = trade['status']
        if self.seen.get(key) == status:
            return
        first = key not in self.seen
        self.seen[key] = status
        side = 'SHORT' if trade['side'] == 'short' else 'LONG'
        if first:
            text = (f'Gap fill {side} on {symbol} (forward test) · gap {trade["gap_pct"]:+.2f}%\n'
                    f'Entry {trade["entry"]} at 9:35 · stop {trade["stop"]} · target {trade["target"]} (yesterday\'s close) · '
                    f'flat by 11:00 · now: {status}.\nBar simulation; broker orders/fills are reported separately.')
        else:
            result = f' ({trade["r"]:+}R before costs)' if trade.get('r') is not None else ''
            text = f'Gap fill {side} on {symbol}: {status}{result}. Bar simulation; broker results separate.'
        self.events.put(text)

    def _report(self, symbol, setup):
        if 'ABC'.index(setup['grade']) > 'ABC'.index(self.min_grade):
            return
        key = f'{symbol}|{setup["direction"]}|{setup["fvg_at"]}'
        status = setup['status']
        if self.seen.get(key) == status:
            return
        first = key not in self.seen
        self.seen[key] = status
        side = 'SHORT' if setup['direction'] == 'short' else 'LONG'
        if first:
            text = (f'ICT 2022 {side} setup on {symbol} · grade {setup["grade"]} ({setup["score"]}/7)\n'
                    f'{setup["liquidity"]} {setup["liquidity_price"]} taken at {setup["swept_at"][-8:]}, structure shifted '
                    f'at {setup["mss_at"][-8:]}.\nEntry {setup["entry"]} · stop {setup["stop"]} · target {setup["target"]} '
                    f'({setup["target_liquidity"]}, {setup["rr"]}R) · now: {status}.\n'
                    'Scanner simulation; '+self._execution_note('ict'))
        else:
            result = f' ({setup["r"]:+}R before costs)' if setup.get('r') is not None else ''
            text = f'ICT 2022 {side} on {symbol} from {setup["fvg_at"][-8:]}: {status}{result}. Bar simulation; broker results separate.'
        self.events.put(text)

    def poll(self):
        """Alert texts gathered since the last call; also saves changed settings (main thread only)."""
        now=self.clock()
        if self.executor and getattr(self.executor,'state_dirty',False) is True:
            self.executor.state_dirty=False;self._unsaved=True
        if self._last_health is None or (now-self._last_health).total_seconds()>=30:
            running=bool(self._thread and self._thread.is_alive())
            self._set('research_watch_health',{'at':now.isoformat(),'thread_running':running,
                'window_active':self._due(),'error':self.last_error,
                'configured':{'ICT':list(self.symbols),'gap fill':list(self.gap_symbols),
                              **{name:list(symbols) for name,symbols in self.research.items()}}})
            self._last_health=now
        if self._unsaved:
            self._unsaved = False
            on = self.symbols or self.gap_symbols or any(self.research.values())
            paper = bool(self.executor and self.executor.enabled)
            settings=dict(symbols=list(self.symbols), min_grade=self.min_grade,
                          gap_symbols=list(self.gap_symbols), gap_paper=paper)
            if paper:
                settings['paper_symbols'] = list(self.executor.symbols)
            modes=getattr(self.executor,'strategy_enabled',()) if self.executor else ()
            if isinstance(modes,(tuple,list)) and modes:settings['strategy_paper']=list(modes)
            if self.research:
                settings['research']={k:list(v) for k,v in self.research.items()}
            self._set('ict_watch', settings if on or paper or (isinstance(modes,(tuple,list)) and modes) else None)
        with self._snapshot_lock:
            if self._snapshots_dirty:
                self._snapshots_dirty=False
                self._set('trading_model_snapshots',deepcopy(self.snapshots))
        seen=dict(self.seen)
        if seen != self._seen_saved:
            self._set('trading_alert_seen',dict(list(seen.items())[-2000:]))
            self._seen_saved=seen
        found = []
        while True:
            try:
                message=self.events.get_nowait()
                # Research observations remain available in status; broker lifecycle
                # alerts use the independent execution queue and are unaffected.
                if message.startswith(('SMC research', 'AMD / Power of Three')):
                    continue
                if message not in found:found.append(message)
            except queue.Empty:
                return found
