from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase
from ano import forex_analysis as fa


class FakeAPI:
    def __init__(self, now):
        start = int(now.timestamp()) // 60 * 60 - 300 * 60
        self.m1 = [dict(t=start + i * 60, o=157.5, h=157.51, l=157.49, c=157.5) for i in range(300)]

    def candles(self, pair, granularity='M1', count=2000):
        return self.m1 if granularity == 'M1' else []

    def quote(self, pair):
        return dict(bid=Decimal('157.495'), ask=Decimal('157.505'))


class ForexAnalysisTests(TestCase):
    def test_pair_names_and_slang_resolve(self):
        for text, pair in (('check usdjpy', 'USD_JPY'), ('analyze eur/usd', 'EUR_USD'), ('ict cable', 'GBP_USD'),
                           ('scan the loonie', 'USD_CAD'), ('check qqq', None)):
            self.assertEqual(fa.pair_from(text), pair)

    def test_outside_sessions_names_the_next_open_in_new_york_time(self):
        friday_night = datetime(2026, 10, 30, 23, 0, tzinfo=timezone.utc)   # 7 PM ET Friday, weekend ahead
        name, start = fa.next_session('EUR_USD', friday_night)
        self.assertEqual(name, 'london')
        self.assertEqual(start.weekday(), 0)  # Monday
        text = fa.describe(fa.analyze('EUR_USD', api=FakeAPI(friday_night), now=friday_night))
        self.assertIn('none trading EUR/USD right now', text)
        self.assertIn('so SMC waits', text)

    def test_active_session_reports_each_strategy_without_orders(self):
        tokyo = datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc)  # 9:30 Tokyo, Monday
        text = fa.describe(fa.analyze('USD_JPY', api=FakeAPI(tokyo), now=tokyo))
        self.assertIn('Session: Tokyo · ICT kill zone', text)
        self.assertIn('• ICT:', text);self.assertIn('• SMC:', text)
        self.assertIn('Research only', text)

    def test_chat_phrases_route_to_the_forex_check_not_the_stock_tools(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import Mock, patch
        from ano.routines import LocalRoutines, RoutineStore
        tmp = TemporaryDirectory();self.addCleanup(tmp.cleanup)
        local = LocalRoutines(Mock(), RoutineStore(Path(tmp.name) / 'r.sqlite3'))
        local.toolbox.allowed.return_value = True
        with patch('ano.forex_analysis.analyze', return_value='x') as run, patch('ano.forex_analysis.describe', return_value='USD/JPY report'):
            self.assertIn('USD/JPY report', local.reply('check usdjpy'))
        run.assert_called_once_with('USD_JPY')
