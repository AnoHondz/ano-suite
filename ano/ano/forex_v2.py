"""Native-clock FX research rules. Deterministic; no execution or account access.

Version changes require new chronological validation. Scores are checklist counts,
never probabilities. All signal evidence is formed from completed source candles.
"""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from zoneinfo import ZoneInfo
import hashlib
from .forex_sessions import SESSIONS, pricing
from .smc import closed_bars
from .trading_risk import TARGET_R,volatility_stop

VERSION='fx-native-v2'
UTC=timezone.utc
# Explicit local windows, not shifted equity clocks. These are research choices.
WINDOWS={
    'london':{'zone':'Europe/London','open':time(8),'entry_end':time(16,50),'flat':time(16,55),
              'ict':((time(8),time(10)),), 'range_start':time(0),'range_end':time(8)},
    'new_york':{'zone':'America/New_York','open':time(9,30),'entry_end':time(15,10),'flat':time(15,55),
                'ict':((time(9,30),time(11)),(time(13,30),time(15))), 'range_start':time(0),'range_end':time(9,30)},
    'tokyo':{'zone':'Asia/Tokyo','open':time(9),'entry_end':time(14,50),'flat':time(14,55),
             'ict':((time(9),time(11)),), 'range_start':time(6),'range_end':time(9)},
}


def session_context(name,now):
    spec=WINDOWS[name];zone=ZoneInfo(spec['zone']);local=now.astimezone(zone)
    def at(clock):return datetime.combine(local.date(),clock,zone).timestamp()
    return {'id':name+':'+str(local.date()),'name':name,'date':str(local.date()),
            'open':at(spec['open']),'entry_end':at(spec['entry_end']),'flatten_at':at(spec['flat']),
            'range_start':at(spec['range_start']),'range_end':at(spec['range_end']),
            'active':local.weekday()<5 and at(spec['open'])<=now.timestamp()<at(spec['entry_end']),
            'ict_active':any(a<=local.time()<b for a,b in spec['ict'])}


def pivots(rows,n=2):
    found=[]
    for i in range(n,len(rows)-n):
        neighborhood=rows[i-n:i+n+1]
        if any(b['t']-a['t']!=60 for a,b in zip(neighborhood,neighborhood[1:])):continue
        other=rows[i-n:i]+rows[i+1:i+n+1]
        for kind,key in (('high','h'),('low','l')):
            if all(rows[i][key]>b[key] if kind=='high' else rows[i][key]<b[key] for b in other):
                found.append({'kind':kind,'price':rows[i][key],'at':rows[i]['t'],
                              'confirmed_at':rows[i+n]['t']+60})
    return found


def weekend_closed_seconds(a,b):
    """Seconds of the weekly FX closure (Friday 17:00 to Sunday 17:00 New York) inside [a, b].

    Without this, Friday's last 4-hour bar looks two days stale until Sunday's first bar
    completes, and every Monday session starts with no 4-hour structure at all."""
    zone=ZoneInfo('America/New_York');total=0.0
    day=datetime.fromtimestamp(a,zone).date()-timedelta(days=7)
    end=datetime.fromtimestamp(b,zone).date()
    while day<=end:
        if day.weekday()==4:
            start=datetime.combine(day,time(17),zone).timestamp()
            stop=datetime.combine(day+timedelta(days=2),time(17),zone).timestamp()
            total+=max(0.0,min(b,stop)-max(a,start))
        day+=timedelta(days=1)
    return total


def structural_bias(rows,now,interval):
    # HTF pivots use interval-aware spacing and two bars on either side.
    bars=closed_bars(rows,now.timestamp(),interval)
    age=now.timestamp()-bars[-1]['t']-interval if bars else 0
    if bars:age-=weekend_closed_seconds(bars[-1]['t']+interval,now.timestamp())
    if len(bars)<9 or age>max(3*interval,3*86400 if interval==86400 else 0):
        return {'state':'unavailable','reason':'missing or stale completed structure'}
    points=[]
    for i in range(2,len(bars)-2):
        neighbors=bars[i-2:i]+bars[i+1:i+3]
        for kind,key in (('high','h'),('low','l')):
            if all(bars[i][key]>b[key] if kind=='high' else bars[i][key]<b[key] for b in neighbors):
                points.append({'kind':kind,'price':bars[i][key],'confirmed_at':bars[i+2]['t']+interval})
    # Latest confirmed close through a known pivot defines structure. Later
    # opposing breaks change it. A candle's colour alone is not a structure vote.
    direction=None;broken=set();last_event=None
    for b in bars:
        for j,p in enumerate(points):
            if j in broken or p['confirmed_at']>b['t']:continue
            if b['c']>p['price'] if p['kind']=='high' else b['c']<p['price']:
                direction='bullish' if p['kind']=='high' else 'bearish';broken.add(j)
                last_event={'level':p['price'],'at':b['t']+interval}
    return {'state':direction or 'unconfirmed','last_break':last_event,'last_closed_at':bars[-1]['t']+interval}


def bias_context(higher,now):
    states={k:structural_bias(higher.get(k,[]),now,interval) for k,interval in [('daily',86400),('4h',14400),('1h',3600)]}
    # Daily structure supplies context; H4 establishes direction and H1 must
    # agree. A daily opposing break blocks; absent daily structure stays WAIT.
    d,h,m=(states[k]['state'] for k in ('daily','4h','1h'))
    direction=h if h in ('bullish','bearish') and m==h and d in (h,'unconfirmed') else None
    return {'timeframes':states,'direction':direction,
            'reason':'H4/H1 confirmed structure with no opposing daily break' if direction else 'higher-timeframe structure incomplete or conflicting'}


def known_levels(rows,c,now):
    levels=[]
    for p in pivots(rows[-2000:]):levels.append({**p,'source':'confirmed swing'})
    zone=ZoneInfo(WINDOWS[c['name']]['zone']);day=datetime.fromtimestamp(c['open'],zone).date()
    # Prior local weekday's completed 24h range; partial source coverage cannot
    # masquerade as a verified prior-day high/low.
    previous=day-timedelta(days=1)
    while previous.weekday()>=5:previous-=timedelta(days=1)
    a=datetime.combine(previous,time(0),zone).timestamp();b=datetime.combine(previous+timedelta(days=1),time(0),zone).timestamp()
    for label,start,end in [('prior local day',a,b),('pre-session range',c['range_start'],c['range_end'])]:
        sample=[r for r in rows if start<=r['t'] and r['t']+60<=end]
        expected=int((end-start)/60)
        if end>now.timestamp() or len(sample)!=expected or any(y['t']-x['t']!=60 for x,y in zip(sample,sample[1:])):continue
        for kind,key in [('high','h'),('low','l')]:
            levels.append({'kind':kind,'price':max(r[key] for r in sample) if kind=='high' else min(r[key] for r in sample),
                           'at':end-60,'confirmed_at':end,'source':label})
    for p in levels:
        p['taken_at']=next((r['t']+60 for r in rows if r['t']>=p['confirmed_at'] and
            (r['h']>p['price'] if p['kind']=='high' else r['l']<p['price'])),None)
    return levels


def untaken(level,rows,until):
    if 'taken_at' in level:return level['taken_at'] is None or level['taken_at']>until
    return not any((r['h']>level['price'] if level['kind']=='high' else r['l']<level['price'])
                   for r in rows if r['t']>=level['confirmed_at'] and r['t']+60<=until)


def objective(rows,levels,side,entry,stop,at,obstacles=(),front=0.0):
    """Nearest untaken opposing pool. front: the target sits this far in front of it (stops rest just beyond a pool,
    so price often tags it and turns); the reward to that front-run price must still be TARGET_R."""
    opposite='high' if side=='long' else 'low';risk=abs(entry-stop)
    candidates=[p for p in levels if p['kind']==opposite and p['confirmed_at']<=at and
                (p['price']>entry if side=='long' else p['price']<entry) and untaken(p,rows,at)]
    # The nearest opposing pool is the objective. Do not jump over a nearer
    # obstacle just to manufacture a 2R opportunity.
    nearest=min(candidates,key=lambda p:abs(p['price']-entry),default=None)
    if not nearest:return None,'no untouched opposing liquidity'
    if not risk or abs(nearest['price']-entry)-front<float(TARGET_R)*risk:return None,f'nearest liquidity offers less than {TARGET_R}R before costs'
    for z in obstacles:
        if z['direction']==('bearish' if side=='long' else 'bullish') and z['status']!='invalidated':
            if z['low']<=entry<=z['high']:return None,'entry inside opposing block'
            edge=z['low'] if side=='long' else z['high']
            if min(entry,nearest['price'])<edge<max(entry,nearest['price']):return None,'opposing block before target'
    if front:
        nearest={**nearest,'pool':nearest['price'],'price':nearest['price']-front if side=='long' else nearest['price']+front}
    return nearest,None


def make_plan(name,pair,side,entry,stop,target,at,c,identity,*,expires=None,evidence=None):
    tick,_,_=pricing(pair)
    def rounded(v,up):return str(Decimal(str(v)).quantize(tick,rounding=ROUND_CEILING if up else ROUND_FLOOR))
    e=rounded(entry,side=='short');s=rounded(stop,side=='short');t=rounded(target['price'],side=='short')
    expires=min(expires or at+90,c['entry_end'])
    body=f'{VERSION}|{c["id"]}|{pair}|{name}|{identity}|{side}'
    return {'version':VERSION,'name':name,'strategy':'fx_'+name+'_native_v2','pair':pair,'session':c['name'],'session_id':c['id'],
            'side':side,'entry':e,'stop':s,'target':t,'confirmed_at':at,'last_closed_at':at,
            'expires_at':expires,'flatten_at':c['flatten_at'],'entry_type':'LIMIT' if name=='ict' else 'MARKET',
            'signal_id':hashlib.sha256(body.encode()).hexdigest()[:28],
            'target_evidence':target,'evidence':evidence or {},'probability':None,
            'validation':'unvalidated research rules; checklist grades are not probabilities'}


def scan(pair,rows,higher,now,session):
    c=session_context(session,now);diagnostics=[];plans=[]
    def reject(strategy,reason,at=None):diagnostics.append({'strategy':strategy,'reason':reason,'at':at or now.timestamp()})
    if pair not in SESSIONS[session]['pairs'] or not c['active']:
        return {'version':VERSION,'plans':[],'diagnostics':[{'strategy':'all','reason':'outside native session/pair scope'}],'context':c}
    rows=closed_bars(rows,now.timestamp())
    if not rows or not 0<=now.timestamp()-rows[-1]['t']-60<=90:
        return {'version':VERSION,'plans':[],'diagnostics':[{'strategy':'all','reason':'stale minute data'}],'context':c}
    if len(rows)<60 or any(b['t']-a['t']!=60 for a,b in zip(rows[-60:],rows[-59:])):
        return {'version':VERSION,'plans':[],'diagnostics':[{'strategy':'all','reason':'incomplete recent minute path'}],'context':c}
    current=[r for r in rows if r['t']>=c['open']];levels=known_levels(rows,c,now);bias=bias_context(higher,now)
    if not bias['direction']:reject('smc',bias['reason'])
    from .smc import analyze
    structure=analyze(rows,now=now.timestamp(),higher={},session_zone=ZoneInfo(WINDOWS[session]['zone']))
    last=rows[-1];at=last['t']+60;tick=float(pricing(pair)[0])
    from .forex_order_policy import FRONT_RUN_PIPS
    front=float(FRONT_RUN_PIPS*pricing(pair)[1])
    for z in structure['order_blocks']+structure['breaker_blocks']:
        if z.get('status')!='retested' or z.get('retested_at')!=at or z['confirmed_at']>=last['t']:continue
        side='long' if z['direction']=='bullish' else 'short'
        if bias['direction']!=z['direction']:reject('smc',bias['reason']);continue
        rejection=last['c']>z['high'] and last['c']>last['o'] if side=='long' else last['c']<z['low'] and last['c']<last['o']
        if not rejection:reject('smc','retest without completed rejection');continue
        stop=volatility_stop(rows,side,last['c'],min(last['l'],z['low'])-tick if side=='long' else max(last['h'],z['high'])+tick)
        if stop is None:reject('smc','not enough 5-minute history for a volatility stop');continue
        target,reason=objective(rows,levels,side,last['c'],stop,at,structure['order_blocks']+structure['breaker_blocks'],front=front)
        if reason:reject('smc',reason);continue
        plans.append(make_plan('smc',pair,side,last['c'],stop,target,at,c,str(z['origin']),evidence={'bias':bias,'zone':z}))
    if not any(p['name']=='smc' for p in plans):reject('smc','no qualifying current retest')
    # Native ICT: confirmed liquidity raid -> opposite pivot close -> displaced
    # three-minute gap. Decisions are made only from the supplied closed prefix.
    if not c['ict_active']:reject('ict','outside native ICT window')
    else:
        points=pivots(rows,n=3)
        for i,sweep in enumerate(current):
            if sweep['t']<at-31*60:continue
            sweep_clock=datetime.fromtimestamp(sweep['t'],ZoneInfo(WINDOWS[session]['zone'])).time()
            if not any(a<=sweep_clock<b for a,b in WINDOWS[session]['ict']):continue
            for pool in levels:
                short=pool['kind']=='high';side='short' if short else 'long'
                if not (sweep['h']>pool['price'] if short else sweep['l']<pool['price']):continue
                if pool['confirmed_at']>sweep['t'] or not untaken(pool,rows,sweep['t']):continue
                before=[p for p in points if p['kind']==('low' if short else 'high') and p['confirmed_at']<=sweep['t']]
                if not before:reject('ict','sweep without confirmed opposite pivot',sweep['t']);continue
                pivot=before[-1];leg=current[i:i+31]
                shift=next((j for j,b in enumerate(leg) if (b['c']<pivot['price'] if short else b['c']>pivot['price'])),None)
                if shift is None:reject('ict','sweep awaiting structure shift',sweep['t']);continue
                path=leg[:shift+1];extreme=max(b['h'] for b in path) if short else min(b['l'] for b in path)
                extreme_index=max(range(len(path)),key=lambda j:path[j]['h']) if short else min(range(len(path)),key=lambda j:path[j]['l'])
                prior=[b for b in rows if b['t']<sweep['t']][-20:]
                avg=sum(abs(b['c']-b['o']) for b in prior)/len(prior) if prior else 0
                bodies=[abs(b['c']-b['o']) for b in path if (b['c']<b['o'] if short else b['c']>b['o'])]
                if not avg or max(bodies,default=0)<1.5*avg:reject('ict','shift lacks displacement',sweep['t']);continue
                gaps=[]
                for k in range(extreme_index+2,min(len(leg),shift+2)):
                    a,b=leg[k-2],leg[k]
                    if b['t']-a['t']!=120:continue
                    if short and b['h']<a['l']:gaps.append((b['h'],a['l'],b['t']+60))
                    if not short and b['l']>a['h']:gaps.append((a['h'],b['l'],b['t']+60))
                if not gaps:reject('ict','shift without fair value gap',sweep['t']);continue
                lo,hi,gap_at=gaps[-1];decision=max(gap_at,path[-1]['t']+60)
                if decision!=at:continue  # pending limit lifetime belongs to the order manager, not rescan
                entry=(lo+hi)/2;stop=volatility_stop(rows,side,entry,extreme+tick if short else extreme-tick)
                if stop is None:reject('ict','not enough 5-minute history for a volatility stop',sweep['t']);continue
                target,reason=objective(rows,levels,side,entry,stop,decision,front=front)
                if reason:reject('ict',reason);continue
                plans.append(make_plan('ict',pair,side,entry,stop,target,decision,c,
                    str(sweep['t'])+'|'+str(gap_at),expires=decision+30*60,
                    evidence={'swept_pool':pool,'shift':pivot,'fvg':[lo,hi],'displacement_threshold':1.5}))
        if not any(p['name']=='ict' for p in plans):reject('ict','no newly confirmed qualifying setup')
    unique={p['signal_id']:p for p in plans}
    return {'version':VERSION,'plans':list(unique.values()),'diagnostics':diagnostics,'context':c,
            'bias':bias,'known_liquidity_levels':len(levels),'mode':'shadow; no automatic promotion'}
