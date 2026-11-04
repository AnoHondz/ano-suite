"""On-demand Alpaca PAPER reconciliation. HTTP GET only; never changes orders."""
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import json
from pathlib import Path
import re
import sqlite3
import httpx
from .paper_evidence import bracket_evidence

STATE=Path.home()/'Documents'/'AnoWorkspace'/'.ano-state'


class PaperReader:
    def __init__(self,credentials,client_factory=httpx.Client):
        self.headers={'APCA-API-KEY-ID':credentials[0],'APCA-API-SECRET-KEY':credentials[1]}
        self.client_factory=client_factory

    def get(self,path,params=None):
        if path not in ('/v2/account','/v2/positions','/v2/orders','/v2/orders:by_client_order_id') and not re.fullmatch(r'/v2/orders/[A-Za-z0-9-]{8,64}',path):
            raise ValueError('Read outside fixed paper reconciliation scope')
        with self.client_factory(timeout=10,trust_env=False,follow_redirects=False) as client:
            response=client.get('https://paper-api.alpaca.markets'+path,headers=self.headers,params=params)
        if response.status_code==404 and path.endswith('by_client_order_id'):return None
        if response.status_code!=200 or len(response.content)>2_000_000:
            raise ValueError('Paper broker read unavailable')
        return response.json()


def quantity(value):
    q=Decimal(str(value))
    if not q.is_finite():raise ValueError('Invalid broker quantity')
    return q


def reconcile(state=STATE,reader=None,now=None):
    state=Path(state);now=now or datetime.now(timezone.utc)
    result={'at':now.isoformat(),'account_mode':'ALPACA PAPER / SIMULATED','broker_verified':False,
            'scope':'Read-only account, positions, open orders and locally tracked unfinished entries. No orders changed.',
            'issues':[]}
    try:
        if reader is None:
            from .credentials import load_paper_credentials
            credentials=load_paper_credentials()
            if not credentials:raise ValueError('Paper credentials unavailable')
            reader=PaperReader(credentials)
        account=reader.get('/v2/account')
        positions=reader.get('/v2/positions')
        orders=reader.get('/v2/orders',{'status':'open','nested':'true','limit':100})
        if not isinstance(account,dict) or not isinstance(positions,list) or not isinstance(orders,list):
            raise ValueError('Invalid broker response shape')
        result.update(account_active=account.get('status')=='ACTIVE',trading_blocked=bool(account.get('trading_blocked',True)),
                      positions=[{'symbol':p['symbol'],'quantity':str(quantity(p['qty'])),'side':p.get('side')}
                                 for p in positions if p.get('symbol') in ('QQQ','DIA')],
                      other_position_count=sum(p.get('symbol') not in ('QQQ','DIA') for p in positions),
                      open_order_count=len(orders),open_orders_complete=len(orders)<100)
        if len(orders)>=100:result['issues'].append('Open-order result reached its cap; absence of additional orders is not established.')
        path=state/'paper-execution.sqlite3'
        local=[]
        if path.is_file():
            db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)
            try:
                have={r[1] for r in db.execute('PRAGMA table_info(gap_trades)')}
                extra=[name for name in ('side','qty','entry','stop','target','exit_order','exit_qty','strategy') if name in have]
                names=['client_id','symbol','state','filled_qty']+extra
                local=[dict(zip(names,r)) for r in db.execute('SELECT '+','.join(names)+" FROM gap_trades WHERE state IN ('submitting','uncertain','acknowledged','partially_filled','filled','exiting','exit_uncertain') LIMIT 21")]
            finally:db.close()
        else:result['issues'].append('Local execution ledger unavailable; broker positions cannot be attributed to Ano.')
        if len(local)>20:result['issues'].append('Local reconciliation cap reached; not every unfinished record was checked.')
        result['tracked_orders']=[]
        for trade in local[:20]:
            client_id,symbol,local_state,local_filled=(trade[k] for k in ('client_id','symbol','state','filled_qty'))
            if symbol not in ('QQQ','DIA') or not re.fullmatch(r'ano-paper-[A-Za-z0-9_-]{1,32}',client_id):
                result['issues'].append('Unrecognized local order identity was not queried.');continue
            order=reader.get('/v2/orders:by_client_order_id',{'client_order_id':client_id,'nested':'true'})
            row={'symbol':symbol,'strategy':trade.get('strategy','gap_fill_v1_2'),'local_state':local_state,'local_filled_qty':local_filled,'broker_order_found':order is not None}
            if order is None:
                result['issues'].append(symbol+': locally tracked entry not found at broker; no state was changed.')
            else:
                if order.get('client_order_id')!=client_id or order.get('symbol')!=symbol:
                    raise ValueError('Broker order identity mismatch')
                filled=quantity(order.get('filled_qty','0'));legs=order.get('legs') or []
                working=('new','accepted','partially_filled')
                opposite='sell' if order.get('side')=='buy' else 'buy' if order.get('side')=='sell' else None
                own_legs=[x for x in legs if opposite and x.get('side')==opposite and x.get('symbol')==symbol]
                # A filled bracket's stop leg sits 'held' while its target is live; it still protects the position.
                from .paper_evidence import STOP_ACTIVE
                stops=[x for x in own_legs if x.get('type') in ('stop','stop_limit') and x.get('status') in STOP_ACTIVE and quantity(x.get('qty',0))>=filled]
                targets=[x for x in own_legs if x.get('type')=='limit' and x.get('status') in working and quantity(x.get('qty',0))>=filled]
                closed_leg=any(x.get('status')=='filled' and quantity(x.get('filled_qty',0))>=filled for x in own_legs) if filled>0 else False
                row.update(broker_state=order.get('status'),broker_filled_qty=str(filled),
                           filled_qty_matches=filled==quantity(local_filled),
                           protective_legs_seen=bool(filled>0 and stops and targets),closing_bracket_fill_seen=closed_leg)
                plan_complete=all(trade.get(k) is not None for k in ('side','qty','entry','stop','target'))
                row['intended_plan_available']=plan_complete
                row['price_risk_verified']=False
                if plan_complete:
                    evidence=bracket_evidence(trade,order)
                    row.update(price_risk_verified=evidence['protection_verified'],remaining_qty=evidence['remaining_qty'],
                               closing_bracket_fill_seen=evidence['closed'])
                    if filled>0 and not evidence['protection_verified'] and not evidence['closed']:
                        result['issues'].append(symbol+': saved stop/target prices, quantities and active legs are not all verified.')
                elif filled>0:
                    result['issues'].append(symbol+': intended price/risk plan unavailable in the legacy ledger.')
                if local_state=='exiting':
                    exit_id=trade.get('exit_order')
                    if not re.fullmatch(r'[A-Za-z0-9-]{8,64}',str(exit_id or '')):
                        result['issues'].append(symbol+': separate close receipt unavailable.')
                    else:
                        exit_order=reader.get('/v2/orders/'+exit_id)
                        opposite='buy' if trade.get('side')=='short' else 'sell'
                        row['separate_exit_fill_seen']=bool(exit_order and exit_order.get('symbol')==symbol and
                            exit_order.get('side')==opposite and exit_order.get('status')=='filled' and
                            quantity(exit_order.get('filled_qty',0))==quantity(trade.get('exit_qty') or local_filled))
                        if not row['separate_exit_fill_seen']:result['issues'].append(symbol+': separate closing fill is not yet verified.')
                if local_state=='exit_uncertain':
                    result['issues'].append(symbol+': exit dispatch uncertain; no close will be replayed automatically.')
                if filled>0 and not row['protective_legs_seen'] and not closed_leg:
                    result['issues'].append(symbol+': active protective stop and target not both verified for the filled quantity. Inspect broker orders.')
                if not row['filled_qty_matches']:result['issues'].append(symbol+': local and broker filled quantities differ.')
            result['tracked_orders'].append(row)
        result['broker_verified']=True
        result['notice']='Positions are total account positions, not proof of Ano ownership. Saved intended price/risk is checked only when the plan is available; legacy plans remain unverified. Separate exit fills require their own receipt. Acknowledgements are not fills. No net P&L or strategy edge is inferred.'
    except Exception as error:
        result.update(broker_verified=False,error_type=type(error).__name__,notice='Broker reconciliation could not finish. Partial observations are not proof that the account is flat or protected. Nothing changed.')
    return result


def main():
    result=reconcile()
    STATE.mkdir(parents=True,exist_ok=True)
    path=STATE/'paper-broker-read.json';temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(result,indent=2),encoding='utf-8');temp.replace(path)
    print(json.dumps(result))


if __name__=='__main__':main()
