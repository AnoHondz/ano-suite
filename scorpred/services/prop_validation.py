"""Book-line validation readiness from immutable paper outcomes, not a flag file."""
import json

VERSION='book-line-validation-v1'


def evaluate(settlements,model):
    latest={item['snapshot_id']:item for item in settlements}
    # Multiple snapshots/books of one player/game cannot inflate sample size.
    observations={}
    for item in latest.values():
        for row in item.get('rows',[]):
            if (row.get('decision') not in ('PASS','OVER','UNDER') or row.get('projection_model')!=model
                    or row.get('brier_over') is None or row.get('market_brier_over') is None):continue
            key=(item['game_id'],row.get('player_id'))
            observations[key]={'game_id':item['game_id'],'kickoff':item.get('kickoff',''),**row}
    games=sorted({(row['kickoff'],row['game_id']) for row in observations.values()})
    held_games={game for _,game in games[int(len(games)*.7):]}
    held=[r for r in observations.values() if r['game_id'] in held_games]
    sufficient=len(observations)>=200 and len(games)>=30 and len(held)>=60 and len(held_games)>=10
    brier=sum(r['brier_over'] for r in held)/len(held) if held else None
    baseline=sum(r['market_brier_over'] for r in held)/len(held) if held else None
    calibration=abs(sum(r['forecast_over']-int(r['over_hit']) for r in held)/len(held)) if held else None
    passed=sufficient and brier<baseline and calibration<=.05
    return {'version':VERSION,'model':model,'validated':bool(passed),'observations':len(observations),
        'distinct_games':len(games),'holdout_observations':len(held),'holdout_games':len(held_games),
        'holdout_brier':brier,'market_holdout_brier':baseline,'holdout_mean_probability_bias':calibration,
        'status':'research_gate_passed' if passed else 'insufficient_data' if not sufficient else 'validation_failed',
        'policy':'Chronological final 30% of games; >=200 player/game observations, >=30 games, >=60 holdout observations across >=10 games; beat margin-adjusted market Brier and mean probability bias <=5 points.',
        'limitations':'A provisional research gate, not proof of profitability. Same-game players remain dependent; mean bias can hide subgroup errors.'}


def for_models(store,models):
    with store.connection() as db:
        records=db.execute("SELECT payload FROM prop_evidence WHERE kind='paper_settlement' ORDER BY received_at,id").fetchall()
    settlements=[json.loads(record['payload']) for record in records]
    return {model:evaluate(settlements,model) for model in models}
