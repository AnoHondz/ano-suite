"""Recover original accepted protection; replacement stops never redefine risk."""
from datetime import datetime
from decimal import Decimal

def original_risk(provider,evidence,lookup=None):
    try:
        if evidence.get('fill_verified') is not True:return None
        if provider=='oanda_practice':
            trade=evidence['receipt'];entry=Decimal(trade['price']);units=Decimal(trade['initialUnits'])
            stop=dict(trade['stopLossOrder']);seen=set()
            for _ in range(12):
                previous=stop.get('replacesOrderID')
                if not previous:break
                if not lookup or previous in seen or not str(previous).isdigit():return None
                seen.add(previous);stop=lookup(str(previous))
                if str(stop.get('id'))!=str(previous):return None
            else:return None
            if stop.get('type')!='STOP_LOSS' or str(stop.get('tradeID'))!=str(trade['id']):return None
            opened=datetime.fromisoformat(trade['openTime'].replace('Z','+00:00'))
            created=datetime.fromisoformat(stop['createTime'].replace('Z','+00:00'))
            # An order created later cannot prove initial protection, even if a broker omits its replacement link.
            if not opened.tzinfo or not created.tzinfo or abs((created-opened).total_seconds())>1:return None
            price=Decimal(stop['price'])
            if not all(v.is_finite() for v in (entry,price,units)) or min(entry,price)<=0:return None
            if units==0 or (entry-price)*(1 if units>0 else -1)<=0:return None
            return {'entry':str(entry),'stop':str(price),'units':str(units),
                    'source':'broker_original_stop_order','order':stop,'opened_at':trade['openTime']}
        order=evidence['entry_order'];entry=Decimal(str(order['filled_avg_price']))
        if order.get('side') not in ('buy','sell'):return None
        units=Decimal(str(evidence['filled_quantity']))*(1 if order.get('side')=='buy' else -1)
        stops=[leg for leg in order.get('legs') or [] if leg.get('type') in ('stop','stop_limit')]
        if len(stops)!=1 or stops[0].get('replaces'):return None
        stop=Decimal(str(stops[0]['stop_price']))
        if not all(v.is_finite() for v in (entry,stop,units)) or min(entry,stop)<=0:return None
        if units==0 or (entry-stop)*(1 if units>0 else -1)<=0:return None
        return {'entry':str(entry),'stop':str(stop),'units':str(units),
                'source':'broker_entry_bracket','order':stops[0]}
    except (KeyError,TypeError,ValueError,ArithmeticError):return None
