"""Bounded NBA paper forecast grading using the existing completed-box adapter.

Missing athletes remain unknown: compact box scores intentionally omit DNPs.
Automatic runs score forecasts only, never assume a sportsbook rule contract.
"""
from datetime import UTC, datetime, timedelta
import json
import math
from services.prediction_lifecycle import parse_kickoff
from services.prop_paper_results import RULE, settle_snapshot


def refresh(store, *, box_reader=None, now=None, max_games=10):
    from services.espn_box_scores import box_score
    reader=box_reader or (lambda game_id:box_score('nba',game_id))
    now=now or datetime.now(UTC)
    with store.connection() as db:
        snapshots=db.execute("SELECT id,payload FROM prop_evidence WHERE kind='research_decision' ORDER BY id").fetchall()
        grades=db.execute("SELECT payload FROM prop_evidence WHERE kind='paper_settlement' ORDER BY id").fetchall()
    completed=set()
    for row in grades:
        payload=json.loads(row['payload'])
        if payload.get('rows') and all(item.get('settlement')!='ungraded' for item in payload['rows']):
            completed.add(payload['snapshot_id'])
    pending={}
    for row in snapshots:
        report=json.loads(row['payload']);start=parse_kickoff(report.get('kickoff'))
        if (row['id'] in completed or not report.get('rows') or not start
                or not timedelta(hours=3) <= now-start <= timedelta(days=7)):
            continue
        pending.setdefault(str(report['game_id']),[]).append((row['id'],start))
    checked=settled=deferred=0
    for game_id, ids in list(pending.items())[:max_games]:
        checked+=1
        try:
            box=reader(game_id)
            if not isinstance(box,dict) or str(box.get('id'))!=game_id or len(box.get('teams',{}))!=2:
                deferred+=len(ids);continue
            box_start=parse_kickoff(box.get('date'))
            if box_start is None:
                deferred+=len(ids);continue
            players={};ambiguous=set()
            for team in box['teams'].values():
                for player in team.get('players',[]):
                    pid=str(player.get('id') or '');points=player.get('points');minutes=player.get('minutes')
                    if not pid or isinstance(points,bool) or not isinstance(points,(int,float)) or not math.isfinite(points) or points<0 or int(points)!=points:
                        continue
                    if not isinstance(minutes,(int,float)) or isinstance(minutes,bool) or not math.isfinite(minutes) or minutes<=0:continue
                    if pid in players:ambiguous.add(pid)
                    players[pid]={'participated':True,'points':int(points)}
                    for market in ('rebounds','assists'):
                        value=player.get(market)
                        if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 and int(value)==value:
                            players[pid][market]=int(value)
            for pid in ambiguous:players.pop(pid,None)
            outcome={'game_id':game_id,'status':'final','source':'ESPN completed-game box_score adapter',
                     'observed_at':now.isoformat(),'players':players}
            for snapshot_id, kickoff in ids:
                if abs((box_start-kickoff).total_seconds())>300:
                    deferred+=1;continue
                settle_snapshot(store,snapshot_id,outcome,rule=RULE,forecast_only=True)
                settled+=1
        except (OSError,ValueError,TypeError,KeyError):
            deferred+=len(ids)
    return {'games_checked':checked,'snapshots_graded':settled,'deferred':deferred,
            'forecast_only':True,'notice':'Book-specific settlement rules and omitted DNPs are not inferred.'}
