from datetime import datetime,timezone,timedelta
from copy import deepcopy
import unittest
from ano.sports_results import verified_result


class SportsResultsTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,29,23,tzinfo=timezone.utc)
        self.evidence={'sport':'nba','event':'123','teams':['Home','Away'],'kickoff':(self.now-timedelta(hours=4)).isoformat()}
        self.payload={'header':{'id':'123','competitions':[{'date':self.evidence['kickoff'],
            'status':{'type':{'completed':True,'state':'post'}},'competitors':[
                {'team':{'displayName':'Home'},'score':'120'}, {'team':{'displayName':'Away'},'score':'115'}]}]}}

    def test_only_exact_completed_event_teams_and_scores_certify_result(self):
        result=verified_result(self.evidence,now=self.now,getter=lambda url:self.payload)
        self.assertEqual(result['winner'],'Home');self.assertTrue(result['verified'])
        self.assertIn('basketball/nba/summary?event=123',result['source'])

    def test_live_mismatched_delayed_and_unscored_events_stay_pending(self):
        for change in ('live','event','team','time','score'):
            data=deepcopy(self.payload);game=data['header']['competitions'][0]
            if change=='live':game['status']['type']['completed']=False
            elif change=='event':data['header']['id']='other'
            elif change=='team':game['competitors'][0]['team']['displayName']='Other'
            elif change=='time':game['date']=self.now.isoformat()
            else:game['competitors'][0]['score']='NaN'
            with self.subTest(change=change):self.assertIsNone(verified_result(self.evidence,now=self.now,getter=lambda url:data))

    def test_extra_time_soccer_final_is_not_a_regulation_time_result(self):
        self.evidence.update(sport='soccer',league=2)
        self.payload['header']['competitions'][0]['status']['type']['name']='STATUS_FINAL_AET'
        self.assertIsNone(verified_result(self.evidence,now=self.now,getter=lambda url:self.payload))
