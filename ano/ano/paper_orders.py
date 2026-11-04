"""Internal paper-only order transport and durable duplicate suppression.

Not exposed as a model tool. A strategy/monitoring runner must validate account,
quotes, portfolio risk and session gates before calling this component. Merely
installing it does not enable automatic execution.
"""
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
import httpx

from .trading_risk import number
from .tools import ToolError

# Unknown strategies remain held. On 2026-10-29 the user explicitly authorized
# the named scanners for experimental PAPER execution. This is not proof of edge.
PAPER_EXECUTION_HOLD = True
# The one exception, chosen by the user on 2026-10-28 (rule sheet STRATEGY_RULES_v1_2.md): the NY-morning gap
# fill may place Alpaca PAPER bracket orders, through ano/paper_execution.py only, and only after the user turns
# it on with their own typed command. Its planned reward:risk is below 2:1 by design (target = yesterday's close).
AUTHORIZED_STRATEGIES = {'gap_fill_v1_2': 0.2}  # strategy -> its minimum gross reward:risk
from .trading_risk import TARGET_R
AUTHORIZED_STRATEGIES.update({name:TARGET_R for name in ('ict_2022_v3','smc_retest_v1','silver_bullet_v1',
    'turtle_soup_intraday_v1','amd_ict_v1','opening_range_v1','ema_cross_v1')})


def bracket_payload(*, client_id, symbol, side, quantity, entry, stop, target, min_reward_risk=TARGET_R):
    if not isinstance(client_id,str) or not re.fullmatch(r'ano-paper-[a-zA-Z0-9_-]{1,32}',client_id):
        raise ToolError('Use a unique Ano paper client-order identifier.')
    if symbol not in ('QQQ','DIA') or side not in ('buy','sell'):
        raise ToolError('Paper execution scope is QQQ/DIA only.')
    try:
        qty,e,s,t = [number(value) for value in (quantity,entry,stop,target)]
    except ValueError as error:
        raise ToolError(str(error)) from None
    if qty <= 0 or qty != qty.to_integral_value() or qty > 100000:
        raise ToolError('Paper bracket quantity must be positive whole shares, at most 100000.')
    if any(price < 1 or price > 1000000 or price % number('.01') for price in (e,s,t)):
        raise ToolError('ETF bracket prices must be positive, bounded, and aligned to cents.')
    if not (s < e < t if side=='buy' else t < e < s):
        raise ToolError('Invalid bracket direction.')
    if abs(t-e) < number(str(min_reward_risk))*abs(e-s):
        raise ToolError(f'Bracket gross reward/risk is below {min_reward_risk}:1; net costs also require a separate risk check.')
    return dict(client_order_id=client_id,symbol=symbol,side=side,qty=str(int(qty)),type='limit',
                limit_price=str(e),time_in_force='day',order_class='bracket',extended_hours=False,
                take_profit={'limit_price':str(t)},stop_loss={'stop_price':str(s)})


class PaperOrderStore:
    def __init__(self,path):
        self.path=str(path)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS paper_orders (client_id TEXT PRIMARY KEY, '
                       'payload_hash TEXT NOT NULL, state TEXT NOT NULL, receipt TEXT NOT NULL DEFAULT "{}")')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self,payload):
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload_hash,state,receipt FROM paper_orders WHERE client_id=?',
                           (payload['client_order_id'],)).fetchone()
            if row:
                if row[0]!=digest:
                    raise ToolError('This paper order identifier already belongs to a different plan.')
                return False,dict(state=row[1],receipt=json.loads(row[2]))
            db.execute('INSERT INTO paper_orders(client_id,payload_hash,state) VALUES (?,?,?)',
                       (payload['client_order_id'],digest,'dispatch_uncertain'))
            db.commit()  # Must survive a crash before the network call.
        return True,None

    def record(self,client_id,state,receipt):
        with self.connect() as db:
            db.execute('UPDATE paper_orders SET state=?,receipt=? WHERE client_id=?',
                       (state,json.dumps(receipt),client_id))
            db.commit()


class PaperOrderTransport:
    def __init__(self,key,secret,store,client_factory=httpx.Client):
        if any(not isinstance(v,str) or not v or any(c.isspace() for c in v) for v in (key,secret)):
            raise ToolError('Missing paper credentials.')
        self._headers={'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret}
        self.store,self._client_factory=store,client_factory

    def submit_once(self,*,client_id,symbol,side,quantity,entry,stop,target,strategy=None,dispatch_deadline=None,can_dispatch=None):
        if PAPER_EXECUTION_HOLD and strategy not in AUTHORIZED_STRATEGIES:
            raise ToolError('Paper execution is on research hold: no orders until strategy evidence is reviewed. Analysis and backtesting remain available.')
        payload=bracket_payload(client_id=client_id,symbol=symbol,side=side,quantity=quantity,
                                entry=entry,stop=stop,target=target,
                                min_reward_risk=AUTHORIZED_STRATEGIES.get(strategy,2))
        fresh,prior=self.store.claim(payload)
        if not fresh:
            return dict(dispatched=False,**prior)
        try:
            with self._client_factory(timeout=15,follow_redirects=False,trust_env=False) as client:
                if (dispatch_deadline is not None and time.time()>=dispatch_deadline) or (can_dispatch is not None and not can_dispatch()):
                    self.store.record(client_id,'not_sent',{'reason':'dispatch deadline or permission changed'})
                    return dict(dispatched=False,state='not_sent',retry_allowed=False)
                response=client.post('https://paper-api.alpaca.markets/v2/orders',headers=self._headers,json=payload)
            if response.status_code in (400,401,403,422):
                self.store.record(client_id,'rejected',{'http_status':response.status_code})
                return dict(dispatched=True,state='rejected',http_status=response.status_code)
            if response.status_code not in (200,201) or len(response.content)>2_000_000:
                return dict(dispatched=True,state='dispatch_uncertain',retry_allowed=False)
            data=response.json()
            if not isinstance(data,dict) or data.get('client_order_id')!=client_id or not data.get('id'):
                return dict(dispatched=True,state='dispatch_uncertain',retry_allowed=False)
            # This acknowledges receipt only. It never proves a fill or active protection.
            receipt={key:data.get(key) for key in ('id','client_order_id','status')}
            self.store.record(client_id,'acknowledged',receipt)
            return dict(dispatched=True,state='acknowledged',receipt=receipt,fill_verified=False,
                        protection_verified=False)
        except (httpx.HTTPError,ValueError,sqlite3.Error):
            return dict(dispatched=True,state='dispatch_uncertain',retry_allowed=False)

    # ---- paper-host reads and protective actions (fixed host, no redirects; no new entries)
    def _call(self,method,path,params=None):
        try:
            with self._client_factory(timeout=15,follow_redirects=False,trust_env=False) as client:
                response=client.request(method,'https://paper-api.alpaca.markets'+path,headers=self._headers,params=params)
            if len(response.content)>2_000_000:
                raise ToolError('Paper response exceeded the size limit.')
            return response.status_code,(response.json() if response.content else None)
        except (httpx.HTTPError,ValueError):
            raise ToolError('Paper broker request could not be verified. A changing request may have reached the broker; do not replay it. Check the paper account.') from None

    def account(self):
        status,data=self._call('GET','/v2/account')
        if status!=200 or not isinstance(data,dict):
            raise ToolError(f'Paper account read failed (HTTP {status}).')
        return dict(active=data.get('status')=='ACTIVE',trading_blocked=bool(data.get('trading_blocked',True)),
                    equity=number(data.get('equity')),last_equity=number(data.get('last_equity')),
                    buying_power=number(data['buying_power']) if data.get('buying_power') is not None else None)

    def market_day(self,day):
        """(open, close) times for a market day as 'HH:MM' New York strings, or None when the market is closed."""
        status,data=self._call('GET','/v2/calendar',dict(start=day.isoformat(),end=day.isoformat()))
        if status!=200 or not isinstance(data,list):
            raise ToolError(f'Exchange calendar read failed (HTTP {status}).')
        match=next((row for row in data if row.get('date')==day.isoformat()),None)
        return (match['open'],match['close']) if match else None

    def open_orders(self,symbol):
        status,data=self._call('GET','/v2/orders',dict(status='open',symbols=symbol,nested='true',limit=100))
        if status!=200 or not isinstance(data,list):
            raise ToolError(f'Open orders read failed (HTTP {status}).')
        return [o for o in data if str(o.get('client_order_id','')).startswith('ano-paper-')]

    def portfolio_snapshot(self):
        """All account exposure, not only orders carrying Ano's client prefix."""
        status,positions=self._call('GET','/v2/positions')
        if status!=200 or not isinstance(positions,list):raise ToolError('Paper positions could not be verified.')
        status,orders=self._call('GET','/v2/orders',dict(status='open',nested='true',limit=100))
        if status!=200 or not isinstance(orders,list):raise ToolError('Paper open orders could not be verified.')
        return {'positions':positions,'orders':orders,'orders_complete':len(orders)<100}

    def position(self,symbol):
        status,data=self._call('GET',f'/v2/positions/{symbol}')
        if status==404:
            return None
        if status!=200 or not isinstance(data,dict):
            raise ToolError(f'Position read failed (HTTP {status}).')
        return dict(qty=number(data.get('qty')),side=data.get('side'),unrealized_pl=number(data.get('unrealized_pl') or 0))

    def cancel(self,order_id):
        """Ask to cancel. True means the request was accepted or the order is already final; it is NOT proof of the
        order's state: callers re-read the order before acting on it."""
        if not re.fullmatch(r'[0-9a-zA-Z-]{8,64}',str(order_id)):
            raise ToolError('Invalid broker order id.')
        status,_data=self._call('DELETE',f'/v2/orders/{order_id}')
        return status in (200,204,404,422)

    def get_order(self,order_id):
        """Any order by its broker id (for example the order that closes Ano's shares)."""
        if not re.fullmatch(r'[0-9a-zA-Z-]{8,64}',str(order_id)):
            raise ToolError('Invalid broker order id.')
        status,data=self._call('GET',f'/v2/orders/{order_id}')
        if status!=200 or not isinstance(data,dict):
            raise ToolError(f'Order read failed (HTTP {status}).')
        return data

    def order(self,client_id):
        """Ano's order by its client id, with its bracket legs (stop and target are child orders with their own ids)."""
        if not re.fullmatch(r'ano-paper-[a-zA-Z0-9_-]{1,32}',client_id):
            raise ToolError('Invalid paper order identifier.')
        status,data=self._call('GET','/v2/orders:by_client_order_id',dict(client_order_id=client_id,nested='true'))
        if status==404:
            return None  # Missing lookup is not proof that a changing request never arrived.
        if status!=200 or not isinstance(data,dict) or data.get('client_order_id')!=client_id:
            raise ToolError(f'Order read failed (HTTP {status}).')
        return data

    def close_shares(self,symbol,qty):
        """Request a market close of exactly Ano's `qty` shares (never the user's). Returns the closing order's broker
        id; the shares are closed only once that order reads back as filled."""
        if symbol not in ('QQQ','DIA'):
            raise ToolError('Paper execution scope is QQQ/DIA only.')
        if type(qty) is not int or qty <= 0:
            raise ToolError('Give the exact number of shares Ano opened.')
        status,data=self._call('DELETE',f'/v2/positions/{symbol}',dict(qty=str(qty)))
        if status not in (200,207) or not isinstance(data,dict) or not data.get('id'):
            raise ToolError(f'The close request was not accepted (HTTP {status}).')
        return data['id']

    def reconcile(self,client_id):
        if not re.fullmatch(r'ano-paper-[a-zA-Z0-9_-]{1,32}',client_id):
            raise ToolError('Invalid paper order identifier.')
        try:
            with self._client_factory(timeout=15,follow_redirects=False,trust_env=False) as client:
                response=client.get('https://paper-api.alpaca.markets/v2/orders:by_client_order_id',
                    headers=self._headers,params={'client_order_id':client_id})
            if response.status_code!=200 or len(response.content)>2_000_000:
                return dict(state='unresolved',retry_allowed=False)
            data=response.json()
            if not isinstance(data,dict) or data.get('client_order_id')!=client_id or not data.get('id'):
                return dict(state='unresolved',retry_allowed=False)
            receipt={key:data.get(key) for key in ('id','client_order_id','status','filled_qty','filled_avg_price')}
            self.store.record(client_id,'reconciled',receipt)
            return dict(state='reconciled',receipt=receipt,retry_allowed=False)
        except (httpx.HTTPError,ValueError,sqlite3.Error):
            return dict(state='unresolved',retry_allowed=False)
