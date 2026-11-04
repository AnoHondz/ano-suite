from datetime import datetime,UTC,timedelta
import pytest
from services.nba_player_line import analyze
from services import nba_props,prop_model

NOW=datetime(2026,10,30,20,tzinfo=UTC)


def fixture():
    source={'status':'verified','state':'fresh','observed_at':NOW.isoformat(),
            'expires_at':(NOW+timedelta(minutes=10)).isoformat(),'max_stale_at':(NOW+timedelta(minutes=20)).isoformat()}
    return {'roster_verification':source,'players':[{'id':'n','name':'Naz Reid','team':'1','markets':[
        {'market':'pa','label':'Points + assists','projection':21,'minutes':28,
         'ladder':{'start':0,'over':[.99]*19+[.65,.5,.4,.2]},
         'opportunity_evidence':{'recent_games':[
             {'team_id':'1','date':'2026-10-28T20:00Z','points':18,'assists':4},
             {'team_id':'1','date':'2026-10-27T20:00Z','points':18,'assists':2},
             {'team_id':'2','date':'2026-10-26T20:00Z','points':30,'assists':3},
             {'team_id':'1','date':'2026-10-31T20:00Z','points':30,'assists':3}]}}]}]}


def test_integer_joint_market_counts_pushes_and_current_team_observations():
    result=analyze(fixture(),'n','pa',20,'over',NOW+timedelta(hours=3),now=NOW)
    f=result['forecast']
    assert f['over_probability']==.5
    assert f['under_probability']==pytest.approx(.35)
    assert f['push_probability']==pytest.approx(.15)
    assert f['historical']['games']==2 and f['historical']['over_hits']==1 and f['historical']['pushes']==1
    assert result['status']=='WAIT'


def test_both_requested_sides_share_one_coherent_distribution():
    a=analyze(fixture(),'n','pa',20.5,'over',NOW+timedelta(hours=3),now=NOW)
    b=analyze(fixture(),'n','pa',20.5,'under',NOW+timedelta(hours=3),now=NOW)
    assert a['forecast']==b['forecast']
    assert a['forecast']['push_probability']==0
    assert a['forecast']['over_probability']+a['forecast']['under_probability']==1


def test_combination_is_fit_to_per_game_joint_counts():
    fields,alpha=nba_props.CONFIG['markets']['pa']
    assert fields==('points','assists')
    rows=[{'minutes':30,'points':p,'assists':a} for p,a in [(10,10),(15,5),(5,15),(18,2),(12,8)]]
    model=prop_model.project_market(rows,fields,alpha,20/30,0,1,5)
    assert model['projection']==20


def test_out_of_distribution_and_invalid_line_do_not_invent_probability():
    assert analyze(fixture(),'n','pa',150,'over',NOW+timedelta(hours=3),now=NOW)['forecast'] is None
    with pytest.raises(ValueError):analyze(fixture(),'n','pa',20.2,'over',NOW,now=NOW)
