"""A user's bet365 line can be analysed without a provider or paid model."""
from datetime import UTC,datetime,timedelta
import pytest
from services.prop_evidence_store import EvidenceStore
from services.prop_pipeline import preserve_inputs
from services.prop_manual_quote import compare_saved
from services.data_freshness import metadata


def setup_quote(tmp_path):
    now=datetime.now(UTC);source=metadata('fixture',now,600)
    pick={'player_id':'p','name':'Fixture Player','market':'points','team':'team','minutes':30,
          'projection':9,'side':'under','probability':.7,'chance':.7,
          'ladder':{'start':0,'over':[.99,.97,.95,.9,.85,.8,.7,.6,.5,.4,.3,.2,.1]}}
    store=EvidenceStore(tmp_path/'evidence.sqlite3')
    inputs={'games':[{'id':'prior'}],'price_candidates':[pick],
            'eligible_rosters':{'team':{'p':{'name':'Fixture Player'}}},'roster_sources':[source],
            'game_environment':{'status':'available','source':source,'lineup_status':'projected'},'injury_source':source}
    inputs_id=preserve_inputs(store,'game',inputs)
    report={'game_id':'game','recorded_at':now.isoformat(),'kickoff':(now+timedelta(hours=2)).isoformat(),'input_snapshot_id':inputs_id}
    snapshot=store.append('nba:game','research_decision','fixture',now,report,received_at=now)
    options={'snapshot_id':snapshot,'player_id':'p','market':'points','line':10.5,'over_american':-130,'under_american':110}
    return store,options,now


def test_actual_entered_prices_produce_a_lean_without_becoming_validated(tmp_path):
    store,options,now=setup_quote(tmp_path)
    report=compare_saved(store,options,now=now)
    row=report['rows'][0]
    assert row['decision']=='PASS'
    assert row['model_lean']['side']=='UNDER'
    assert row['model_lean']['decimal_odds']==2.1
    assert row['model_lean']['validated'] is False
    assert row['quote']['verification']=='not_independently_verified'
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM prop_evidence WHERE kind='manual_market_comparison'").fetchone()[0]==1
        # Manual scenarios cannot silently become book-line calibration samples.
        assert db.execute("SELECT COUNT(*) FROM prop_evidence WHERE kind='research_decision'").fetchone()[0]==1


def test_old_inputs_and_wrong_player_are_rejected(tmp_path):
    store,options,now=setup_quote(tmp_path)
    with pytest.raises(ValueError):compare_saved(store,options,now=now+timedelta(minutes=6))
    with pytest.raises(ValueError):compare_saved(store,{**options,'player_id':'other'},now=now)


@pytest.mark.parametrize('change',[{'line':float('nan')},{'line':-1},{'line':3.2},{'over_american':-90},{'under_american':True}])
def test_invalid_line_and_price_are_not_used(tmp_path,change):
    store,options,now=setup_quote(tmp_path)
    with pytest.raises(ValueError):compare_saved(store,{**options,**change},now=now)
