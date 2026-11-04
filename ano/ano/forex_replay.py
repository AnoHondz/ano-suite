"""Prefix-only, bid/ask FX replay. Research output cannot enable execution."""
from collections import Counter
from datetime import datetime, timezone
import hashlib,json,math
from zoneinfo import ZoneInfo
from .forex_v2 import scan,VERSION
from .forex_sessions import pricing
from .forex_order_policy import prepare


def replay(pair,session,candles,higher,*,news=None,scanner=scan,slippage_pips=.2,evaluation_start=None):
    if not 0<=slippage_pips<=3:raise ValueError('invalid slippage assumption')
    if len(candles)>10000:raise ValueError('split replay into bounded chronological files')
    seen=set();pending=None;position=None;trades=[];diagnostics=Counter();history=[];risk_days={}
    pip=float(pricing(pair)[1]);slip=slippage_pips*pip;previous=None
    for bar in candles:
        if previous is not None and bar['t']<=previous:raise ValueError('unordered replay')
        if previous is not None and bar['t']-previous!=60:
            diagnostics['missing minute path']+=1
            if pending:diagnostics['pending cancelled on missing minute path']+=1;pending=None
            if position:
                trades.append({**position,'status':'unresolved_data_gap','exit_at':bar['t'],'net_r':None});position=None
        previous=bar['t']
        for side in ('bid','ask'):
            p=bar[side]
            if any(not isinstance(p.get(k),(int,float)) or isinstance(p[k],bool) or not math.isfinite(p[k]) for k in 'ohlc'):
                raise ValueError('invalid executable-side candle')
            if not 0<p['l']<=min(p['o'],p['c'])<=max(p['o'],p['c'])<=p['h']:raise ValueError('invalid OHLC')
        if any(bar['bid'][k]>bar['ask'][k] for k in 'ohlc'):raise ValueError('crossed sides')
        now=datetime.fromtimestamp(bar['t']+60,timezone.utc)
        day=str(datetime.fromtimestamp(bar['t'],ZoneInfo('America/New_York')).date())
        budget=risk_days.setdefault(day,{'fills':0,'submissions':0,'session_submissions':{},'r':0.,'streak':0})
        if pending and bar['t']>=pending['confirmed_at']:
            relevant=set(pair.split('_'))
            blocked_news=news is None or any(e['currency'] in relevant and abs(e['t']-bar['t'])<=600 for e in news)
            if bar['t']>=pending['expires_at'] or blocked_news:
                diagnostics['pending expired or news blocked']+=1;pending=None
            else:
                p=pending;long=p['side']=='long';side=bar['ask'] if long else bar['bid']
                stop,target=float(p['stop']),float(p['target']);reference=float(p['entry'])
                # Before a retracement, an invalidation or reached objective
                # retires the setup. Ambiguous OHLC paths are not optimistic fills.
                invalid=bar['bid']['l']<=stop if long else bar['ask']['h']>=stop
                target_first=bar['bid']['h']>=target if long else bar['ask']['l']<=target
                if p['entry_type']=='LIMIT':
                    touched=side['l']<=reference if long else side['h']>=reference
                    if invalid or target_first:
                        diagnostics['limit invalidated/ambiguous before verified fill']+=1;pending=None
                    elif touched:
                        position={**p,'fill':reference,'entered_at':bar['t'],'risk_day':day};pending=None;budget['fills']+=1
                else:
                    fill=side['o']+(slip if long else -slip)
                    bound=float(p.get('bound',reference+(0.5*pip if long else -.5*pip)))
                    if (fill>bound if long else fill<bound) or not (stop<fill<target if long else target<fill<stop):
                        diagnostics['market bound/bracket rejected']+=1;pending=None
                    else:position={**p,'fill':fill,'entered_at':bar['t'],'risk_day':day};pending=None;budget['fills']+=1
        if position:
            p=position;long=p['side']=='long';side=bar['bid'] if long else bar['ask']
            stop,target=float(p['stop']),float(p['target'])
            stop_hit=side['l']<=stop if long else side['h']>=stop
            target_hit=side['h']>=target if long else side['l']<=target
            status=None;exit_price=None
            if stop_hit:status='loss';exit_price=(min(stop,side['o'])-slip if long else max(stop,side['o'])+slip)
            elif target_hit:status='win';exit_price=target+(-slip if long else slip)
            elif bar['t']+60>=p['flatten_at']:status='time_exit';exit_price=side['c']+(-slip if long else slip)
            if status:
                risk=abs(p['fill']-stop);r=(exit_price-p['fill'])*(1 if long else -1)/risk
                trades.append({**p,'status':status,'exit_at':bar['t']+60,'exit_price':exit_price,'net_r':r})
                risk_budget=risk_days[p['risk_day']];risk_budget['r']+=r
                risk_budget['streak']=risk_budget['streak']+1 if r<0 else 0;position=None
        history.append({'t':bar['t'],**{k:(bar['bid'][k]+bar['ask'][k])/2 for k in 'ohlc'}})
        if evaluation_start is not None and now.timestamp()<evaluation_start:continue
        # Scanner gets only closed price prefixes and completed HTF candles.
        visible={k:[b for b in rows if b['t']+{'daily':86400,'4h':14400,'1h':3600}[k]<=now.timestamp()]
                 for k,rows in higher.items()}
        report=scanner(pair,history[-5000:],visible,now,session)
        diagnostics.update(d['reason'] for d in report.get('diagnostics',[]))
        for p in report['plans']:
            if p['signal_id'] in seen:continue
            seen.add(p['signal_id'])
            if p['confirmed_at']>now.timestamp() or p['confirmed_at']<bar['t']:raise ValueError('scanner emitted noncurrent signal')
            sid=p['session_id'];ns=budget['session_submissions'].get(sid,0)
            if pending or position:diagnostics['one position or pending reservation']+=1;continue
            if budget['fills']>=3 or budget['r']<=-3 or budget['streak']>=2:
                diagnostics['daily fills/loss halt']+=1;continue
            if ns>=3 or budget['submissions']>=9:diagnostics['submission cap']+=1;continue
            if news is None:diagnostics['historical news unavailable']+=1;continue
            bid,ask=bar['bid']['c'],bar['ask']['c']
            conversion={'loss':1,'gain':1} if pair.endswith('_USD') else {'loss':1/bid,'gain':1/ask}
            try:
                # Normalize to a fixed research account; reports are net-R
                # comparisons, not claims about the user's historical account.
                pending=prepare(p,{'bid':bid,'ask':ask,**conversion},
                    {'minimumTradeSize':'1','maximumOrderUnits':'100000','marginRate':'.05'},
                    {'NAV':'100000','marginAvailable':'100000'},{'nav':'100000'},
                    entries=budget['fills'],losses=budget['streak'])
            except ValueError as error:diagnostics[str(error)]+=1;continue
            budget['submissions']+=1;budget['session_submissions'][sid]=ns+1
    if position:trades.append({**position,'status':'unresolved_end_of_data','net_r':None})
    return {'version':VERSION,'pair':pair,'session':session,'trades':trades,'diagnostics':dict(diagnostics),
            'assumptions':{'slippage_pips':slippage_pips,'bid_ask':True,'same_bar_stop_first':True,
                           'news_supplied':news is not None,'unfilled_end_of_data':pending is not None},
            'input_sha256':hashlib.sha256(json.dumps({'candles':candles,'higher':higher,'news':news},sort_keys=True).encode()).hexdigest(),
            'scope':'Fixed-rule research replay; retrospective input receipt and OHLC sequencing limit conclusions.'}


def chronological_review(report,cutoff,*,grouped=True):
    cutoff=float(cutoff)
    holdout=[t for t in report['trades'] if t['confirmed_at']>=cutoff and t['entered_at']>=cutoff]
    completed=[t for t in holdout if t.get('net_r') is not None]
    values=[t['net_r'] for t in completed];n=len(values);mean=sum(values)/n if n else None
    variance=sum((r-mean)**2 for r in values)/(n-1) if n>1 else None
    # Cluster by session date; trade-by-trade independence would overstate
    # evidence. This rough interval is descriptive, not promotion authority.
    days={}
    for t in completed:days.setdefault(t['session_id'],[]).append(t['net_r'])
    daily=[sum(v)/len(v) for v in days.values()];m=len(daily)
    dm=sum(daily)/m if m else None
    dv=sum((v-dm)**2 for v in daily)/(m-1) if m>1 else None
    lower=dm-1.96*math.sqrt(dv/m) if m>1 else None
    peak=equity=drawdown=0.
    for r in values:equity+=r;peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
    result={'holdout_cutoff':cutoff,'holdout_closed':n,'holdout_session_dates':m,'mean_net_r':mean,
            'session_cluster_lower_mean_r':lower,'max_realized_drawdown_r':drawdown,
            'unresolved':len(holdout)-n,'execution_enabled':False,
            'review_state':'insufficient/unreviewed evidence',
            'notice':'No parameter fitting or automatic approval. Assess each strategy/pair/session separately and collect prospective evidence.'}
    if grouped:
        groups={}
        for t in report['trades']:
            key='/'.join(str(t.get(k,'unknown')) for k in ('name','pair','session'))
            groups.setdefault(key,[]).append(t)
        result['by_strategy_pair_session']={key:chronological_review({'trades':rows},cutoff,grouped=False)
                                           for key,rows in groups.items()}
    return result


def main():
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cutoff',required=True,help='UTC ISO timestamp fixed before holdout inspection')
    parser.add_argument('--start',help='UTC ISO timestamp for first evaluated decision; earlier candles warm up features')
    args=parser.parse_args();data=json.loads(args.input.read_text(encoding='utf-8'))
    start=datetime.fromisoformat(args.start.replace('Z','+00:00')) if args.start else None
    if start and not start.tzinfo:raise ValueError('timezone required')
    result=replay(data['pair'],data['session'],data['candles'],data['higher'],news=data.get('news'),
                  evaluation_start=start.timestamp() if start else None)
    cutoff=datetime.fromisoformat(args.cutoff.replace('Z','+00:00'))
    if not cutoff.tzinfo:raise ValueError('timezone required')
    result['review']=chronological_review(result,cutoff.timestamp())
    from .forex_shadow import fingerprint
    result['engine']=fingerprint()
    result['input_received_at']=data.get('received_at')
    result['historical_news_status']=data.get('news_status','unverified provenance')
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result['review']))


if __name__=='__main__':main()
