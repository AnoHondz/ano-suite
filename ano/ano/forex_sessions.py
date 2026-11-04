"""Explicit forex windows, using exchange-local civil time and DST-aware zones."""
from datetime import datetime,time,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from . import ict
from .strategy_signals import bounds

SESSIONS={
    'new_york':dict(zone='America/New_York',start=time(9,30),end=time(15,10),
                    pairs=('EUR_USD','GBP_USD','USD_CAD','USD_JPY','AUD_USD','USD_CHF')),
    'london':dict(zone='Europe/London',start=time(8),end=time(17),pairs=('EUR_USD','GBP_USD')),
    'tokyo':dict(zone='Asia/Tokyo',start=time(9),end=time(15),pairs=('USD_JPY','AUD_USD','NZD_USD','EUR_JPY')),
}


def pricing(pair):
    # One normalized cent must equal the broker's actual price tick.
    return (Decimal('.001'),Decimal('.01'),10) if pair.endswith('_JPY') else (Decimal('.00001'),Decimal('.0001'),1000)


def context(name,now):
    spec=SESSIONS[name];local=now.astimezone(ZoneInfo(spec['zone']))
    start=datetime.combine(local.date(),spec['start'],local.tzinfo)
    end=datetime.combine(local.date(),spec['end'],local.tzinfo)
    virtual_start=datetime.combine(local.date(),time(9,30),ict.NY)
    shift=virtual_start.timestamp()-start.timestamp()
    return dict(name=name,id=name+':'+str(local.date()),start=start,end=end,shift=shift,
                virtual_end=datetime.fromtimestamp(end.timestamp()+shift,ict.NY))


def active(pair,now):
    result=[]
    for name,spec in SESSIONS.items():
        c=context(name,now)
        if pair in spec['pairs'] and c['start'].weekday()<5 and c['start']-timedelta(minutes=5)<=now<c['end']:
            result.append(name)
    return result


def virtual_bounds(name,now,end,session):
    if session=='new_york':return bounds(name,now)
    start={'smc':time(9,35),'turtle soup':time(9,50),'technicals':time(10,20)}.get(name)
    if start is not None:
        first=datetime.combine(now.astimezone(ict.NY).date(),start,ict.NY)
        return (end-timedelta(minutes=10),end-timedelta(minutes=5)) if first<=now<end-timedelta(minutes=10) else None
    original=bounds(name,now)
    if original and now<end-timedelta(minutes=10):
        return min(original[0],end-timedelta(minutes=10)),min(original[1],end-timedelta(minutes=5))
    return None


def entry_bounds(plan,now):
    session=plan.get('session','new_york')
    if session not in SESSIONS or plan['pair'] not in SESSIONS[session]['pairs']:return None
    c=context(session,now)
    if c['start'].weekday()>=5:return None
    if plan.get('session_id',c['id'])!=c['id']:return None
    virtual=datetime.fromtimestamp(now.timestamp()+c['shift'],ict.NY)
    window=virtual_bounds(plan['name'],virtual,c['virtual_end'],session)
    if not window:return None
    return tuple(datetime.fromtimestamp(t.timestamp()-c['shift'],now.tzinfo) for t in window)
