"""Explicit experimental paper adapters. Signals are not fills or evidence of edge."""
from datetime import datetime,time,timedelta
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
from . import ict
from .trading_risk import TARGET_R,volatility_stop

STRATEGIES={
    'ict':'ict_2022_v3','smc':'smc_retest_v1','silver bullet':'silver_bullet_v1',
    'turtle soup':'turtle_soup_intraday_v1','amd':'amd_ict_v1',
    'opening range':'opening_range_v1','technicals':'ema_cross_v1',
}


def stamp(value):
    return int(datetime.strptime(value,'%Y-%m-%d %H:%M ET').replace(tzinfo=ict.NY).timestamp())+60


def bounds(name,now):
    local=now.astimezone(ict.NY);clock=local.time()
    if local.weekday()>=5:return None
    windows={'ict':((time(9,30),time(11)),(time(13,30),time(15,10))),
             'silver bullet':((time(10),time(11)),(time(14),time(15))),
             'turtle soup':((time(9,50),time(15,10)),),'smc':((time(9,35),time(15,10)),),
             'amd':((time(9,30),time(11)),),'opening range':((time(10),time(11)),),
             'technicals':((time(10,20),time(15,10)),)}
    window=next((w for w in windows.get(name,()) if w[0]<=clock<w[1]),None)
    if not window:return None
    exit_clock=window[1] if name=='silver bullet' else time(12) if name=='opening range' else time(15,55)
    return datetime.combine(local.date(),window[1],ict.NY),datetime.combine(local.date(),exit_clock,ict.NY)


def cost_target(entry,stop,side):
    # TARGET_R net risk units with the existing $0.02 round-trip per-share allowance.
    e,s=Decimal(str(entry)),Decimal(str(stop));distance=TARGET_R*abs(e-s)+(TARGET_R+1)*Decimal('.02')
    target=e+distance if side=='long' else e-distance
    return float(target.quantize(Decimal('.01'),rounding=ROUND_CEILING if side=='long' else ROUND_FLOOR))


def build(name,symbol,side,entry,stop,confirmed,identity,bars,now,target=None,*,window_fn=bounds):
    if name not in STRATEGIES or symbol not in ('QQQ','DIA') or side not in ('long','short'):return None
    window=window_fn(name,now)
    if not window or not 0<=now.timestamp()-confirmed<=90:return None
    closed=[b for b in bars if b['t']+60<=now.timestamp()]
    if len(closed)<3 or any(b['t']-a['t']!=60 for a,b in zip(closed[-3:],closed[-2:])):return None
    if not 0<=now.timestamp()-closed[-1]['t']-60<=90:return None
    if ict.ny(confirmed).date()!=now.astimezone(ict.NY).date():return None
    entry=round(float(entry),2)
    stop=volatility_stop(closed,side,entry,float(stop))
    if stop is None:return None
    stop=float(Decimal(str(stop)).quantize(Decimal('.01'),rounding=ROUND_FLOOR if side=='long' else ROUND_CEILING))
    target=round(float(target),2) if target is not None else cost_target(entry,stop,side)
    if not (0<stop<entry<target if side=='long' else 0<target<entry<stop):return None
    return dict(name=name,strategy=STRATEGIES[name],symbol=symbol,side=side,entry=entry,stop=stop,target=target,
                confirmed_at=confirmed,last_closed_at=closed[-1]['t']+60,signal_id=str(identity))


def trend_allows(plan,bars,now):
    """Stocks: trade with the day's trend (the 5-month QQQ replay's best variant, 2026-11-03). A long needs the last
    close above the session VWAP and, from 10:00, an up first half hour; a short the reverse. No volume, no trade."""
    today=now.astimezone(ict.NY).date()
    session=[b for b in bars if b['t']+60<=now.timestamp() and ict.ny(b['t']).date()==today and time(9,30)<=ict.ny(b['t']).time()<time(16)]
    volume=sum(float(b.get('v') or 0) for b in session)
    if not session or volume<=0:return False
    vwap=sum((float(b['h'])+float(b['l'])+float(b['c']))/3*float(b.get('v') or 0) for b in session)/volume
    first=[b for b in session if ict.ny(b['t']).time()<time(10)]
    half=float(first[-1]['c'])-float(first[0]['o']) if len(first)>=25 and ict.ny(session[-1]['t']).time()>=time(9,59) else None
    long=plan['side']=='long';last=float(session[-1]['c'])
    return (last>vwap if long else last<vwap) and (half is None or (half>0 if long else half<0))


def candidates(name,symbol,bars,now,report=None,*,window_fn=bounds,regular_end=time(16)):
    """Use only current closed bars. Historical simulated fills never become orders."""
    report=report or {};result=[]
    closed=[b for b in bars if b['t']+60<=now.timestamp() and ict.ny(b['t']).date()==now.astimezone(ict.NY).date()
            and time(9,30)<=ict.ny(b['t']).time()<regular_end]
    if not closed:return result
    last=closed[-1];at=last['t']+60
    def add(side,entry,stop,confirmed,identity,target=None):
        plan=build(name,symbol,side,entry,stop,confirmed,identity,closed,now,target,window_fn=window_fn)
        if plan:result.append(plan)
    if name in ('ict','silver bullet','amd'):
        if report.get('stale'):return result
        for setup in report.get('setups') or []:
            if name=='amd' and not setup.get('amd_aligned'):continue
            if setup.get('status')!='waiting for entry' or setup.get('grade') not in ('A','B'):continue
            confirmed=stamp(setup['decided_at'])
            add(setup['direction'],setup['entry'],setup['stop'],confirmed,
                setup['fvg_at']+'|'+setup['direction'],setup['target'])
    elif name=='turtle soup':
        for setup in report.get('setups') or []:
            add(setup['direction'],setup['entry'],setup['stop'],setup['confirmed_at']+60,
                str(setup['confirmed_at'])+'|'+setup['direction'])
    elif name=='smc':
        if report.get('stale'):return result
        alignment=(report.get('higher_timeframes') or {}).get('alignment')
        for zone in (report.get('order_blocks') or [])+(report.get('breaker_blocks') or []):
            if zone.get('status')!='retested' or zone.get('retested_at')!=at or zone['confirmed_at']>=last['t']:continue
            bullish=zone['direction']=='bullish';side='long' if bullish else 'short'
            if alignment!=zone['direction']:continue
            rejection=last['c']>zone['high'] and last['c']>last['o'] if bullish else last['c']<zone['low'] and last['c']<last['o']
            if rejection:
                stop=min(last['l'],zone['low'])-.01 if bullish else max(last['h'],zone['high'])+.01
                add(side,last['c'],stop,at,str(zone['origin'])+'|'+str(zone['confirmed_at'])+'|'+side)
    elif name=='opening range':
        opening=[b for b in closed if ict.ny(b['t']).time()<time(10)]
        if len(opening)!=30 or ict.ny(opening[0]['t']).time()!=time(9,30) or any(b['t']-a['t']!=60 for a,b in zip(opening,opening[1:])):return result
        following=[b for b in closed if ict.ny(b['t']).time()>=time(10)]
        if not following or any(b['t']-a['t']!=60 for a,b in zip(closed,closed[1:])):return result
        high=max(b['h'] for b in opening);low=min(b['l'] for b in opening)
        first=next((b for b in following if b['c']>high or b['c']<low),None)
        if first is last:
            add('long' if last['c']>high else 'short',last['c'],(high+low)/2,at,str(now.date())+'|opening')
    elif name=='technicals':
        from .chart_analysis import indicator_snapshot,ema
        if len(closed)<51 or any(b['t']-a['t']!=60 for a,b in zip(closed[-51:],closed[-50:])):return result
        history=closed[-500:]
        data=indicator_snapshot(history,now=int(now.timestamp()));values=[b['c'] for b in history]
        old_fast,old_slow=ema(values[:-1],20),ema(values[:-1],50)
        if data['stale'] or not data['atr14']:return result
        long=old_fast<=old_slow and data['ema20']>data['ema50'] and 50<=data['rsi14']<=70
        short=old_fast>=old_slow and data['ema20']<data['ema50'] and 30<=data['rsi14']<=50
        if long or short:
            side='long' if long else 'short';stop=last['c']+(-1 if long else 1)*1.5*data['atr14']
            add(side,last['c'],stop,at,str(at)+'|'+side)
    return result
