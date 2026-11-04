import unittest
from datetime import UTC,datetime,timedelta
from services.prop_validation import evaluate


class ValidationTests(unittest.TestCase):
    def test_no_data_never_becomes_a_validated_model(self):
        report=evaluate([],'model')
        self.assertFalse(report['validated'])
        self.assertEqual(report['status'],'insufficient_data')

    def test_repeated_player_game_snapshots_do_not_inflate_sample(self):
        settlements=[{'snapshot_id':i,'game_id':'one-game','kickoff':'2026-10-28T20:00:00Z',
            'rows':[{'player_id':'p','decision':'PASS','projection_model':'model','brier_over':.1,'market_brier_over':.25,
                     'forecast_over':.6,'over_hit':True}]} for i in range(300)]
        result=evaluate(settlements,'model')
        self.assertEqual(result['observations'],1)
        self.assertFalse(result['validated'])

    def test_chronological_holdout_failure_is_not_hidden_by_good_training_history(self):
        settlements=[]
        for game in range(40):
            rows=[{'player_id':str(player),'decision':'PASS','projection_model':'model','brier_over':.01 if game<28 else .5,
                   'market_brier_over':.25,'forecast_over':.5,'over_hit':player%2==0} for player in range(10)]
            settlements.append({'snapshot_id':game,'game_id':str(game),'kickoff':(datetime(2026,10,1,tzinfo=UTC)+timedelta(days=game)).isoformat(),'rows':rows})
        result=evaluate(settlements,'model')
        self.assertEqual(result['holdout_games'],12)
        self.assertFalse(result['validated'])
        self.assertEqual(result['status'],'validation_failed')
