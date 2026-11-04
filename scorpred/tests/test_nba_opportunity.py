import unittest
from services.nba_opportunity import evidence,projected_lineups


class OpportunityTests(unittest.TestCase):
    def test_historical_projection_requires_enough_current_eligible_starters(self):
        players=[{'id':f'{team}-{i}','team':team,'name':f'Player {i}'} for team in ('a','b') for i in range(5)]
        histories={p['id']:[{'team':p['team'],'date':f'2026-10-{day}T20:00:00Z','minutes':30,'starter':True} for day in (24,26,28)] for p in players}
        result=projected_lineups(players,histories,'2026-10-29T20:00:00Z',('a','b'))
        self.assertEqual(result['status'],'projected')
        self.assertEqual(len(result['teams']['a']['players']),5)
        self.assertEqual(projected_lineups(players[:-1],histories,'2026-10-29T20:00:00Z',('a','b'))['status'],'unverified')
    def test_summary_identity_is_verified_before_market_context_is_used(self):
        from unittest.mock import patch
        from services import nba_history
        with patch.object(nba_history,'_get',return_value={'header':{'id':'wrong'}}):
            self.assertEqual(nba_history.upcoming('expected')['status'],'unavailable')
        with patch.object(nba_history,'_get',return_value={'header':{'id':'expected','competitions':[{}]}}):
            context=nba_history.upcoming('expected')
            self.assertEqual(context['lineup_status'],'unverified')
            self.assertEqual(context['source']['state'],'fresh')
    def test_history_windows_keep_samples_and_exclude_future_missing_and_nonfinite_stats(self):
        rows=[{'date':'2026-10-27T20:00:00Z','minutes':30,'points':20},
              {'date':'2026-10-28T20:00:00Z','minutes':40,'points':30},
              {'date':'2026-10-29T20:00:00Z','minutes':48,'points':90},
              {'date':'2026-10-26T20:00:00Z','minutes':float('nan'),'points':0}]
        result=evidence(rows,'2026-10-29T20:00:00Z')
        self.assertEqual(result['windows']['5']['games'],2)
        self.assertEqual(result['windows']['20']['mean_minutes'],35)
        self.assertEqual(result['hours_between_game_starts'],24)
        self.assertEqual(result['minutes_last_three_appearances'],70)
        self.assertEqual(len(result['recent_games']),2)

    def test_missing_history_does_not_become_zero_production(self):
        result=evidence([],'2026-10-29T20:00:00Z')
        self.assertIsNone(result['windows']['5']['mean_points'])
        self.assertIsNone(result['hours_between_game_starts'])
