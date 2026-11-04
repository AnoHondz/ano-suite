"""Experimental session-relative forex variants of Ano's seven closed-bar scanners.

Prices are normalized by 1,000 (10 for JPY) inside the existing analytics (their cent
precision becomes 0.1 pip), then converted back before any risk/transport use.
London and Tokyo clocks are mapped to a synthetic 09:30 analytics clock; all
confirmed times are converted back before dispatch. These are session-relative
research variants, not claims of canonical ICT London or Asian Silver Bullet rules.
The equity opening-gap strategy is deliberately not applied to 24h forex.
"""
from datetime import datetime,time
from decimal import Decimal
from . import ict
from .strategy_signals import STRATEGIES,candidates
from .oanda_practice import PAIRS
from .forex_sessions import context,virtual_bounds,pricing

SCALE=1000


def scaled(rows,scale=SCALE,shift=0):
    return [dict(t=int(b['t']+shift),**{k:float(b[k])*scale for k in 'ohlc'}) for b in rows]


def scan(pair,rows,higher,now,session='new_york'):
    if pair not in PAIRS:raise ValueError('Unsupported forex pair')
    c=context(session,now);tick,pip,scale=pricing(pair)
    shift=c['shift']
    now=datetime.fromtimestamp(now.timestamp()+shift,ict.NY)
    rows=scaled(rows,scale,shift)
    regular_end=time(16) if session=='new_york' else c['virtual_end'].time()
    window_fn=lambda name,t:virtual_bounds(name,t,c['virtual_end'],session)
    today=now.astimezone(ict.NY).date()
    current=[b for b in rows if ict.ny(b['t']).date()==today]
    prior=[b for b in rows if ict.ny(b['t']).date()<today and time(9,30)<=ict.ny(b['t']).time()<regular_end]
    prior_day=max((ict.ny(b['t']).date() for b in prior),default=None)
    prior=[b for b in prior if ict.ny(b['t']).date()==prior_day]
    expected=(regular_end.hour*60+regular_end.minute)-(9*60+30)
    previous=(max(b['h'] for b in prior),min(b['l'] for b in prior)) if len(prior)==expected else None
    day=ict.analyze_day(current,previous)
    from .smc import analyze as smc
    from .silver_bullet import analyze as silver
    from .turtle_soup import analyze as turtle
    reports={'ict':day,'amd':day,
             'smc':smc(current,now=int(now.timestamp()),higher={k:scaled(v,scale,shift) for k,v in higher.items()}),
             'turtle soup':turtle(current,int(now.timestamp()),regular_end=regular_end),
             'silver bullet':next((d for d in silver(rows,now=int(now.timestamp()))['days'] if d['date']==str(today)),{}),
             'opening range':{},'technicals':{}}
    result=[];observations={}
    for name in STRATEGIES:
        # QQQ is only the analytics function's internal scope identifier. Its
        # prices are normalized FX bars; it never reaches a broker payload.
        plans=candidates(name,'QQQ',current,now,reports[name],window_fn=window_fn,regular_end=regular_end)
        observations[name]=f'{len(plans)} fresh {session} candidate(s)' if window_fn(name,now) else f'WAIT - outside {session} strategy entry window'
        for p in plans:
            p={**p,'pair':pair,'strategy':'fx_'+p['strategy'],'session':session,'session_id':c['id']}
            p['confirmed_at']-=shift;p['last_closed_at']-=shift
            p.pop('symbol')
            for key in ('entry','stop','target'):p[key]=str((Decimal(str(p[key]))/scale).quantize(tick))
            result.append(p)
    return result,observations
