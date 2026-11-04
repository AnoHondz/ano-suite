"""Grade immutable pregame research against identified final NBA box scores.

Only the explicitly supplied settlement contract is supported. Callers must
verify the book uses it; other contracts stay ungraded. No bets are submitted.
"""
from datetime import UTC, datetime
import json
import math
from services.prop_evidence_store import instant

RULE = 'full_game_including_overtime_dnp_void_v1'


def grade(report, outcome, *, rule):
    if rule != RULE:
        raise ValueError('Unsupported sportsbook settlement rules')
    if outcome.get('status') != 'final' or not outcome.get('source'):
        raise ValueError('Identified final results are required')
    if str(report.get('game_id')) != str(outcome.get('game_id')):
        raise ValueError('Final result does not match the forecast game')
    if instant(report['recorded_at']) >= instant(report['kickoff']):
        raise ValueError('Forecast must have been recorded strictly before tipoff')
    if instant(outcome['observed_at']) <= instant(report['kickoff']):
        raise ValueError('Final result observation must be after tipoff')
    results = []
    for index, row in enumerate(report.get('rows', [])):
        player_id = row.get('player_id')
        player = outcome.get('players', {}).get(str(player_id)) if player_id is not None else None
        quote = row.get('quote') or {}
        result = {'row_index':index,'player_id':player_id,'decision':row.get('decision'),
                  'projection_model':row.get('calibration_key') or row.get('projection_model'),'market':row.get('market'),'market_brier_over':None,
                  'settlement':'ungraded','brier_over':None,'simulated_profit':None}
        if not player or row.get('market') not in ('points','rebounds','assists') or quote.get('side') not in ('over','under'):
            results.append(result); continue
        if player.get('participated') is False:
            result['settlement'] = 'void'
            if row.get('decision') in ('OVER','UNDER'): result['simulated_profit'] = 0
            results.append(result); continue
        points, line = player.get(row['market']), quote.get('line')
        if (player.get('participated') is not True or isinstance(points,bool) or not isinstance(points,int) or points < 0
                or isinstance(line,bool) or not isinstance(line,(int,float)) or not math.isfinite(line) or line < 0):
            results.append(result); continue
        is_push, over_hit = points == line, points > line
        result.update(actual_points=points if row['market']=='points' else None,actual_value=points, line=line, settlement='push' if is_push else 'over' if over_hit else 'under')
        value = row.get('two_sided_value') or {}
        p_over = (value.get('over') or {}).get('model_win_probability')
        push=value.get('push_probability',0)
        if isinstance(push,(int,float)) and not isinstance(push,bool) and 0<push<1 and isinstance(p_over,(int,float)):
            p_over=p_over/(1-push)
        if (not is_push and isinstance(p_over,(int,float)) and not isinstance(p_over,bool)
                and math.isfinite(p_over) and 0 <= p_over <= 1):
            result['brier_over'] = (p_over - int(over_hit)) ** 2
            result['forecast_over'] = p_over
            result['over_hit'] = over_hit
            market_p=(value.get('over') or {}).get('market_no_vig_probability')
            if isinstance(market_p,(int,float)) and not isinstance(market_p,bool) and math.isfinite(market_p) and 0<=market_p<=1:
                result['market_brier_over']=(market_p-int(over_hit))**2
        decision = row.get('decision')
        if decision in ('OVER','UNDER'):
            side = decision.lower()
            price = quote.get(side + '_decimal')
            if isinstance(price,(int,float)) and not isinstance(price,bool) and math.isfinite(price) and price > 1:
                result['simulated_profit'] = 0 if is_push else price-1 if (side=='over') == over_hit else -1
        results.append(result)
    return {'version':'nba-paper-results-v1','game_id':str(report['game_id']), 'forecast_version':report['version'],
            'kickoff':report['kickoff'],
            'rule':rule,'result_source':outcome['source'],'result_observed_at':outcome['observed_at'],'rows':results}


def settle_snapshot(store, snapshot_id, outcome, *, rule, forecast_only=False):
    with store.connection() as db:
        snapshot = db.execute('SELECT * FROM prop_evidence WHERE id=? AND kind=?', (snapshot_id,'research_decision')).fetchone()
    if snapshot is None:
        raise ValueError('Research snapshot not found')
    report = json.loads(snapshot['payload'])
    # Validate actual storage receipt, not only a timestamp inside the payload.
    if instant(snapshot['received_at']) >= instant(report['kickoff']):
        raise ValueError('Snapshot was received after tipoff')
    result = grade(report,outcome,rule=rule)
    from services.prop_closing_price import last_observed_price
    for row in result['rows']:
        row['price_movement'] = last_observed_price(store, report['rows'][row['row_index']], report['kickoff'])
    result['forecast_only'] = forecast_only
    if forecast_only:
        for row in result['rows']:
            row['simulated_profit'] = None
    result['snapshot_id'] = snapshot_id
    now=datetime.now(UTC)
    return store.append(snapshot['event_id'],'paper_settlement','nba-paper-results-v1',now,result)


def summarize(settlements):
    # Repeated runs or corrected final scores do not multiply observations.
    latest = {item['snapshot_id']:item for item in settlements}
    rows = [row for item in latest.values() for row in item['rows']]
    scored = [r for r in rows if r.get('brier_over') is not None]
    bets = [r['simulated_profit'] for r in rows if r.get('simulated_profit') is not None]
    prices=[r['price_movement']['initial_to_last_price_ratio'] for r in rows
            if r.get('price_movement',{}).get('status')=='last_observed_pregame']
    bins=[]
    for low in range(0,10):
        subset=[r for r in scored if low/10 <= r['forecast_over'] < (low+1)/10 or low==9 and r['forecast_over']==1]
        if subset: bins.append({'lower':low/10,'count':len(subset),
            'mean_forecast':sum(r['forecast_over'] for r in subset)/len(subset),
            'observed_over_rate':sum(r['over_hit'] for r in subset)/len(subset)})
    return {'strategy':'individual_points','forecast_observations':len(scored),
        'mean_brier':sum(r['brier_over'] for r in scored)/len(scored) if scored else None,
        'simulated_bets':len(bets),'simulated_profit_units':sum(bets),
        'simulated_roi':sum(bets)/len(bets) if bets else None,
        'observed_pregame_price_comparisons':len(prices),
        'mean_initial_to_last_price_ratio':sum(prices)/len(prices) if prices else None,
        'closing_price_notice':'Last observed exact-line prices are not verified sportsbook closing prices.',
        'calibration_bins':bins,'notice':'Descriptive paper results, not proof of an edge. Repeated snapshots of the same game are dependent.'}


def stored_summary(store):
    with store.connection() as db:
        rows = db.execute('SELECT payload FROM prop_evidence WHERE kind=? ORDER BY received_at,id', ('paper_settlement',)).fetchall()
        parlays=db.execute("SELECT payload FROM prop_evidence WHERE kind='parlay_research' ORDER BY id").fetchall()
    settlements=[json.loads(row['payload']) for row in rows]
    result=summarize(settlements)
    result['by_market']={market:summarize([{**item,'rows':[r for r in item['rows'] if r.get('market','points')==market]} for item in settlements]) for market in ('points','rebounds','assists')}
    reports=[json.loads(row['payload']) for row in parlays]
    result['small_parlays']={'research_snapshots':len(reports),
        'no_qualifying_parlay_snapshots':sum(r.get('status')=='NO QUALIFYING PARLAY' for r in reports),
        'qualified_candidates':sum(len(r.get('candidates',[])) for r in reports),
        'simulated_bets':0,'simulated_roi':None,
        'status':'No actual combination-price source or validated joint model; no parlay positions are opened.'}
    return result
