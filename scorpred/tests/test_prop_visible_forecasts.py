from datetime import UTC,datetime,timedelta
import pytest
from services.nba_prop_research import build,forecast_details


def test_wait_still_shows_model_probabilities_and_observed_hits():
    now=datetime.now(UTC)
    pick={'player_id':'p','team_id':'current','name':'Fixture','market':'points','line':10.5,
          'side':'under','chance':.6,'opportunity_evidence':{'recent_games':[
              {'team_id':'current','date':(now-timedelta(days=i)).isoformat(),'points':value}
              for i,value in ((3,8),(2,11),(1,5))]+[
              {'team_id':'old','date':(now-timedelta(days=4)).isoformat(),'points':30},
              {'team_id':'current','date':(now+timedelta(days=3)).isoformat(),'points':30}]}}
    row=build({'picks':[pick]},game_id='g',kickoff=now+timedelta(hours=2),now=now)['rows'][0]
    assert row['decision']=='WAIT'
    assert row['model_lean'] is None
    forecast=row['forecast']
    assert forecast['under_probability']==.6
    assert forecast['over_probability']==.4
    assert forecast['historical']['under_hits']==2
    assert forecast['historical']['games']==3
    assert forecast['historical']['under_hit_rate']==pytest.approx(2/3)
    assert 'not a verified bet365 offer' in forecast['line_basis']


def test_missing_estimates_are_not_invented():
    assert forecast_details({'market':'points'},None,None,datetime.now(UTC)) is None


def test_quoted_integer_line_retains_push_probability_and_push_samples():
    now=datetime.now(UTC)
    pick={'market':'rebounds','team_id':'t','opportunity_evidence':{'recent_games':[
        {'team_id':'t','date':(now-timedelta(days=day)).isoformat(),'rebounds':value}
        for day,value in ((3,4),(2,5),(1,6))]}}
    comparison={'over':{'model_win_probability':.3},'under':{'model_win_probability':.5},'push_probability':.2}
    forecast=forecast_details(pick,{'line':5},comparison,now+timedelta(hours=1))
    assert forecast['push_probability']==.2
    assert forecast['historical']['pushes']==1
    assert forecast['historical']['under_hit_rate']==pytest.approx(1/3)
