import unittest
from services.prop_parlay_research import build


class ParlayTests(unittest.TestCase):
    def legs(self):
        return [{'selection_id':str(i),'game_id':str(i),'bookmaker_key':'book','decision':'OVER','calibration_validated':True} for i in range(3)]

    def test_no_force_parlay_and_no_unjustified_independence(self):
        legs=self.legs()[:2]
        price={'selection_ids':['0','1'],'bookmaker_key':'book','verified_current':True,'decimal_odds':2}
        result=build(legs,combination_quotes=[price])
        self.assertEqual(result['status'],'NO QUALIFYING PARLAY')
        self.assertIn('not multiplied',' '.join(result['reasons']))
        self.assertEqual(build([])['status'],'NO QUALIFYING PARLAY')

    def test_same_game_and_shared_dependency_are_rejected_before_joint_model(self):
        legs=self.legs();legs[1]['game_id']='0'
        model=lambda _:self.fail('Unsafe combination must not reach model')
        self.assertEqual(build(legs[:2],joint_model=model)['candidates'],[])
        legs=self.legs()[:2]
        for leg in legs:leg['dependency_tags']=['shared_injury_assumption']
        self.assertEqual(build(legs,joint_model=model)['candidates'],[])

    def test_actual_price_and_validated_joint_estimate_required(self):
        price={'selection_ids':['0','1'],'bookmaker_key':'book','verified_current':True,'decimal_odds':2.05}
        result=build(self.legs()[:2],combination_quotes=[price],joint_model=lambda _: {'validated':True,'probability':.55,'version':'fixture'})
        self.assertAlmostEqual(result['candidates'][0]['expected_profit_per_unit'],.1275)
        self.assertEqual(result['candidates'][0]['decimal_odds'],2.05)
