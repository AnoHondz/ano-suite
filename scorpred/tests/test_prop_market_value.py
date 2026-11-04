import unittest
from services.prop_market_value import compare_market, decimal_odds


class MarketValueTests(unittest.TestCase):
    def test_two_sided_margin_and_expected_returns(self):
        result = compare_market(-110, -110, .55, .45)
        self.assertAlmostEqual(result['over']['market_no_vig_probability'], .5)
        self.assertAlmostEqual(result['market_overround'], 1 / 21)
        self.assertAlmostEqual(result['over']['expected_profit_per_unit'], .05)
        self.assertLess(result['under']['expected_profit_per_unit'], 0)

    def test_push_refunds_stake_instead_of_counting_as_loss(self):
        result = compare_market(100, 100, .45, .45, .10)
        self.assertAlmostEqual(result['over']['expected_profit_per_unit'], 0)
        self.assertAlmostEqual(result['under']['expected_profit_per_unit'], 0)

    def test_invalid_inputs_fail_without_a_price_or_projection(self):
        for odds in (0, 99, -99, True, float('nan'), float('inf'), '100'):
            with self.assertRaises(ValueError):
                decimal_odds(odds)
        for probabilities in ((.6,.6,0), (.5,.4,float('nan')), (True,0,0), (-.1,1.1,0)):
            with self.assertRaises(ValueError):
                compare_market(-110, -110, *probabilities)
