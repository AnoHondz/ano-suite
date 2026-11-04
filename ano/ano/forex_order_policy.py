"""Explicit FX order economics and separate bounded submission/fill budgets."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
from .forex_sessions import pricing
from .trading_risk import MAX_ENTRIES_DAY, TARGET_R, number, size_paper_trade

# User 2026-11-02: up to two open at once on different pairs, and 'technicals' (0 for 3 on forex) switched off.
# User 2026-11-03 ("fix them"): at most 4 forex entries a day (7 a day lost $552 with no proven edge), counted
# from the latest fresh start, and only when the spread is at most 1.5 pips (wider spreads ate 20-40% of the risk).
MAX_FILLS_DAY=4
MAX_SUBMISSIONS_SESSION=4
MAX_SUBMISSIONS_DAY=8
MAX_SPREAD_PIPS=Decimal('1.5')
MAX_OPEN_TRADES=2
RISK_PER_TRADE=Decimal('.0025')
DISABLED_STRATEGIES=('technicals',)
UNRESOLVED=('submitting','uncertain','pending_cancel_uncertain')
PROTECTIVE=('TAKE_PROFIT','STOP_LOSS','TRAILING_STOP_LOSS','GUARANTEED_STOP_LOSS')
# User 2026-11-03 ("wait for further confirmation and have good entry and exit points"): 5 of the last 7 forex stops hit
# within 12 minutes of entry (chasing spikes), and every winner but one was cut by a time exit.
CONFIRM_BARS=5          # a legacy signal waits up to 5 closed minutes for a confirming candle, else it is dropped
SPIKE_RANGE=1.5         # a signal candle over 1.5x the last 20 candles' average range is a spike: no chase
HOLD_MIN_R=Decimal('.5')  # at a time exit, a trade up 0.5R+ is held with its exit locked at entry + 0.1R
HOLD_MAX_SECONDS=6*3600   # ... for at most 6 hours, and never past 15:55 New York
FRONT_RUN_PIPS=Decimal('1.5')  # liquidity targets sit 1.5 pips in front of the pool, never on or past it


def confirmation(plan,rows):
    """('wait'|'go'|reason to drop, plan). rows: closed 1-minute mid candles, oldest first.

    The signal candle is the last candle closed at the plan's confirmation. It must not be a spike. Then the first
    later candle that closes in the trade's direction without breaking the signal candle's extreme confirms it;
    a later candle through the stop, or CONFIRM_BARS candles without confirmation, drop it. Only a confirmation on
    the newest closed candle is acted on, so the entry is never late."""
    long=plan['side']=='long';stop=float(plan['stop']);at=plan['confirmed_at']
    signal=next((i for i in range(len(rows)-1,-1,-1) if rows[i]['t']+60<=at),None)
    if signal is None:return 'wait',plan
    bar=rows[signal];prior=rows[max(0,signal-20):signal]
    average=sum(r['h']-r['l'] for r in prior)/len(prior) if prior else 0
    if average and bar['h']-bar['l']>SPIKE_RANGE*average:return 'spike signal candle: not chasing',plan
    after=rows[signal+1:]
    for i,b in enumerate(after[:CONFIRM_BARS]):
        if (b['l']<=stop) if long else (b['h']>=stop):return 'invalidated before confirmation',plan
        if (b['c']>b['o'] and b['l']>=bar['l']) if long else (b['c']<b['o'] and b['h']<=bar['h']):
            if signal+1+i!=len(rows)-1:return 'confirmation missed between scans',plan
            closed=b['t']+60
            tick=pricing(plan['pair'])[0]
            return 'go',{**plan,'confirmed_at':closed,'last_closed_at':closed,
                         'entry':str(Decimal(str(b['c'])).quantize(tick)),
                         'confirmation':{'signal_at':bar['t'],'confirmed_by':b['t'],'signal_entry':plan['entry']}}
    if len(after)>=CONFIRM_BARS:return f'no confirmation within {CONFIRM_BARS} minutes',plan
    return 'wait',plan


def hold_winner(long,entry,stop,mark,now_ts,flatten_at):
    """At a time exit: (locked exit level, new flatten time) when the trade is up HOLD_MIN_R or more, else None."""
    from zoneinfo import ZoneInfo
    entry,stop,mark=number(entry),number(stop),number(mark);risk=abs(entry-stop)
    if risk<=0 or ((mark-entry) if long else (entry-mark))<HOLD_MIN_R*risk:return None
    ny=ZoneInfo('America/New_York');local=datetime.fromtimestamp(now_ts,ny)
    until=min(local.replace(hour=15,minute=55,second=0,microsecond=0).timestamp(),now_ts+HOLD_MAX_SECONDS)
    if until<=max(now_ts,flatten_at):return None
    lock=entry+risk*Decimal('.1') if long else entry-risk*Decimal('.1')
    return lock,until


def exposure(pair,rows,trades,orders):
    """Why a new entry must wait, or None: never while an intent is unresolved, never a second
    position on one pair, at most MAX_OPEN_TRADES trades / entry orders (a trade's own stop and
    target are not entries)."""
    if any(r['state'] in UNRESOLVED for r in rows):return 'unresolved order intent'
    # Anything Ano did not open blocks new entries: it is never adopted, closed or traded alongside.
    trade_ids={str(r['trade_id']) for r in rows if r.get('trade_id')};row_ids={r['id'] for r in rows}
    entries=[o for o in orders if o.get('type') not in PROTECTIVE]
    if (any(str(t.get('id')) not in trade_ids for t in trades)
            or any((o.get('clientExtensions') or {}).get('id') not in row_ids for o in entries)):
        return 'account exposure not opened by Ano'
    pairs=[t.get('instrument') for t in trades]+[o.get('instrument') for o in entries]
    if pair in pairs:return 'already exposed on this pair'
    if len(pairs)>=MAX_OPEN_TRADES:return f'{MAX_OPEN_TRADES} trades or entry orders already open'
    return None


def open_risk(nav,trades,orders):
    """Planned risk of what is already open: each trade or entry order was sized to RISK_PER_TRADE."""
    count=len(trades)+sum(1 for o in orders if o.get('type') not in PROTECTIVE)
    return number(nav)*RISK_PER_TRADE*count
SUBMISSION_COOLDOWN_SECONDS=60
# Size comes from the 0.25% risk rule and the 3x-notional / margin caps; this is only a broker-scale bound.
# (A former 100,000-unit cap silently cut risk to ~0.04% per trade, so forex was not comparable to stocks.)
MAX_UNITS=1000000
# A stop inside the normal bid/ask flicker is a near-certain loss: at least 3x the spread and 5 pips.
MIN_STOP_SPREADS=3
MIN_STOP_PIPS=5


def budget(rows,session_id,verified_cancelled=()):
    submitted=[r for r in rows if r['state']!='not_sent']
    # A terminal status alone does not establish zero fills. Cancelled intents
    # remain reserved until the broker cancellation receipt has been verified.
    reserved=[r for r in submitted if r.get('trade_id') or r['state'] not in ('cancelled','rejected')
              or (r['state']=='cancelled' and r['id'] not in verified_cancelled)]
    session=[r for r in submitted if json.loads(r['plan']).get('session_id','new_york:'+r['day'])==session_id]
    reason=(f'{MAX_FILLS_DAY} filled/reserved entries in risk day' if len(reserved)>=MAX_FILLS_DAY else
            'bounded daily submission cap' if len(submitted)>=MAX_SUBMISSIONS_DAY else
            'bounded session submission cap' if len(session)>=MAX_SUBMISSIONS_SESSION else None)
    return {'fills_or_reserved':len(reserved),'submissions_day':len(submitted),'submissions_session':len(session),'reason':reason}


def prepare(plan,quote,instrument,account,baseline,*,entries=0,losses=0,existing_risk=0):
    """Size against worst permitted entry; never move a structural target."""
    tick,pip,_=pricing(plan['pair']);long=plan['side']=='long'
    bid,ask=number(quote['bid']),number(quote['ask']);spread=ask-bid
    if not 0<bid<=ask or spread>MAX_SPREAD_PIPS*pip:raise ValueError(f'invalid quote or spread above {MAX_SPREAD_PIPS}-pip cap')
    stop,target=number(plan['stop']),number(plan['target']);reference=number(plan['entry'])
    kind=plan.get('entry_type','MARKET')
    if kind=='LIMIT':
        if not (stop<reference<target if long else target<reference<stop):raise ValueError('invalid limit bracket')
        # Do not turn a missed retracement into a marketable chasing order.
        if (ask<=reference if long else bid>=reference):raise ValueError('limit is already marketable; require a new setup')
        worst=reference
    elif kind=='MARKET':
        entry=ask if long else bid
        if abs(entry-reference)>3*pip:raise ValueError('price moved beyond plan tolerance')
        allowance=min(pip*Decimal('.5'),abs(entry-stop)*Decimal('.1'))
        worst=(entry+allowance if long else entry-allowance).quantize(tick,rounding=ROUND_CEILING if long else ROUND_FLOOR)
    else:raise ValueError('unknown entry type')
    if not (0<stop<worst<target if long else 0<target<worst<stop):raise ValueError('price outside structural bracket')
    if abs(worst-stop)<max(MIN_STOP_SPREADS*spread,MIN_STOP_PIPS*pip):raise ValueError('stop closer than 3x spread or 5 pips')
    loss,gain=number(quote['loss']),number(quote['gain'])
    cost=(spread+pip*Decimal('.2'))*loss
    effective=(worst+(target-worst)*gain/loss).quantize(tick,rounding=ROUND_FLOOR if long else ROUND_CEILING)
    check=size_paper_trade(equity=account['NAV'],session_equity=baseline['nav'],
        daily_pnl=number(account['NAV'])-number(baseline['nav']),existing_risk=existing_risk,entries=entries,
        consecutive_losses=losses,side='buy' if long else 'sell',entry=worst,stop=stop,target=effective,
        point_value=1,fx_rate=loss,tick_size=tick,minimum_quantity=instrument['minimumTradeSize'],
        quantity_increment=1,round_trip_cost_per_unit=cost,minimum_reward_risk=TARGET_R)
    if not check['risk_check_passed']:raise ValueError('net risk check: '+'; '.join(check['reasons']))
    margin=number(instrument['marginRate'])
    if not 0<margin<=1:raise ValueError('invalid margin requirement')
    qty=min(int(check['quantity']),MAX_UNITS,int(number(instrument['maximumOrderUnits'])),
            int(number(account['marginAvailable'])*Decimal('.8')/(worst*loss*margin)),
            int(number(account['NAV'])*3/(worst*loss)))
    if qty<number(instrument['minimumTradeSize']):raise ValueError('insufficient whole-unit capacity')
    return {**plan,'units':qty if long else -qty,'worst_entry':str(worst),'bound':str(worst),
            'planned_risk_usd':str((abs(worst-stop)*loss+cost)*qty),
            'cost_allowance_per_unit':str(cost),'risk_evidence':check}


def payload(identity,prepared):
    """Reviewable OANDA order payload, without credentials or transport."""
    import re
    if not re.fullmatch(r'ano-fx-[a-f0-9]{28}',identity):raise ValueError('invalid client identity')
    from .oanda_practice import PAIRS,strategy_extensions
    p=prepared
    if p['pair'] not in PAIRS:raise ValueError('unsupported pair')
    if not (p.get('risk_evidence') or {}).get('risk_check_passed'):raise ValueError('sized risk evidence required')
    units=p['units'];tick,_,_=pricing(p['pair'])
    if type(units)!=int or not 1<=abs(units)<=MAX_UNITS:raise ValueError('invalid quantity')
    for k in ('entry','stop','target','bound'):
        if number(p[k])%tick:raise ValueError('invalid instrument precision')
    result={'instrument':p['pair'],'units':str(units),'positionFill':'OPEN_ONLY',
        'clientExtensions':strategy_extensions(identity,p),'tradeClientExtensions':strategy_extensions(identity,p),
        'stopLossOnFill':{'price':str(p['stop']),'timeInForce':'GTC'},
        'takeProfitOnFill':{'price':str(p['target']),'timeInForce':'GTC'}}
    if p['entry_type']=='LIMIT':
        result.update(type='LIMIT',price=str(p['entry']),timeInForce='GTD',triggerCondition='DEFAULT',
            gtdTime=datetime.fromtimestamp(p['expires_at'],timezone.utc).isoformat().replace('+00:00','Z'))
    elif p['entry_type']=='MARKET':result.update(type='MARKET',timeInForce='FOK',priceBound=p['bound'])
    else:raise ValueError('unknown order type')
    return result
