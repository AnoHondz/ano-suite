import unittest
from ano.trading_risk import MAX_ENTRIES_DAY, five_minute_atr, size_paper_trade, volatility_stop


class TradingRiskTests(unittest.TestCase):
    def plan(self, **changes):
        args = dict(equity=100000, session_equity=100000, daily_pnl=0,
                    existing_risk=0, entries=0, consecutive_losses=0,
                    side='buy', entry=100, stop=99, target=104,
                    point_value=1, fx_rate=1, tick_size='.01',
                    minimum_quantity=1, quantity_increment=1,
                    round_trip_cost_per_unit='.10')
        return size_paper_trade(**(args | changes))

    def test_costs_reduce_quantity_and_never_authorize_execution(self):
        result = self.plan()
        self.assertTrue(result['risk_check_passed'])
        self.assertEqual(result['quantity'], '227')
        self.assertFalse(result['execution_authorized'])

    def test_stops_inside_five_minute_noise_are_widened_never_tightened(self):
        # Every minute ranges 1 point, so each complete 5-minute bar ranges 1 point: floor = 1.5.
        bars = [dict(t=600 + 60 * i, h=101.0, l=100.0, c=100.5) for i in range(30)]
        self.assertAlmostEqual(five_minute_atr(bars), 1.0)
        self.assertAlmostEqual(volatility_stop(bars, 'long', 100.5, 100.0), 99.0)
        self.assertAlmostEqual(volatility_stop(bars, 'short', 100.5, 101.0), 102.0)
        self.assertEqual(volatility_stop(bars, 'long', 100.5, 97.0), 97.0)
        self.assertIsNone(volatility_stop(bars[:15], 'long', 100.5, 100.0))  # under three 5-minute ranges

    def test_watched_breakeven_arms_at_one_r_and_exits_on_the_way_back(self):
        from decimal import Decimal as D
        from ano.trading_risk import breakeven
        self.assertEqual(breakeven(True, '100', '99', '100.5'), (None, None))           # half an R: nothing yet
        action, level = breakeven(True, '100', '99', '101')
        self.assertEqual((action, level), ('arm', D('100.1')))                           # +1R: lock entry + 0.1R
        self.assertEqual(breakeven(True, '100', '99', '100.6', level), (None, level))   # still above the level
        self.assertEqual(breakeven(True, '100', '99', '100.1', level)[0], 'exit')       # came back: close
        self.assertEqual(breakeven(False, '100', '101', '99')[1], D('99.9'))             # shorts mirror it

    def test_aggregate_pending_risk_and_daily_headroom(self):
        self.assertEqual(self.plan(existing_risk=450)['quantity'], '45')
        self.assertEqual(self.plan(daily_pnl=-9950)['quantity'], '45')  # $50 left before the 10% daily stop
        self.assertEqual(self.plan(daily_pnl=-700)['quantity'], '227')  # a small loss no longer shrinks trades

    def test_daily_halts(self):
        for change in ({'entries': MAX_ENTRIES_DAY}, {'consecutive_losses': 2}, {'daily_pnl': -10000}):
            self.assertFalse(self.plan(**change)['risk_check_passed'])
        self.assertTrue(self.plan(daily_pnl=-10000)['flatten_required'])
        self.assertTrue(self.plan(entries=3, daily_pnl=-750)['risk_check_passed'])  # the old 3-entry / 0.75% stops are gone

    def test_invalid_inputs_fail(self):
        for change in ({'equity': 'NaN'}, {'fx_rate': 0}, {'entries': True},
                       {'stop': 101}, {'entry': '100.001'},
                       {'round_trip_cost_per_unit': -1}):
            with self.assertRaises(ValueError):
                self.plan(**change)

    def test_minimum_size_and_net_reward(self):
        self.assertFalse(self.plan(minimum_quantity=1000)['risk_check_passed'])
        self.assertFalse(self.plan(target=101.6)['risk_check_passed'])  # 1.5 net reward vs 1.1 net risk: under 1.5R
        self.assertTrue(self.plan(target=101.8)['risk_check_passed'])   # 1.7 vs 1.1: over 1.5R

    def test_short_and_fixed_costs(self):
        self.assertTrue(self.plan(side='sell', stop=101, target=96)['risk_check_passed'])
        self.assertEqual(self.plan(fixed_round_trip_cost=10)['quantity'], '218')
