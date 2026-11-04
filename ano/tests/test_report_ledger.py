from datetime import datetime,timezone,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock
from ano.report_ledger import ReportLedger


class ReportLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=datetime(2026,10,29,12,tzinfo=timezone.utc)
        self.store=ReportLedger(Path(self.tmp.name)/'reports.db',clock=lambda:self.now)
        self.row={'matchId':'1','teamA':'Home','teamB':'Away','side':'Home','winProbability':60,
            'matchDate':(self.now+timedelta(hours=2)).isoformat(),'action':'SKIP',
            'dataFreshness':{'observed_at':self.now.isoformat(),'expires_at':(self.now+timedelta(minutes=10)).isoformat()}}

    def test_channels_share_immutable_forecast_and_skip_is_included(self):
        self.store.record_snapshot('nba',{'slate':[self.row]},'chat')
        self.store.record_snapshot('nba',{'slate':[self.row]},'email')
        result=self.store.summary()
        self.assertEqual(result['recorded'],1);self.assertEqual(result['pre_event_eligible'],1)
        self.assertEqual(result['channels'],{'chat':1,'email':1})
        self.assertIsNone(result['brier_score'])

    def test_hindsight_stale_and_scenario_shares_never_become_scored_forecasts(self):
        self.now+=timedelta(hours=3)
        self.store.record_snapshot('nba',{'slate':[self.row]})
        self.store.record_snapshot('nfl',{'slate':[dict(self.row,winProbability=None,scenarioShare=95)]})
        self.assertEqual(self.store.summary()['pre_event_eligible'],0)
        self.assertEqual(self.store.summary()['recorded'],1)

    def test_verified_final_result_scores_once_against_declared_baseline(self):
        self.store.record_snapshot('nba',{'slate':[self.row]})
        self.store.record_snapshot('nba',{'slate':[dict(self.row,winProbability=65)]})
        with self.assertRaises(ValueError):self.store.settle('nba','1','Home','https://example.test/result',self.now.isoformat())
        self.now+=timedelta(hours=4)
        self.store.settle('nba','1','Home','https://example.test/result',self.now.isoformat(),verified=True,status='final')
        result=self.store.summary();self.assertEqual(result['settled_scored'],1)
        self.assertAlmostEqual(result['brier_score'],.16);self.assertEqual(result['uniform_baseline_brier'],.25)
        with self.assertRaises(ValueError):self.store.settle('nba','1','Away','https://example.test/result',self.now.isoformat(),verified=True,status='final')

    def test_unknown_winner_cannot_invent_a_result(self):
        self.store.record_snapshot('nba',{'slate':[self.row]})
        self.now+=timedelta(hours=4)
        with self.assertRaises(ValueError):self.store.settle('nba','1','Other','https://example.test/result',self.now.isoformat(),verified=True,status='final')
        self.assertEqual(self.store.summary()['settled_scored'],0)
    def test_model_and_quality_cohorts_are_scored_with_baselines(self):
        self.store.record_snapshot('nba',{'slate':[{**self.row,'modelSource':'model-v1','dataQuality':{'state':'waiting'}}]})
        self.now+=timedelta(hours=4)
        self.store.settle('nba','1','Home','https://example.test/final',self.now.isoformat(),verified=True,status='final')
        group=self.store.summary()['by_model'][0]
        self.assertEqual(group['model'],'model-v1');self.assertEqual(group['data_quality'],'waiting')
        self.assertAlmostEqual(group['brier'],.16);self.assertEqual(group['baseline_brier'],.25)

    def test_result_refresh_is_bounded_and_retries_after_cooldown_only(self):
        self.store.record_snapshot('nba',{'slate':[self.row]});self.now+=timedelta(hours=7)
        fetch=Mock(return_value=None)
        self.assertEqual(self.store.refresh_results(fetch),0);self.store.refresh_results(fetch)
        self.assertEqual(fetch.call_count,1)
        self.now+=timedelta(minutes=16)
        fetch.return_value={'winner':'Home','verified':True,'source':'https://example.test/final','observed_at':self.now.isoformat()}
        self.assertEqual(self.store.refresh_results(fetch),1)
        self.assertEqual(self.store.summary()['settled_scored'],1)

    def test_ledger_write_problem_does_not_break_a_read_only_report(self):
        from ano.sports_reports import SportsReports
        self.store.record_snapshot=Mock(side_effect=OSError('offline fixture disk failure'))
        payload={'slate':[self.row]}
        reports=SportsReports(self.tmp.name,getter=lambda path,params:(payload,200),ledger=self.store)
        data,code=reports.snapshot('nba','2026-10-29')
        self.assertEqual(code,200)
        self.assertIn(data['slate'][0]['dataQuality']['state'],('waiting','provisional'))
        self.assertEqual(data['slate'][0]['researchForecast']['winProbability'],60)
        self.assertEqual(payload['slate'][0]['winProbability'],60)
