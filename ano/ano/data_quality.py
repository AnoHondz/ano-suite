"""Show labelled provisional model leans; never invent missing evidence or prices."""
from datetime import datetime,timezone,timedelta
import math


def stamp(value):
    try:
        at=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return at if at.tzinfo else None
    except (ValueError,TypeError):return None


def fresh(evidence,now):
    if not isinstance(evidence,dict):return False
    start,end=stamp(evidence.get('observed_at')),stamp(evidence.get('expires_at'))
    return bool(start and end and start<=now+timedelta(seconds=1) and now<end and evidence.get('state','fresh')=='fresh')


def guard(card,now=None):
    now=now or datetime.now(timezone.utc);result=dict(card)
    if 'matchId' not in result:return result
    kickoff=stamp(result.get('matchDate'));status=result.get('eventStatus') or {}
    scheduled=kickoff is not None and kickoff>now and (not isinstance(status,dict) or (not status.get('completed') and status.get('state','pre')=='pre'))
    market=result.get('market') or {};updated=stamp(market.get('updated_at')) if isinstance(market,dict) else None
    prices=[market.get(k) for k in ('home_decimal','away_decimal')] if isinstance(market,dict) else []
    price_ok=bool(updated and 0<=(now-updated).total_seconds()<900 and len(prices)==2 and
                  all(type(p) in (int,float) and math.isfinite(p) and p>1 for p in prices) and
                  (not market.get('data_freshness') or fresh(market['data_freshness'],now)))
    lineups=result.get('lineupEvidence') or {}
    injuries=result.get('injuryEvidence') or {}
    checks={'schedule':'ready' if scheduled and fresh(result.get('dataFreshness'),now) else 'waiting',
            'prices':'ready' if price_ok else 'waiting',
            'lineups':'ready' if lineups.get('status')=='confirmed' and fresh(lineups,now) else 'waiting',
            'injuries':'ready' if injuries.get('coverage_complete') is True and fresh(injuries,now) else 'waiting'}
    reasons=[k+' evidence missing, stale or incomplete' for k,v in checks.items() if v!='ready']
    result['dataQuality']={'state':'waiting' if reasons else 'ready','checks':checks,'reasons':reasons}
    if reasons:
        result.setdefault('researchForecast',{'side':result.get('side'),'winProbability':result.get('winProbability'),
            'modelSource':result.get('modelSource'),'confidence':result.get('confidence'),
            'scenarioShare':result.get('scenarioShare'),'homeProbability':result.get('homeProbability'),
            'drawProbability':result.get('drawProbability'),'tiedScenarioShare':result.get('tiedScenarioShare'),
            'action':result.get('action'),'reason':result.get('reason'),
            'scope':'provisional research; incomplete evidence; not a verified betting edge'})
        research=result['researchForecast']
        side=research.get('side');probability=research.get('winProbability');scenario=research.get('scenarioShare')
        valid=lambda value:type(value) in (int,float) and math.isfinite(value) and 0<=value<=100
        teams=[result.get('teamA'),result.get('teamB')]
        has_model=side in teams+(['Draw','draw'] if result.get('sport')=='soccer' else []) and (valid(probability) or valid(scenario))
        if checks['schedule']=='ready' and has_model:
            result['dataQuality']['state']='provisional'
            result.update(side=side,winProbability=probability if valid(probability) else None,
                confidence=research.get('confidence') if valid(research.get('confidence')) else 0,
                scenarioShare=scenario if valid(scenario) else None,
                homeProbability=research.get('homeProbability'),drawProbability=research.get('drawProbability'),
                tiedScenarioShare=research.get('tiedScenarioShare'),pregameAvailable=True,
                action='SKIP',researchPick=True,pickLabel='PROVISIONAL RESEARCH PICK',
                reason='PROVISIONAL: '+str(side)+'. Unverified: '+'; '.join(reasons)+
                    '. No verified price-based edge. '+str(research.get('reason') or ''))
            return result
        result.update(action='SKIP',side='Unavailable',confidence=0,winProbability=None,homeProbability=None,
            drawProbability=None,scenarioShare=None,tiedScenarioShare=None,pregameAvailable=False,
            researchPick=False,reason='WAIT: '+'; '.join(reasons),data='Limited Data')
    elif result.get('action')=='SKIP' and result.get('side') in [result.get('teamA'),result.get('teamB'),'Draw']:
        values=[result.get('winProbability'),result.get('scenarioShare')]
        if any(type(v) in (int,float) and math.isfinite(v) and 0<=v<=100 for v in values):
            result.update(researchPick=True,pickLabel='RESEARCH PICK — NO BET RECOMMENDED')
    return result
