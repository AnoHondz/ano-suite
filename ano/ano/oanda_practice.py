"""OANDA v20 practice only. No selectable host, live endpoint, or model order tool."""
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
import httpx
from .credentials import vault
from .trading_risk import number

PAIRS = ('EUR_USD', 'GBP_USD', 'USD_CAD', 'USD_JPY', 'AUD_USD', 'NZD_USD', 'USD_CHF', 'EUR_JPY')
HOST = 'https://api-fxpractice.oanda.com'


def credentials():
    value = vault().get_password('Ano/OandaPractice', 'credentials')
    if not value:raise ValueError('OANDA practice credentials missing')
    return json.loads(value)


def strategy_extensions(identity, plan):
    """Ano's order identity plus the strategy and session, readable on the broker's own trade record."""
    name = re.sub(r'[^a-z0-9 ]', '', str(plan.get('name', '')).lower())[:40] or 'unknown'
    session = re.sub(r'[^a-z_]', '', str(plan.get('session', '')).lower())[:20]
    return {'id': identity, 'tag': 'ano ' + name, 'comment': f'Ano {name} {session}'.strip()}


class PracticeAPI:
    def __init__(self, keys=None, client_factory=httpx.Client):
        keys = keys or credentials()
        self.account = keys['account']
        if not re.fullmatch(r'101-\d{3}-\d{8}-\d{3}', self.account):
            raise ValueError('Only a v20 practice account is supported')
        self.token, self.factory = keys['token'], client_factory

    def request(self, method, path, *, params=None, payload=None):
        if not path.startswith('/v3/') or '://' in path or '..' in path:
            raise ValueError('Invalid OANDA path')
        with self.factory(base_url=HOST, headers={'Authorization':'Bearer '+self.token},
                          timeout=12, follow_redirects=False, trust_env=False) as client:
            response = client.request(method, path, params=params, json=payload)
        # Never print broker bodies, headers or credentials in failures.
        if response.status_code not in (200,201):
            raise RuntimeError('OANDA practice HTTP '+str(response.status_code))
        return response.json()

    def read(self, suffix, params=None):
        return self.request('GET','/v3/accounts/'+self.account+suffix,params=params)

    def account_summary(self):
        result=self.read('/summary')['account']
        if result['id']!=self.account or result['currency']!='USD':
            raise ValueError('Expected the configured USD practice account')
        return result

    def portfolio(self):
        trades=self.read('/openTrades')['trades'];orders=self.read('/pendingOrders')['orders']
        if not isinstance(trades,list) or not isinstance(orders,list):raise ValueError('Incomplete portfolio')
        return trades,orders

    def instruments(self):
        rows=self.read('/instruments',{'instruments':','.join(PAIRS)})['instruments']
        if {r['name'] for r in rows}!=set(PAIRS):raise ValueError('Missing forex instrument')
        return {r['name']:r for r in rows}

    def quote(self,pair):
        if pair not in PAIRS:raise ValueError('Unsupported pair')
        data=self.read('/pricing',{'instruments':pair,'includeHomeConversions':'true'})
        price=next(p for p in data['prices'] if p['instrument']==pair)
        bid=number(price['bids'][0]['price']);ask=number(price['asks'][0]['price'])
        if not price['tradeable'] or not 0<bid<=ask:raise ValueError('Non-tradeable quote')
        conversions={r['currency']:r for r in data['homeConversions']}
        row=conversions[pair.split('_')[1]]
        loss=number(row['accountLoss']);gain=number(row['accountGain'])
        if not 0<gain<=loss or loss>100:raise ValueError('Invalid USD conversion')
        return dict(bid=bid,ask=ask,loss=loss,gain=gain,
                    at=datetime.fromisoformat(price['time'].replace('Z','+00:00')))

    def candles(self,pair,granularity='M1',count=2000):
        if pair not in PAIRS or granularity not in ('M1','H1','H4','D'):raise ValueError('Invalid candle scope')
        data=self.request('GET','/v3/instruments/'+pair+'/candles',
                          params={'granularity':granularity,'count':count,'price':'M'})
        result=[];previous=0
        for row in data['candles']:
            if not row['complete']:continue
            at=int(datetime.fromisoformat(row['time'].replace('Z','+00:00')).timestamp())
            values={k:float(number(row['mid'][k])) for k in 'ohlc'}
            o,h,l,c=(values[k] for k in 'ohlc')
            if not 0<l<=min(o,c)<=max(o,c)<=h or at<=previous:raise ValueError('Invalid ordered candles')
            previous=at;result.append(dict(t=at,**values))
        return result

    def order(self,identity):
        if not re.fullmatch(r'ano-fx-[a-f0-9]{28}',identity):raise ValueError('Invalid client identity')
        return self.read('/orders/@'+identity)['order']

    def cancellation(self,identity):
        order=self.order(identity)
        if order.get('clientExtensions',{}).get('id')!=identity or order.get('state')!='CANCELLED':
            raise ValueError('Cancellation order identity/state unverified')
        tid=str(order.get('cancellingTransactionID',''))
        if not tid.isdigit():raise ValueError('Cancellation transaction missing')
        transaction=self.read('/transactions/'+tid)['transaction']
        if (str(transaction.get('id'))!=tid or transaction.get('type')!='ORDER_CANCEL'
            or str(transaction.get('orderID'))!=str(order.get('id'))
            or transaction.get('clientOrderID',identity)!=identity):raise ValueError('Cancellation receipt mismatch')
        reason=transaction.get('reason','')
        if not re.fullmatch('[A-Z_]{1,100}',reason):raise ValueError('Unknown cancellation reason')
        return {'transaction_id':tid,'reason':reason,'at':transaction['time'],'order_id':str(order['id'])}

    def trade(self,trade_id):
        if not str(trade_id).isdigit():raise ValueError('Invalid trade identity')
        return self.read('/trades/'+str(trade_id))['trade']

    def submit(self,identity,plan,units,bound):
        if plan['pair'] not in PAIRS or not re.fullmatch(r'ano-fx-[a-f0-9]{28}',identity):raise ValueError('Invalid scope')
        from .forex_order_policy import MAX_UNITS
        if type(units)!=int or not 1<=abs(units)<=MAX_UNITS:raise ValueError('Invalid whole forex units')
        stop,target=number(plan['stop']),number(plan['target'])
        from .forex_sessions import pricing
        tick,_,_=pricing(plan['pair'])
        if any(number(p)%tick for p in (stop,target,bound)):raise ValueError('Instrument price precision mismatch')
        if not (0<stop<number(bound)<target if units>0 else 0<target<number(bound)<stop):raise ValueError('Invalid bracket')
        return self.request('POST','/v3/accounts/'+self.account+'/orders',payload={'order':{
            'type':'MARKET','instrument':plan['pair'],'units':str(units),'timeInForce':'FOK',
            'positionFill':'OPEN_ONLY','priceBound':str(bound),'clientExtensions':strategy_extensions(identity,plan),
            'tradeClientExtensions':strategy_extensions(identity,plan),
            'stopLossOnFill':{'price':str(stop),'timeInForce':'GTC'},
            'takeProfitOnFill':{'price':str(target),'timeInForce':'GTC'}}})

    def close(self,trade_id):
        if not str(trade_id).isdigit():raise ValueError('Invalid owned trade ID')
        return self.request('PUT','/v3/accounts/'+self.account+'/trades/'+str(trade_id)+'/close',payload={'units':'ALL'})

    def submit_order_payload(self,order):
        """Native candidate transport; validates practice scope and order contract."""
        if order.get('instrument') not in PAIRS:raise ValueError('Invalid pair')
        if order.get('positionFill')!='OPEN_ONLY':raise ValueError('Open-only required')
        identity=order.get('clientExtensions',{}).get('id','')
        if not re.fullmatch(r'ano-fx-[a-f0-9]{28}',identity):raise ValueError('Invalid client identity')
        if order.get('tradeClientExtensions',{}).get('id')!=identity:raise ValueError('Trade identity missing')
        units=number(order['units'])
        from .forex_order_policy import MAX_UNITS
        if units!=int(units) or not 1<=abs(units)<=MAX_UNITS:raise ValueError('Invalid whole units')
        kind=order.get('type')
        if kind=='LIMIT':
            if order.get('timeInForce')!='GTD' or order.get('triggerCondition')!='DEFAULT':raise ValueError('GTD limit contract required')
            expiry=datetime.fromisoformat(order['gtdTime'].replace('Z','+00:00'))
            if not expiry.tzinfo or expiry<=datetime.now(timezone.utc):raise ValueError('Expired limit')
            price=number(order['price'])
        elif kind=='MARKET' and order.get('timeInForce')=='FOK':price=number(order['priceBound'])
        else:raise ValueError('Unsupported native order type')
        stop=number(order['stopLossOnFill']['price']);target=number(order['takeProfitOnFill']['price'])
        if not (0<stop<price<target if units>0 else 0<target<price<stop):raise ValueError('Invalid protective bracket')
        from .forex_sessions import pricing
        tick=pricing(order['instrument'])[0]
        if any(p%tick for p in (price,stop,target)):raise ValueError('Price precision mismatch')
        if any(order[k].get('timeInForce')!='GTC' for k in ('stopLossOnFill','takeProfitOnFill')):raise ValueError('Protective GTC legs required')
        return self.request('POST','/v3/accounts/'+self.account+'/orders',payload={'order':order})

    def cancel_order(self,identity):
        if not re.fullmatch(r'ano-fx-[a-f0-9]{28}',identity):raise ValueError('Invalid owned client identity')
        return self.request('PUT','/v3/accounts/'+self.account+'/orders/@'+identity+'/cancel')

    def bid_ask_candles(self,pair,count=5000):
        """Completed historical executable-side candles for explicit-cost replay."""
        if pair not in PAIRS or type(count)!=int or not 1<=count<=5000:raise ValueError('Invalid candle scope')
        data=self.request('GET','/v3/instruments/'+pair+'/candles',
                          params={'granularity':'M1','count':count,'price':'BA'})
        rows=[];previous=0
        for r in data['candles']:
            if not r['complete']:continue
            t=int(datetime.fromisoformat(r['time'].replace('Z','+00:00')).timestamp())
            if t<=previous:raise ValueError('Unordered candles')
            previous=t;row={'t':t}
            for side in ('bid','ask'):
                values={k:float(number(r[side][k])) for k in 'ohlc'}
                if not 0<values['l']<=min(values['o'],values['c'])<=max(values['o'],values['c'])<=values['h']:
                    raise ValueError('Invalid side candle')
                row[side]=values
            if any(row['bid'][k]>row['ask'][k] for k in 'ohlc'):raise ValueError('Crossed side candles')
            rows.append(row)
        return rows
