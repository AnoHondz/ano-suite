"""Gap fill paper execution under the user's rule sheet STRATEGY_RULES_v1_2.md. Alpaca PAPER account only.

Runs on its own thread (ExecutionWorker), separate from the chat, the UI and the alert watcher, so a slow
model, a closed chat window or a failed market-data read never stops protective management.

Each loop: (1) manage every unfinished trade from broker evidence, (2) only then, and only after start-up
recovery has completed, decide new entries. Every state change comes from what Alpaca reports:

  submitting ──► acknowledged ──► partially_filled ──► filled ──► exiting ──► closed
        │              │                  │               │
        └► uncertain   └► expired/cancelled/rejected      └► closed (its stop or target filled)

"submitting/uncertain": the order request may or may not have reached Alpaca; it is never sent again, only read.
"filled": verified by the order's filled_qty; its stop and target legs must be seen working, or Ano closes the
shares at once. "exiting": a close of Ano's exact shares was requested; "closed" only when that closing order
reads back filled. Off until the user types "start paper trading the gap fill".
"""
from contextlib import contextmanager
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal,InvalidOperation
import json
from pathlib import Path
import queue
import sqlite3
import threading

from . import session_strategies as ss
from .trading_risk import breakeven, size_paper_trade
from .tools import ToolError
from .paper_evidence import bracket_evidence, whole

STRATEGY = 'gap_fill_v1_2'
RULES = dict(max_gap=0.006, min_gap=0.0005, stop_gaps=1.5)
DECIDE_FROM, DECIDE_UNTIL = dtime(9, 35), dtime(9, 37)   # the signal is the 9:34 close; act on it only while fresh
ENTRY_EXPIRES = dtime(9, 40)                              # an unfilled entry is cancelled (5 minutes)
FLATTEN_AT = dtime(11, 0)
REPORT_AT = dtime(11, 5)
NEWS_MINUTES = 10
COST_PER_SHARE = Decimal('0.02')
MIN_NET_RR = Decimal('0.2')
NEWS_URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'  # public weekly economic calendar, read-only
WORKING = ('new', 'accepted', 'held', 'pending_new', 'accepted_for_bidding', 'partially_filled')
UNFINISHED = ('submitting', 'uncertain', 'acknowledged', 'partially_filled', 'filled', 'exiting', 'exit_uncertain')
# Final states: closed, expired, rejected, not_sent (an order Alpaca never received).
FINAL_BROKER = ('canceled', 'expired', 'rejected')


def fetch_news(now=None,client_factory=None):
    """High-impact US releases this week as UTC datetimes. Raises on any doubt (the caller then trades nothing)."""
    import httpx
    now=now or datetime.now(timezone.utc)
    with (client_factory or httpx.Client)(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = client.get(NEWS_URL)
    if response.status_code != 200 or len(response.content) > 2_000_000:
        raise ToolError(f'News calendar unavailable (HTTP {response.status_code}).')
    events = response.json()
    if not isinstance(events, list):
        raise ToolError('News calendar was not a list.')
    dates=[datetime.fromisoformat(e['date']) for e in events]
    if any(t.tzinfo is None for t in dates) or not any(t.astimezone(ss.NY).isocalendar()[:2]==now.astimezone(ss.NY).isocalendar()[:2] for t in dates):
        raise ToolError('News calendar is empty, undated, or does not cover the current week.')
    return [t.astimezone(timezone.utc) for e,t in zip(events,dates)
            if e.get('country') == 'USD' and e.get('impact') == 'High']


def gap_signal(regular, prior):
    """The live decision from closed bars up to 9:34: (side, open, signal close, gap), or (None, reason)."""
    if prior is None:
        return None, 'no previous close'
    try:
        valid_prior=Decimal(str(prior))
        if not valid_prior.is_finite() or valid_prior<=0:raise ValueError()
    except (ValueError,InvalidOperation):return None,'invalid previous close'
    if len(regular)!=5 or any(b['t']!=regular[0]['t']+i*60 for i,b in enumerate(regular)):
        return None, 'stale or missing data: all five consecutive 9:30–9:34 closed bars are required'
    for b in regular:
        prices=[Decimal(str(b[k])) for k in ('o','h','l','c')]
        if not all(p.is_finite() and p>0 for p in prices) or not prices[2]<=min(prices[0],prices[3])<=max(prices[0],prices[3])<=prices[1]:
            return None, 'invalid OHLC signal data'
    if not regular or ss.ny(regular[0]['t']).time() != ss.OPEN:
        return None, 'no 9:30 bar'
    open_price = regular[0]['o']
    gap = (open_price - prior) / prior
    if not RULES['min_gap'] <= abs(gap) <= RULES['max_gap']:
        return None, f'gap {gap * 100:+.2f}% outside the {RULES["min_gap"] * 100:.2f}–{RULES["max_gap"] * 100:.1f}% band'
    # Sparse IEX observations cannot establish an unchanged consolidated price.
    last = regular[-1]
    if ss.ny(last['t']).time() != dtime(9,34):
        return None, 'no bar between 9:32 and 9:34 (stale or missing data)'
    side = 'short' if gap > 0 else 'long'
    toward = last['c'] < open_price if side == 'short' else last['c'] > open_price
    if not toward:
        return None, 'the first five minutes did not move toward the fill'
    if (side == 'short' and last['c'] <= prior) or (side == 'long' and last['c'] >= prior):
        return None, 'already filled before entry'
    return (side, open_price, last['c'], gap), None


class GapFillExecutor:
    def __init__(self, transport_factory, db_path, journal_path, news=fetch_news, allowed=lambda: True,
                 clock=lambda: datetime.now(timezone.utc)):
        self.transport_factory, self.news, self.allowed, self.clock = transport_factory, news, allowed, clock
        self.db_path, self.journal_path = str(db_path), Path(journal_path)
        self.enabled = False
        self.ready = False          # start-up recovery completed: only then are new entries allowed
        self.management_verified = False
        self.management_errors = []
        self.symbols = ()           # symbols the user is forward-testing; decisions only for these
        self._lock = threading.RLock()  # one management/decision pass at a time (worker and the user's stop)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS gap_trades (client_id TEXT PRIMARY KEY, day TEXT, symbol TEXT, '
                       'side TEXT, qty INTEGER, entry TEXT, stop TEXT, target TEXT, risk_usd TEXT, state TEXT, '
                       'filled_qty INTEGER DEFAULT 0, exit_order TEXT DEFAULT "", note TEXT DEFAULT "")')
            # A database made by the first version lacks the lifecycle columns: add them (existing rows keep theirs).
            have = {row[1] for row in db.execute('PRAGMA table_info(gap_trades)')}
            for column, kind in (('filled_qty', 'INTEGER DEFAULT 0'), ('exit_order', 'TEXT DEFAULT ""'), ('note', 'TEXT DEFAULT ""')):
                if column not in have:
                    db.execute(f'ALTER TABLE gap_trades ADD COLUMN {column} {kind}')
            if 'exit_qty' not in have:
                db.execute('ALTER TABLE gap_trades ADD COLUMN exit_qty INTEGER DEFAULT 0')
            if 'exit_requested' not in have:
                db.execute('ALTER TABLE gap_trades ADD COLUMN exit_requested INTEGER DEFAULT 0')
            for column,kind in (('strategy','TEXT DEFAULT "gap_fill_v1_2"'),('expires_at','REAL DEFAULT 0'),('flatten_at','REAL DEFAULT 0')):
                if column not in have:db.execute(f'ALTER TABLE gap_trades ADD COLUMN {column} {kind}')
            db.execute('CREATE TABLE IF NOT EXISTS gap_decisions (day TEXT, symbol TEXT, PRIMARY KEY (day, symbol))')
            db.execute('CREATE TABLE IF NOT EXISTS gap_reports (day TEXT PRIMARY KEY)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _now(self):
        now = self.clock()
        local = ss.ny(int(now.timestamp()))
        return now, local.date().isoformat(), local.time()

    def journal(self, text):
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = ss.ny(int(self.clock().timestamp())).strftime('%Y-%m-%d %H:%M ET')
        with self.journal_path.open('a', encoding='utf-8') as f:
            f.write(f'- {stamp} · {text}\n')
        return text

    def _trades(self, where='1', args=()):
        with self._db() as db:
            rows = db.execute(f'SELECT client_id, day, symbol, side, qty, entry, stop, target, risk_usd, state, filled_qty, '
                              f'exit_order,exit_qty,exit_requested,strategy,expires_at,flatten_at FROM gap_trades WHERE {where}', args).fetchall()
        keys = ('client_id', 'day', 'symbol', 'side', 'qty', 'entry', 'stop', 'target', 'risk_usd', 'state',
                'filled_qty', 'exit_order', 'exit_qty','exit_requested','strategy','expires_at','flatten_at')
        return [dict(zip(keys, row)) for row in rows]

    def _set(self, client_id, **fields):
        with self._db() as db:
            for key, value in fields.items():
                db.execute(f'UPDATE gap_trades SET {key}=? WHERE client_id=?', (value, client_id))

    def _breakeven(self, trade, now, alerts):
        """Watched breakeven for an open, protected position (user 2026-11-03). True when Ano should close it.

        Needs a quote from the last minute; without one nothing changes and the broker's stop stays the backstop.
        Armed levels are kept beside the ledger so a restart remembers them."""
        source = getattr(self, 'quote_source', None)
        if source is None or trade['state'] != 'filled' or trade.get('entry') is None or trade.get('stop') is None:
            return False
        try:
            quote = source(trade['symbol'])
            at = datetime.fromisoformat(str(quote['at']).replace('Z', '+00:00'))
            if not 0 <= (now - at).total_seconds() <= 60:
                return False
            long = trade['side'] == 'long'
            mark = Decimal(str(quote['bid'] if long else quote['ask']))
        except Exception:
            return False
        from .reliability import atomic_json, read_json
        path = Path(self.db_path).with_name('stock-breakeven.json')
        armed = read_json(path)
        action, level = breakeven(long, trade['entry'], trade['stop'], mark, armed.get(trade['client_id']))
        if action == 'arm':
            armed[trade['client_id']] = str(level.quantize(Decimal('.01')))
            atomic_json(path, armed)
            alerts.append(self.journal(f'{trade["symbol"]} {trade.get("strategy") or "gap fill"}: up 1R. If price comes back to '
                                       f'{armed[trade["client_id"]]}, Ano closes it at breakeven.'))
        return action == 'exit'

    def has_open(self):
        return bool(self._trades("state IN (%s)" % ','.join('?' * len(UNFINISHED)), UNFINISHED))

    # ---------------------------------------------------- management (broker evidence only)
    def recover(self):
        """Start-up: reconcile every unfinished trade with Alpaca before any new entry is allowed."""
        with self._lock:
            alerts = self.manage()
            self.ready = True
            return alerts

    def manage(self):
        """One pass over every unfinished trade, any day. Returns alert texts (problems and state changes)."""
        with self._lock:
            transport = self.transport_factory()
            _now, today, clock = self._now()
            alerts = []
            self.management_verified=True
            self.management_errors=[]
            trades=self._trades("state IN (%s)" % ','.join('?' * len(UNFINISHED)), UNFINISHED)
            # Stops slip in news spikes: in the 5 minutes before a high-impact US release, open trades are closed
            # (and unfilled entries cancelled). An unavailable calendar leaves trades to their stops.
            news_soon=False
            if trades:
                try:
                    cached=getattr(self,'_news_cache',None)
                    if not cached or not 0<=(_now-cached[0]).total_seconds()<=300:self._news_cache=cached=(_now,self.news())
                    news_soon=any(0<=(release-_now).total_seconds()<=300 for release in cached[1])
                except Exception:news_soon=False
            for trade in trades:
                cutoff=(_now.timestamp()>=trade['flatten_at']) if trade.get('flatten_at') else clock>=FLATTEN_AT
                be_exit=self._breakeven(trade,_now,alerts)
                session_over = trade['day'] < today or cutoff or bool(trade['exit_requested']) or news_soon or be_exit
                trade['news_exit']=news_soon
                trade['breakeven_exit']=be_exit
                try:
                    alerts += self._advance(transport, trade, clock, session_over)
                except ToolError as error:
                    self.management_verified=False
                    self.management_errors.append(type(error).__name__)
                    alerts.append(f'Paper trading: could not check {trade["symbol"]} ({error}). Retrying; check the '
                                  'Alpaca paper account in TradingView if this repeats.')
            if clock >= REPORT_AT:
                alerts += self._daily_report(today)
            return [a for a in alerts if a]

    def _advance(self, transport, trade, clock, session_over):
        symbol, client_id, state = trade['symbol'], trade['client_id'], trade['state']
        expired=(self.clock().timestamp()>=trade['expires_at']) if trade.get('expires_at') else clock>=ENTRY_EXPIRES
        if state=='exit_uncertain':
            raise ToolError('Exit submission is uncertain. No close will be replayed; inspect the broker and reconcile its receipt.')
        if state == 'exiting':
            exit_order = transport.get_order(trade['exit_order'])
            if exit_order.get('status') == 'filled':
                expected='buy' if trade['side']=='short' else 'sell'
                if exit_order.get('id')!=trade['exit_order'] or exit_order.get('symbol')!=symbol or exit_order.get('side')!=expected or whole(exit_order.get('filled_qty',0))!=whole(trade.get('exit_qty') or trade['filled_qty']):
                    raise ToolError('Closing fill identity or quantity does not match Ano’s saved exit.')
                self._set(client_id, state='closed', note=f'closed at {exit_order.get("filled_avg_price")}')
                return [self.journal(f'{symbol} {trade.get("strategy") or "gap fill"}: CLOSED — Ano\'s {trade.get("exit_qty") or trade["filled_qty"]} remaining shares sold/bought back '
                                     f'at {exit_order.get("filled_avg_price")} (verified fill).')]
            if exit_order.get('status') in FINAL_BROKER:
                self._set(client_id,state='exit_uncertain',note='Closing order final without a complete verified fill; inspect remaining broker exposure.')
                raise ToolError('Earlier close did not fully fill. No automatic replacement close; reconcile remaining exposure first.')
            if exit_order.get('status') not in WORKING+('pending_cancel','pending_replace'):
                raise ToolError('Unresolved closing order state; no replacement close or completed fill assumed.')
            return []
        order = transport.order(client_id)  # raises on a failed read (then nothing is assumed)
        if order is None:
            # A missing lookup cannot prove a changing request never arrived.
            if state in ('submitting', 'uncertain') and (session_over or expired):
                self._set(client_id,state='uncertain',note='Broker lookup found no entry; submission history is unresolved and will not be replayed.')
                raise ToolError('Entry not found at broker; this does not prove it was never submitted. Inspect broker history; no retry.')
            if state in ('submitting', 'uncertain'):
                return []
            raise ToolError('Alpaca has no record of an order Ano tracked as placed')
        status = order.get('status')
        try:
            evidence=bracket_evidence(trade,order)
        except (ValueError,KeyError) as error:
            raise ToolError(str(error)) from None
        filled = evidence['filled_qty']
        legs = order.get('legs') or []
        working_legs = [leg for leg in legs if leg.get('status') in WORKING]
        filled_leg = next((leg for leg in legs if leg.get('status') == 'filled'), None) if evidence['closed'] else None
        alerts = []
        if filled and filled != trade['filled_qty']:
            self._set(client_id, filled_qty=filled)
            trade['filled_qty'] = filled
            alerts.append(self.journal(f'{symbol} {trade.get("strategy") or "gap fill"}: {filled} shares {trade.get("side","long")} FILLED at {order.get("filled_avg_price")} '
                                       f'(verified; order {status}).'))
        if status in WORKING and not filled:
            self._set(client_id, state='acknowledged')
            if session_over or expired:
                transport.cancel(order['id'])  # the next pass reads the result; nothing is assumed now
            return alerts
        if status in WORKING and filled:
            self._set(client_id, state='partially_filled')
            if session_over or expired or not evidence['protection_verified'] or status!='partially_filled':
                transport.cancel(order['id'])  # cancel the rest; the filled part is handled once the order is final
            return alerts
        if status not in FINAL_BROKER+('filled',):
            raise ToolError(f'Unresolved broker entry state {status}; cancellation/finality not yet verified.')
        if status in FINAL_BROKER and not filled:
            self._set(client_id, state='expired' if status in ('canceled', 'expired', 'done_for_day') else status)
            return alerts + [self.journal(f'{symbol} {trade.get("strategy") or "gap fill"}: entry {status}, nothing filled — no position.')]
        if not filled:
            raise ToolError(f'Unresolved broker entry state {status}; no fill or cancellation assumed.')
        # From here Ano holds `filled` shares (status filled, or cancelled after a partial fill).
        if filled_leg:
            for leg in working_legs:
                transport.cancel(leg['id'])
            refreshed=transport.order(client_id)
            if not refreshed or any(x.get('status') not in FINAL_BROKER+('filled',) for x in refreshed.get('legs') or []):
                raise ToolError('Exit fill seen but sibling cancellation not yet verified; continuing monitoring.')
            try:closed=bracket_evidence(trade,refreshed)['closed']
            except (ValueError,KeyError) as error:raise ToolError(str(error)) from None
            if not closed:raise ToolError('Bracket final fills no longer cover the entry; inspect remaining exposure.')
            self._set(client_id, state='closed', note=f'{filled_leg.get("type")} leg filled at {filled_leg.get("filled_avg_price")}')
            return alerts + [self.journal(f'{symbol} {trade.get("strategy") or "gap fill"}: CLOSED by its {"target" if filled_leg.get("type") == "limit" else "stop"} '
                                          f'at {filled_leg.get("filled_avg_price")} (verified).')]
        if session_over:
            return alerts + [self._exit(transport, trade, 'high-impact news in under 5 minutes' if trade.get('news_exit') else
                                        'breakeven exit after +1R' if trade.get('breakeven_exit') else 'planned session cutoff or stop request')]
        if not evidence['protection_verified']:
            return alerts + [self._exit(transport, trade, 'its stop/target were not working')]
        if state != 'filled':
            self._set(client_id, state='filled')
            alerts.append(self.journal(f'{symbol} {trade.get("strategy") or "gap fill"}: position of {filled} shares protected — stop and target '
                                       f'working ({len(working_legs)} legs verified).'))
        return alerts

    def _exit(self, transport, trade, why):
        """Cancel Ano's working legs, then request a close of exactly Ano's filled shares; 'closed' waits for the fill."""
        if trade['state'] in ('exiting','exit_uncertain'):
            raise ToolError('A closing request already exists or is uncertain; it will not be replayed.')
        order = transport.order(trade['client_id'])
        if not order:raise ToolError('Entry identity unavailable before closing; nothing guessed.')
        try:evidence=bracket_evidence(trade,order)
        except (ValueError,KeyError) as error:raise ToolError(str(error)) from None
        remaining=evidence['remaining_qty']
        if order.get('status') not in FINAL_BROKER+('filled',):
            if order.get('status') in WORKING:transport.cancel(order['id'])
            raise ToolError('Entry cancellation/finality must be verified before closing partially filled shares.')
        if not remaining:
            raise ToolError('Bracket exit fills cover the entry; verify sibling cancellations before closing the record.')
        position=transport.position(trade['symbol'])
        expected='long' if trade['side']=='long' else 'short'
        if not position or position.get('side')!=expected or abs(Decimal(str(position['qty'])))!=remaining:
            raise ToolError('Account position does not exactly match Ano’s remaining shares. No close/cancel guessed.')
        for leg in order.get('legs') or []:
            if leg.get('status') in WORKING:
                transport.cancel(leg['id'])
        checked=transport.order(trade['client_id'])
        if not checked or any(x.get('status') not in FINAL_BROKER+('filled',) for x in checked.get('legs') or []):
            raise ToolError('Bracket cancellation not yet verified; waiting before closing shares.')
        try:after=bracket_evidence(trade,checked)
        except (ValueError,KeyError) as error:raise ToolError(str(error)) from None
        if after['remaining_qty']!=remaining:
            raise ToolError('A bracket filled during cancellation; rechecking exposure before any close.')
        checked_position=transport.position(trade['symbol'])
        if not checked_position or checked_position.get('side')!=expected or abs(Decimal(str(checked_position['qty'])))!=remaining:
            raise ToolError('Account exposure changed during cancellation; no close guessed.')
        # Persist BEFORE dispatch. An ambiguous DELETE response can never cause
        # a second market close on the next loop or after a restart.
        self._set(trade['client_id'],state='exit_uncertain',exit_qty=remaining,note='Close dispatch reserved; waiting for a broker receipt.')
        exit_id = transport.close_shares(trade['symbol'],remaining)
        self._set(trade['client_id'], state='exiting', exit_order=exit_id)
        return self.journal(f'{trade["symbol"]} {trade.get("strategy") or "gap fill"}: EXITING ({why}) — close of Ano\'s {remaining} remaining shares '
                            'requested; waiting for the verified fill.')

    def _daily_report(self, today):
        with self._db() as db:
            if db.execute('SELECT 1 FROM gap_reports WHERE day=?', (today,)).fetchone():
                return []
            db.execute('INSERT INTO gap_reports VALUES (?)', (today,))
        trades = self._trades('day=?', (today,))
        lines = [f'{t["symbol"]} {t["side"]}: {t["state"]}, {t["filled_qty"]} of {t["qty"]} shares' for t in trades]
        text = (f'Daily report {today}: ' + ('; '.join(lines) if lines else 'no trades placed') +
                '. Decisions and reasons are above in this journal.')
        return [self.journal(text)]

    # ---------------------------------------------------- new entries (only after recovery, only if enabled)
    def decide(self, symbol, regular, prior):
        with self._lock:
            now, today, clock = self._now()
            if not (self.enabled and self.ready and symbol in self.symbols and DECIDE_FROM <= clock < DECIDE_UNTIL):
                return []
            if datetime.fromisoformat(today).weekday() >= 5:
                return []  # weekend: no session, nothing to decide or journal
            with self._db() as db:
                if db.execute('SELECT 1 FROM gap_decisions WHERE day=? AND symbol=?', (today, symbol)).fetchone():
                    return []
                db.execute('INSERT INTO gap_decisions VALUES (?, ?)', (today, symbol))
            return [self._decide(self.transport_factory(), symbol, regular, prior, now, today)]

    def _decide(self, transport, symbol, regular, prior, now, today):
        if not self.allowed():
            return self.journal(f'{symbol} gap fill: NO TRADE — Ano is paused or locked.')
        if self.has_open():
            return self.journal(f'{symbol} gap fill: NO TRADE — an earlier entry/position/exit is still unfinished.')
        if any(ss.ny(b['t']).date().isoformat()!=today or b['t']+60>now.timestamp() for b in regular):
            return self.journal(f'{symbol} gap fill: NO TRADE — wrong-session or forming signal bars.')
        try:
            hours = transport.market_day(datetime.fromisoformat(today).date())
        except Exception as error:
            return self.journal(f'{symbol} gap fill: NO TRADE — could not verify a required check ({error}).')
        if not hours or hours[0] != '09:30':
            return self.journal(f'{symbol} gap fill: NO TRADE — not a normal market day ({hours}).')
        signal, reason = gap_signal(regular, prior)
        if not signal:
            return self.journal(f'{symbol} gap fill: NO TRADE — {reason}.')
        side, open_price, last_close, gap = signal
        try:
            releases = self.news()
            near = [t for t in releases if abs((t - now).total_seconds()) <= NEWS_MINUTES * 60]
            if near:
                return self.journal(f'{symbol} gap fill: NO TRADE — high-impact US release at '
                                    f'{ss.ny(int(near[0].timestamp())):%H:%M} ET (±{NEWS_MINUTES} min news filter).')
            account = transport.account()
            if not account['active'] or account['trading_blocked']:
                return self.journal(f'{symbol} gap fill: NO TRADE — paper account not active or blocked.')
            portfolio=transport.portfolio_snapshot()
            if portfolio['positions'] or portfolio['orders'] or not portfolio['orders_complete']:
                return self.journal(f'{symbol} gap fill: NO TRADE — account exposure is not verified empty; no overlap with manual or other-strategy positions.')
            consecutive=self.loss_streak(transport,today) if hasattr(self,'loss_streak') else 0
        except Exception as error:
            return self.journal(f'{symbol} gap fill: NO TRADE — could not verify a required check ({error}).')
        entry = Decimal(str(last_close)).quantize(Decimal('0.01'))
        size = Decimal(str(abs(open_price - prior)))
        stop = (entry + Decimal(str(RULES['stop_gaps'])) * size if side == 'short'
                else entry - Decimal(str(RULES['stop_gaps'])) * size).quantize(Decimal('0.01'))
        target = Decimal(str(prior)).quantize(Decimal('0.01'))
        today_trades = self._trades('day=?', (today,))
        # Open and pending risk across QQQ and DIA (counted together, as correlated exposure).
        existing = sum(Decimal(t['risk_usd']) for t in self._trades(
            "state IN (%s)" % ','.join('?' * len(UNFINISHED)), UNFINISHED))
        # Multi-strategy ownership supplies the fill-verified shared loss streak.
        check = size_paper_trade(
            equity=account['equity'], session_equity=account['last_equity'],
            daily_pnl=account['equity'] - account['last_equity'], existing_risk=existing,
            entries=len(today_trades), consecutive_losses=consecutive, side='sell' if side == 'short' else 'buy',
            entry=entry, stop=stop, target=target, point_value=1, fx_rate=1, tick_size=Decimal('0.01'),
            minimum_quantity=1, quantity_increment=1, round_trip_cost_per_unit=COST_PER_SHARE,
            minimum_reward_risk=MIN_NET_RR)
        if not check['risk_check_passed']:
            return self.journal(f'{symbol} gap fill: NO TRADE — risk check: {" ".join(check["reasons"])}')
        client_id = f'ano-paper-gf{today.replace("-", "")}{symbol.lower()}'
        quantity = int(Decimal(check['quantity']))
        if not self.allowed():return self.journal(f'{symbol}: NO TRADE — stopped during validation.')
        with self._db() as db:  # recorded BEFORE the request, so a crash mid-request is recovered, never resent
            db.execute('INSERT OR IGNORE INTO gap_trades(client_id, day, symbol, side, qty, entry, stop, target, risk_usd, state) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?)', (client_id, today, symbol, side, quantity, str(entry), str(stop),
                                                         str(target), check['planned_loss_usd'], 'submitting'))
        if not self.allowed():
            self._set(client_id,state='not_sent')
            return self.journal(f'{symbol}: NO TRADE — stopped before dispatch.')
        result = transport.submit_once(client_id=client_id, symbol=symbol, side='sell' if side == 'short' else 'buy',
                                       quantity=quantity, entry=entry, stop=stop, target=target, strategy=STRATEGY,can_dispatch=self.allowed)
        state = {'acknowledged': 'acknowledged', 'rejected': 'rejected','not_sent':'not_sent'}.get(result.get('state'), 'uncertain')
        self._set(client_id, state=state)
        return self.journal(
            f'{symbol} gap fill {side.upper()} (gap {gap * 100:+.2f}%): PAPER bracket {state} — {quantity} shares, limit '
            f'{entry}, stop {stop}, target {target}, planned loss ${Decimal(check["planned_loss_usd"]):.2f}. '
            'Acknowledged is not filled: fills are read back from Alpaca.')

    def stop_everything(self):
        """The user's 'stop paper trading': no more entries; cancel or close only what Ano itself opened."""
        with self._lock:
            self.enabled = False
            for trade in self._trades("state IN (%s)" % ','.join('?' * len(UNFINISHED)), UNFINISHED):
                self._set(trade['client_id'],exit_requested=1)
            transport = self.transport_factory()
            notes = []
            for trade in self._trades("state IN (%s)" % ','.join('?' * len(UNFINISHED)), UNFINISHED):
                try:
                    if trade['state']=='exit_uncertain':
                        notes.append(f'{trade["symbol"]}: earlier close uncertain; no duplicate close. Inspect broker receipt.')
                        continue
                    if trade['filled_qty'] and trade['state'] != 'exiting':
                        notes.append(self._exit(transport, trade, 'you stopped paper trading'))
                    elif trade['state'] in ('acknowledged', 'partially_filled', 'uncertain', 'submitting'):
                        order = transport.order(trade['client_id'])
                        if order:
                            transport.cancel(order['id'])
                            notes.append(f'{trade["symbol"]}: entry cancel requested (the result is read back next pass)')
                except ToolError as error:
                    notes.append(f'{trade["symbol"]}: could not reach Alpaca ({error}) — check TradingView now')
            return self.journal('Paper trading stopped by you. ' + (' '.join(notes) if notes else 'Nothing was open.'))


class ExecutionWorker:
    """The executor's own loop: management every 15 s whenever trades are unfinished; decisions in their window.
    Market data is read here only for decisions, after management, in its own try: a data failure never skips
    protection. Alerts are queued for Ano's main thread (exception-only notifications and state changes)."""

    def __init__(self, executor, feed_factory, interval=15):
        self.executor, self.feed_factory, self.interval = executor, feed_factory, interval
        self.events = queue.Queue()
        self._stop = threading.Event()
        self._prior = {}
        self._thread = threading.Thread(target=self._run, name='ano-paper-execution', daemon=True)
        self.last_beat = None  # time of the last completed pass (a heartbeat for the status command)

    def start(self):
        self._thread.start()

    def close(self):
        self._stop.set()
        pause=getattr(self.executor,'pause_entries',None)
        if callable(pause):pause()

    def _run(self):
        while not self.executor.ready and not self._stop.is_set():
            try:
                for text in self.executor.recover():
                    self.events.put(text)
            except Exception as error:
                self.events.put(f'Paper trading: start-up recovery failed ({error}); no new entries until it succeeds.')
                self._stop.wait(60)
        while not self._stop.wait(0 if self.last_beat is None else self.interval):
            self.step()

    def step(self):
        errors=[]
        modes=getattr(self.executor,'strategy_enabled',())
        if not isinstance(modes,(tuple,list,set)):modes=()
        # Read-only quotes for the watched breakeven of open positions.
        self.executor.quote_source=lambda symbol:self.feed_factory().latest_quote(symbol)
        try:
            if self.executor.has_open() or self.executor.enabled or modes or getattr(self.executor,'session_reports',False) is True:
                for text in self.executor.manage():
                    self.events.put(text)
        except Exception as error:
            errors.append('management:'+type(error).__name__)
            self.events.put(f'Paper trading: management pass failed ({error}); retrying.')
        try:
            if not errors and self.executor.management_verified:
                self._decisions()
                if modes:
                    for text in self.executor.process_candidates(self.feed_factory):self.events.put(text)
            elif self.executor.enabled or modes:
                errors.append('entry_gate:management_unverified')
        except Exception as error:
            errors.append('market_data:'+type(error).__name__)
            self.events.put(f'Paper trading: could not read market data for the 9:35 decision ({error}).')
        self.last_beat = self.executor.clock()
        # An external status reader can distinguish a running UI from a fresh
        # management loop. This is NOT a live quote or broker exposure receipt.
        try:
            path=Path(self.executor.db_path).with_suffix('.health.json')
            temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'at':self.last_beat.isoformat(),'state':'degraded' if errors else 'running',
                'enabled':self.executor.enabled,'recovery_ready':self.executor.ready,'symbols':list(self.executor.symbols),
                'strategy_execution':list(modes),
                'risk_halted':bool(isinstance(getattr(self.executor,'strategy_enabled',None),tuple) and self.executor.halted(self.executor._now()[1])),
                'safety_version':'paper_safety_v3',
                'errors':errors,'scope':'Loop completion only; not verification of quotes, fills or protection.'}),encoding='utf-8')
            temporary.replace(path)
        except (OSError,TypeError,AttributeError):
            pass  # Diagnostics must never interrupt protective management.

    def _decisions(self):
        executor = self.executor
        now, today, clock = executor._now()
        if not (executor.enabled and executor.ready and DECIDE_FROM <= clock < DECIDE_UNTIL):
            return
        feed = self.feed_factory()
        for symbol in executor.symbols:
            prior = self._prior.get((symbol, today))
            if prior is None:
                prior = self._prior_close(feed, symbol, datetime.fromisoformat(today).date(),executor.transport_factory().market_day)
                self._prior[(symbol, today)] = prior
            start = datetime.combine(datetime.fromisoformat(today).date(), ss.OPEN, ss.NY).astimezone(timezone.utc)
            bars = [b for b in feed.between(symbol, start, now, pages=2) if b['t'] + 60 <= now.timestamp()]
            regular = [dict(t=b['t'], **{k: float(b[k]) for k in 'ohlc'}) for b in bars]
            for text in executor.decide(symbol, regular, prior):
                self.events.put(text)

    @staticmethod
    def _prior_close(feed, symbol, today,calendar=None):
        for back in range(1, 6):
            day = today - timedelta(days=back)
            hours=calendar(day) if calendar else (('09:30','16:00') if day.weekday()<5 else None)
            if not hours:continue
            start = datetime.combine(day,dtime.fromisoformat(hours[0]), ss.NY).astimezone(timezone.utc)
            end = datetime.combine(day,dtime.fromisoformat(hours[1]), ss.NY).astimezone(timezone.utc)
            bars = feed.between(symbol, start, end, pages=2)
            closing=[b for b in bars if b['t']==end.timestamp()-60]
            if len(closing)==1:
                value=Decimal(str(closing[0]['c']))
                if value.is_finite() and value>0:return float(value)
            return None  # Never substitute a stale minute or an older trading day.
        return None

    def poll(self):
        found = []
        while True:
            try:
                found.append(self.events.get_nowait())
            except queue.Empty:
                return found
