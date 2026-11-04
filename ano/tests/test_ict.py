from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from ano import ict

NY = ZoneInfo('America/New_York')
PREVIOUS = (502.5, 498.5)  # previous day high / low: equilibrium 500.5


def at(hour, minute):
    return int(datetime(2026, 10, 27, hour, minute, tzinfo=NY).timestamp())


def bar(t, o, h, l, c):
    return dict(t=t, o=o, h=h, l=l, c=c)


def textbook_day(after_fill='run'):
    """Pre-market range 500.0–501.0, a Judas raid above it at 9:45, then the bearish 2022 model."""
    rows = []
    for m in range(30):  # 9:00–9:29 pre-market, inside 500.0–501.0
        price = 500.5 + (0.5 if m == 10 else -0.5 if m == 20 else 0.1 * ((m % 3) - 1))
        rows.append(bar(at(9, m), 500.5, max(price, 500.6), min(price, 500.4), 500.5))
    regular = [  # (o, h, l, c) from 9:30
        (500.70, 500.80, 500.60, 500.70), (500.70, 500.80, 500.60, 500.65), (500.65, 500.75, 500.55, 500.60),
        (500.60, 500.70, 500.50, 500.55), (500.55, 500.65, 500.45, 500.50), (500.50, 500.60, 500.40, 500.55),  # swing low 500.40
        (500.55, 500.70, 500.50, 500.65), (500.65, 500.80, 500.60, 500.75), (500.75, 500.85, 500.65, 500.80),
        (500.80, 500.90, 500.70, 500.85), (500.85, 500.95, 500.75, 500.90), (500.90, 500.98, 500.80, 500.92),
        (500.92, 500.97, 500.85, 500.90), (500.90, 500.96, 500.84, 500.93), (500.93, 500.99, 500.88, 500.95),
        (500.95, 501.60, 500.90, 501.30),  # 9:45 raid above the pre-market high 501.0
        (501.30, 501.40, 501.00, 501.05),
        (501.00, 501.05, 500.15, 500.20),  # displacement, closes below the 500.40 swing low: shift
        (500.20, 500.30, 500.00, 500.10),  # leaves the gap 500.30–501.00
        (500.10, 500.40, 500.05, 500.35), (500.35, 500.55, 500.30, 500.50),
        (500.50, 500.70, 500.45, 500.60),  # retrace into the gap: entry 500.65 filled
    ]
    if after_fill == 'run':
        regular += [(500.60 - 0.1 * k, 500.65 - 0.1 * k, 500.45 - 0.1 * k, 500.50 - 0.1 * k) for k in range(25)]
    else:  # the stop above the raid high is hit
        regular += [(500.60, 501.70, 500.55, 501.65)] + [(501.65, 501.70, 501.55, 501.60)] * 24
    for m, (o, h, l, c) in enumerate(regular):
        rows.append(bar(at(9, 30) + 60 * m, o, h, l, c))
    return rows


def mirror(rows):
    return [bar(r['t'], 1000 - r['o'], 1000 - r['l'], 1000 - r['h'], 1000 - r['c']) for r in rows]


class IctModelTests(unittest.TestCase):
    def setup(self, rows, previous=PREVIOUS, direction='short'):
        day = ict.analyze_day(rows, previous)
        found = [s for s in day['setups'] if s['direction'] == direction and s['liquidity'].startswith('pre-market')]
        self.assertTrue(found, day['setups'])
        return day, found[0]

    def test_the_bearish_2022_model_is_found_with_its_levels(self):
        day, setup = self.setup(textbook_day())
        self.assertEqual(setup['liquidity'], 'pre-market high')
        self.assertEqual(setup['mss_level'], 500.40)
        self.assertEqual(setup['fvg'], [500.30, 501.00])
        self.assertEqual((setup['entry'], setup['stop']), (500.65, 501.61))
        self.assertEqual((setup['target_liquidity'], setup['target']), ('PDL', 498.5))
        self.assertEqual(setup['status'], 'win')
        self.assertEqual(setup['r'], 2.24)
        self.assertEqual(setup['killzone'], 'NY AM')
        self.assertTrue(setup['checks']['fvg_in_ote'])
        self.assertTrue(setup['checks']['premium_discount'])
        self.assertEqual(setup['grade'], 'A')

    def test_amd_reads_the_judas_swing_and_the_distribution(self):
        day, setup = self.setup(textbook_day())
        self.assertEqual(day['amd']['accumulation']['source'], 'pre-market')
        self.assertEqual(day['amd']['manipulation']['direction'], 'up')
        self.assertEqual(day['amd']['distribution']['direction'], 'down')
        self.assertTrue(setup['amd_aligned'])

    def test_the_bullish_mirror_is_found_too(self):
        _day, setup = self.setup(mirror(textbook_day()), previous=(1000 - PREVIOUS[1], 1000 - PREVIOUS[0]),
                                 direction='long')
        self.assertEqual(setup['liquidity'], 'pre-market low')
        self.assertEqual((setup['entry'], setup['stop'], setup['target']), (499.35, 498.39, 501.5))
        self.assertEqual(setup['status'], 'win')

    def test_a_stop_above_the_raid_is_a_loss(self):
        _day, setup = self.setup(textbook_day(after_fill='stop'))
        self.assertEqual((setup['status'], setup['r']), ('loss', -1.0))

    def test_a_raid_without_a_structure_shift_is_no_setup(self):
        # After the raid price just drifts sideways above the 500.40 swing low: liquidity taken, no shift.
        rows = [r for r in textbook_day() if r['t'] < at(9, 47)]
        rows += [bar(at(9, 47) + 60 * k, 500.95, 501.0, 500.85, 500.9) for k in range(30)]
        day = ict.analyze_day(rows, PREVIOUS)
        self.assertFalse([s for s in day['setups'] if s['liquidity'] == 'pre-market high'])

    def test_no_entry_is_filled_before_the_shift_has_closed(self):
        # The swing low is 499.90, so the 9:47 drop (close 500.20) does not shift structure; the gap 500.30–501.00
        # is there at 9:48, but the shift only closes at 9:49, on a bar that also trades up through the entry.
        rows = textbook_day()
        def put(minute, values):
            i = next(k for k, r in enumerate(rows) if r['t'] == at(9, minute))
            rows[i] = bar(rows[i]['t'], *values)
        put(35, (500.50, 500.60, 499.90, 500.55))
        put(49, (500.10, 500.70, 499.80, 499.85))
        _day, setup = self.setup(rows)
        self.assertEqual(setup['mss_at'][-8:], '09:49 ET')
        self.assertEqual(setup['decided_at'][-8:], '09:49 ET')
        self.assertGreater(setup['entered_at'], setup['decided_at'])  # 9:51, never on or before 9:49

    def test_there_is_no_setup_without_real_liquidity_to_target(self):
        # Without a previous day there is no PDL; nothing below is 2R away, so no target is invented.
        day = ict.analyze_day(textbook_day(), previous=None)
        self.assertFalse([s for s in day['setups'] if s['liquidity'] == 'pre-market high'])
        self.assertGreaterEqual(day['rejected'].get('no_liquidity_target', 0), 1)
        self.assertFalse([s for s in day['setups'] if s['target_liquidity'] == '2R'])

    def test_a_lower_high_made_after_the_days_high_is_still_liquidity(self):
        reg = [bar(m, 0, h, l, 0) for m, (h, l) in enumerate([(510, 505), (504, 500), (502, 498), (503, 499), (501, 499)])]
        # The 504 lower high formed at bar 1, below the day's 510 high: nothing after it traded above 504.
        self.assertTrue(ict.untouched(reg, 'high', 504, 1, 4))
        # The 502 high formed at bar 2 is taken by bar 3 (503).
        self.assertFalse(ict.untouched(reg, 'high', 502, 2, 4))
        self.assertTrue(ict.untouched(reg, 'low', 498, 3, 4))

    def test_liquidity_broken_outside_a_kill_zone_cannot_be_swept_later(self):
        # The pre-market high (501.0) is taken at 11:30, between the kill zones; the 13:45 raid is not a sweep of it.
        textbook = textbook_day()
        pre = [r for r in textbook if r['t'] < at(9, 30)]
        model = [r for r in textbook if r['t'] >= at(9, 30)]
        quiet = lambda start, count: [bar(start + 60 * k, 500.5, 500.6 + 0.02 * (k % 2), 500.4 - 0.02 * (k % 3), 500.5)
                                      for k in range(count)]
        rows = pre + quiet(at(9, 30), 120) + [bar(at(11, 30), 500.5, 501.3, 500.4, 500.5)]
        rows += quiet(at(11, 31), 134)  # to 13:44
        rows += [dict(r, t=r['t'] + (at(13, 30) - at(9, 30))) for r in model]  # the textbook raid at 13:45
        day = ict.analyze_day(rows, PREVIOUS)
        self.assertFalse([s for s in day['setups'] if s['liquidity'] == 'pre-market high'])

    def test_research_rules_apply_and_are_restored(self):
        with ict.rules(ENTRY='edge', MIN_RR=1.2):
            _day, setup = self.setup(textbook_day())
            self.assertEqual(setup['entry'], 500.30)  # the gap's near edge instead of its midpoint
            self.assertEqual(setup['rr'], 1.37)  # the PDL is nearer in R from the edge, so 2R would reject it
        self.assertEqual((ict.ENTRY, ict.MIN_RR), ('ce', 1.5))
        with self.assertRaises(ValueError):
            with ict.rules(TICK=1):
                pass

    def test_overlapping_trades_count_once(self):
        a = dict(r=2.0, status='win', grade='A', score=6, rr=2, _entered_t=100, _exited_t=500)
        b = dict(r=-1.0, status='loss', grade='B', score=4, rr=2, _entered_t=300, _exited_t=400)  # while A is open
        c = dict(r=-1.0, status='loss', grade='C', score=2, rr=2, _entered_t=600, _exited_t=700)
        taken, skipped = ict.one_at_a_time([b, c, a])
        self.assertEqual(taken, [a, c])
        self.assertEqual(skipped, [b])
        summary = ict.summarize([dict(setups=[a, b, c])])
        self.assertEqual((summary['traded'], summary['skipped_overlapping'], summary['overall']['total_r']), (2, 1, 1.0))

    def test_backtest_summary_counts_r_by_grade(self):
        days = ict.analyze(textbook_day(), previous=PREVIOUS)
        summary = ict.summarize(days)
        self.assertGreaterEqual(summary['by_grade']['A']['trades'], 1)
        self.assertIn('before fees and slippage', summary['note'])
        self.assertTrue(all(not key.startswith('_') for setup in summary['best'] for key in setup))


class IctToolTests(unittest.TestCase):
    def plugin(self, folder, candles):
        from pathlib import Path
        from unittest.mock import Mock
        from ano.plugins.paper_trading import PaperTrading
        from ano.plugins.workshop import Workshop
        feed = Mock()
        feed.history.return_value = dict(candles=candles, feed='iex')
        return PaperTrading(Workshop(Path(folder), None), key_loader=lambda: ('k', 's'),
                            feed_factory=lambda *keys: feed), feed

    def two_days(self):
        # The day before: a regular session whose high/low become PDH/PDL (502.5 / 498.5).
        before = int(datetime(2026, 10, 26, 9, 30, tzinfo=NY).timestamp())
        yesterday = [bar(before + 60 * m, 500, 502.5 if m == 50 else 500.5, 498.5 if m == 100 else 499.5, 500)
                     for m in range(390)]
        return yesterday + textbook_day()

    def test_today_setups_are_ranked_and_charted_without_orders(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            plugin, feed = self.plugin(folder, self.two_days())
            text, note = plugin.run('ict_setups', {'symbol': 'QQQ'})
            report = json.loads(text)
            self.assertEqual(report['date'], '2026-10-27')
            best = report['setups'][0]
            self.assertEqual((best['direction'], best['grade'], best['target_liquidity']), ('short', 'A', 'PDL'))
            self.assertTrue(Path(report['chart']).read_text(encoding='utf-8').startswith('<svg'))
            self.assertFalse(report['execution_authorized'])
            self.assertIn('no orders', note)
            feed.verify.assert_called_once()

    def test_backtest_writes_evidence_and_skips_the_warm_up_day(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            plugin, _feed = self.plugin(folder, self.two_days())
            summary = json.loads(plugin.run('ict_backtest', {'symbol': 'QQQ', 'days': 2})[0])
            self.assertEqual(summary['days'], 1)
            self.assertGreaterEqual(summary['by_grade']['A']['wins'], 1)
            self.assertTrue(Path(summary['evidence']).exists())
            # Running the same window again does not grow the sample: one record per day in the ledger.
            again = json.loads(plugin.run('ict_backtest', {'symbol': 'QQQ', 'days': 2})[0])
            self.assertEqual(again['ledger']['unique_days'], 1)
            self.assertEqual(again['ledger']['traded'], summary['ledger']['traded'])

    def test_the_tradingview_indicator_is_saved(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            plugin, _feed = self.plugin(folder, [])
            saved = Path(json.loads(plugin.run('ict_tradingview_indicator', {})[0])['path'])
            pine = saved.read_text(encoding='utf-8')
            self.assertTrue(pine.startswith('//@version=5'))
            self.assertIn('alertcondition(shortSignal', pine)
            self.assertNotIn('strategy.entry', pine)  # an indicator, not an order-placing strategy


class HistoryFeedTests(unittest.TestCase):
    def test_history_follows_pages_on_the_fixed_host_and_merges_them(self):
        import httpx
        from ano.paper_feed import AlpacaPaperFeed
        calls = []
        def route(request):
            calls.append(request)
            page = request.url.params.get('page_token')
            minutes = range(0, 15) if page is None else range(15, 30)
            bars = [dict(t=f'2026-10-27T14:{m:02d}:00Z', o=1, h=1, l=1, c=1) for m in minutes]
            return httpx.Response(200, json={'bars': {'QQQ': bars}, 'next_page_token': None if page else 'next'})
        feed = AlpacaPaperFeed('k', 's', client_factory=lambda **kw: httpx.Client(transport=httpx.MockTransport(route), **kw))
        data = feed.history('QQQ', 2)
        self.assertEqual(len(data['candles']), 30)
        self.assertEqual({c.url.host for c in calls}, {'data.alpaca.markets'})
        self.assertEqual(calls[1].url.params['page_token'], 'next')
        with self.assertRaises(Exception):
            feed.history('TSLA', 2)


if __name__ == '__main__':
    unittest.main()
