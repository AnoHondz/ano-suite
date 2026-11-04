"""Describe observed opportunity without inventing usage or fatigue effects."""
import math
from datetime import timedelta
from services.prediction_lifecycle import parse_kickoff


def stable_role(pick, tipoff):
    """Player-level support when the full starting five is still unknown.

    This changes a lineup gate only; it never changes a projection or probability.
    Require recent appearances for the CURRENT team, not a prior club's rotation.
    """
    start=parse_kickoff(tipoff)
    minutes=pick.get('minutes')
    team=pick.get('team_id',pick.get('team'))
    unsupported={'supported':False,'basis':'Recent current-team minutes do not establish a stable role.'}
    if (not start or team is None or isinstance(minutes,bool) or not isinstance(minutes,(int,float))
            or not math.isfinite(minutes) or minutes<24 or pick.get('availability_uncertain') or pick.get('minutes_restriction')):
        return unsupported
    rows=[row for row in (pick.get('opportunity_evidence') or {}).get('recent_games',[])
          if str(row.get('team_id'))==str(team) and (when:=parse_kickoff(row.get('date'))) is not None
          and timedelta(0)<start-when<=timedelta(days=14)]
    rows.sort(key=lambda row:row['date']);recent=rows[-3:]
    if len(recent)<3 or start-parse_kickoff(recent[-1]['date'])>timedelta(days=4):return unsupported
    values=[row.get('minutes') for row in recent]
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<20 for v in values):return unsupported
    mean=sum(values)/3
    variation=math.sqrt(sum((value-mean)**2 for value in values)/3)/mean
    if mean<24 or variation>.2 or abs(minutes-mean)/mean>.25:return unsupported
    return {'supported':True,'current_team':str(team),'appearances':3,'mean_minutes':mean,
            'minutes_variation':variation,'latest_appearance':recent[-1]['date'],
            'basis':'Three current-team appearances within 14 days, latest within four days; >=20 minutes each, mean/projected >=24, variation <=20%. Full lineup remains unconfirmed.'}


def evidence(rows, tipoff, projected_minutes=None):
    start=parse_kickoff(tipoff)
    valid=[]
    for row in rows:
        when=parse_kickoff(row.get('date'));minutes=row.get('minutes');points=row.get('points')
        if (not start or not when or when>=start or isinstance(minutes,bool) or not isinstance(minutes,(int,float))
                or not math.isfinite(minutes) or minutes<=0 or isinstance(points,bool)
                or not isinstance(points,(int,float)) or not math.isfinite(points) or points<0):continue
        valid.append({'date':when.isoformat(),'minutes':minutes,'points':points,
                      **{key:row.get(key) for key in ('fga','fta','turnovers','rebounds','assists')},
                      'team_id':str(row.get('team') or ''),'opponent_id':str(row.get('opponent') or '')})
    valid.sort(key=lambda r:r['date'])
    windows={}
    for count in (5,10,20):
        selected=valid[-count:]
        windows[str(count)]={'games':len(selected),'mean_minutes':sum(r['minutes'] for r in selected)/len(selected) if selected else None,
                            'mean_points':sum(r['points'] for r in selected)/len(selected) if selected else None}
    last=parse_kickoff(valid[-1]['date']) if valid else None
    opportunities={}
    for stat in ('fga','fta','turnovers','rebounds','assists'):
        observed=[r for r in valid[-20:] if isinstance(r.get(stat),(int,float)) and not isinstance(r.get(stat),bool) and math.isfinite(r[stat]) and r[stat]>=0]
        rate=sum(r[stat] for r in observed)/sum(r['minutes'] for r in observed) if observed else None
        estimate=rate*projected_minutes if rate is not None and isinstance(projected_minutes,(int,float)) and math.isfinite(projected_minutes) and projected_minutes>0 else None
        opportunities[stat]={'observed_games':len(observed),'per_minute':rate,'minutes_based_baseline':estimate}
    return {'windows':windows,'recent_games':valid[-20:],
        'hours_between_game_starts':(start-last).total_seconds()/3600 if last else None,
        'minutes_last_three_appearances':sum(r['minutes'] for r in valid[-3:]) if valid else None,
        'scope':'Player appearances, not a complete team schedule. A gap does not establish rest days, travel or health.',
        'observed_rates':opportunities,
        'unavailable':['usage rate','potential assists','rebound opportunities','travel','confirmed minutes restriction']+(['shot attempts'] if not opportunities['fga']['observed_games'] else []),
        'adjustment_policy':'Descriptive only: no unvalidated fatigue or injury multiplier is applied.'}


def projected_lineups(players,histories,tipoff,team_ids):
    """Historical-role estimate, explicitly distinct from a confirmed lineup."""
    start=parse_kickoff(tipoff);teams={}
    for team_id in team_ids:
        candidates=[]
        for player in players:
            if str(player.get('team'))!=str(team_id):continue
            rows=[r for r in histories.get(player.get('id'),[]) if str(r.get('team'))==str(team_id)
                  and (when:=parse_kickoff(r.get('date'))) is not None and start and when<start]
            rows.sort(key=lambda r:r['date']);recent=rows[-5:]
            if len(recent)<3 or start-parse_kickoff(recent[-1]['date'])>timedelta(days=14):continue
            starts=sum(r.get('starter') is True for r in recent)
            minutes=[r.get('minutes') for r in recent]
            if starts<2 or any(isinstance(m,bool) or not isinstance(m,(int,float)) or not math.isfinite(m) or m<=0 for m in minutes):continue
            mean=sum(minutes)/len(minutes)
            candidates.append({'player_id':player['id'],'player':player.get('name'),'recent_starts':starts,
                               'recent_appearances':len(recent),'mean_minutes':mean})
        candidates.sort(key=lambda p:(-p['recent_starts'],-p['mean_minutes']))
        ambiguous=len(candidates)>5 and (candidates[4]['recent_starts'],candidates[4]['mean_minutes'])==(candidates[5]['recent_starts'],candidates[5]['mean_minutes'])
        teams[str(team_id)]={'status':'projected' if len(candidates)>=5 and not ambiguous else 'unverified',
                             'players':candidates[:5] if len(candidates)>=5 and not ambiguous else []}
    return {'status':'projected' if len(teams)==2 and all(t['status']=='projected' for t in teams.values()) else 'unverified',
            'teams':teams,'basis':'Current eligible roster IDs and last five player appearances; >=3 appearances and >=2 starts, within 14 days. Not a coach-confirmed lineup.',
            'limitations':'New rotations and replacements can differ. This estimate does not remove injury, minutes-restriction or role uncertainty.'}
