from services import prop_model


def test_matchup_evidence_is_descriptive_and_keeps_existing_multiplier():
    rows=[{'opponent':'2','points':10},{'opponent':'2','points':20},{'opponent':'3','points':99}]
    # The model's configured point field is an iterable of stat names.
    context=prop_model.matchup_evidence(rows,['points'],'2',{'2':[100,120]},105,1.04)
    assert context['opponent_sample_games']==2
    assert context['opponent_allowed_per_game']==110
    assert context['player_vs_opponent_games']==2
    assert context['player_vs_opponent_average']==15
    assert context['rule_opponent_multiplier']==1.04
    assert 'not positional' in context['scope']


def test_missing_matchup_history_is_unavailable_not_zero():
    context=prop_model.matchup_evidence([],['points'],'2',{},0,1)
    assert context['opponent_allowed_per_game'] is None
    assert context['player_vs_opponent_average'] is None
    assert context['opponent_sample_games']==0
