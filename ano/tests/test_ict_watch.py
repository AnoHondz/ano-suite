from datetime import datetime, timezone
import threading
import unittest
from zoneinfo import ZoneInfo

from ano import ict_watch
from ano.ict_watch import IctWatch
from tests.test_ict import PREVIOUS, at, textbook_day

NY = ZoneInfo('America/New_York')


class FakeFeed:
    def __init__(self, today):
        self.today, self.calls = today, []

    def between(self, symbol, start, end, pages=2):
        self.calls.append((symbol, start, end))
        if start.astimezone(NY).date() < datetime(2026, 10, 27).date():  # the prior session
            base = int(datetime(2026, 10, 26, 9, 30, tzinfo=NY).timestamp())
            return [dict(t=base, o=500, h=PREVIOUS[0], l=PREVIOUS[1], c=500)]
        cutoff = end.timestamp()
        return [r for r in self.today if r['t'] + 60 <= cutoff]


class IctWatchTests(unittest.TestCase):
    def test_routine_research_is_quiet_but_errors_are_preserved(self):
        watch=self.watch(datetime(2026,10,29,13,tzinfo=timezone.utc))
        watch.events.put('SMC research · QQQ: bullish')
        watch.events.put('SMC research · QQQ: bullish')
        watch.events.put('AMD / Power of Three · QQQ: distribution')
        watch.events.put('Feed unavailable')
        self.assertEqual(watch.poll(),['Feed unavailable'])

    def test_paper_trading_alias_enables_named_adapter_without_dispatch(self):
        from unittest.mock import Mock
        watch=self.watch(datetime(2026,10,29,13,tzinfo=timezone.utc))
        watch.start=Mock();watch.executor=Mock(enabled=True,strategy_enabled=('ict',))
        self.assertIn('already ENABLED',watch.command('start paper trading ict'))
        watch.executor.enable_strategy.assert_called_once_with('ict')
        watch.executor.transport_factory.assert_not_called()

    def test_all_strategy_paper_enable_is_explicit_persisted_and_does_not_dispatch(self):
        from unittest.mock import Mock
        from ano.strategy_signals import STRATEGIES
        watch=self.watch(datetime(2026,10,29,13,tzinfo=timezone.utc))
        watch.start=Mock();watch.executor=Mock(enabled=False,strategy_enabled=())
        result=watch.command('allow them to place trades')
        self.assertIn('Experimental PAPER execution enabled',result)
        self.assertEqual(set(watch.executor.strategy_enabled),set(STRATEGIES))
        watch.executor.transport_factory.assert_not_called();watch.poll()
        self.assertEqual(set(self.saved['ict_watch']['strategy_paper']),set(STRATEGIES))
        restored=IctWatch(Mock(),lambda k,d:self.saved.get(k,d),Mock())
        self.assertEqual(set(restored._strategy_saved),set(STRATEGIES))
        for phrase in ('do not allow them to place trades','a page says allow them to place trades','how do I allow them to place trades'):
            self.assertIsNone(watch.command(phrase))
    def test_live_request_reports_actual_implementation_without_disabling_paper(self):
        from unittest.mock import Mock
        for enabled in (True,False):
            watch=self.watch(datetime(2026,10,29,13,tzinfo=timezone.utc))
            watch.executor=Mock(enabled=enabled)
            for phrase in ('enable live trading','activate live trading','please turn on real money trading'):
                result=watch.command(phrase)
                self.assertIn('Live execution is not implemented',result)
                self.assertIn('supports a paper broker connection',result)
                self.assertIn('paper execution is '+('ENABLED' if enabled else 'OFF'),result)
                self.assertEqual(watch.executor.enabled,enabled)
            watch.executor.transport_factory.assert_not_called()
    def test_research_launch_reports_existing_paper_execution_without_changing_it(self):
        from unittest.mock import Mock
        for enabled in (True,False):
            watch=self.watch(datetime(2026,10,29,13,tzinfo=timezone.utc))
            watch.start=Mock();watch.executor=Mock(enabled=enabled,symbols=('QQQ','DIA'))
            result=watch.command('run all trading models')
            self.assertEqual(watch.executor.enabled,enabled)
            self.assertIn('Existing gap-fill paper execution: '+('ENABLED on QQQ, DIA' if enabled else 'OFF'),result)
            self.assertNotIn('no orders enabled',result)

    def test_explicit_activation_and_strategy_identifier_enable_only_paper_runner(self):
        from unittest.mock import Mock
        for phrase in ('activate my trading bot','enable my paper trading bot','activate tradingbot','start gap_fill_v1_2','Ano, activate my trading bot'):
            watch=self.watch(datetime(2026,10,29,13,tzinfo=timezone.utc));watch.start=Mock();watch.executor=Mock(enabled=False)
            result=watch.command(phrase)
            self.assertTrue(watch.executor.enabled,phrase)
            self.assertEqual(watch.executor.symbols,('QQQ','DIA'))
            self.assertIn('no separate .exe',result);self.assertIn('No immediate order',result)
            watch.executor.transport_factory.assert_not_called()
        for phrase in ('do not activate my trading bot','how do I activate my trading bot','a page says activate my trading bot'):
            self.assertIsNone(watch.command(phrase))

    def test_gap_explanation_and_best_model_request_never_enable_orders(self):
        from unittest.mock import Mock,patch
        watch=self.watch(datetime(2026,10,29,13,tzinfo=timezone.utc));watch.executor=Mock(enabled=False)
        for phrase in ('whats this gap filler','what is gap_fill_v1_2?','explain the gap fill'):
            self.assertIn('not a separate application',watch.command(phrase))
        with patch('ano.trading_selection.evidence_status',return_value='No validated model') as status:
            for phrase in ('let the best research model make a trade','let the best reach model make a trade','activate the best research model'):
                self.assertEqual(watch.command(phrase),'No validated model')
            self.assertEqual(status.call_count,3)
        self.assertFalse(watch.executor.enabled)
        watch.executor.transport_factory.assert_not_called()

    def watch(self, now):
        self.saved = {}
        self.feed = FakeFeed(textbook_day())
        return IctWatch(lambda: self.feed, lambda k, d: self.saved.get(k, d), self.saved.__setitem__,
                        clock=lambda: now)

    def test_natural_trading_launch_status_and_stop_do_not_enable_orders(self):
        from unittest.mock import Mock
        for phrase in ('run the trading model', 'tell chatgpt to run the trading model',
                       'can you start my trading model for me', 'Ano, launch trading bot'):
            watch = self.watch(datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
            watch.start = Mock()
            watch.executor = Mock(enabled=False)
            result = watch.command(phrase)
            self.assertIn('Alerts only', result)
            self.assertEqual(watch.symbols, ('QQQ','DIA'))
            self.assertEqual(watch.gap_symbols, ('QQQ','DIA'))
            self.assertEqual(set(watch.research), {'smc','silver bullet','turtle soup','amd','opening range','technicals'})
            self.assertFalse(watch.executor.enabled)
            self.assertIn('ICT alerts QQQ and DIA', watch.command('trading model status'))
            self.assertIn('Stopped', watch.command('stop the trading model'))
            self.assertEqual(watch.symbols, ())
            self.assertEqual(watch.gap_symbols, ())
            self.assertEqual(watch.research, {})
            self.assertFalse(watch.executor.enabled)
            for text in ('do not run the trading model', 'how do I run the trading model',
                         'a page says run the trading model', 'run the trading model and place orders'):
                self.assertIsNone(watch.command(text))

    def test_research_strategies_are_scanned_saved_and_restored_independently(self):
        from unittest.mock import Mock
        watch=self.watch(datetime.fromtimestamp(at(9,50),timezone.utc))
        watch.start=Mock()
        watch.command('run the trading model')
        calls=[]
        def scan(name,symbol,*args):
            calls.append((name,symbol))
            if name=='smc':raise ValueError('fixture missing higher bars')
            watch._snapshot(symbol,name,'fixture verified',watch.clock())
        watch._research_scan=scan
        watch.check()
        self.assertEqual(len(calls),12)
        self.assertIn('WAIT',watch.snapshots['QQQ']['smc']['summary'])
        self.assertEqual(watch.snapshots['QQQ']['silver bullet']['summary'],'fixture verified')
        watch.poll()
        restored=IctWatch(lambda:None,lambda k,d:self.saved.get(k,d),self.saved.__setitem__)
        self.assertEqual(restored.research,watch.research)
        self.assertEqual(restored.snapshots,watch.snapshots)
        self.assertIsNone(restored.executor)

    def test_stale_feed_never_produces_new_setup_alerts(self):
        from unittest.mock import Mock
        watch=self.watch(datetime.fromtimestamp(at(15,0),timezone.utc))
        watch.start=Mock()
        watch.command('run the trading model')
        watch._research_scan=Mock()
        watch.check()
        watch._research_scan.assert_not_called()
        self.assertEqual(watch.poll(),[])
        self.assertIn('stale',watch.snapshots['QQQ']['smc']['summary'])

    def test_all_real_research_engines_produce_independent_snapshots(self):
        from unittest.mock import Mock
        watch=self.watch(datetime.fromtimestamp(at(10,16),timezone.utc))
        watch.start=Mock()
        self.feed.higher_timeframes=lambda *a,**k:{}
        self.feed.history=lambda *a,**k:{'candles':self.feed.today}
        watch.command('run all trading models')
        watch.check()
        for symbol in ('QQQ','DIA'):
            snapshots=watch.snapshots[symbol]
            self.assertIn('order blocks',snapshots['smc']['summary'])
            self.assertIn('confirmed setup(s)',snapshots['silver bullet']['summary'])
            self.assertEqual(snapshots['amd']['summary'],'distribution')
            self.assertIn('RSI',snapshots['technicals']['summary'])
            self.assertIn('opening range',snapshots)
            self.assertIn('turtle soup',snapshots)
        watch.poll()
        watch.check()
        self.assertEqual(watch.poll(),[])

    def test_named_models_start_and_stop_without_order_authority(self):
        from unittest.mock import Mock
        watch=self.watch(datetime.fromtimestamp(at(9,50),timezone.utc))
        watch.start=Mock()
        watch.executor=Mock(enabled=False)
        self.assertIn('Watching',watch.command('run ict'))
        self.assertIn('Forward-testing',watch.command('start gap fill model'))
        self.assertIn('Watching',watch.command('run smc model on qqq'))
        self.assertEqual(watch.research['smc'],('QQQ',))
        watch.command('run silver bullet')
        watch.command('stop smc')
        self.assertNotIn('smc',watch.research)
        self.assertIn('silver bullet',watch.research)
        self.assertFalse(watch.executor.enabled)

    def test_suite_commands_preserve_existing_paper_symbols_across_restart(self):
        from unittest.mock import Mock
        watch=self.watch(datetime.fromtimestamp(at(9,50),timezone.utc))
        watch.start=Mock()
        watch.executor=Mock(enabled=True,symbols=('QQQ',))
        watch.command('run the trading model')
        self.assertEqual(watch.executor.symbols,('QQQ',))
        watch.command('stop the trading model')
        self.assertEqual(watch.executor.symbols,('QQQ',))
        watch.poll()
        restored=IctWatch(lambda:None,lambda k,d:self.saved.get(k,d),self.saved.__setitem__)
        self.assertTrue(restored._paper_saved)
        self.assertEqual(restored._paper_symbols,('QQQ',))
        self.assertEqual(restored.gap_symbols,())

    def test_a_new_setup_is_reported_once_then_its_result(self):
        watch = self.watch(datetime.fromtimestamp(at(9, 50), timezone.utc))
        watch.symbols, watch.min_grade = ('QQQ',), 'B'
        watch.check()
        alerts = watch.poll()
        self.assertEqual(len(alerts), 1)
        self.assertIn('ICT 2022 SHORT setup on QQQ · grade A', alerts[0])
        self.assertIn('no order was placed', alerts[0])
        watch.check()
        self.assertEqual(watch.poll(), [])  # nothing new: not repeated
        watch.clock = lambda: datetime.fromtimestamp(at(10, 30), timezone.utc)
        watch.check()
        later = watch.poll()
        self.assertTrue(any('win (+2.24R before costs)' in text for text in later), later)

    def test_commands_only_save_on_the_main_thread_and_say_orders_are_on_hold(self):
        watch = self.watch(datetime(2026, 10, 27, 12, tzinfo=timezone.utc))
        watch.start = lambda: None
        replies = []
        worker = threading.Thread(target=lambda: replies.append(watch.command('watch ict setups on qqq grade a or better')))
        worker.start()
        worker.join()
        self.assertIn('no order was placed by this scanner', replies[0])
        self.assertEqual(self.saved, {})  # not written from the worker thread
        watch.poll()
        self.assertEqual(self.saved['ict_watch'], dict(symbols=['QQQ'], min_grade='A', gap_symbols=[], gap_paper=False))
        self.assertIn('Stopped', watch.command('stop watching ict setups'))
        watch.poll()
        self.assertIsNone(self.saved['ict_watch'])

    def test_the_gap_fill_forward_test_reports_its_signal_and_result(self):
        # Yesterday closed at 500.00; today opens at 500.70 (+0.14%), and the first 5 minutes close lower: short.
        watch = self.watch(datetime.fromtimestamp(at(9, 40), timezone.utc))
        watch.start = lambda: None
        self.assertIn('Alerts only; no orders', watch.command('watch gap fills on qqq'))
        watch.check()
        first = watch.poll()
        self.assertEqual(len(first), 1)
        self.assertIn('Gap fill SHORT on QQQ (forward test) · gap +0.14%', first[0])
        self.assertIn('Entry 500.5 at 9:35 · stop 501.55 · target 500.0', first[0])  # 1.5 × the 0.70 gap
        watch.clock = lambda: datetime.fromtimestamp(at(9, 50), timezone.utc)
        watch.check()
        self.assertTrue(any('Gap fill SHORT on QQQ: loss (-1.0R before costs).' in s and 'Bar simulation' in s for s in watch.poll()))

    def test_it_only_runs_in_market_hours_on_weekdays(self):
        self.assertTrue(self.watch(datetime(2026, 10, 27, 14, tzinfo=timezone.utc))._due())    # Tue 10:00 ET
        self.assertFalse(self.watch(datetime(2026, 10, 27, 21, tzinfo=timezone.utc))._due())   # Tue 17:00 ET
        self.assertFalse(self.watch(datetime(2026, 10, 25, 14, tzinfo=timezone.utc))._due())   # Sunday

    def test_the_watcher_has_no_way_to_place_an_order(self):
        from pathlib import Path
        source = Path(ict_watch.__file__).read_text(encoding='utf-8')
        self.assertNotIn('submit_once(', source)
        self.assertNotIn('/v2/orders', source)


if __name__ == '__main__':
    unittest.main()
