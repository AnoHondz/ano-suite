"""One paper-account owner for gap fill plus explicitly enabled strategy adapters.

Scanner threads only enqueue plans. The independent management thread owns
reconciliation, risk checks, durable reservation and all broker mutations.
"""
from pathlib import Path
from collections import deque
from datetime import datetime,timedelta,time
from decimal import Decimal
import hashlib,json,threading
from . import ict
from .paper_execution import GapFillExecutor,UNFINISHED,COST_PER_SHARE,NEWS_MINUTES
from .paper_evidence import bracket_evidence,whole
from .paper_orders import bracket_payload
from .strategy_signals import STRATEGIES,bounds
from .trading_risk import DAILY_LOSS_LIMIT,MAX_ENTRIES_DAY,TARGET_R,size_paper_trade,number
from .tools import ToolError

# User 2026-11-03 ("fix them"): the 5-month QQQ replay (Jun-Oct 2026) lost money with these in every filter variant
# (ICT -24.4R, AMD -18.7R, opening range -14.0R). SMC (+17.7R), turtle soup and technicals keep trading, with the trend filter.
PAUSED_STOCK_STRATEGIES=('ict','amd','opening range')
RETRACE_ENTRIES=('ict','silver bullet','amd')  # limit at a fair value gap; needs time for price to return


class StrategyExecutor(GapFillExecutor):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.session_reports=True;self._report_hours={}
        self.strategy_enabled=();self.pending=deque(maxlen=100);self._queued=set()
        self._stop_entries=threading.Event();self.state_dirty=False
        original=self.allowed
        self.allowed=lambda:original() and not self._stop_entries.is_set()
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS strategy_attempts(client_id TEXT PRIMARY KEY,at TEXT,plan TEXT,note TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS risk_halts(day TEXT PRIMARY KEY,reason TEXT)')

    def halted(self,today):
        with self._db() as db:return bool(db.execute('SELECT 1 FROM risk_halts WHERE day=?',(today,)).fetchone())

    def resume_entries(self):self._stop_entries.clear()
    def pause_entries(self):self._stop_entries.set()

    def enable_strategy(self,name):
        if name not in STRATEGIES:raise ToolError('Unknown paper strategy adapter.')
        with self._lock:
            self.resume_entries()
            self.strategy_enabled=tuple(dict.fromkeys((*self.strategy_enabled,name)))
            self.state_dirty=True

    def execution_status(self):
        today=self._now()[1]
        with self._db() as db:
            count=db.execute('SELECT count(*) FROM gap_trades WHERE day=?',(today,)).fetchone()[0]
            notes=[r[0] for r in db.execute('SELECT note FROM strategy_attempts ORDER BY rowid DESC LIMIT 5')]
        return (f'Alpaca PAPER only. Gap fill: {"enabled" if self.enabled else "off"}. '
                f'Additional enabled adapters: {", ".join(self.strategy_enabled) or "none"}. '
                f'Shared entry attempts today: {count}/3. Daily risk halt: {self.halted(today)}.\n'
                'Recent candidate decisions:\n'+('\n'.join(notes) if notes else 'No candidate evaluated yet.')+
                '\nLocal permissions/ledger only; use trading bot readiness for fresh broker exposure checks.')

    def offer(self,plan):
        if plan.get('name') not in self.strategy_enabled:return
        encoded=json.dumps(plan,sort_keys=True)
        identity='ano-paper-ms'+hashlib.sha256((plan['name']+'|'+plan['symbol']+'|'+plan['signal_id']).encode()).hexdigest()[:28]
        with self._lock:
            if identity in self._queued:return
            if len(self.pending)==self.pending.maxlen:
                old,_=self.pending.popleft();self._queued.discard(old)
            self.pending.append((identity,json.loads(encoded)));self._queued.add(identity)

    def process_candidates(self,feed_factory):
        notes=[]
        for _ in range(8):
            with self._lock:
                if not self.pending:break
                identity,plan=self.pending.popleft();self._queued.discard(identity)
                with self._db() as db:
                    if db.execute('SELECT 1 FROM strategy_attempts WHERE client_id=?',(identity,)).fetchone():continue
                    db.execute('INSERT INTO strategy_attempts VALUES (?,?,?,?)',
                               (identity,self.clock().isoformat(),json.dumps(plan),'reserved evaluation; never replay a submission'))
                try:note=self._submit_candidate(identity,plan,feed_factory)
                except Exception as error:
                    with self._db() as db:reserved=db.execute('SELECT state FROM gap_trades WHERE client_id=?',(identity,)).fetchone()
                    outcome='SUBMISSION UNVERIFIED; inspect saved broker receipt, never replay' if reserved else 'NO TRADE; validation failed before dispatch'
                    note=f'{plan.get("symbol")} / {plan.get("name")}: {outcome} — {type(error).__name__}: {str(error)[:200]}.'
                with self._db() as db:db.execute('UPDATE strategy_attempts SET note=? WHERE client_id=?',(note,identity))
                notes.append(self.journal(note))
        return notes

    def _submit_candidate(self,identity,p,feed_factory):
        now,today,_=self._now();name=p['name'];symbol=p['symbol']
        def no(reason):return f'{symbol} / {name}: NO TRADE — {reason}'
        if name not in self.strategy_enabled or STRATEGIES.get(name)!=p['strategy']:return no('strategy not enabled or adapter version mismatch')
        if name in PAUSED_STOCK_STRATEGIES:return no('paused: lost money in every version of the 5-month QQQ replay')
        if not self.allowed() or not self.ready or not self.management_verified or self.halted(today):return no('paused, unrecovered, unverified management, or daily risk halt')
        if symbol not in ('QQQ','DIA') or self.has_open():return no('scope or shared account exposure gate')
        window=bounds(name,now)
        if not window:return no('outside strategy entry window')
        for key in ('confirmed_at','last_closed_at'):
            if type(p[key]) not in (int,float) or not 0<=now.timestamp()-p[key]<=90:return no('stale, future or forming signal')
            if ict.ny(p[key]).date().isoformat()!=today:return no('wrong signal session')
        transport=self.transport_factory()
        hours=transport.market_day(now.astimezone(ict.NY).date())
        if not hours or hours[0]!='09:30':return no('market day/calendar unavailable')
        close=datetime.combine(now.astimezone(ict.NY).date(),time.fromisoformat(hours[1]),ict.NY)
        flatten=min(window[1],close-timedelta(minutes=5))
        # Gap-retracement entries wait for price to come back, as the forex ICT limit does (30 minutes);
        # entries placed at the signal close only need a short fill window.
        lifetime=timedelta(minutes=30) if name in RETRACE_ENTRIES else timedelta(minutes=2)
        expires=min(now+lifetime,window[0],flatten-timedelta(seconds=30))
        if expires<=now or flatten<=now+timedelta(minutes=2):return no('insufficient session time remaining')
        releases=self.news()
        if any(abs((release-now).total_seconds())<=NEWS_MINUTES*60 for release in releases):return no('high-impact US news filter')
        account=transport.account()
        if not account['active'] or account['trading_blocked']:return no('paper account unavailable or blocked')
        portfolio=transport.portfolio_snapshot()
        if portfolio['positions'] or portfolio['orders'] or not portfolio['orders_complete']:return no('account is not verified empty, including manual orders')
        quote=feed_factory().latest_quote(symbol)
        at=datetime.fromisoformat(str(quote['at']).replace('Z','+00:00'))
        checked=self.clock()
        if not at.tzinfo or not 0<=(checked-at).total_seconds()<=30:return no('quote is stale or future-dated')
        bid,ask=number(quote['bid']),number(quote['ask'])
        entry,stop,target=(number(p[k]) for k in ('entry','stop','target'))
        if not 0<bid<=ask or (ask-bid)/bid>Decimal('.001'):return no('invalid or wide IEX quote')
        if abs((bid+ask)/2-entry)/entry>Decimal('.0025'):return no('price moved too far from the plan')
        if not (stop<bid<=ask<target if p['side']=='long' else target<bid<=ask<stop):return no('stop/target already crossed by the current quote')
        side='buy' if p['side']=='long' else 'sell' if p['side']=='short' else ''
        trades=self._trades('day=? ORDER BY rowid',(today,))
        check=size_paper_trade(equity=account['equity'],session_equity=account['last_equity'],
            daily_pnl=account['equity']-account['last_equity'],existing_risk=0,entries=len(trades),
            consecutive_losses=self.loss_streak(transport,today),side=side,entry=entry,stop=stop,target=target,
            point_value=1,fx_rate=1,tick_size=Decimal('.01'),minimum_quantity=1,quantity_increment=1,
            round_trip_cost_per_unit=COST_PER_SHARE,minimum_reward_risk=TARGET_R)
        if not check['risk_check_passed']:return no(' '.join(check['reasons']))
        portfolio=transport.portfolio_snapshot()
        if portfolio['positions'] or portfolio['orders'] or not portfolio['orders_complete']:return no('account exposure changed during validation')
        final_now=self.clock()
        if not self.allowed() or name not in self.strategy_enabled or not bounds(name,final_now) or final_now>=expires:
            return no('execution permission or session changed during validation')
        if any(not 0<=final_now.timestamp()-p[k]<=90 for k in ('confirmed_at','last_closed_at')):return no('signal aged during broker checks')
        if not 0<=(final_now-at).total_seconds()<=30:return no('quote aged during risk checks')
        if any(abs((release-final_now).total_seconds())<=NEWS_MINUTES*60 for release in releases):return no('entered news exclusion window during validation')
        qty=int(Decimal(check['quantity']))
        buying_power=number(account.get('buying_power'))
        notional=max(entry,ask*Decimal('1.03')) if side=='sell' else max(entry,ask)
        qty=min(qty,100000,int(buying_power*Decimal('.95')/notional))
        if qty<1:return no('insufficient verified buying power for one whole share')
        bracket_payload(client_id=identity,symbol=symbol,side=side,quantity=qty,entry=entry,stop=stop,target=target,min_reward_risk=TARGET_R)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM gap_trades WHERE state IN (%s)" % ','.join('?'*len(UNFINISHED)),UNFINISHED).fetchone():return no('another strategy reserved the shared account')
            if db.execute('SELECT count(*) FROM gap_trades WHERE day=?',(today,)).fetchone()[0]>=MAX_ENTRIES_DAY:return no(f'shared {MAX_ENTRIES_DAY}-entry daily safety limit')
            if db.execute('SELECT 1 FROM risk_halts WHERE day=?',(today,)).fetchone():return no('daily risk halt')
            db.execute('INSERT INTO gap_trades(client_id,day,symbol,side,qty,entry,stop,target,risk_usd,state,strategy,expires_at,flatten_at) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(identity,today,symbol,p['side'],qty,str(entry),str(stop),str(target),
                       str(qty*(abs(entry-stop)+COST_PER_SHARE)),'submitting',p['strategy'],expires.timestamp(),flatten.timestamp()))
        if not self.allowed() or name not in self.strategy_enabled:
            self._set(identity,state='not_sent');return no('stopped before dispatch')
        result=transport.submit_once(client_id=identity,symbol=symbol,side=side,quantity=qty,
                                     entry=entry,stop=stop,target=target,strategy=p['strategy'],
                                     dispatch_deadline=min(expires.timestamp(),p['confirmed_at']+90,p['last_closed_at']+90,at.timestamp()+30),
                                     can_dispatch=lambda:self.allowed() and name in self.strategy_enabled)
        state={'acknowledged':'acknowledged','rejected':'rejected','not_sent':'not_sent'}.get(result.get('state'),'uncertain')
        self._set(identity,state=state)
        return f'{symbol} / {name}: PAPER bracket {state}; {qty} shares; limit {entry}, stop {stop}, target {target}. Acknowledgement is not a fill. Cancel by {expires.astimezone(ict.NY):%H:%M}; flatten by {flatten:%H:%M} NY.'

    def loss_streak(self,transport,today):
        streak=0
        for trade in reversed(self._trades('day=? ORDER BY rowid',(today,))):
            if trade['state']!='closed' or not trade['filled_qty']:continue
            pnl=self._trade_pnl(transport,trade)
            if pnl<0:streak+=1
            else:break
        return streak

    def _trade_pnl(self,transport,trade):
        order=transport.order(trade['client_id'])
        if not order:raise ToolError('Prior closing result unavailable; consecutive-loss gate cannot pass.')
        evidence=bracket_evidence(trade,order);filled=evidence['filled_qty']
        entry=number(order.get('filled_avg_price'));quantity=0;proceeds=Decimal(0);closed_times=[]
        if not 0<entry<=1000000:raise ToolError('Prior entry fill price invalid; new entries blocked.')
        for leg in order.get('legs') or []:
            amount=whole(leg.get('filled_qty') or 0)
            if amount:
                price=number(leg.get('filled_avg_price'))
                if not 0<price<=1000000:raise ToolError('Prior bracket fill price invalid; new entries blocked.')
                proceeds+=amount*price;quantity+=amount
                closed_times.append(leg.get('filled_at'))
        if trade.get('exit_order'):
            close=transport.get_order(trade['exit_order']);amount=whole(close.get('filled_qty') or 0)
            opposite='sell' if trade['side']=='long' else 'buy'
            if close.get('id')!=trade['exit_order'] or close.get('symbol')!=trade['symbol'] or close.get('side')!=opposite or close.get('status')!='filled' or amount!=whole(trade.get('exit_qty') or 0):
                raise ToolError('Prior separate closing fill is unverified; new entries blocked.')
            price=number(close.get('filled_avg_price'))
            if not 0<price<=1000000:raise ToolError('Prior closing fill price invalid; new entries blocked.')
            proceeds+=amount*price;quantity+=amount
            closed_times.append(close.get('filled_at'))
        if not filled or quantity!=filled:raise ToolError('Prior exit quantities do not reconcile; new entries blocked.')
        gross=(proceeds-filled*entry)*(1 if trade['side']=='long' else -1)
        from .performance import Performance
        closed_at=max(closed_times) if closed_times and all(closed_times) else None
        Performance(Path(self.db_path).parent).record(provider='alpaca_paper',trade_id=trade['client_id'],strategy=trade.get('strategy') or 'gap_fill_v1_2',
            pair=trade['symbol'],session='new_york',gross=gross,cost=filled*COST_PER_SHARE,cost_basis='configured_allowance',closed_at=closed_at,
            evidence={'fill_verified':True,'entry_order':order,'closing_order':close if trade.get('exit_order') else None,'filled_quantity':filled})
        return gross-filled*COST_PER_SHARE

    def _decide(self,transport,symbol,regular,prior,now,today):
        if self.halted(today):return self.journal(f'{symbol}: NO TRADE — shared daily risk halt.')
        return super()._decide(transport,symbol,regular,prior,now,today)

    def manage(self):
        notes=super().manage()
        if not self.has_open():
            from .performance import Performance
            known=Performance(Path(self.db_path).parent).known('alpaca_paper')
            for trade in [t for t in self._trades("state='closed' AND filled_qty>0 ORDER BY rowid") if t['client_id'] not in known][:2]:
                try:self._trade_pnl(self.transport_factory(),trade)
                except Exception:
                    from .reliability import atomic_json
                    atomic_json(Path(self.db_path).parent/'performance.health.json',{'at':self.clock().isoformat(),'state':'waiting','reason':'Historical broker fills not yet fully verified'})
        if self.has_open():
            account=self.transport_factory().account();today=self._now()[1]
            if account['equity']-account['last_equity']<=-account['last_equity']*DAILY_LOSS_LIMIT:
                with self._db() as db:db.execute('INSERT OR IGNORE INTO risk_halts VALUES (?,?)',(today,'shared daily loss limit'))
                notes.append(self.stop_everything())
        return notes

    def stop_everything(self):
        self._stop_entries.set()
        with self._lock:
            self.strategy_enabled=();self.pending.clear();self._queued.clear();self.state_dirty=True
            return super().stop_everything()

    def _daily_report(self,today):
        now=self.clock().astimezone(ict.NY)
        transport=self.transport_factory()
        if today not in self._report_hours:self._report_hours[today]=transport.market_day(now.date())
        hours=self._report_hours[today]
        if not hours:return []
        close=datetime.combine(now.date(),time.fromisoformat(hours[1]),ict.NY)
        if now<close+timedelta(minutes=5):return []
        with self._db() as db:
            if db.execute('SELECT 1 FROM gap_reports WHERE day=?',(today,)).fetchone():return []
        trades=self._trades('day=? ORDER BY rowid',(today,))
        groups={name:{'attempts':0,'closed':0,'pnl':Decimal(0)} for name in ('gap_fill_v1_2',*STRATEGIES.values())};unverified=0;wins=losses=0
        for trade in trades:
            name=trade.get('strategy') or 'gap_fill_v1_2'
            group=groups.setdefault(name,{'attempts':0,'closed':0,'pnl':Decimal(0)})
            group['attempts']+=1
            if trade['state']=='closed' and trade['filled_qty']:
                try:pnl=self._trade_pnl(transport,trade)
                except Exception:unverified+=1;continue
                group['closed']+=1;group['pnl']+=pnl
                wins+=int(pnl>0);losses+=int(pnl<0)
            elif trade['filled_qty'] or trade['state'] in UNFINISHED:unverified+=1
        total=sum((g['pnl'] for g in groups.values()),Decimal(0))
        lines=[f'Paper session summary {today}',
               f'{len(trades)} entry attempts; {wins} profitable / {losses} losing verified closed trades.',
               f'Verified closed-trade P/L after configured cost allowance: ${total:+.2f}.']
        lines += [f'{name}: {g["attempts"]} attempts, {g["closed"]} verified closed, ${g["pnl"]:+.2f}.' for name,g in groups.items()]
        if not trades:lines.append('No orders attempted; no qualifying risk-approved setup.')
        with self._db() as db:
            reasons=db.execute("SELECT note FROM strategy_attempts WHERE at LIKE ? AND note LIKE '%NO TRADE%'",(today+'%',)).fetchall()
        if reasons:
            from collections import Counter
            counts=Counter(r[0].split('NO TRADE',1)[-1].strip(' —;') for r in reasons)
            lines.append('Main skipped candidate reasons: '+', '.join(f'{reason} ({count})' for reason,count in counts.most_common(5)))
        lines += [f'Unresolved/unverified results: {unverified}; excluded from P/L.',
                  f'Daily risk halt: {self.halted(today)}. Shared caps: {MAX_ENTRIES_DAY} attempts/day safety limit, 0.25% planned risk/entry, {DAILY_LOSS_LIMIT:.0%} daily loss, 2 consecutive losses.',
                  'Paper experiment only. P/L uses broker fills less an estimated cost allowance; it is not broker net account P/L. No automatic strategy ranking from a small sample.']
        text='\n'.join(lines)
        from .summary_notes import notes as operational_notes
        problems=operational_notes(Path(self.db_path).parent)
        if problems:text+='\nOperational notes: '+'; '.join(problems)
        self.journal(text)
        with self._db() as db:
            db.execute('INSERT OR IGNORE INTO gap_reports VALUES (?)',(today,))
        return [text]
