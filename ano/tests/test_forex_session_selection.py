from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from ano.forex_runner import ForexRunner
from ano.reliability import atomic_json


class SelectionTests(TestCase):
    def test_dated_selection_restricts_only_named_session_and_expires(self):
        with TemporaryDirectory() as directory:
            now=datetime(2026,10,30,12,tzinfo=timezone.utc)
            runner=ForexRunner(Path(directory),clock=lambda:now)
            atomic_json(Path(directory)/'forex-session-selection.json',
                        {'session':'london','date':'2026-10-30','strategies':['smc']})
            self.assertTrue(runner.strategy_allowed({'session':'london','name':'smc'}))
            self.assertFalse(runner.strategy_allowed({'session':'london','name':'ict'}))
            self.assertTrue(runner.strategy_allowed({'session':'new_york','name':'ict'}))
            now+=timedelta(days=1)
            self.assertTrue(runner.strategy_allowed({'session':'london','name':'ict'}))

    def test_dispatch_cannot_queue_nonselected_strategy(self):
        with TemporaryDirectory() as directory:
            runner=ForexRunner(Path(directory),clock=lambda:datetime(2026,10,30,12,tzinfo=timezone.utc))
            atomic_json(Path(directory)/'forex-session-selection.json',
                        {'session':'london','date':'2026-10-30','strategies':['smc']})
            runner.dispatch({'session':'london','name':'ict','pair':'EUR_USD'})
            self.assertEqual(runner.rows(),[])
            with runner.db() as db:
                self.assertEqual(db.execute('select reason from skip_checks').fetchone()[0],
                                 'today session strategy selection')
