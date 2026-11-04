"""Independent OANDA practice management and scan threads; durable order intents."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime,time,timedelta,timezone
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING
from zoneinfo import ZoneInfo
import hashlib,json,queue,sqlite3,threading
from . import ict
from .oanda_practice import PracticeAPI,PAIRS
from .forex_signals import scan
from .strategy_signals import STRATEGIES,bounds
from .forex_sessions import SESSIONS,active,context,entry_bounds,pricing
from .trading_risk import DAILY_LOSS_LIMIT,TARGET_R,breakeven,number,size_paper_trade
from .reliability import atomic_json
from .forex_order_policy import (budget as entry_budget,MAX_UNITS,MIN_STOP_SPREADS,MIN_STOP_PIPS,MAX_SPREAD_PIPS,
                                 DISABLED_STRATEGIES,UNRESOLVED,confirmation,exposure,hold_winner,open_risk)

UNRESOLVED_SQL="state IN ("+",".join("'"+s+"'" for s in UNRESOLVED)+")"


def utc():return datetime.now(timezone.utc)


def forex_news(now):
    """Fail closed on unavailable or wrong-week high-impact currency news."""
    import httpx
    from .paper_execution import NEWS_URL
    with httpx.Client(timeout=10,follow_redirects=False,trust_env=False) as client:r=client.get(NEWS_URL)
    if r.status_code!=200 or len(r.content)>2000000:raise ValueError('News unavailable')
    events=r.json()
    if not isinstance(events,list):raise ValueError('Invalid news')
    dates=[datetime.fromisoformat(e['date']) for e in events]
    if any(t.tzinfo is None for t in dates) or not any(t.astimezone(ict.NY).isocalendar()[:2]==now.astimezone(ict.NY).isocalendar()[:2] for t in dates):
        raise ValueError('News is not current-week evidence')
    return [(e['country'],t) for e,t in zip(events,dates) if e.get('impact')=='High' and e.get('country') in ('USD','EUR','GBP','CAD','JPY','AUD','NZD','CHF')]


class ForexRunner:
    def __init__(self,state,enabled=False,allowed=lambda:True,api_factory=PracticeAPI,clock=utc,news=forex_news):
        self.state=state;self.enabled=enabled;self.allowed=allowed;self.api_factory=api_factory;self.clock=clock;self.news=news
        self.path=state/'forex-execution.sqlite3';self.events=queue.Queue();self.pending=deque(maxlen=100)
        self.stop=threading.Event();self.gate=threading.RLock();self.ready=False;self.error='';self.scan_error=''
        self.observations={};self.last_scan=None;self.threads=[];self._higher={};self._news=None
        self.awaiting={};self.decided=set()  # legacy signals waiting for a confirming candle (forex_order_policy.confirmation)
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS orders(
                id TEXT PRIMARY KEY,day TEXT NOT NULL,plan TEXT NOT NULL,units INTEGER NOT NULL,
                state TEXT NOT NULL,trade_id TEXT,notified INTEGER DEFAULT 0,pnl TEXT,note TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS sessions(day TEXT PRIMARY KEY,nav TEXT NOT NULL,halt INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS reports(day TEXT PRIMARY KEY,body TEXT NOT NULL,delivered INTEGER DEFAULT 0);''')
            db.execute('CREATE TABLE IF NOT EXISTS account_binding(id TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS watched_sessions(id TEXT PRIMARY KEY,name TEXT,due REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS broker_events(id TEXT PRIMARY KEY,reason TEXT,receipt TEXT,verified_at TEXT)')
            db.execute('''CREATE TABLE IF NOT EXISTS skip_checks(session_id TEXT,strategy TEXT,pair TEXT,reason TEXT,n INTEGER,at TEXT,
                PRIMARY KEY(session_id,strategy,pair,reason))''')

    def skip(self,plan,reason):
        session_id=plan.get('session_id') or context(plan.get('session','new_york'),self.clock())['id']
        with self.db() as db:db.execute('''INSERT INTO skip_checks VALUES (?,?,?,?,1,?)
            ON CONFLICT(session_id,strategy,pair,reason) DO UPDATE SET n=n+1,at=excluded.at''',
            (session_id,plan['name'],plan['pair'],reason,self.clock().isoformat()))

    def recover_cancellations(self,limit=1):
        api=self.api()
        with self.db() as db:
            rows=db.execute("SELECT id FROM orders WHERE state='cancelled' AND id NOT IN (SELECT id FROM broker_events) LIMIT ?",(limit,)).fetchall()
        for row in rows:
            try:
                receipt=api.cancellation(row['id'])
                with self.db() as db:db.execute('INSERT OR IGNORE INTO broker_events VALUES (?,?,?,?)',
                    (row['id'],receipt['reason'],json.dumps(receipt),self.clock().isoformat()))
                self.update(row['id'],note='Broker cancellation: '+receipt['reason']+'; transaction '+receipt['transaction_id'])
            except Exception:
                self.update(row['id'],note='Cancellation receipt not yet verified; no retry of entry')

    def watch_session(self,name,now):
        c=context(name,now)
        close=(datetime.combine(c['start'].date(),time(16),ict.NY) if name=='new_york' else c['end'])
        with self.db() as db:db.execute('INSERT OR IGNORE INTO watched_sessions VALUES (?,?,?)',
            (c['id'],name,(close+timedelta(minutes=5)).timestamp()))
        return c

    def api(self):
        api=self.api_factory()
        identity=getattr(api,'account',None)
        if identity is not None:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT id FROM account_binding').fetchone()
                if row and row['id']!=identity:raise ValueError('Forex ledger is bound to a different practice account')
                if not row:db.execute('INSERT INTO account_binding(id) VALUES (?)',(identity,))
        return api

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def rows(self,where='1',args=()):
        with self.db() as db:return [dict(r) for r in db.execute('SELECT * FROM orders WHERE '+where,args)]

    def update(self,identity,**fields):
        if not set(fields)<= {'state','trade_id','notified','pnl','note'}:raise ValueError('Invalid ledger mutation')
        with self.db() as db:db.execute('UPDATE orders SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),identity))

    def session(self,api):
        now=self.clock();day=str(now.astimezone(ict.NY).date());account=api.account_summary()
        nav=number(account['NAV'])
        if nav<=0:raise ValueError('Invalid practice NAV')
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO sessions(day,nav) VALUES (?,?)',(day,str(nav)))
            row=dict(db.execute('SELECT * FROM sessions WHERE day=?',(day,)).fetchone())
            if nav-number(row['nav'])<=-number(row['nav'])*DAILY_LOSS_LIMIT:
                db.execute('UPDATE sessions SET halt=1 WHERE day=?',(day,));row['halt']=1
        return day,account,row

    @staticmethod
    def owned(trade,row):
        p=json.loads(row['plan']);initial=number(trade['initialUnits']);remaining=number(trade['currentUnits'])
        if (trade['id']!=row['trade_id'] or trade['instrument']!=p['pair'] or initial!=row['units']
            or trade.get('clientExtensions',{}).get('id')!=row['id'] or abs(remaining)>abs(initial)
            or (remaining and (remaining>0)!=(initial>0))):raise ValueError('Forex ownership/quantity mismatch')

    def manage(self):
        api=self.api();day,account,session=self.session(api);now=self.clock()
        rows=self.rows("state NOT IN ('closed','cancelled','rejected','not_sent')")
        for row in rows:
            p=json.loads(row['plan'])
            if not row['trade_id']:
                order=api.order(row['id'])
                if (order['instrument']!=p['pair'] or number(order['units'])!=row['units']
                    or order.get('clientExtensions',{}).get('id')!=row['id']):raise ValueError('Order identity mismatch')
                if order['state']=='CANCELLED':self.update(row['id'],state='cancelled');continue
                if order['state']=='PENDING' and p.get('version')=='fx-native-v2':
                    self.manage_native_pending(api,row,p,order,session)
                    continue
                trade_id=order.get('tradeOpenedID')
                if order['state']!='FILLED' or not trade_id:raise ValueError('Uncertain entry; manual reconciliation required')
                self.update(row['id'],state='filled',trade_id=str(trade_id));row['trade_id']=str(trade_id)
            trade=api.trade(row['trade_id']);self.owned(trade,row)
            price=number(trade['price'])
            if price<=0:raise ValueError('Invalid entry fill')
            if not row['notified']:
                self.events.put(f'{p["pair"]} / {p["name"]} [{p.get("session","new_york")}]: {abs(row["units"])} units '+
                    ('LONG' if row['units']>0 else 'SHORT')+f' FILLED at {price} (verified; OANDA demo trade {row["trade_id"]}).')
                self.update(row['id'],notified=1)
            if trade['state']=='CLOSED':
                if number(trade['currentUnits'])!=0:raise ValueError('Closed trade still has units')
                pnl=number(trade['realizedPL'])+number(trade.get('financing','0'))-number(trade.get('commission','0'))-number(trade.get('guaranteedExecutionFees','0'))
                from .performance import Performance
                Performance(self.state).record(provider='oanda_practice',trade_id=row['trade_id'],
                    strategy=p['strategy'] if p.get('version')=='fx-native-v2' else p['name'],pair=p['pair'],
                    session=p.get('session','new_york'),gross=trade['realizedPL'],cost=number(trade['realizedPL'])-pnl,cost_basis='broker_reported',
                    closed_at=trade.get('closeTime'),evidence={'fill_verified':True,'trade_id':row['trade_id'],'receipt':trade})
                self.update(row['id'],state='closed',pnl=str(pnl));continue
            if trade['state']!='OPEN' or not number(trade['currentUnits']):raise ValueError('Unknown trade lifecycle')
            protection=(number(p['stop'])<price<number(p['target']) and price<=number(p['entry'])
                        if row['units']>0 else number(p['target'])<price<number(p['stop']) and price>=number(p['entry']))
            for key,kind,price_key in (('stopLossOrder','STOP_LOSS','stop'),('takeProfitOrder','TAKE_PROFIT','target')):
                leg=trade.get(key) or {}
                protection=protection and (leg.get('state')=='PENDING' and leg.get('type')==kind and
                    str(leg.get('tradeID'))==row['trade_id'] and number(leg.get('price',0))==number(p[price_key]))
            if row['state']=='closing':raise ValueError('Close outcome uncertain; not resending')
            # Watched breakeven: at +1R arm a level just past entry; if price comes back to it, close the trade.
            be_exit=False
            if protection:
                try:
                    quote=api.quote(p['pair']);long=row['units']>0
                    action,level=breakeven(long,price,p['stop'],quote['bid'] if long else quote['ask'],p.get('breakeven_level'))
                    if action=='arm':
                        p={**p,'breakeven_level':str(level),'breakeven_armed_at':now.timestamp()}
                        with self.db() as db:db.execute('UPDATE orders SET plan=? WHERE id=?',(json.dumps(p),row['id']))
                        self.events.put(f'{p["pair"]} / {p["name"]}: up 1R. If price comes back to {level.quantize(pricing(p["pair"])[0])}, '
                                        'Ano closes it at breakeven.')
                    be_exit=action=='exit'
                except Exception:be_exit=False  # a missing quote never closes a trade; the broker stop remains
            # Stops slip in news spikes, so an open trade is closed in the 5 minutes before a high-impact
            # release for either of its currencies. An unavailable calendar leaves the trade to its stop.
            try:
                relevant=set(p['pair'].split('_'))
                news_soon=any(currency in relevant and 0<=(release-now).total_seconds()<=300 for currency,release in self.native_news(now))
            except Exception:news_soon=False
            time_exit=now.timestamp()>=p['flatten_at']
            if (time_exit and protection and not p.get('held') and not be_exit and not news_soon
                    and not session['halt'] and self.enabled):
                # A winner is not cut by the clock: lock its exit at entry + 0.1R and let it run to target (max 6h / 15:55 NY).
                try:
                    quote=api.quote(p['pair']);long=row['units']>0
                    held=hold_winner(long,price,p['stop'],quote['bid'] if long else quote['ask'],now.timestamp(),p['flatten_at'])
                except Exception:held=None
                if held:
                    level,until=held
                    if p.get('breakeven_level'):  # keep whichever lock is better
                        level=max(level,number(p['breakeven_level'])) if long else min(level,number(p['breakeven_level']))
                    p={**p,'held':True,'flatten_at':until,'breakeven_level':str(level),'held_at':now.timestamp()}
                    with self.db() as db:db.execute('UPDATE orders SET plan=? WHERE id=?',(json.dumps(p),row['id']))
                    self.events.put(f'{p["pair"]} / {p["name"]}: time exit reached in profit, so Ano is holding it for the target. '
                                    f'If price comes back to {level.quantize(pricing(p["pair"])[0])}, Ano closes it.')
                    time_exit=False
            if not protection or time_exit or session['halt'] or not self.enabled or news_soon or be_exit:
                # Read exact trade again immediately before closing; never close by symbol.
                fresh=api.trade(row['trade_id']);self.owned(fresh,row)
                if fresh['state']=='CLOSED':continue
                self.update(row['id'],state='closing',note='high-impact news exit' if news_soon else
                            ('locked-profit exit while holding a winner' if p.get('held') else 'breakeven exit after +1R') if be_exit else
                            'held winner reached its hold limit' if p.get('held') and time_exit else 'protective/session/risk exit')
                api.close(row['trade_id'])
        # An unknown/manual position blocks entry, but is never adopted or closed.
        trades,orders=api.portfolio()
        ids={r['trade_id'] for r in self.rows() if r['trade_id']}
        if any(t['id'] not in ids for t in trades):raise ValueError('Manual/unknown OANDA trade present; entries blocked')
        owned_ids={r['id'] for r in self.rows()}
        if any(o.get('clientExtensions',{}).get('id') not in owned_ids and str(o.get('tradeID','')) not in ids for o in orders):
            raise ValueError('Manual/unknown pending OANDA order; entries blocked')
        self.ready=True
        if not trades:self.recover_cancellations()
        self.report(day,session)

    def dispatch(self,plan):
        """Only the management thread calls this, after reconciliation succeeds."""
        if not self.strategy_allowed(plan):return self.skip(plan,'today session strategy selection')
        if plan.get('version')=='fx-native-v2':return self.dispatch_native(plan)
        if plan['name'] in ('smc','ict'):
            return self.skip(plan,'native v2 shadow validation; legacy adapter retired')
        if plan['name'] in DISABLED_STRATEGIES:return self.skip(plan,'strategy switched off for forex')
        now=self.clock();window=entry_bounds(plan,now)
        if (not self.enabled or not self.allowed() or not self.ready or not window
            or plan['pair'] not in PAIRS or plan['strategy']!='fx_'+STRATEGIES[plan['name']]):return self.skip(plan,'permissions, recovery, scope or session gate')
        for k in ('confirmed_at','last_closed_at'):
            if not 0<=now.timestamp()-plan[k]<=90:return self.skip(plan,'stale or future signal')
        api=self.api();day,account,session=self.session(api)
        if session['halt']:return self.skip(plan,'daily loss halt')
        with self.db() as db:
            prior=[dict(r) for r in db.execute('SELECT * FROM orders WHERE day=? ORDER BY rowid',(day,))]
        with self.db() as db:verified_cancelled={r['id'] for r in db.execute('SELECT id FROM broker_events')}
        session_id=plan.get('session_id',context(plan.get('session','new_york'),now)['id'])
        limits=entry_budget(self.counted(prior),session_id,verified_cancelled)
        if limits['reason']:return self.skip(plan,limits['reason'])
        if self.rows(UNRESOLVED_SQL):return self.skip(plan,'unresolved exposure')
        if any(0<=now.timestamp()-json.loads(r['plan']).get('submitted_at',0)<60 for r in prior):
            return self.skip(plan,'submission cooldown')
        streak=0
        for t in reversed(prior):
            if self.forgiven(t):break
            if t['state']!='closed':continue
            if t['pnl'] is None:return
            if number(t['pnl'])<0:streak+=1
            else:break
        if streak>=2:return self.skip(plan,'two consecutive verified losses')
        trades,orders=api.portfolio()
        blocked=exposure(plan['pair'],self.rows(),trades,orders)
        if blocked:return self.skip(plan,blocked)
        if not self._news or (now-self._news[0]).total_seconds()>300:self._news=(now,self.news(now))
        relevant=set(plan['pair'].split('_'))
        if any(currency in relevant and abs((release-now).total_seconds())<=600 for currency,release in self._news[1]):return self.skip(plan,'high-impact currency news')
        instrument=api.instruments()[plan['pair']]
        tick,pip,_=pricing(plan['pair'])
        if int(instrument['displayPrecision'])!=-tick.as_tuple().exponent or int(instrument['tradeUnitsPrecision'])!=0:return self.skip(plan,'instrument precision unsupported')
        quote=api.quote(plan['pair']);now=self.clock()
        if not 0<=(now-quote['at']).total_seconds()<=15:return self.skip(plan,'stale or future quote')
        bid,ask=quote['bid'],quote['ask'];spread=ask-bid
        if spread>MAX_SPREAD_PIPS*pip:return self.skip(plan,f'spread exceeds {MAX_SPREAD_PIPS}-pip cap')
        long=plan['side']=='long';entry=ask if long else bid
        if abs(entry-number(plan['entry']))>3*pip:return self.skip(plan,'price moved beyond plan tolerance')
        stop,target=number(plan['stop']),number(plan['target'])
        if not (0<stop<bid<=ask<target if long else 0<target<bid<=ask<stop):return self.skip(plan,'quote outside protective bracket')
        if abs(entry-stop)<max(MIN_STOP_SPREADS*spread,MIN_STOP_PIPS*pip):return self.skip(plan,'stop closer than 3x spread or 5 pips')
        # Size against the maximum adverse fill, rather than quote then allow
        # unsized slippage. Monetary risk and net-R gates still apply.
        allowance=min(pip*Decimal('.5'),abs(entry-stop)*Decimal('.1'))
        entry=(entry+allowance if long else entry-allowance).quantize(tick,rounding=ROUND_CEILING if long else ROUND_FLOOR)
        if not (stop<entry<target if long else target<entry<stop):return self.skip(plan,'slippage bound outside bracket')
        # Execution uses bid/ask, not mid-price simulation. Include a further
        # spread and 0.2-pip allowance; liquidity-based targets are never moved.
        cost=(spread+pip*Decimal('.2'))*quote['loss']
        if plan['name'] in ('smc','turtle soup','opening range','technicals'):
            distance=(TARGET_R*abs(entry-stop)*quote['loss']+(TARGET_R+1)*cost)/quote['gain']
            target=(entry+distance if long else entry-distance).quantize(tick,rounding=ROUND_CEILING if long else ROUND_FLOOR)
        # Conservative conversion: compute reward with accountGain, risk with
        # accountLoss. The generic calculator uses loss; reduce target to the
        # gain-equivalent distance for its independent net-R check.
        effective=entry+(target-entry)*quote['gain']/quote['loss']
        effective=effective.quantize(tick,rounding=ROUND_FLOOR if long else ROUND_CEILING)
        check=size_paper_trade(equity=account['NAV'],session_equity=session['nav'],daily_pnl=number(account['NAV'])-number(session['nav']),
            existing_risk=open_risk(account['NAV'],trades,orders),entries=limits['fills_or_reserved'],consecutive_losses=streak,side='buy' if long else 'sell',entry=entry,stop=stop,target=effective,
            point_value=1,fx_rate=quote['loss'],tick_size=tick,minimum_quantity=instrument['minimumTradeSize'],quantity_increment=1,
            round_trip_cost_per_unit=cost,minimum_reward_risk=TARGET_R)
        if not check['risk_check_passed']:return self.skip(plan,'risk sizing: '+'; '.join(check['reasons']))
        margin=number(instrument['marginRate'])
        if not 0<margin<=1:return self.skip(plan,'invalid margin requirement')
        qty=min(int(check['quantity']),MAX_UNITS,int(number(instrument['maximumOrderUnits'])),
            int(number(account['marginAvailable'])*Decimal('.8')/(entry*quote['loss']*margin)),
            int(number(account['NAV'])*3/(entry*quote['loss'])))
        if qty<number(instrument['minimumTradeSize']):return self.skip(plan,'insufficient quantity capacity')
        units=qty if long else -qty
        name=plan.get('session','new_york')
        c=context(name,now)
        source=plan['pair']+'|'+plan['name']+'|'+plan['signal_id']
        if name!='new_york':source=c['id']+'|'+source
        identity='ano-fx-'+hashlib.sha256(source.encode()).hexdigest()[:28]
        # Limit maximum execution slippage, and include it in sizing/net RR.
        # This worst-price bound is already included in position sizing.
        bound=entry
        plan={**plan,'entry':str(entry),'target':str(target),'flatten_at':window[1].timestamp(),
              'session':name,'session_id':c['id'],'submitted_at':now.timestamp()}
        trades,orders=api.portfolio()
        final=self.clock()
        if exposure(plan['pair'],self.rows(),trades,orders) or not self.enabled or not self.allowed() or not entry_bounds(plan,final):return
        if any(not 0<=final.timestamp()-plan[k]<=90 for k in ('confirmed_at','last_closed_at')) or not 0<=(final-quote['at']).total_seconds()<=15:return
        if any(currency in relevant and abs((release-final).total_seconds())<=600 for currency,release in self._news[1]):return self.skip(plan,'high-impact currency news')
        with self.gate:
            if not self.enabled or not self.allowed():return
            if not self.strategy_allowed(plan):return self.skip(plan,'today session strategy selection')
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM orders WHERE id=?',(identity,)).fetchone():return
                current=[dict(r) for r in db.execute('SELECT * FROM orders WHERE day=?',(day,))]
                if entry_budget(self.counted(current),c['id'],verified_cancelled)['reason']:return
                if db.execute('SELECT 1 FROM orders WHERE '+UNRESOLVED_SQL).fetchone():return
                db.execute('INSERT INTO orders(id,day,plan,units,state) VALUES (?,?,?,?,?)',(identity,day,json.dumps(plan),units,'submitting'))
            self.watch_session(name,now)
            if not self.enabled or not self.allowed() or self.stop.is_set():
                self.update(identity,state='not_sent');return
            dispatch_now=self.clock()
            if (not entry_bounds(plan,dispatch_now) or
                any(not 0<=dispatch_now.timestamp()-plan[k]<=90 for k in ('confirmed_at','last_closed_at')) or
                not 0<=(dispatch_now-quote['at']).total_seconds()<=15):
                self.update(identity,state='not_sent');return
            try:
                result=api.submit(identity,plan,units,bound)
                if result.get('orderRejectTransaction'):self.update(identity,state='rejected')
                elif result.get('orderCancelTransaction'):self.update(identity,state='cancelled')
                else:self.update(identity,state='uncertain') # GET-only recovery verifies identity and fills.
            except Exception:
                self.update(identity,state='uncertain',note='Dispatch outcome unknown; never resend')
                raise

    def native_status(self):
        from .forex_shadow import execution_settings
        mode='OANDA PRACTICE AUTO; unvalidated experiment' if execution_settings(self.state)['enabled'] else 'shadow only'
        return {'smc':mode,'ict':mode}

    def native_news(self,now):
        if not self._news or (now-self._news[0]).total_seconds()>300:self._news=(now,self.news(now))
        return self._news[1]

    def manage_native_pending(self,api,row,p,order,session):
        """Manage only the exact owned GTD limit; uncertain cancellation is GET-only."""
        if (p.get('entry_type')!='LIMIT' or order.get('type')!='LIMIT' or order.get('timeInForce')!='GTD'
            or number(order.get('price',0))!=number(p['entry'])):raise ValueError('Native pending contract mismatch')
        expires=datetime.fromisoformat(order['gtdTime'].replace('Z','+00:00')).timestamp()
        if abs(expires-p['expires_at'])>.001:raise ValueError('Native pending expiry mismatch')
        for key,field in [('stopLossOnFill','stop'),('takeProfitOnFill','target')]:
            leg=order.get(key) or {}
            if number(leg.get('price',0))!=number(p[field]) or leg.get('timeInForce')!='GTC':
                raise ValueError('Native pending protective leg mismatch')
        if row['state']=='pending_cancel_uncertain':raise ValueError('Pending cancel outcome uncertain; GET-only reconciliation')
        from .forex_shadow import execution_settings
        now=self.clock();reason=None
        if (not self.enabled or not self.allowed() or not execution_settings(self.state)['enabled']
            or not self.strategy_allowed(p) or session['halt']):reason='native permission/risk stop'
        if now.timestamp()>=p['expires_at']:reason='native limit expired'
        try:
            relevant=set(p['pair'].split('_'))
            if any(currency in relevant and abs((release-now).total_seconds())<=600 for currency,release in self.native_news(now)):
                reason='native high-impact news'
            quote=api.quote(p['pair']);fresh=0<=(self.clock()-quote['at']).total_seconds()<=15
            if not fresh:reason='native pending quote unavailable'
            long=row['units']>0
            if (quote['bid']<=number(p['stop']) or quote['bid']>=number(p['target']) if long else
                quote['ask']>=number(p['stop']) or quote['ask']<=number(p['target'])):
                reason='native setup invalidated or objective reached'
        except Exception:reason='native pending evidence unavailable'
        if reason:
            self.update(row['id'],state='pending_cancel_uncertain',note=reason)
            api.cancel_order(row['id'])
        else:self.update(row['id'],state='pending_verified')

    def dispatch_native(self,plan):
        from .forex_shadow import execution_settings
        from .forex_v2 import session_context,VERSION
        from .forex_order_policy import prepare,payload
        now=self.clock()
        if not execution_settings(self.state)['enabled']:return self.skip(plan,'native automatic practice execution disabled')
        if (not self.enabled or not self.allowed() or not self.ready or self.stop.is_set()
            or plan.get('version')!=VERSION or plan.get('name') not in ('smc','ict')
            or plan.get('strategy')!='fx_'+plan['name']+'_native_v2'
            or plan.get('pair') not in PAIRS):return self.skip(plan,'native permission or scope gate')
        c=session_context(plan['session'],now)
        if (not c['active'] or c['id']!=plan['session_id'] or not self.strategy_allowed(plan)
            or plan['pair'] not in SESSIONS[plan['session']]['pairs']):return self.skip(plan,'native session gate')
        if any(not 0<=now.timestamp()-plan[k]<=90 for k in ('confirmed_at','last_closed_at')):
            return self.skip(plan,'native stale/future signal')
        if plan['entry_type']!=('LIMIT' if plan['name']=='ict' else 'MARKET'):
            return self.skip(plan,'native entry contract mismatch')
        api=self.api();day,account,baseline=self.session(api)
        if baseline['halt']:return self.skip(plan,'native daily loss halt')
        trades,orders=api.portfolio()
        blocked=exposure(plan['pair'],self.rows(),trades,orders)
        if blocked:return self.skip(plan,'native '+blocked)
        identity='ano-fx-'+plan['signal_id']
        with self.db() as db:
            if db.execute('SELECT 1 FROM orders WHERE id=?',(identity,)).fetchone():return
            prior=[dict(r) for r in db.execute('SELECT * FROM orders WHERE day=? ORDER BY rowid',(day,))]
            verified={r['id'] for r in db.execute('SELECT id FROM broker_events')}
        limits=entry_budget(self.counted(prior),c['id'],verified)
        if limits['reason']:return self.skip(plan,limits['reason'])
        if any(0<=now.timestamp()-json.loads(r['plan']).get('submitted_at',0)<60 for r in prior):
            return self.skip(plan,'native submission cooldown')
        streak=0
        for row in reversed(prior):
            if self.forgiven(row):break
            if row['state']!='closed':continue
            if row['pnl'] is None:return self.skip(plan,'native closed P/L unverified')
            if number(row['pnl'])<0:streak+=1
            else:break
        relevant=set(plan['pair'].split('_'));news=self.native_news(now)
        if any(currency in relevant and abs((release-now).total_seconds())<=600 for currency,release in news):
            return self.skip(plan,'native high-impact news')
        expiry=min(plan['expires_at'],c['entry_end'])
        for currency,release in news:
            if currency in relevant and release>now:expiry=min(expiry,release.timestamp()-600)
        if expiry<=now.timestamp():return self.skip(plan,'native expired entry window')
        instrument=api.instruments()[plan['pair']];tick=pricing(plan['pair'])[0]
        if int(instrument['displayPrecision'])!=-tick.as_tuple().exponent or int(instrument['tradeUnitsPrecision'])!=0:
            return self.skip(plan,'native unsupported instrument precision')
        quote=api.quote(plan['pair'])
        if not 0<=(self.clock()-quote['at']).total_seconds()<=15:return self.skip(plan,'native stale quote')
        try:
            p=prepare({**plan,'expires_at':expiry},quote,instrument,account,baseline,
                      entries=limits['fills_or_reserved'],losses=streak,existing_risk=open_risk(account['NAV'],trades,orders))
            body=payload(identity,p)
        except ValueError as error:return self.skip(plan,'native order economics: '+str(error))
        # Preserve the intended entry separately; management validates actual
        # market fills against the worst entry used for risk sizing.
        p={**p,'reference_entry':p['entry'],'entry':p['worst_entry'],'engine':execution_settings(self.state)['engine'],
           'submitted_at':self.clock().timestamp()}
        final=self.clock();trades,orders=api.portfolio()
        if exposure(plan['pair'],self.rows(),trades,orders):return self.skip(plan,'native final exposure gate')
        if final.timestamp()>=expiry or not 0<=(final-quote['at']).total_seconds()<=15:
            return self.skip(plan,'native expired before submission')
        if any(not 0<=final.timestamp()-plan[k]<=90 for k in ('confirmed_at','last_closed_at')):
            return self.skip(plan,'native signal expired before submission')
        with self.gate:
            if not self.enabled or not self.allowed() or self.stop.is_set() or not execution_settings(self.state)['enabled']:return
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM orders WHERE id=?',(identity,)).fetchone():return
                current=[dict(r) for r in db.execute('SELECT * FROM orders WHERE day=?',(day,))]
                if entry_budget(self.counted(current),c['id'],verified)['reason']:return
                if db.execute('SELECT 1 FROM orders WHERE '+UNRESOLVED_SQL).fetchone():return
                db.execute('INSERT INTO orders(id,day,plan,units,state) VALUES (?,?,?,?,?)',
                           (identity,day,json.dumps(p),p['units'],'submitting'))
            final=self.clock()
            if (not self.enabled or not self.allowed() or self.stop.is_set() or not self.strategy_allowed(plan)
                or final.timestamp()>=expiry or final.timestamp()>=c['entry_end']
                or any(not 0<=final.timestamp()-plan[k]<=90 for k in ('confirmed_at','last_closed_at'))
                or not 0<=(final-quote['at']).total_seconds()<=15):
                self.update(identity,state='not_sent');return
            try:
                response=api.submit_order_payload(body)
                state='rejected' if response.get('orderRejectTransaction') else 'cancelled' if response.get('orderCancelTransaction') else 'uncertain'
                self.update(identity,state=state)
                self.watch_session(plan['session'],self.clock())
            except Exception:
                self.update(identity,state='uncertain',note='Native submission unverified; GET only, never resend')
                raise

    def forgiven(self,row):
        """Losses submitted before a user-requested fresh start no longer count toward the two-loss stop.
        Daily loss halt and entry caps are unchanged."""
        from .reliability import read_json
        since=read_json(self.state/'forex-fresh-start.json').get('since',0)
        at=json.loads(row['plan']).get('submitted_at')
        return at is not None and at<since

    def counted(self,rows):
        """The day's entries that count toward the daily caps: those since the latest fresh start."""
        return [row for row in rows if not self.forgiven(row)]

    def strategy_allowed(self,plan):
        """Optional dated restriction; never grants execution or changes risk limits."""
        from .reliability import read_json
        policy=read_json(self.state/'forex-session-selection.json')
        name=plan.get('session','new_york')
        if policy.get('session')!=name:return True
        local=self.clock().astimezone(ZoneInfo(SESSIONS[name]['zone']))
        if policy.get('date')!=str(local.date()):return True
        return plan.get('name') in policy.get('strategies',[])

    def scan_once(self):
        now=self.clock()
        api=self.api()
        errors=[]
        for pair in PAIRS:
            windows=active(pair,now)
            if not windows:
                self.observations[pair]={'at':self.clock().isoformat(),'waiting':'Outside configured session','sessions':{}}
                continue
            try:
                for name in windows:self.watch_session(name,now)
                rows=api.candles(pair,'M1',5000)
                if not rows or not 0<=self.clock().timestamp()-rows[-1]['t']-60<=90:raise ValueError('Stale M1 candles')
                if pair not in self._higher or (now-self._higher[pair][0]).total_seconds()>=900:
                    higher={name:api.candles(pair,granularity,100) for name,granularity in (('daily','D'),('4h','H4'),('1h','H1'))}
                    self._higher[pair]=(now,higher)
                sessions={};combined={}
                for name in windows:
                    # Run the revised SMC/ICT rules in persistent shadow mode.
                    # Other legacy strategies retain their separate controls.
                    from .forex_shadow import ShadowStore
                    native=ShadowStore(self.state).observe(pair,rows,self._higher[pair][1],self.clock(),name)
                    plans,notes=scan(pair,rows,self._higher[pair][1],self.clock(),session=name)
                    plans=[p for p in plans if p['name'] not in ('smc','ict')]
                    from .forex_shadow import execution_settings
                    auto=execution_settings(self.state)['enabled']
                    if auto:plans.extend(native['plans'])
                    for strategy in ('smc','ict'):
                        count=sum(p['name']==strategy for p in native['plans'])
                        notes[strategy]=(f'PRACTICE AUTO native v2: {count} candidate(s); all risk checks required' if auto else
                                         f'SHADOW native v2: {count} candidate(s); unvalidated, no orders')
                    plans=[p for p in plans if self.strategy_allowed(p)]
                    for strategy in notes:
                        if not self.strategy_allowed({'session':name,'name':strategy}):
                            notes[strategy]='WAIT - disabled by today session strategy selection'
                    sessions[name]=notes;combined.update({name+' / '+k:v for k,v in notes.items()})
                    for strategy,note in notes.items():
                        if not any(p['name']==strategy for p in plans):
                            self.skip({'pair':pair,'name':strategy,'session':name},
                                'today session strategy selection' if 'disabled by today' in note else
                                'native v2 shadow validation' if 'SHADOW' in note else
                                'outside strategy window' if 'outside' in note else 'no fresh confirmed setup')
                    # Native SMC / ICT carry their own confirmation (rejection candle, FVG pullback limit). Legacy
                    # signals wait for a confirming candle instead of chasing the signal candle.
                    ready=[p for p in plans if p.get('version')=='fx-native-v2']
                    for p in plans:
                        if p.get('version')!='fx-native-v2' and (pair,p['signal_id']) not in self.decided:
                            self.awaiting.setdefault((pair,p['signal_id']),p)
                    if len(self.decided)>5000:self.decided.clear()  # signal ids are per day; old ones never return
                    for key,p in list(self.awaiting.items()):  # a signal stranded by a session end or feed gap
                        if p['confirmed_at']<now.timestamp()-15*60:del self.awaiting[key]
                    for key,p in list(self.awaiting.items()):
                        if key[0]!=pair or p.get('session')!=name:continue
                        verdict,confirmed=confirmation(p,rows)
                        if verdict=='wait':continue
                        del self.awaiting[key];self.decided.add(key)
                        if verdict=='go':ready.append(confirmed)
                        else:self.skip(p,verdict)
                    with self.gate:
                        for p in ready:self.pending.append(p)
                self.observations[pair]={'at':self.clock().isoformat(),'strategies':combined,'sessions':sessions}
            except Exception as e:
                self.observations[pair]={'at':self.clock().isoformat(),'error':type(e).__name__}
                errors.append(pair+': '+type(e).__name__)
        self.scan_error='; '.join(errors)
        self.last_scan=self.clock().isoformat()

    def report(self,day,session):
        now=self.clock()
        with self.db() as db:
            watched=[dict(r) for r in db.execute('SELECT * FROM watched_sessions WHERE due<=?',(now.timestamp(),))]
        for watched_session in watched:
            identity=watched_session['id']
            with self.db() as db:
                if db.execute('SELECT 1 FROM reports WHERE day=?',(identity,)).fetchone():continue
            rows=[r for r in self.rows() if json.loads(r['plan']).get('session_id','new_york:'+r['day'])==identity]
            closed=[r for r in rows if r['state']=='closed' and r['pnl'] is not None]
            total=sum((number(r['pnl']) for r in closed),Decimal(0))
            lines=[f'Paper session summary {identity} · OANDA DEMO',
                   f'{len(rows)} entry attempts; {len(closed)} verified closed; closed-trade P/L ${total:+.2f}.']
            for name in STRATEGIES:
                selected=[r for r in rows if json.loads(r['plan'])['name']==name]
                pnl=sum((number(r['pnl']) for r in selected if r['pnl'] is not None),Decimal(0))
                lines.append(f'{name}: {len(selected)} attempts, ${pnl:+.2f}.')
            unresolved=len([r for r in rows if r['state'] not in ('closed','cancelled','rejected','not_sent')])
            lines += [f'Unresolved: {unresolved}; excluded from P/L. Current OANDA risk-day halt: {bool(session["halt"])}.',
                      'Broker trade realized P/L plus financing, less commission/guaranteed fees. Experimental session-relative variants; gap fill does not apply to forex.']
            with self.db() as db:
                reasons=db.execute('SELECT reason,sum(n) AS n FROM skip_checks WHERE session_id=? GROUP BY reason ORDER BY n DESC LIMIT 5',(identity,)).fetchall()
                cancelled=db.execute('SELECT e.reason,count(*) AS n FROM broker_events e JOIN orders o ON o.id=e.id WHERE o.plan LIKE ? GROUP BY e.reason',('%'+identity+'%',)).fetchall()
            if reasons:lines.append('Main skipped candidate checks: '+', '.join(f'{r["reason"]} ({r["n"]})' for r in reasons)+'. Counts are checks, not unique trades.')
            if cancelled:lines.append('Verified broker cancellations: '+', '.join(f'{r["reason"]} ({r["n"]})' for r in cancelled))
            from .summary_notes import notes as operational_notes
            problems=operational_notes(self.state)
            if problems:lines.append('Operational notes: '+'; '.join(problems))
            with self.db() as db:db.execute('INSERT OR IGNORE INTO reports(day,body) VALUES (?,?)',(identity,'\n'.join(lines)))

    def heartbeat(self):
        from .reliability import read_json
        from .forex_shadow import execution_settings
        native_settings=execution_settings(self.state)
        atomic_json(self.state/'forex-execution.health.json',{'at':self.clock().isoformat(),'mode':'OANDA PRACTICE ONLY',
            'session_strategy_selection':read_json(self.state/'forex-session-selection.json'),
            'enabled':self.enabled,'ready':self.ready,'error':self.error,'scan_error':self.scan_error,
            'last_scan':self.last_scan,'sessions':{name:{'zone':spec['zone'],'start':str(spec['start']),'end':str(spec['end']),'pairs':list(spec['pairs'])} for name,spec in SESSIONS.items()},'pairs':list(PAIRS),'strategies':list(STRATEGIES),'observations':self.observations,
            'native_strategy_mode':'SMC/ICT native v2 OANDA PRACTICE AUTO' if native_settings['enabled'] else 'SMC/ICT native v2 shadow only; legacy SMC/ICT entries retired',
            'native_execution':native_settings,
            'risk_scope':'Separate OANDA practice account; 0.25% planned risk/entry, 0.5% open in total; at most 4 entries a day (from the latest fresh start); spread at most 1.5 pips; 1.5R targets; breakeven exit after +1R; 60s cooldown; 10% daily loss halt; 2 consecutive losses; up to 2 trades at once on different pairs; technicals off; max 3x notional.',
            'notification_mode':'verified entries and session summary only'})

    def step(self):
        self.ready=False
        try:
            self.manage();self.error=''
            if self.ready and self.enabled and self.allowed():
                with self.gate:plans=list(self.pending);self.pending.clear()
                for plan in plans:self.dispatch(plan)
        except Exception as e:self.ready=False;self.error=type(e).__name__+': management/dispatch unverified; new entries blocked'
        self.heartbeat()

    def start(self):
        def manage_loop():
            while not self.stop.is_set():
                try:self.step()
                except Exception:self.error='Health write failed; entries blocked';self.ready=False
                self.stop.wait(15)
        def scan_loop():
            while not self.stop.is_set():
                try:
                    if self.enabled and self.allowed():self.scan_once()
                except Exception as e:self.scan_error=type(e).__name__+': forex scan unavailable'
                # Start each scan 3 s after the minute closes, not 60 s after the last one ended: a six-pair
                # scan takes ~45-60 s, and the extra wait let new signals age past the 90 s freshness limit.
                self.stop.wait(max(1,63-self.clock().timestamp()%60))
        for target,name in ((manage_loop,'ano-forex-management'),(scan_loop,'ano-forex-scan')):
            thread=threading.Thread(target=target,name=name,daemon=True);thread.start();self.threads.append(thread)

    def close(self):
        self.enabled=False;self.stop.set()

    def poll(self):
        found=[]
        while True:
            try:found.append(self.events.get_nowait())
            except queue.Empty:break
        with self.db() as db:
            for row in db.execute('SELECT day,body FROM reports WHERE delivered=0').fetchall():
                found.append(row['body']);db.execute('UPDATE reports SET delivered=1 WHERE day=?',(row['day'],))
        return found

    def command(self,text):
        key=' '.join(text.lower().split()).rstrip('.!?')
        if key in ('forex cancellations','why no forex trades','forex entry failures'):
            with self.db() as db:
                reasons=[dict(r) for r in db.execute('SELECT o.id,o.state,o.note,e.reason,e.receipt FROM orders o LEFT JOIN broker_events e ON e.id=o.id ORDER BY o.rowid DESC LIMIT 10')]
            return json.dumps({'recent_entries':reasons,'scope':'Saved receipts only; no entry retried. BOUNDS_VIOLATION means the order could not fill inside its price bound.'},indent=2)
        if key in ('oanda status','forex status','oanda trading status','forex trading status',
                   'is ano connected to oanda','is ano connected to onada','can ano take trades on oanda'):
            return json.dumps({'mode':'OANDA DEMO','enabled':self.enabled,'management_ready':self.ready,'error':self.error,
                'execution_modes':self.native_status(),
                'last_scan':self.last_scan,'scan_error':self.scan_error,'pairs':PAIRS,'sessions':{name:{'zone':spec['zone'],'start':str(spec['start']),'end':str(spec['end']),'pairs':list(spec['pairs'])} for name,spec in SESSIONS.items()},'strategies':list(STRATEGIES),
                'observations':self.observations},indent=2)
        if key in ('stop forex trading','stop oanda trading','pause forex trading'):
            self.enabled=False
            return 'Forex entries stopped. Owned OANDA demo trades will be managed to close; use forex status for evidence.'
        if key in ('start forex paper trading','start oanda paper trading'):
            with self.gate:self.enabled=True
            return 'OANDA practice manager enabled. Native SMC/ICT mode: '+json.dumps(self.native_status())+'. All risk and session controls remain required.'
        return None
