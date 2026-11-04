from datetime import datetime,timezone,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from ano.forecast_watch import ForecastWatch
from ano.report_ledger import ReportLedger


class ForecastWatchTests(TestCase):
    def test_automatic_capture_keeps_provisional_research_without_report_or_email(self):
        with TemporaryDirectory() as folder:
            state=Path(folder);root=state/'ScorPred';now=datetime.now(timezone.utc)
            row={'matchId':'123','side':'Home','teamA':'Home','teamB':'Away','winProbability':60,'modelSource':'fixture-model',
                'matchDate':(now+timedelta(hours=2)).isoformat(),
                'dataFreshness':{'state':'fresh','observed_at':now.isoformat(),'expires_at':(now+timedelta(minutes=10)).isoformat()}}
            watch=ForecastWatch(state,root)
            watch.get=lambda path,params:({'app':'ScorPred','root':str(root)},200) if path=='/api/local-runtime' else ({'slate':[row] if path.endswith('/nba') else []},200)
            watch.sample();summary=ReportLedger(state/'report-observations.sqlite3').summary()
            self.assertEqual(summary['recorded'],1);self.assertEqual(summary['pre_event_eligible'],1)
            self.assertEqual(summary['channels'],{'automatic-watch':1});self.assertIsNone(summary['brier_score'])

            from ano.reliability import read_json
            health=read_json(state/'forecast-watch.health.json')
            self.assertEqual(health['quality_waiting_rows'],0)
            self.assertEqual(health['quality_provisional_rows'],2)
