from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from ano.performance import Performance


class PerformanceTests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Performance(Path(self.tmp.name))
    def record(self,identity,gross,cost=1,**kw):
        self.store.record(provider='oanda_practice',trade_id=identity,strategy='smc',pair='GBP_USD',session='london',
            gross=gross,cost=cost,cost_basis='broker_reported',evidence={'fill_verified':True},**kw)
    def test_costs_sample_and_realized_drawdown_are_separate_and_deduplicated(self):
        self.record('one',11,closed_at='2026-10-28T10:00:00Z');self.record('two',-19,closed_at='2026-10-28T11:00:00Z')
        self.record('one',11,closed_at='2026-10-28T10:00:00Z')
        group=self.store.summary()['groups'][0]
        self.assertEqual(group['trades'],2);self.assertEqual(group['cost'],'2');self.assertEqual(group['net'],'-10')
        self.assertEqual(group['max_realized_drawdown'],'20');self.assertEqual(self.store.summary()['selection_authority'],'none')
    def test_unknown_times_do_not_invent_drawdown_and_conflicts_do_not_overwrite(self):
        self.record('one',11)
        self.assertIsNone(self.store.summary()['groups'][0]['max_realized_drawdown'])
        with self.assertRaises(ValueError):self.record('one',99)
        self.assertEqual(self.store.summary()['groups'][0]['net'],'10')
    def test_scorecard_compares_bots_with_shared_names_and_sets_aside_bug_closes(self):
        from ano.performance import scorecard,describe_scorecard
        stock=lambda identity,strategy,gross,at:self.store.record(provider='alpaca_paper',trade_id=identity,strategy=strategy,pair='QQQ',
            session='new_york',gross=gross,cost=1,cost_basis='configured_allowance',evidence={'fill_verified':True},closed_at=at)
        stock('bug','ict_2022_v3',3,'2026-10-29T14:36:00Z')        # closed by the fixed stop-leg bug
        stock('a','ict_2022_v3',21,'2026-11-01T19:00:00Z')
        self.store.record(provider='oanda_practice',trade_id='f',strategy='fx_ict_native_v2',pair='USD_JPY',session='tokyo',
            gross=-9,cost=1,cost_basis='broker_reported',evidence={'fill_verified':True},closed_at='2026-11-01T20:00:00Z')
        card=scorecard(Path(self.tmp.name))
        self.assertEqual(card['excluded'],1)
        self.assertEqual(set(card['strategies']),{('alpaca_paper','ict'),('oanda_practice','ict')})
        self.assertEqual(card['bots']['alpaca_paper']['net'],20);self.assertEqual(card['bots']['oanda_practice']['losses'],1)
        text=describe_scorecard(Path(self.tmp.name))
        self.assertIn('Stocks (Alpaca paper): 1 trades',text);self.assertIn('too few trades',text)
    def test_weekly_scorecard_counts_only_recent_closes(self):
        from datetime import datetime,timezone
        from ano.performance import scorecard
        self.record('old',5,closed_at='2026-10-20T10:00:00Z');self.record('new',-3,closed_at='2026-11-02T10:00:00Z')
        card=scorecard(Path(self.tmp.name),since=datetime(2026,10,27,tzinfo=timezone.utc))
        self.assertEqual(card['bots']['oanda_practice']['trades'],1);self.assertEqual(card['bots']['oanda_practice']['losses'],1)
    def test_unverified_results_cannot_be_scored(self):
        with self.assertRaises(ValueError):self.store.record(provider='oanda_practice',trade_id='x',strategy='smc',pair='GBP_USD',session='london',gross=10,cost=0,cost_basis='broker_reported',evidence={'fill_verified':False})
