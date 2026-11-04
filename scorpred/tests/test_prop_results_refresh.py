from datetime import UTC,datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from services.prop_evidence_store import EvidenceStore
from services.prop_results_refresh import refresh
from services.prop_paper_results import stored_summary


class RefreshTests(unittest.TestCase):
    def test_final_box_grades_forecasts_not_bets_and_does_not_repeat_complete_snapshots(self):
        with TemporaryDirectory() as folder:
            store=EvidenceStore(Path(folder)/'paper.sqlite3')
            report={'game_id':'g','version':'fixture','recorded_at':'2026-10-28T15:00:00Z','kickoff':'2026-10-28T20:00:00Z',
                    'rows':[{'player_id':'p','market':'points','decision':'OVER','quote':{'side':'over','line':20.5,'over_decimal':2},
                             'two_sided_value':{'over':{'model_win_probability':.6}}}]}
            store.append('nba:g','research_decision','fixture',report['recorded_at'],report,received_at=report['recorded_at'])
            box={'id':'g','date':report['kickoff'],'teams':{'a':{'players':[{'id':'p','points':21.,'minutes':32}]},'b':{'players':[]}}}
            now=datetime(2026,10,29,1,tzinfo=UTC)
            wrong={**box,'date':'2026-10-27T20:00:00Z'}
            self.assertEqual(refresh(store,box_reader=lambda _:wrong,now=now)['snapshots_graded'],0)
            result=refresh(store,box_reader=lambda _:box,now=now)
            self.assertEqual(result['snapshots_graded'],1)
            summary=stored_summary(store)
            self.assertEqual(summary['forecast_observations'],1)
            self.assertEqual(summary['simulated_bets'],0)
            self.assertEqual(refresh(store,box_reader=lambda _:self.fail('Should not refetch'),now=now)['games_checked'],0)

    def test_no_snapshots_means_no_provider_calls(self):
        with TemporaryDirectory() as folder:
            store=EvidenceStore(Path(folder)/'paper.sqlite3')
            self.assertEqual(refresh(store,box_reader=lambda _:self.fail('No data needed'))['games_checked'],0)
