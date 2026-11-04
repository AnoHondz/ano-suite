from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from ano import paper_orders
from ano.paper_execution import ExecutionWorker, GapFillExecutor, gap_signal
from ano.paper_orders import bracket_payload
from ano.tools import ToolError

NY = ZoneInfo('America/New_York')


def ny(hour, minute, day=27):
    return datetime(2026, 10, day, hour, minute, 20, tzinfo=NY).astimezone(timezone.utc)


def bars(closes_toward=True):
    """9:30–9:34 bars: opens at 100.30 over yesterday's 100.00 close (+0.30%)."""
    start = int(datetime(2026, 10, 27, 9, 30, tzinfo=NY).timestamp())
    last = 100.20 if closes_toward else 100.40
    return [dict(t=start + 60 * i, o=100.30, h=100.45, l=100.15, c=100.30 if i < 4 else last) for i in range(5)]


class FakeAlpaca:
    """Broker state the tests set; Ano may only learn it by reading."""

    def __init__(self):
        self.orders, self.calls, self.calendar = {}, [], ('09:30', '16:00')
        self.equity = (Decimal('100000'), Decimal('100000'))
        self.exit_status = 'new'
        self.fail_submit = False

    def market_day(self, day):
        return self.calendar

    def account(self):
        return dict(active=True, trading_blocked=False, equity=self.equity[0], last_equity=self.equity[1])

    def portfolio_snapshot(self):
        return {'positions':[],'orders':[],'orders_complete':True}

    def position(self,symbol):
        for order in self.orders.values():
            if order.get('symbol')==symbol and Decimal(order['filled_qty'])>0:
                exited=sum(Decimal(str(x.get('filled_qty',0))) for x in order.get('legs') or [])
                qty=Decimal(order['filled_qty'])-exited
                return dict(qty=qty if order['side']=='buy' else -qty,side='long' if order['side']=='buy' else 'short')
        return None

    def submit_once(self, **order):
        self.calls.append(('submit', order))
        if self.fail_submit:
            raise RuntimeError('crashed mid-request')
        self.orders[order['client_id']] = dict(id='broker-1', client_order_id=order['client_id'], status='new',
            symbol=order['symbol'],side=order['side'],qty=str(order['quantity']),limit_price=str(order['entry']),
            stop=str(order['stop']),target=str(order['target']),filled_qty='0', legs=[])
        return dict(dispatched=True, state='acknowledged')

    def order(self, client_id):
        found = self.orders.get(client_id)
        return dict(found, legs=[dict(leg) for leg in found['legs']]) if found else None

    def cancel(self, order_id):
        self.calls.append(('cancel', order_id))
        for order in self.orders.values():
            for leg in order.get('legs') or []:
                if leg['id']==order_id:leg['status']='canceled'
        return True

    def close_shares(self, symbol, qty):
        self.calls.append(('close', symbol, qty))
        return 'exit-1'

    def get_order(self, order_id):
        close=next((c for c in reversed(self.calls) if c[0]=='close'),None)
        entry=next(iter(self.orders.values()))
        return dict(id=order_id,status=self.exit_status,filled_avg_price='100.10',symbol=close[1] if close else 'QQQ',
                    side='buy' if entry['side']=='sell' else 'sell',filled_qty=str(close[2]) if close and self.exit_status=='filled' else '0')

    # helpers for the tests: what Alpaca did
    def fill(self, client_id, qty, status='filled', legs=('new', 'new')):
        entry=self.orders[client_id];opposite='buy' if entry['side']=='sell' else 'sell'
        self.orders[client_id].update(status=status, filled_qty=str(qty), filled_avg_price='100.20',
            legs=[dict(id='leg-target',type='limit',status=legs[0],symbol=entry['symbol'],side=opposite,
                       qty=str(qty),filled_qty=str(qty) if legs[0]=='filled' else '0',limit_price=entry['target']),
                  dict(id='leg-stop',type='stop',status=legs[1],symbol=entry['symbol'],side=opposite,
                       qty=str(qty),filled_qty=str(qty) if legs[1]=='filled' else '0',stop_price=entry['stop'])])


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.alpaca, self.now, self.news = FakeAlpaca(), ny(9, 35), []
        self.executor = self.make()

    def make(self):
        executor = GapFillExecutor(lambda: self.alpaca, Path(self.tmp.name) / 'x.sqlite3',
                                   Path(self.tmp.name) / 'Trading' / 'JOURNAL.md', news=lambda: self.news,
                                   clock=lambda: self.now)
        executor.symbols = ('QQQ',)
        return executor

    def start(self):
        self.executor.enabled = True
        self.executor.recover()
        return self.executor.decide('QQQ', bars(), 100.0)

    def state(self):
        return self.executor._trades()[0]['state']

    def calls(self, kind):
        return [c for c in self.alpaca.calls if c[0] == kind]

    def journal(self):
        return (Path(self.tmp.name) / 'Trading' / 'JOURNAL.md').read_text(encoding='utf-8')

    def at(self, hour, minute, day=27):
        self.now = ny(hour, minute, day)
        return self.executor.manage()

    def test_off_or_before_recovery_nothing_is_placed(self):
        self.executor.recover()
        self.executor.decide('QQQ', bars(), 100.0)
        fresh = self.make()
        fresh.enabled = True  # on, but start-up recovery has not run
        fresh.decide('QQQ', bars(), 100.0)
        self.assertEqual(self.calls('submit'), [])

    def test_weekend_is_skipped_silently_and_not_recorded(self):
        self.now = ny(9, 35, 31)  # Saturday 2026-10-31
        self.executor.enabled = True
        self.executor.recover()
        self.assertEqual(self.executor.decide('QQQ', [], 100.0), [])
        self.assertEqual(self.calls('submit'), [])
        with self.executor._db() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM gap_decisions').fetchone())

    def test_one_sized_bracket_recorded_before_it_is_sent(self):
        alerts = self.start()
        self.executor.decide('QQQ', bars(), 100.0)
        orders = [c[1] for c in self.calls('submit')]
        self.assertEqual(len(orders), 1)
        self.assertEqual((orders[0]['side'], orders[0]['entry'], orders[0]['stop'], orders[0]['target'], orders[0]['quantity']),
                         ('sell', Decimal('100.20'), Decimal('100.65'), Decimal('100.00'), 531))
        self.assertIn('PAPER bracket acknowledged', alerts[0])

    def test_a_crash_mid_request_is_never_resent_and_resolves_from_broker_evidence(self):
        self.alpaca.fail_submit = True
        with self.assertRaises(RuntimeError):
            self.start()
        self.assertEqual(self.state(), 'submitting')
        self.at(9, 41)  # Alpaca never received it
        self.assertEqual(self.state(), 'uncertain')
        self.assertEqual(len(self.calls('submit')), 1)

    def test_gates_that_cannot_pass_are_no_trade(self):
        for name, change, reason in [
                ('news', lambda: setattr(self, 'news', [self.now + timedelta(minutes=5)]), 'news filter'),
                ('holiday', lambda: setattr(self.alpaca, 'calendar', None), 'not a normal market day'),
                ('daily stop', lambda: setattr(self.alpaca, 'equity', (Decimal('89000'), Decimal('100000'))), 'Daily loss')]:
            with self.subTest(name):
                self.setUp()
                change()
                self.start()
                self.assertEqual(self.calls('submit'), [])
                self.assertIn(reason, self.journal())

    def test_an_unfilled_entry_is_cancelled_and_only_broker_evidence_ends_it(self):
        self.start()
        self.at(9, 40)
        self.assertIn(('cancel', 'broker-1'), self.alpaca.calls)
        self.assertEqual(self.state(), 'acknowledged')  # a cancel request is not a cancel
        self.alpaca.orders['ano-paper-gf20261027qqq']['status'] = 'canceled'
        self.at(9, 41)
        self.assertEqual(self.state(), 'expired')

    # ---- the four problems from the review
    def test_a_partial_fill_is_only_trusted_if_its_stop_really_exists(self):
        self.start()
        self.alpaca.fill('ano-paper-gf20261027qqq', 20, status='partially_filled')
        self.at(9, 40)
        self.assertIn(('cancel', 'broker-1'), self.alpaca.calls)
        # Alpaca cancels the rest, and with it the legs: 20 shares are left with no stop.
        self.alpaca.fill('ano-paper-gf20261027qqq', 20, status='canceled', legs=('canceled', 'canceled'))
        self.at(9, 41)
        self.assertIn(('close', 'QQQ', 20), self.alpaca.calls)  # Ano's 20 shares, not the planned 531
        self.assertEqual(self.state(), 'exiting')

    def test_closed_means_the_closing_fill_was_seen(self):
        self.start()
        self.alpaca.fill('ano-paper-gf20261027qqq', 531)
        self.at(9, 50)
        self.assertEqual(self.state(), 'filled')
        self.at(11, 0)
        self.assertEqual(self.calls('close'), [('close', 'QQQ', 531)])
        self.assertIn(('cancel', 'leg-target'), self.alpaca.calls)
        self.assertEqual(self.state(), 'exiting')  # requested, not yet closed
        self.at(11, 1)
        self.assertEqual(self.state(), 'exiting')
        self.alpaca.exit_status = 'filled'
        self.at(11, 2)
        self.assertEqual(self.state(), 'closed')
        self.assertIn('verified fill', self.journal())

    def test_alpaca_held_stop_leg_counts_as_protection(self):
        # After a bracket entry fills, Alpaca shows the target 'new' and the stop 'held'.
        self.start()
        self.alpaca.fill('ano-paper-gf20261027qqq', 531, legs=('new', 'held'))
        self.at(9, 50)
        self.assertEqual(self.state(), 'filled')
        self.assertEqual(self.calls('close'), [])
        self.assertNotIn(('cancel', 'leg-stop'), self.alpaca.calls)

    def test_restart_recovery_finds_a_fill_that_happened_while_ano_was_closed(self):
        self.start()
        self.assertEqual(self.state(), 'acknowledged')
        # Ano closes; the entry fills; the stop and target still work. Next day Ano starts again.
        self.alpaca.fill('ano-paper-gf20261027qqq', 531)
        self.now = ny(8, 0, day=28)
        restarted = self.make()
        restarted.recover()
        self.assertTrue(restarted.ready)
        self.assertEqual(self.calls('close'), [('close', 'QQQ', 531)])  # closed, never mistaken for unfilled
        self.assertNotIn(('cancel', 'broker-1'), self.alpaca.calls)

    def test_a_market_data_failure_never_skips_protection(self):
        self.start()
        self.alpaca.fill('ano-paper-gf20261027qqq', 531)
        self.now = ny(11, 0)
        broken_feed = lambda: (_ for _ in ()).throw(ToolError('data down'))
        worker = ExecutionWorker(self.executor, broken_feed)
        worker.step()
        self.assertEqual(self.calls('close'), [('close', 'QQQ', 531)])

    def test_a_stop_or_target_fill_closes_it_without_any_order_from_ano(self):
        self.start()
        self.alpaca.fill('ano-paper-gf20261027qqq', 531, legs=('filled', 'canceled'))
        self.at(10, 5)
        self.assertEqual(self.state(), 'closed')
        self.assertEqual(self.calls('close'), [])
        self.assertIn('CLOSED by its target', self.journal())

    def test_stop_paper_trading_closes_only_anos_shares(self):
        self.start()
        self.alpaca.fill('ano-paper-gf20261027qqq', 531)
        self.at(9, 50)
        self.assertIn('stopped by you', self.executor.stop_everything())
        self.assertFalse(self.executor.enabled)
        self.assertEqual(self.calls('close'), [('close', 'QQQ', 531)])

    def test_a_database_from_the_first_version_is_upgraded(self):
        import sqlite3
        path = Path(self.tmp.name) / 'old.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE gap_trades (client_id TEXT PRIMARY KEY, day TEXT, symbol TEXT, side TEXT, qty INTEGER, '
                       'entry TEXT, stop TEXT, target TEXT, risk_usd TEXT, state TEXT)')
            db.execute("INSERT INTO gap_trades VALUES ('ano-paper-old', '2026-10-26', 'QQQ', 'short', 5, '1', '2', '0.5', '1', 'closed')")
        db.close()
        executor = GapFillExecutor(lambda: self.alpaca, path, Path(self.tmp.name) / 'J.md', clock=lambda: self.now)
        self.assertEqual(executor.recover(), [])
        self.assertTrue(executor.ready)
        self.assertEqual(executor._trades()[0]['filled_qty'], 0)

    def test_a_daily_report_is_written_once(self):
        self.start()
        self.at(11, 6)
        self.at(11, 7)
        self.assertEqual(self.journal().count('Daily report 2026-10-27'), 1)


class HoldTests(unittest.TestCase):
    def test_only_the_authorized_strategy_gets_past_the_hold(self):
        transport = paper_orders.PaperOrderTransport('k', 's', store=None, client_factory=None)
        for strategy in (None, 'ict'):
            with self.assertRaisesRegex(ToolError, 'research hold'):
                transport.submit_once(client_id='ano-paper-x1', symbol='QQQ', side='buy', quantity=1,
                                      entry=100, stop=99, target=103, strategy=strategy)

    def test_the_gap_fills_lower_reward_risk_is_allowed_only_for_it(self):
        args = dict(client_id='ano-paper-gf1', symbol='QQQ', side='sell', quantity=10, entry=100.2, stop=100.65, target=100)
        with self.assertRaises(ToolError):
            bracket_payload(**args)
        self.assertEqual(bracket_payload(**args, min_reward_risk=0.2)['order_class'], 'bracket')

    def test_the_signal_reads_only_closed_bars_up_to_934(self):
        self.assertEqual(gap_signal(bars(), 100.0)[0][0], 'short')
        self.assertIn('stale', gap_signal(bars()[:2], 100.0)[1])  # latest bar 9:31: stale
        quiet = bars()[:4]  # no IEX trade in the 9:34 minute: the 9:33 close is still the price
        quiet[-1] = dict(quiet[-1], c=100.20)
        self.assertIn('missing',gap_signal(quiet,100.0)[1])
        self.assertIn('outside', gap_signal(bars(), 100.28)[1])  # a 0.02% gap says so, not "stale"
        self.assertIn('did not move toward', gap_signal(bars(closes_toward=False), 100.0)[1])


if __name__ == '__main__':
    unittest.main()
