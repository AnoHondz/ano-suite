from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
from tests.test_paper_execution import FakeAlpaca,ny
from ano.strategy_execution import StrategyExecutor
from ano.strategy_signals import STRATEGIES


class StrategyExecutionTests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=ny(10,5);self.broker=FakeAlpaca();self.notes=[]
        account=self.broker.account
        self.broker.account=lambda:{**account(),'buying_power':Decimal('400000')}
        self.executor=self.make();self.executor.strategy_enabled=tuple(STRATEGIES);self.executor.recover()
        self.feed=Mock();self.feed.latest_quote.side_effect=lambda symbol:{'bid':99.99,'ask':100.01,'at':self.now.isoformat()}

    def make(self):
        return StrategyExecutor(lambda:self.broker,Path(self.tmp.name)/'paper.sqlite3',Path(self.tmp.name)/'journal.md',
                                news=lambda:[],clock=lambda:self.now)

    def plan(self,name='turtle soup',identity='signal1'):
        return dict(name=name,strategy=STRATEGIES[name],symbol='QQQ',side='long',entry=100,stop=99,target=103,
                    confirmed_at=self.now.timestamp()-20,last_closed_at=self.now.timestamp()-20,signal_id=identity)

    def send(self,plan=None):
        self.executor.offer(plan or self.plan());return self.executor.process_candidates(lambda:self.feed)

    def submissions(self):return [v for v in self.broker.calls if v[0]=='submit']

    def test_session_summary_waits_for_close_and_is_once_per_session(self):
        self.now=ny(16,4)
        self.assertEqual(self.executor._daily_report(self.executor._now()[1]),[])
        self.now=ny(16,5)
        report=self.executor._daily_report(self.executor._now()[1])
        self.assertEqual(len(report),1)
        self.assertIn('No orders attempted',report[0])
        self.assertIn('$+0.00',report[0])
        self.assertEqual(self.executor._daily_report(self.executor._now()[1]),[])

    def test_session_summary_respects_early_close(self):
        self.broker.market_day=lambda day:('09:30','13:00')
        self.now=ny(13,5)
        self.assertEqual(len(self.executor._daily_report(self.executor._now()[1])),1)

    def test_reservation_precedes_dispatch_and_one_account_owner_blocks_other_strategy(self):
        original=self.broker.submit_once
        def submit(**kwargs):
            self.assertEqual(self.executor._trades()[0]['state'],'submitting')
            return original(**kwargs)
        self.broker.submit_once=submit
        self.send();self.send(self.plan('smc','signal2'))
        self.assertEqual(len(self.submissions()),1)
        trade=self.executor._trades()[0]
        self.assertEqual(trade['strategy'],'turtle_soup_intraday_v1')
        self.assertGreater(trade['flatten_at'],trade['expires_at'])

    def test_lost_submit_response_never_replays_after_restart(self):
        self.broker.fail_submit=True;self.send()
        self.assertEqual(self.executor._trades()[0]['state'],'submitting')
        self.executor=self.make();self.executor.strategy_enabled=tuple(STRATEGIES);self.executor.recover()
        self.send();self.assertEqual(len(self.submissions()),1)

    def test_stale_signal_and_quote_and_unimplemented_version_do_not_dispatch(self):
        for change in ('signal','quote','version','forming'):
            plan=self.plan(identity=change)
            if change=='signal':plan['confirmed_at']-=100
            if change=='forming':plan['confirmed_at']+=100
            if change=='quote':self.feed.latest_quote.side_effect=lambda symbol:{'bid':99.99,'ask':100.01,'at':(self.now-timedelta(minutes=1)).isoformat()}
            if change=='version':plan['strategy']='unknown'
            self.send(plan)
        self.assertEqual(self.submissions(),[])

    def test_exposure_appearing_during_validation_blocks_order(self):
        calls=[0]
        def portfolio():
            calls[0]+=1
            return {'positions':[] if calls[0]==1 else [{'symbol':'MANUAL'}],'orders':[],'orders_complete':True}
        self.broker.portfolio_snapshot=portfolio;notes=self.send()
        self.assertEqual(self.submissions(),[]);self.assertIn('exposure changed',notes[0])

    def test_silver_bullet_uses_afternoon_expiry_and_window_exit_not_gap_times(self):
        self.now=ny(14,5);self.send(self.plan('silver bullet'))
        trade=self.executor._trades()[0];key=trade['client_id'];qty=trade['qty']
        self.now=ny(14,6);self.executor.manage()
        self.assertFalse(any(c[0]=='cancel' for c in self.broker.calls))
        self.broker.fill(key,qty);self.executor.manage()
        self.assertEqual(self.executor._trades()[0]['state'],'filled')
        self.now=ny(15,0);self.executor.manage()
        self.assertEqual(self.executor._trades()[0]['state'],'exiting')
        self.broker.exit_status='filled';self.executor.manage()
        self.assertEqual(self.executor._trades()[0]['state'],'closed')

    def test_stop_clears_all_modes_and_pending_candidates(self):
        self.executor.enabled=True;self.executor.offer(self.plan());self.executor.stop_everything()
        self.assertFalse(self.executor.enabled);self.assertEqual(self.executor.strategy_enabled,())
        self.executor.process_candidates(lambda:self.feed);self.assertEqual(self.submissions(),[])

    def insert(self,client,state='rejected',filled=0):
        with self.executor._db() as db:
            db.execute('INSERT INTO gap_trades(client_id,day,symbol,side,qty,entry,stop,target,risk_usd,state,filled_qty) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (client,self.executor._now()[1],'QQQ','long',1,'100','99','103','1.02',state,filled))

    def test_daily_entry_safety_limit_is_shared_across_every_strategy(self):
        from ano.trading_risk import MAX_ENTRIES_DAY
        for i in range(MAX_ENTRIES_DAY):self.insert('old'+str(i))
        notes=self.send();self.assertEqual(self.submissions(),[]);self.assertIn('entry daily safety limit',notes[0])

    def test_two_fill_verified_losses_block_next_trade_and_unknown_result_is_not_a_win(self):
        for i in range(2):
            key='ano-paper-loss'+str(i);self.insert(key,'closed',1)
            self.broker.orders[key]={'id':'broker-old'+str(i),'client_order_id':key,'symbol':'QQQ','side':'buy','qty':'1',
                'filled_qty':'1','status':'filled','filled_avg_price':'100','legs':[
                    {'id':'stop-old'+str(i),'symbol':'QQQ','side':'sell','type':'stop','status':'filled','qty':'1','filled_qty':'1','filled_avg_price':'99'}]}
        notes=self.send();self.assertEqual(self.submissions(),[]);self.assertIn('Two consecutive losses',notes[0])

    def test_daily_loss_halt_disables_entries_and_survives_reconstruction(self):
        self.send();self.broker.equity=(Decimal('89000'),Decimal('100000'))  # down 11%: past the 10% daily stop
        self.executor.manage();self.assertEqual(self.executor.strategy_enabled,())
        restored=self.make();self.assertTrue(restored.halted(self.executor._now()[1]))

    def test_pending_entry_expires_after_its_own_two_minute_timeout(self):
        self.now=ny(10,5);self.send(self.plan('turtle soup'))
        self.now=ny(10,8);self.executor.manage()
        self.assertIn(('cancel','broker-1'),self.broker.calls)

    def test_open_stock_trade_is_closed_just_before_high_impact_news(self):
        self.send();key=self.executor._trades()[0]['client_id'];self.broker.fill(key,self.executor._trades()[0]['qty'])
        self.executor.manage();self.assertEqual(self.executor._trades()[0]['state'],'filled')
        self.executor.news=lambda:[self.now+timedelta(minutes=4)];self.executor._news_cache=None
        self.executor.manage();self.assertEqual(self.executor._trades()[0]['state'],'exiting')

    def test_gap_retracement_limit_waits_like_the_forex_ict_limit(self):
        # ICT / Silver Bullet / AMD wait for a pullback into the gap: 30 minutes, capped by the strategy window.
        self.now=ny(14,5);self.send(self.plan('silver bullet'))
        trade=self.executor._trades()[0]
        self.assertEqual(trade['expires_at'],ny(14,35).timestamp())
        self.now=ny(14,8);self.executor.manage()
        self.assertNotIn(('cancel','broker-1'),self.broker.calls)
        self.now=ny(14,36);self.executor.manage()
        self.assertIn(('cancel','broker-1'),self.broker.calls)

    def test_strategy_not_enabled_has_no_attempt_or_submission(self):
        self.executor.strategy_enabled=();self.send()
        self.assertEqual(self.submissions(),[])
        with self.executor._db() as db:self.assertEqual(db.execute('SELECT count(*) FROM strategy_attempts').fetchone()[0],0)

    def test_enabling_one_adapter_preserves_other_permissions_without_dispatch(self):
        self.executor.strategy_enabled=('ict',)
        self.executor.enable_strategy('smc');self.executor.enable_strategy('smc')
        self.assertEqual(self.executor.strategy_enabled,('ict','smc'))
        self.assertEqual(self.submissions(),[])

    def test_quantity_respects_buying_power_as_well_as_risk_budget(self):
        original=self.broker.account
        self.broker.account=lambda:{**original(),'buying_power':Decimal('1000')}
        self.send();trade=self.executor._trades()[0]
        self.assertEqual(trade['qty'],9)
        self.assertEqual(Decimal(trade['risk_usd']),Decimal('9.18'))

    def test_replay_losers_are_paused_for_stocks(self):
        for name in ('ict','amd','opening range'):
            notes=self.send(self.plan(name,'paused-'+name))
            self.assertEqual(self.submissions(),[]);self.assertIn('paused',notes[0])

    def test_minimum_gross_r_without_net_cost_coverage_does_not_pass_risk_gate(self):
        plan=self.plan('turtle soup');plan['target']=101.5  # 1.5R gross, short of 1.5R once costs are counted
        notes=self.send(plan)
        self.assertEqual(self.submissions(),[]);self.assertIn('Net planned reward-to-risk',notes[0])

    def test_half_day_uses_actual_exchange_close_before_accepting_afternoon_signal(self):
        self.now=ny(14,5);self.broker.calendar=('09:30','13:00')
        notes=self.send(self.plan('silver bullet'))
        self.assertEqual(self.submissions(),[]);self.assertIn('session time',notes[0])

    def test_two_process_owners_cannot_reserve_different_strategies_on_empty_snapshot(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        second=self.make();second.strategy_enabled=tuple(STRATEGIES);second.recover()
        barrier=threading.Barrier(2)
        def quote(symbol):
            barrier.wait(timeout=5)
            return {'bid':99.99,'ask':100.01,'at':self.now.isoformat()}
        self.feed.latest_quote.side_effect=quote
        self.executor.offer(self.plan());second.offer(self.plan('smc','another'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(owner.process_candidates,lambda:self.feed) for owner in (self.executor,second)]
            for future in futures:future.result(timeout=15)
        self.assertEqual(len(self.submissions()),1)

    def test_stop_during_quote_validation_prevents_submission(self):
        def quote(symbol):
            self.executor.stop_everything()
            return {'bid':99.99,'ask':100.01,'at':self.now.isoformat()}
        self.feed.latest_quote.side_effect=quote;self.send()
        self.assertEqual(self.submissions(),[])

    def test_news_window_crossing_during_validation_blocks_order(self):
        release=self.now+timedelta(minutes=10,seconds=1)
        self.executor.news=lambda:[release]
        def quote(symbol):
            self.now+=timedelta(seconds=2)
            return {'bid':99.99,'ask':100.01,'at':self.now.isoformat()}
        self.feed.latest_quote.side_effect=quote
        notes=self.send();self.assertEqual(self.submissions(),[])
        self.assertIn('news exclusion',notes[0])
