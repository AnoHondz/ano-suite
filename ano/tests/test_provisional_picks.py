from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from ano.sports_reports import SportsReports

class ProvisionalReportTests(TestCase):
    def report(self,stale=False):
        now=datetime.now(timezone.utc)
        with TemporaryDirectory() as folder:
            root=Path(folder)
            row={'matchId':'test-game','sport':'nfl','teamA':'Home','teamB':'Away','side':'Home',
                 'matchup':'Home vs Away','matchDate':(now+timedelta(hours=2)).isoformat(),
                 'winProbability':None,'scenarioShare':62,'confidence':62,'action':'SKIP',
                 'reason':'Recent-scoring scenario lean','modelSource':'test-model',
                 'dataFreshness':{'state':'fresh','observed_at':(now-timedelta(minutes=2)).isoformat(),
                                  'expires_at':(now+timedelta(minutes=-1 if stale else 5)).isoformat()}}
            def get(path,params):
                if path=='/api/local-runtime':return {'app':'ScorPred','root':str(root)},200
                self.assertEqual(path,'/api/dashboard/nfl', 'Incomplete winner evidence must not enable prop requests')
                return {'slate':[row]},200
            return SportsReports(root,getter=get).build('nfl',props=True)
    def test_report_shows_provisional_scenario_without_enabling_props(self):
        report=self.report()
        self.assertEqual(report['count'],1)
        self.assertIn('PROVISIONAL RESEARCH PICK',report['content'])
        self.assertIn('62% scoring-scenario share (not win probability)',report['content'])
        self.assertIn('No verified price-based edge',report['content'])
    def test_report_does_not_present_expired_model_as_pick(self):
        report=self.report(stale=True)
        self.assertEqual(report['count'],0)
        self.assertIn('No fresh pregame model pick',report['content'])
