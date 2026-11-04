from copy import deepcopy
import unittest
from services.prop_paper_results import grade, summarize, settle_snapshot, stored_summary, RULE


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.report={'game_id':'g','version':'v','recorded_at':'2026-10-28T15:00:00Z','kickoff':'2026-10-28T20:00:00Z',
            'rows':[{'player_id':'p','market':'points','decision':'PASS',
                'quote':{'side':'over','line':20.5,'over_decimal':2,'under_decimal':1.9},
                'two_sided_value':{'over':{'model_win_probability':.6}}}]}
        self.outcome={'game_id':'g','status':'final','source':'fixture','observed_at':'2026-10-28T23:00:00Z',
                      'players':{'p':{'participated':True,'points':21}}}

    def test_pass_is_forecast_observation_but_not_simulated_bet(self):
        result=grade(self.report,self.outcome,rule=RULE)
        result['snapshot_id']=1
        stats=summarize([result,result])
        self.assertEqual(stats['forecast_observations'],1)
        self.assertAlmostEqual(stats['mean_brier'],.16)
        self.assertEqual(stats['simulated_bets'],0)
        self.assertIsNone(stats['simulated_roi'])

    def test_push_dnp_and_missing_players_are_distinct(self):
        report=deepcopy(self.report);report['rows'][0]['decision']='OVER';report['rows'][0]['quote']['line']=21
        result=grade(report,self.outcome,rule=RULE)['rows'][0]
        self.assertEqual(result['settlement'],'push');self.assertEqual(result['simulated_profit'],0)
        self.outcome['players']['p']={'participated':False}
        self.assertEqual(grade(report,self.outcome,rule=RULE)['rows'][0]['settlement'],'void')
        self.outcome['players']={}
        self.assertEqual(grade(report,self.outcome,rule=RULE)['rows'][0]['settlement'],'ungraded')

    def test_mismatched_game_late_forecast_and_unknown_rules_fail(self):
        with self.assertRaises(ValueError):grade(self.report,self.outcome,rule='unknown')
        self.outcome['game_id']='other'
        with self.assertRaises(ValueError):grade(self.report,self.outcome,rule=RULE)

    def test_snapshot_receipt_gate_and_persistent_summary(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from services.prop_evidence_store import EvidenceStore
        with TemporaryDirectory() as folder:
            store=EvidenceStore(Path(folder)/'paper.sqlite3')
            early=store.append('nba:g','research_decision','v',self.report['recorded_at'],self.report,received_at=self.report['recorded_at'])
            settle_snapshot(store,early,self.outcome,rule=RULE)
            self.assertEqual(stored_summary(EvidenceStore(store.path))['forecast_observations'],1)
            late=store.append('nba:g','research_decision','v',self.report['recorded_at'],self.report,received_at='2026-10-28T21:00:00Z')
            with self.assertRaisesRegex(ValueError,'received after'):
                settle_snapshot(store,late,self.outcome,rule=RULE)
        self.outcome['game_id']='g';self.report['recorded_at']=self.report['kickoff']
        with self.assertRaises(ValueError):grade(self.report,self.outcome,rule=RULE)
