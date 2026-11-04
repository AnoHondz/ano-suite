from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from ano import session_strategies as ss

NY = ZoneInfo('America/New_York')


def day(bars):
    start = int(datetime(2026, 10, 27, 9, 30, tzinfo=NY).timestamp())
    return [dict(t=start + 60 * i, o=o, h=h, l=l, c=c) for i, (o, h, l, c) in enumerate(bars)]


class GapFillTests(unittest.TestCase):
    def test_a_small_gap_up_is_faded_to_yesterdays_close(self):
        bars = day([(100.3, 100.35, 100.25, 100.3)] * 5 + [(100.28, 100.3, 99.95, 100.0)] + [(100, 100.1, 99.9, 100)] * 5)
        trade = ss.gap_fill(bars, prior=100.0, max_gap=0.006, stop_gaps=1.5, confirm=False)
        self.assertEqual((trade['side'], trade['entry'], trade['target'], trade['status']), ('short', 100.3, 100.0, 'win'))
        self.assertEqual(trade['stop'], 100.75)  # 1.5 × the 0.30 gap above the entry

    def test_confirmation_waits_for_the_first_five_minutes_to_move_toward_the_fill(self):
        rising = day([(100.3, 100.5, 100.3, 100.45)] * 6 + [(100.4, 100.5, 100.2, 100.3)] * 5)
        self.assertIsNone(ss.gap_fill(rising, prior=100.0, confirm=True))  # moving away: no trade

    def test_gaps_outside_the_band_are_skipped(self):
        bars = day([(101.0, 101.1, 100.9, 101.0)] * 10)
        self.assertIsNone(ss.gap_fill(bars, prior=100.0, max_gap=0.006))  # a 1% gap is too big
        self.assertIsNone(ss.gap_fill(bars, prior=101.0))                  # no gap


class OpeningRangeTests(unittest.TestCase):
    def test_the_first_close_beyond_the_range_is_entered_next_bar(self):
        bars = day([(100, 100.5, 99.5, 100)] * 15 + [(100, 100.8, 100, 100.7), (100.7, 102, 100.6, 101.9)])
        trade = ss.opening_range(bars, minutes=15, stop_at='mid', reward=1.5)
        self.assertEqual((trade['side'], trade['entry'], trade['stop'], trade['target']), ('long', 100.7, 100.0, 101.75))
        self.assertEqual(trade['status'], 'win')

    def test_a_bar_hitting_stop_and_target_is_a_loss(self):
        bars = day([(100, 100.5, 99.5, 100)] * 15 + [(100, 100.8, 100, 100.7), (100.7, 102, 99.9, 101)])
        self.assertEqual(ss.opening_range(bars, minutes=15)['status'], 'loss')


if __name__ == '__main__':
    unittest.main()
