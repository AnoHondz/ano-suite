"""Complete research flow with explicit fixtures, never real provider credits."""
from datetime import UTC,datetime,timedelta
from copy import deepcopy
import pytest
from services.data_freshness import metadata
from services.nba_prop_research import build
from services.prop_evidence_store import EvidenceStore
from services.prop_paper_results import grade,RULE
from services.prop_pipeline import preserve_inputs,explain,load_report,readiness


def research_fixture(market='points',alternate=False):
    now=datetime.now(UTC)
    source=metadata('fixture',now,600)
    key='fixture-model'+(':'+market if market!='points' else '')+(':alternate' if alternate else '')
    quote={'side':'over','line':10.5,'market':'player_'+market+('_alternate' if alternate else ''),
           'model_chance':.25,'decimal_odds':1.8,'over_decimal':1.8,'under_decimal':2.2,
           'expires_at':(now+timedelta(minutes=2)).isoformat()}
    payload={'game_environment':{'status':'available','source':source,'lineup_status':'projected'},
             'roster_verification':{**source,'status':'verified'},'validation_by_model':{key:{'validated':True}},
             'picks':[{'name':'Fixture Player','player_id':'p','model':'fixture-model','market':market,
                       'projection':8,'minutes':30,'sportsbook_quotes':[quote]}]}
    report=build(payload,game_id='fixture-game',kickoff=now+timedelta(hours=2),injury_source=source,
                 now=now,markets=('points','rebounds','assists'))
    return payload,report,now


@pytest.mark.parametrize('market',['points','rebounds','assists'])
def test_chosen_side_price_probability_and_grade_remain_consistent(market):
    _,report,now=research_fixture(market)
    row=report['rows'][0]
    assert row['decision']=='UNDER'
    assert row['quote']['side']=='under'
    assert row['quote']['decimal_odds']==2.2
    assert row['quoted_side_probability']==.75
    assert row['expected_profit_per_unit']==pytest.approx(.65)
    outcome={'game_id':'fixture-game','status':'final','source':'fixture final box',
             'observed_at':(now+timedelta(hours=5)).isoformat(),
             'players':{'p':{'participated':True,market:8}}}
    result=grade(report,outcome,rule=RULE)['rows'][0]
    assert result['settlement']=='under'
    assert result['simulated_profit']==pytest.approx(1.2)
    assert result['projection_model']==row['calibration_key']


def test_alternative_lines_require_their_own_validation():
    payload,_,now=research_fixture(alternate=True)
    payload['validation_by_model']={'fixture-model':{'validated':True}}
    report=build(payload,game_id='fixture-game',kickoff=now+timedelta(hours=2),
                 injury_source=payload['game_environment']['source'],now=now)
    assert report['rows'][0]['decision']=='PASS'
    assert report['rows'][0]['calibration_validated'] is False


def test_snapshot_inputs_explanation_and_readiness_are_connected(tmp_path):
    store=EvidenceStore(tmp_path/'evidence.sqlite3')
    _,report,now=research_fixture()
    inputs={'games':[{'id':'prior-game','date':(now-timedelta(days=1)).isoformat()}]}
    inputs_id=preserve_inputs(store,'fixture-game',inputs)
    assert preserve_inputs(store,'fixture-game',inputs)==inputs_id
    report['input_snapshot_id']=inputs_id
    snapshot=store.append('nba:fixture-game','research_decision','fixture',report['recorded_at'],report)
    explanation=explain(load_report(store,snapshot))
    assert explanation['paid_ai_calls']==0
    assert explanation['evidence']['input_snapshot_id']==inputs_id
    assert 'UNDER' in explanation['text']
    status=readiness(store)
    assert status['latest_snapshot_id']==snapshot
    assert status['evidence_counts']['model_inputs']==1
    assert status['evidence_counts']['model_bundle']==1


def test_local_explanation_never_sends_to_remote_or_paid_provider(monkeypatch):
    monkeypatch.setenv('SCORPRED_LOCAL_LLM_URL','https://api.openai.com/v1')
    _,report,_=research_fixture()
    with pytest.raises(ValueError):explain(report,use_local_ai=True)


def test_research_operational_endpoints():
    import app
    with app.app.test_client() as client:
        assert client.get('/api/nba/props/research/readiness').status_code==200
        url='/api/nba/props/research/snapshots/99999999/explanation'
        assert client.get(url).status_code==404
        assert client.post(url,json={'mode':'paid'}).status_code==400


def test_same_player_different_markets_and_every_alternative_line_match(monkeypatch):
    from services import prop_markets as markets
    now=datetime.now(UTC)
    picks=[{'name':'Fixture Player','player_id':'p','market':market,'line':5.5,'side':'over',
            'probability':.6,'chance':.6,'ladder':{'start':0,'over':[.99,.95,.9,.85,.8,.6,.4,.2]}}
           for market in ('points','rebounds')]
    quotes=[{'player':markets._name('Fixture Player'),'market':market,'line':line,
             'over_decimal':1.9,'under_decimal':1.9,'bookmaker':'DraftKings','bookmaker_key':'draftkings',
             'expires_at':(now+timedelta(minutes=2)).isoformat()}
            for market,line in [('player_points',5.5),('player_points_alternate',3.5),
                                ('player_points_alternate',6.5),('player_rebounds',5.5)]]
    def snapshot(*args):
        assert 'player_points_alternate' in args[4]
        return {'status':'available','quotes':quotes,'event_id':'provider-fixture'}
    monkeypatch.setattr(markets,'snapshot',snapshot)
    output=markets.compare({'picks':picks},'nba','home','away',now+timedelta(hours=1),all_lines=True,alternatives=True)
    assert len(output['picks'][0]['sportsbook_quotes'])==3
    assert len(output['picks'][1]['sportsbook_quotes'])==1
    assert output['sportsbook']['matched_picks']==2


def test_integer_quote_uses_push_mass_and_separate_calibration():
    from services.prop_markets import _comparison
    pick={'side':'over','probability':.6,'chance':.6,'ladder':{'start':0,'over':[.95,.9,.85,.8,.7,.6,.4,.2]}}
    quote={'line':6,'over_decimal':2,'under_decimal':2,'player':'fixture','market':'player_points'}
    comparison=_comparison(pick,quote,'event',allow_push=True)
    assert comparison['model_chance']==.4
    assert comparison['model_push_probability']==pytest.approx(.2)
    payload,_,now=research_fixture()
    payload['picks'][0]['sportsbook_quotes']=[{**comparison,'expires_at':(now+timedelta(minutes=2)).isoformat()}]
    report=build(payload,game_id='fixture-game',kickoff=now+timedelta(hours=2),injury_source=payload['game_environment']['source'],now=now)
    assert report['rows'][0]['decision']=='PASS'
    assert report['rows'][0]['calibration_key']=='fixture-model:integer'
    assert report['rows'][0]['two_sided_value']['push_probability']==pytest.approx(.2)


def test_explanation_post_keeps_real_csrf_protection(monkeypatch):
    import re
    import app
    from runtime_paths import persistent_root
    store=EvidenceStore(persistent_root()/'prop-research'/'evidence.sqlite3')
    _,report,_=research_fixture()
    snapshot=store.append('nba:fixture-game','research_decision','fixture',report['recorded_at'],report)
    monkeypatch.setitem(app.app.config,'WTF_CSRF_ENABLED',True)
    with app.app.test_client() as client:
        page=client.get('/nba/props/research')
        token=re.search(rb'name="csrf-token" content="([^"]+)"',page.data).group(1).decode()
        url=f'/api/nba/props/research/snapshots/{snapshot}/explanation'
        assert client.post(url,json={'mode':'deterministic'}).status_code==400
        response=client.post(url,json={'mode':'deterministic','csrf_token':token})
        assert response.status_code==200
        assert response.json['result']['paid_ai_calls']==0
        assert b'\xc3\x82\xc2\xb7' not in page.data


def test_research_schedule_does_not_require_a_game_winner_recommendation(monkeypatch):
    import app,nba_routes
    now=datetime.now(UTC)
    fixture={'id':'scheduled-fixture','date':{'start':(now+timedelta(hours=2)).isoformat()},
             'status':{'state':'pre','completed':False},'data_freshness':metadata('schedule fixture',now,600),
             'teams':{'home':{'name':'Home'},'visitors':{'name':'Away'}}}
    monkeypatch.setattr(nba_routes.nc,'get_scoreboard_games',lambda *args:[fixture])
    with app.app.test_client() as client:
        response=client.get('/api/nba/props/research/games?date='+now.date().isoformat())
        assert response.status_code==200
        assert response.json['slate'][0]['pregameAvailable'] is True
        assert client.get('/api/nba/props/research/games?date=2026-99-99').status_code==400


@pytest.mark.parametrize('answer,accepted',[('{"fact_ids":[0,1]}',True),('{"fact_ids":[999]}',False),('invented recommendation',False)])
def test_local_writer_can_only_select_recorded_facts(monkeypatch,answer,accepted):
    from services import prop_pipeline
    class Response:
        status_code=200
        def json(self):return {'choices':[{'message':{'content':answer}}]}
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            assert self.trust_env is False
            assert url.startswith('http://127.0.0.1:11434/')
            assert kwargs['allow_redirects'] is False
            return Response()
    monkeypatch.setattr(prop_pipeline.requests,'Session',Session)
    _,report,_=research_fixture()
    result=explain(report,use_local_ai=True)
    assert result['paid_ai_calls']==0
    if accepted:
        assert result['output_guard']=='verified_fact_selection'
        assert 'Recorded research decisions: UNDER.' in result['ai_commentary']
    else:assert result['mode']=='deterministic_local_unavailable'


def test_stable_player_can_have_a_lean_without_a_complete_starting_five():
    payload,_,now=research_fixture()
    payload['game_environment']['lineup_status']='unverified'
    payload['validation_by_model']={}
    pick=payload['picks'][0]
    pick['team_id']='current-team'
    pick['opportunity_evidence']={'recent_games':[{'team_id':'current-team','date':(now-timedelta(days=day)).isoformat(),'minutes':30} for day in (5,3,1)]}
    report=build(payload,game_id='fixture-game',kickoff=now+timedelta(hours=2),injury_source=payload['game_environment']['source'],now=now)
    row=report['rows'][0]
    assert row['decision']=='PASS'  # A lean never pretends to have passed validation.
    assert row['model_lean']['side']=='UNDER'
    assert row['model_lean']['validated'] is False
    assert row['warnings']
    assert 'Starting lineup evidence is unverified' not in row['reasons']
    pick['availability_uncertain']=True
    report=build(payload,game_id='fixture-game',kickoff=now+timedelta(hours=2),injury_source=payload['game_environment']['source'],now=now)
    assert report['rows'][0]['decision']=='WAIT'
    assert report['rows'][0]['model_lean'] is None


def test_prior_team_appearances_cannot_remove_a_lineup_block():
    from services.nba_opportunity import stable_role
    _,_,now=research_fixture()
    pick={'team_id':'new-team','minutes':30,'opportunity_evidence':{'recent_games':[
        {'team_id':'old-team','date':(now-timedelta(days=day)).isoformat(),'minutes':30} for day in (5,3,1)]}}
    assert stable_role(pick,now+timedelta(hours=2))['supported'] is False
