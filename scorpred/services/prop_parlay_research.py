"""Small-parlay research; no independence assumption or fabricated book price."""
from itertools import combinations
import math


def build(legs, *, leg_count=2, target_decimal=2.0, tolerance=.25, combination_quotes=(), joint_model=None):
    if leg_count not in (2,3) or isinstance(leg_count,bool):raise ValueError('Choose two or three legs')
    for value in (target_decimal,tolerance):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Finite odds controls required')
    if target_decimal<=1 or tolerance<0:raise ValueError('Invalid target odds range')
    eligible=[leg for leg in legs if leg.get('decision') in ('OVER','UNDER') and leg.get('calibration_validated') is True]
    proposals=[];rejections=[]
    for group in combinations(eligible,leg_count):
        ids=[leg.get('selection_id') for leg in group]
        games=[leg.get('game_id') for leg in group]
        if not all(ids) or len(set(ids))!=leg_count or not all(games) or len(set(games))!=leg_count:
            rejections.append('Repeated selections or same-game legs excluded');continue
        dependencies=[set(leg.get('dependency_tags') or []) for leg in group]
        if any(a&b for a,b in combinations(dependencies,2)):
            rejections.append('Shared dependency excluded');continue
        books={leg.get('bookmaker_key') for leg in group}
        if len(books)!=1 or not all(books):
            rejections.append('All legs require the same verified sportsbook');continue
        quotes=[q for q in combination_quotes if set(q.get('selection_ids',[]))==set(ids)
                and q.get('bookmaker_key') in books and q.get('verified_current') is True]
        if len(quotes)!=1:
            rejections.append('Exact current sportsbook combination price unavailable');continue
        odds=quotes[0].get('decimal_odds')
        if isinstance(odds,bool) or not isinstance(odds,(int,float)) or not math.isfinite(odds) or odds<=1:continue
        if abs(odds-target_decimal)>tolerance:continue
        if joint_model is None:
            rejections.append('Validated joint-probability model unavailable; individual probabilities were not multiplied');continue
        estimate=joint_model(group)
        probability=estimate.get('probability')
        if (estimate.get('validated') is not True or isinstance(probability,bool) or not isinstance(probability,(int,float))
                or not math.isfinite(probability) or not 0<=probability<=1):
            rejections.append('Joint-probability estimate is unvalidated');continue
        if probability*odds<=1:
            rejections.append('No estimated value at the actual combination price');continue
        proposals.append({'selection_ids':ids,'decimal_odds':odds,'break_even_probability':1/odds,
            'joint_probability':probability,'expected_profit_per_unit':probability*odds-1,
            'joint_model':estimate.get('version'),'research_only':True,
            'counterargument':'The joint model can be misspecified; every leg must succeed and current prices can change.'})
    return {'strategy':'small_parlays','status':'RESEARCH_CANDIDATES' if proposals else 'NO QUALIFYING PARLAY',
        'leg_count':leg_count,'target_decimal':target_decimal,'candidates':proposals,
        'reasons':sorted(set(rejections)) or ([] if proposals else ['No individually qualified, validated legs']),
        'notice':'No wager execution. Same-game and known shared-dependency combinations are excluded.'}
