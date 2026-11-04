from datetime import datetime,timezone,timedelta
from unittest import TestCase
from copy import deepcopy
from ano.data_quality import guard


class QualityTests(TestCase):
    def setUp(self):
        self.now=datetime(2026,10,29,12,tzinfo=timezone.utc)
        fresh={'state':'fresh','observed_at':self.now.isoformat(),'expires_at':(self.now+timedelta(minutes=5)).isoformat()}
        self.card={'matchId':'1','matchDate':(self.now+timedelta(hours=2)).isoformat(),'teamA':'A','teamB':'B','side':'A',
            'winProbability':80,'confidence':80,'action':'BET','dataFreshness':fresh,
            'market':{'home_decimal':2,'away_decimal':2,'updated_at':self.now.isoformat()},
            'lineupEvidence':{**fresh,'status':'confirmed'},'injuryEvidence':{**fresh,'coverage_complete':True}}
    def test_complete_evidence_preserves_engine_action(self):
        self.assertEqual(guard(self.card,self.now)['action'],'BET')
    def test_missing_optional_inputs_show_provisional_but_missing_source_waits(self):
        for key in ('market','lineupEvidence','injuryEvidence','dataFreshness'):
            row=deepcopy(self.card);row.pop(key);clean=guard(row,self.now)
            if key=='dataFreshness':
                self.assertIsNone(clean['winProbability']);self.assertEqual(clean['dataQuality']['state'],'waiting')
            else:
                self.assertEqual(clean['winProbability'],80);self.assertEqual(clean['dataQuality']['state'],'provisional')
                self.assertTrue(clean['researchPick']);self.assertEqual(clean['action'],'SKIP')
            self.assertEqual(clean['researchForecast']['winProbability'],80)
            self.assertEqual(guard(clean,self.now)['researchForecast']['winProbability'],80)
    def test_projected_lineups_allow_research_but_expired_source_waits(self):
        row=deepcopy(self.card);row['lineupEvidence']['status']='projected'
        self.assertTrue(guard(row,self.now)['pregameAvailable'])
        self.assertEqual(guard(row,self.now)['dataQuality']['state'],'provisional')
        self.assertEqual(guard(self.card,self.now+timedelta(minutes=6))['dataQuality']['state'],'waiting')

    def test_nfl_scenario_is_preserved_without_inventing_win_probability(self):
        row={**self.card,'sport':'nfl','winProbability':None,'scenarioShare':64,'confidence':64,'market':None,'lineupEvidence':{},'injuryEvidence':{}}
        clean=guard(row,self.now)
        self.assertTrue(clean['researchPick']);self.assertEqual(clean['scenarioShare'],64)
        self.assertIsNone(clean['winProbability']);self.assertEqual(guard(clean,self.now)['scenarioShare'],64)

    def test_no_model_or_started_match_cannot_create_pick(self):
        row={**self.card,'market':None,'winProbability':None,'scenarioShare':None}
        self.assertFalse(guard(row,self.now)['researchPick'])
        self.assertFalse(guard(self.card,self.now+timedelta(hours=3))['researchPick'])
    def test_complete_no_bet_can_show_unchanged_model_as_research(self):
        clean=guard({**self.card,'action':'SKIP'},self.now)
        self.assertTrue(clean['researchPick']);self.assertEqual(clean['action'],'SKIP')
        self.assertEqual(clean['dataQuality']['state'],'ready');self.assertEqual(clean['winProbability'],80)
