"""User-reported bet365 prices: zero-network, explicitly unvalidated analysis."""
from datetime import UTC,datetime,timedelta
import json
import math
from services.prop_pipeline import load_report
from services.prop_market_value import decimal_odds
from services.prop_markets import _comparison
from services.nba_prop_research import build
from services.prediction_lifecycle import parse_kickoff
from services.data_freshness import worst


def compare_saved(store, options, *, now=None):
    now=now or datetime.now(UTC)
    report=load_report(store,int(options['snapshot_id']))
    recorded=parse_kickoff(report.get('recorded_at'));start=parse_kickoff(report.get('kickoff'))
    if not recorded or not start or not timedelta(0)<=now-recorded<timedelta(minutes=5) or now>=start:
        raise ValueError('Load fresh pregame research before checking a bet365 line.')
    with store.connection() as db:
        row=db.execute("SELECT payload FROM prop_evidence WHERE id=? AND kind='model_inputs'",(report.get('input_snapshot_id'),)).fetchone()
    if row is None:raise ValueError('Saved model inputs are unavailable; reload research.')
    inputs=json.loads(row['payload'])['inputs']
    matches=[pick for pick in inputs.get('price_candidates',[]) if str(pick.get('player_id'))==str(options.get('player_id')) and pick.get('market')==options.get('market')]
    if len(matches)!=1:raise ValueError('Choose an identified player and market from the loaded game.')
    pick=matches[0]
    memberships=[team for team,players in inputs.get('eligible_rosters',{}).items() if str(pick['player_id']) in players]
    if len(memberships)!=1:raise ValueError('Current roster identity is ambiguous.')
    line=options.get('line')
    if isinstance(line,bool) or not isinstance(line,(int,float)) or not math.isfinite(line) or line<0 or line%1 not in (0,.5):
        raise ValueError('Enter a nonnegative whole or half-point line.')
    quote={'player':'user-selected exact player ID','market':'player_'+pick['market'],'line':line,
           'over_decimal':decimal_odds(options.get('over_american')),'under_decimal':decimal_odds(options.get('under_american')),
           'bookmaker':'bet365 (entered by you)','bookmaker_key':'bet365',
           'updated_at':now.isoformat(),'fetched_at':now.isoformat(),
           'expires_at':min(start,now+timedelta(minutes=2)).isoformat()}
    quote=_comparison(pick,quote,None,allow_push=True)
    quote.update(source='User-reported bet365 prices',verification='not_independently_verified')
    roster={**worst(inputs.get('roster_sources',[]),now=now),'status':'verified'}
    output=build({'picks':[{**pick,'team_id':memberships[0],'sportsbook_quotes':[quote]}],
                  'game_environment':inputs.get('game_environment'),'roster_verification':roster,
                  'validation_by_model':{}},game_id=report['game_id'],kickoff=start,
                 injury_source=inputs.get('injury_source'),now=now,markets=('points','rebounds','assists'))
    output.update(parent_snapshot_id=int(options['snapshot_id']),
                  notice='User-reported bet365 prices. Leans are unvalidated model estimates, not independently verified book quotes or demonstrated profit.')
    for item in output['rows']:
        item['warnings'].append('bet365 line and prices were entered by you; availability has not been checked independently.')
    output['comparison_id']=store.append('nba:'+str(report['game_id']),'manual_market_comparison',
        'User-reported bet365 prices',now,output,received_at=now)
    return output
