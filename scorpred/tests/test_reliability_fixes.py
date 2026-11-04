from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from services import daft_uploads as uploads
from services.ano_data_quality import guard
from services.prop_evidence_store import EvidenceStore


def test_future_research_remains_accessible_without_a_model_or_book_price():
    now=datetime.now(UTC)
    card={'matchId':'fixture','teamA':'Home','teamB':'Away','matchDate':(now+timedelta(hours=2)).isoformat(),
          'eventStatus':{'state':'pre'},'dataFreshness':{'observed_at':now.isoformat(),
          'expires_at':(now+timedelta(minutes=5)).isoformat()},'action':'CONSIDER'}
    result=guard(card,now)
    assert result['pregameAvailable'] is True
    assert result['recommendationEligible'] is False
    assert result['winProbability'] is None and result['action']=='SKIP'
    result=guard({**card,'eventStatus':{'state':'in'}},now)
    assert result['pregameAvailable'] is False


def test_temporary_caption_failure_retries_after_cooldown_then_stops_on_success(monkeypatch,tmp_path):
    from services import prop_video_content
    now=datetime.now(UTC);identifier='abcDEF12345';published=(now-timedelta(hours=2)).isoformat()
    store=EvidenceStore(tmp_path/'prop-research/evidence.sqlite3')
    store.append('youtube:'+uploads.CHANNEL,'youtube_upload','fixture',published,
                 {'video_id':identifier,'title':'NBA morning research','url':'https://www.youtube.com/watch?v='+identifier,'published_at':published})
    feed=Mock();feed.__enter__=Mock(return_value=feed);feed.__exit__=Mock(return_value=False)
    feed.read.return_value=('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
                            '<yt:channelId>'+uploads.CHANNEL+'</yt:channelId></feed>').encode()
    monkeypatch.setattr(uploads.urllib.request,'urlopen',Mock(return_value=feed))
    monkeypatch.setattr(uploads,'persistent_root',lambda:tmp_path)
    fetch=Mock(side_effect=[RuntimeError('temporarily unavailable'),{'video_id':identifier,'status':'available','segments':[{'start':0,'text':'Fixture captions'}]}])
    monkeypatch.setattr(prop_video_content,'fetch',fetch)
    assert uploads.collect(now=now)['content_updates'][0]['status']=='unavailable'
    assert uploads.collect(now=now+timedelta(minutes=30))['content_updates']==[]
    assert uploads.collect(now=now+timedelta(hours=1,seconds=1))['content_updates'][0]['status']=='available'
    assert uploads.collect(now=now+timedelta(hours=2))['content_updates']==[]
    assert fetch.call_count==2


def test_legacy_keys_cannot_reenable_sports_paid_chat():
    from services.analysis_assistant import chat_reply
    sdk=Mock()
    result=chat_reply('Explain predictions',anthropic_module=sdk,api_key='fixture-not-a-real-key')
    sdk.Anthropic.assert_not_called()
    assert result['mode']=='fallback'
    assert result['cost_policy']=='free_deterministic_no_paid_fallback'


def test_expired_card_cannot_retain_an_old_recommendation_permission():
    from services.prediction_lifecycle import sanitize_decision
    now=datetime.now(UTC)
    evidence={'observed_at':now.isoformat(),'expires_at':(now+timedelta(minutes=5)).isoformat()}
    card={'matchId':'fixture','teamA':'Home','teamB':'Away','side':'Home','winProbability':60,
          'matchDate':(now+timedelta(hours=2)).isoformat(),'eventStatus':{'state':'pre'},
          'dataFreshness':evidence,'lineupEvidence':{**evidence,'status':'confirmed'},
          'injuryEvidence':{**evidence,'coverage_complete':True},'action':'CONSIDER',
          'market':{'updated_at':now.isoformat(),'home_decimal':2.0,'away_decimal':2.0}}
    ready=guard(card,now)
    assert ready['recommendationEligible'] is True
    expired=sanitize_decision(ready,now+timedelta(minutes=6))
    assert expired['pregameAvailable'] is False
    assert expired['recommendationEligible'] is False
    assert expired['researchPick'] is False and expired['winProbability'] is None
