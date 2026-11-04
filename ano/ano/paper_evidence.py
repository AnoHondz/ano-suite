"""Pure bracket evidence checks shared by execution and read-only readiness."""
from decimal import Decimal
from .trading_risk import number

ACTIVE = ('new', 'accepted', 'partially_filled')
# Alpaca keeps a filled bracket's stop-loss leg 'held' while its take-profit leg is live (one OCO side at a
# time); the held stop is still protecting the position and triggers at its stop price.
STOP_ACTIVE = ACTIVE + ('held',)


def whole(value):
    n=number(value)
    if n < 0 or n != n.to_integral_value() or n > 100000:
        raise ValueError('Broker quantity must be bounded, nonnegative whole shares.')
    return int(n)


def bracket_evidence(trade, order):
    expected='buy' if trade.get('side')=='long' else 'sell' if trade.get('side')=='short' else trade.get('side')
    if expected not in ('buy','sell') or order.get('client_order_id')!=trade['client_id'] or order.get('symbol')!=trade['symbol'] or order.get('side')!=expected:
        raise ValueError('Broker entry identity or direction mismatch.')
    filled=whole(order.get('filled_qty') or 0)
    planned=whole(trade['qty']) if trade.get('qty') is not None else None
    if planned is not None and (filled>planned or whole(order.get('qty',0))!=planned):
        raise ValueError('Broker entry quantity differs from the saved plan.')
    if order.get('status')=='filled' and planned is not None and filled!=planned:
        raise ValueError('Filled entry status does not match the saved whole quantity.')
    opposite='sell' if expected=='buy' else 'buy'
    legs=order.get('legs') or []
    if not isinstance(legs,list) or any(not isinstance(x,dict) or not x.get('id') or x.get('symbol')!=trade['symbol'] or x.get('side')!=opposite for x in legs):
        raise ValueError('Bracket child identity or direction mismatch; no cancellation guessed.')
    if len({x['id'] for x in legs})!=len(legs):raise ValueError('Duplicate bracket child identifiers.')
    if order.get('group_id') and any(x.get('group_id')!=order['group_id'] for x in legs):
        raise ValueError('Bracket child group differs from the entry.')
    exited=sum(whole(x.get('filled_qty') or 0) for x in legs)
    if exited>filled:
        raise ValueError('Bracket exit fills exceed entry fills; manual broker review required.')
    remaining=filled-exited
    def working(leg,kind,price_key,planned_key):
        if leg.get('type')!=kind or leg.get('status') not in (STOP_ACTIVE if kind=='stop' else ACTIVE):
            return False
        if whole(leg.get('qty') or 0)-whole(leg.get('filled_qty') or 0)!=remaining:
            return False
        return (trade.get(planned_key) is not None and leg.get(price_key) is not None
                and number(leg[price_key])==number(trade[planned_key]))
    stops=[x for x in legs if working(x,'stop','stop_price','stop')]
    targets=[x for x in legs if working(x,'limit','limit_price','target')]
    group=order.get('group_id')
    group_ok=not group or all(x.get('group_id')==group for x in stops+targets)
    return dict(entry_identity_verified=True,filled_qty=filled,exit_filled_qty=exited,remaining_qty=remaining,
                closed=filled>0 and remaining==0,
                protection_verified=remaining>0 and len(stops)==len(targets)==1 and group_ok,
                active_stop_verified=bool(stops),active_target_verified=bool(targets))
