"""One requested player/line, including joint per-game points+assists totals."""
from datetime import UTC,datetime
import math
from services.prediction_lifecycle import parse_kickoff,freshness_state
import json
import threading

_index_lock=threading.Lock()
_index_cache={}


def snapshot_index(store):
    """Cache small index fields, never all raw historical game payloads."""
    with _index_lock:
        with store.connection() as db:
            latest=db.execute("SELECT max(id) FROM prop_evidence WHERE kind='model_inputs'").fetchone()[0]
            key=(str(store.path),latest)
            if key in _index_cache:return _index_cache[key]
            rows=[dict(r) for r in db.execute("SELECT id,json_extract(payload,'$.game_id') AS game_id,json_extract(payload,'$.inputs.kickoff') AS kickoff,json_extract(payload,'$.inputs.eligible_rosters') AS rosters,json_extract(payload,'$.inputs.price_candidates') AS candidates,received_at FROM prop_evidence WHERE kind='model_inputs' ORDER BY id DESC LIMIT 150")]
        _index_cache.clear();_index_cache[key]=rows
        return rows


def player_snapshot(store,name,day,now=None):
    from services.injury_reports import normalize_name
    from zoneinfo import ZoneInfo
    now=now or datetime.now(UTC);seen=set();matches=[]
    rows=snapshot_index(store)
    for r in rows:
        start=parse_kickoff(r['kickoff']);gid=r['game_id']
        if gid in seen or not start or start<=now or start.astimezone(ZoneInfo('America/New_York')).date().isoformat()!=day:continue
        seen.add(gid)
        if not 0<=(now-parse_kickoff(r['received_at'])).total_seconds()<=86400:continue
        for tid,roster in json.loads(r['rosters'] or '{}').items():
            for pid,athlete in roster.items():
                if normalize_name(athlete.get('displayName',''))==normalize_name(name):
                    matches.append({'game_id':gid,'team_id':tid,'player_id':pid,'snapshot_id':r['id'],'received_at':r['received_at']})
    if len(matches)!=1:return None
    match=matches[0]
    with store.connection() as db:r=db.execute('SELECT payload FROM prop_evidence WHERE id=?',(match['snapshot_id'],)).fetchone()
    match['inputs']=json.loads(r['payload'])['inputs']
    return match


def project_snapshot(snapshot,rosters,sources,excluded):
    from services import nba_props,prop_model
    from services.nba_opportunity import evidence
    from services.data_freshness import worst
    i=snapshot['inputs'];start=parse_kickoff(i['kickoff']);teams=list(rosters)
    if len(teams)!=2:raise ValueError('Two verified current rosters required')
    games=[g for g in i.get('games',[]) if (at:=parse_kickoff(g.get('date'))) and at<start]
    allowed={tid:set(r) for tid,r in rosters.items()};ambiguous=set.intersection(*allowed.values())
    allowed={tid:ids-ambiguous for tid,ids in allowed.items()}
    result=prop_model.game_props(nba_props.CONFIG,*teams,games,excluded,eligible_players=allowed)
    histories=prop_model.player_histories(games)
    for player in result['players']:
        player['name']=rosters[player['team']][player['id']]['displayName']
        for market in player['markets']:
            market['opportunity_evidence']=evidence(histories.get(player['id'],[]),start,market['minutes'])
    result['roster_verification']={**worst(sources),'status':'verified'}
    result['_research_inputs']={**i,'roster_sources':sources,'eligible_rosters':rosters,
        'configuration':{k:v for k,v in nba_props.CONFIG.items() if not callable(v)},
        'historical_inputs_received_at':snapshot['received_at'],'source':'Saved pregame box-score inputs; refreshed current rosters'}
    return result


def analyze(projection,player_id,market,line,side,kickoff,*,injury_source=None,now=None):
    now=now or datetime.now(UTC);start=parse_kickoff(kickoff)
    if market not in ('points','rebounds','assists','pa') or side not in ('over','under'):
        raise ValueError('Unsupported market or side')
    if isinstance(line,bool) or not isinstance(line,(int,float)) or not math.isfinite(line) or not 0<=line<=200 or line%1 not in (0,.5):
        raise ValueError('Line must be a whole or half number from 0 to 200')
    player=next((p for p in projection.get('players',[]) if str(p['id'])==str(player_id)),None)
    target=next((m for m in (player or {}).get('markets',[]) if m['market']==market),None)
    if not target:return {'status':'WAIT','forecast':None,'reason':'Current player role or complete statistical coverage is unavailable.'}
    ladder=target.get('ladder') or {};values=ladder.get('over',[]);offset=ladder.get('start',0)
    def tail(k):
        if k<0:return 1.
        i=k-offset
        return values[i] if 0<=i<len(values) else None
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in values):
        return {'status':'WAIT','forecast':None,'reason':'Stored distribution is invalid.'}
    if any(a<b for a,b in zip(values,values[1:])):raise ValueError('Nonmonotone distribution')
    above=tail(math.floor(line));at_least=tail(int(line)-1) if line%1==0 else above
    if above is None or at_least is None:return {'status':'WAIT','forecast':None,'reason':'Requested line is outside the stored model distribution.'}
    below=1-at_least;push=at_least-above if line%1==0 else 0
    history=[]
    for r in (target.get('opportunity_evidence') or {}).get('recent_games',[]):
        when=parse_kickoff(r.get('date'))
        if not start or not when or when>=start or str(r.get('team_id'))!=str(player['team']):continue
        fields=('points','assists') if market=='pa' else (market,)
        if not all(isinstance(r.get(k),(int,float)) and not isinstance(r[k],bool) and math.isfinite(r[k]) and r[k]>=0 for k in fields):continue
        history.append((when,sum(r[k] for k in fields)))
    history.sort();history=history[-10:];n=len(history)
    h={'games':n,'over_hits':sum(v>line for _,v in history),'under_hits':sum(v<line for _,v in history),
       'pushes':sum(v==line for _,v in history),'scope':'Up to ten observed current-team appearances; may span seasons.'}
    missing=[]
    if not start or now>=start:missing.append('game is not verified pregame')
    roster=projection.get('roster_verification') or {}
    if roster.get('status')!='verified' or freshness_state(roster,now)!='fresh':missing.append('roster verification unavailable')
    if not injury_source or freshness_state(injury_source,now)!='fresh':missing.append('injury evidence unverified')
    missing.append('exact sportsbook price unavailable')
    return {'status':'WAIT','player':player['name'],'player_id':str(player_id),'team_id':str(player['team']),
        'market':market,'label':target['label'],'line':line,'side':side,'kickoff':str(kickoff),
        'projection':target['projection'],'minutes':target['minutes'],'reason':'; '.join(missing),
        'forecast':{'over_probability':above,'under_probability':below,'push_probability':push,'historical':h},
        'model':target.get('model') or 'minutes-rate-count baseline',
        'probability_scope':'Raw, unvalidated count-model estimate at supplied line. No price or profitability claim.',
        'combined_method':'Fit distribution to observed per-game points+assists totals, not a sum of marginal probabilities.' if market=='pa' else None}


def saved_brief(store,day,now=None):
    """Both sides of saved reference forecasts; not a fresh-price recommendation."""
    from zoneinfo import ZoneInfo
    now=now or datetime.now(UTC);seen=set();choices={'over':[],'under':[]}
    records=snapshot_index(store)
    for record in records:
        start=parse_kickoff(record['kickoff']);gid=record['game_id']
        if not start or start<=now or start.astimezone(ZoneInfo('America/New_York')).date().isoformat()!=day or gid in seen:continue
        # An old snapshot remains dated evidence, not a current roster assertion.
        if not 0<=(now-parse_kickoff(record['received_at'])).total_seconds()<=86400:continue
        seen.add(gid)
        for p in json.loads(record['candidates'] or '[]'):
            chance=p.get('chance');side=p.get('side')
            if isinstance(chance,bool) or not isinstance(chance,(float,int)) or not math.isfinite(chance) or not 0<=chance<=1 or side not in ('over','under'):continue
            for direction,probability in [('over',chance if side=='over' else 1-chance),('under',chance if side=='under' else 1-chance)]:
                if probability<=.5:continue
                choices[direction].append({'player':p['name'],'team':p.get('team'),'market':p['market'],'label':p.get('label',p['market']),
                    'line':p['line'],'probability':probability,'received_at':record['received_at'],
                    'status':'WAIT','line_source':'historical reference; not a verified sportsbook offer'})
    for side in choices:
        ranked=[];names=set()
        for p in sorted(choices[side],key=lambda p:-p['probability']):
            if p['player'] in names:continue
            ranked.append(p);names.add(p['player'])
            if len(ranked)==3:break
        choices[side]=ranked
    return {'date':day,'choices':choices,'games':len(seen),
            'notice':'Saved unvalidated model estimates at historical reference lines. Current roster, injuries and bet365 prices need rechecking.'}
