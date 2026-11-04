from datetime import UTC, datetime, timedelta
import unittest
from services.nba_prop_research import build, points_candidates, projected_range


class ResearchTests(unittest.TestCase):
    def test_distribution_range_does_not_invent_unstored_tail(self):
        self.assertEqual(projected_range({'start':0,'over':[.9,.7,.4,.2,.05]})['p90'],4)
        self.assertIsNone(projected_range({'start':0,'over':[.9,.7,.4]})['p90'])
        self.assertIsNone(projected_range({'start':0,'over':[.4,.9]}))
    def test_points_candidates_are_not_limited_to_legacy_picks(self):
        original={'players':[{'id':'p','name':'Player','team':'team','markets':[
            {'market':'points','probability':.8,'side':'over','matchup_context':{'games':8}},
            {'market':'assists','probability':.9}]}],'picks':[{'market':'assists'}]}
        candidates=points_candidates(original,rule_shrink=.5)
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0]['market'],'points')
        self.assertAlmostEqual(candidates[0]['chance'],.65)
        self.assertNotIn('chance',original['players'][0]['markets'][0])
        report=build({'picks':candidates},game_id='g',kickoff=None)
        self.assertEqual(report['rows'][0]['matchup_context'],{'games':8})
    def test_under_selected_model_is_converted_to_correct_over_probability(self):
        now=datetime(2026,10,29,15,tzinfo=UTC)
        quote={'side':'under','line':20.5,'model_chance':.7,'decimal_odds':1.8,
               'over_decimal':2.2,'under_decimal':1.8,'expires_at':(now+timedelta(minutes=2)).isoformat()}
        report=build({'picks':[{'market':'points','sportsbook_quotes':[quote]}]},game_id='g',
                     kickoff=now+timedelta(hours=4),now=now)
        value=report['rows'][0]['two_sided_value']
        self.assertAlmostEqual(value['over']['model_win_probability'],.3)
        self.assertAlmostEqual(value['under']['expected_profit_per_unit'],.26)
        self.assertAlmostEqual(value['over']['expected_profit_per_unit'],-.34)
        self.assertEqual(report['rows'][0]['decision'],'WAIT')
    def test_missing_injuries_and_quotes_are_wait_not_opportunities(self):
        now=datetime(2026,10,29,15,tzinfo=UTC)
        result=build({'picks':[{'name':'Fixture','market':'points','projection':20,'minutes':30}]},
                     game_id='g',kickoff=now+timedelta(hours=4),now=now)
        self.assertEqual(result['rows'][0]['decision'],'WAIT')
        self.assertIsNone(result['rows'][0]['quote'])
        self.assertIsNone(result['rows'][0]['expected_profit_per_unit'])

    def test_non_points_do_not_become_points_research(self):
        result=build({'picks':[{'market':'assists'}]},game_id='g',kickoff=None)
        self.assertEqual(result['rows'],[])
        self.assertEqual(result['status'],'WAIT')

    def test_positive_arithmetic_does_not_override_missing_evidence(self):
        now=datetime(2026,10,29,15,tzinfo=UTC)
        quote={'model_chance':.8,'decimal_odds':2,'expires_at':(now+timedelta(minutes=2)).isoformat()}
        report=build({'picks':[{'market':'points','sportsbook_quotes':[quote]}]},
                     game_id='g',kickoff=now+timedelta(hours=4),now=now)
        self.assertAlmostEqual(report['rows'][0]['expected_profit_per_unit'], .6)
        self.assertEqual(report['rows'][0]['decision'],'WAIT')


def test_research_page_renders_without_fetching_providers():
    import app
    with app.app.test_client() as client:
        response=client.get('/nba/props/research')
        assert response.status_code == 200
        assert b'NBA points research' in response.data
        assert b'research-form' in response.data
        results=client.get('/api/nba/props/research/results')
        assert results.status_code == 200
        assert results.json['result']['strategy'] == 'individual_points'
        assert 'simulated_bets' in results.json['result']
        parlay=client.get('/api/nba/props/research/parlays?legs=2&target=2')
        assert parlay.status_code == 200
        assert parlay.json['result']['status'] == 'NO QUALIFYING PARLAY'
        assert client.get('/api/nba/props/research/parlays?legs=4').status_code == 400
