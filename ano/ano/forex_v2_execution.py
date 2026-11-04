"""Native execution state machine, dependency-injected for offline/practice tests.

The running app's shared ForexRunner owns automatic practice execution when the
user opts in. This dependency-injected component supports isolated lifecycle
tests; it is not a second worker or an independently authorized connection.
"""
from contextlib import closing
from datetime import datetime,timezone
from .forex_order_policy import payload


class IntentStore:
    def __init__(self,path):
        import sqlite3
        self.path=path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS intents(id TEXT PRIMARY KEY,payload TEXT)')

    def create(self,record):
        import json,sqlite3
        with closing(sqlite3.connect(self.path)) as db, db:
            return db.execute('INSERT OR IGNORE INTO intents VALUES (?,?)',
                (record['id'],json.dumps(record,default=str))).rowcount==1

    def save(self,record):
        import json,sqlite3
        with closing(sqlite3.connect(self.path)) as db, db:
            if db.execute('UPDATE intents SET payload=? WHERE id=?',
                (json.dumps(record,default=str),record['id'])).rowcount!=1:raise ValueError('intent missing')

    def read(self,identity):
        import json,sqlite3
        with closing(sqlite3.connect(self.path)) as db, db:row=db.execute('SELECT payload FROM intents WHERE id=?',(identity,)).fetchone()
        return json.loads(row[0]) if row else None


class Lifecycle:
    def __init__(self,api,store,clock=lambda:datetime.now(timezone.utc)):
        self.api=api;self.store=store;self.save=store.save;self.clock=clock

    def submit(self,identity,prepared):
        if self.clock().timestamp()>=prepared['expires_at']:raise ValueError('expired signal')
        order=payload(identity,prepared)
        # Persist before transport; the caller's save must enforce unique ids.
        record={'id':identity,'state':'submitting','prepared':prepared,'order':order}
        if not self.store.create(record):raise ValueError('durable intent exists; do not resubmit')
        try:
            response=self.api.submit_order_payload(order)
        except Exception:
            self.save({**record,'state':'uncertain','note':'GET-only reconciliation; never resubmit'})
            raise
        self.save({**record,'state':'awaiting_broker_verification','receipt':response})
        return response

    def reconcile(self,record,*,allow_pending=True):
        identity=record['id'];p=record['prepared'];order=self.api.order(identity)
        if (order.get('clientExtensions',{}).get('id')!=identity or order.get('instrument')!=p['pair']
            or str(order.get('units'))!=str(p['units'])):raise ValueError('order identity mismatch')
        status=order['state']
        if status=='PENDING':
            if (order.get('type')!='LIMIT' or order.get('timeInForce')!='GTD'
                or str(order.get('price'))!=str(p['entry'])):raise ValueError('pending order contract mismatch')
            from .trading_risk import number
            if any(number((order.get(k) or {}).get('price',0))!=number(p[v]) for k,v in
                   [('stopLossOnFill','stop'),('takeProfitOnFill','target')]):raise ValueError('pending bracket mismatch')
            expires=datetime.fromisoformat(order['gtdTime'].replace('Z','+00:00')).timestamp()
            if abs(expires-p['expires_at'])>.001:raise ValueError('pending expiry mismatch')
            if record['state']=='cancel_uncertain':raise ValueError('cancel outcome uncertain; GET only')
            if not allow_pending or self.clock().timestamp()>=p['expires_at']:
                self.save({**record,'state':'cancel_uncertain'})
                self.api.cancel_order(identity)
                return 'cancel_uncertain'
            self.save({**record,'state':'pending_verified'});return 'pending_verified'
        if status=='CANCELLED':
            receipt=self.api.cancellation(identity)
            self.save({**record,'state':'cancelled_verified','cancellation':receipt});return 'cancelled_verified'
        if status=='FILLED':
            tid=order.get('tradeOpenedID')
            if not tid:raise ValueError('fill without verified opened trade')
            trade=self.api.trade(str(tid))
            from .trading_risk import number
            if (trade.get('clientExtensions',{}).get('id')!=identity or trade['instrument']!=p['pair']
                or number(trade['initialUnits'])!=p['units']):raise ValueError('fill ownership mismatch')
            fill=number(trade['price']);long=p['units']>0
            if not (number(p['stop'])<fill<number(p['target']) if long else number(p['target'])<fill<number(p['stop'])):
                raise ValueError('fill outside bracket')
            if fill>number(p['bound']) if long else fill<number(p['bound']):raise ValueError('fill exceeded sized worst price')
            if trade['state']=='OPEN':
                for key,kind,pricekey in [('stopLossOrder','STOP_LOSS','stop'),('takeProfitOrder','TAKE_PROFIT','target')]:
                    leg=trade.get(key) or {}
                    if (leg.get('state')!='PENDING' or leg.get('type')!=kind or str(leg.get('tradeID'))!=str(tid)
                        or number(leg.get('price',0))!=number(p[pricekey])):raise ValueError('fill protection mismatch')
            elif trade['state']!='CLOSED':raise ValueError('unknown trade lifecycle')
            self.save({**record,'state':'fill_verified','trade_id':str(tid),'trade':trade});return 'fill_verified'
        raise ValueError('unresolved broker state; new entries remain blocked')
