from datetime import datetime,timedelta
from unittest import TestCase
from unittest.mock import patch
from ano import ict
from ano.strategy_signals import candidates,cost_target,bounds


class SignalAdapterTests(TestCase):
    def rows(self,n=37):
        start=int(datetime(2026,10,29,9,30,tzinfo=ict.NY).timestamp())
        return [dict(t=start+i*60,o=100,h=101,l=99,c=100) for i in range(n)]

    def now(self,rows):return datetime.fromtimestamp(rows[-1]['t']+80,ict.NY)

    def setup(self,rows,**kwargs):
        return dict(direction='long',entry=100,stop=99,target=103,grade='A',status='waiting for entry',
                    decided_at=ict.stamp(rows[-1]['t']),fvg_at=ict.stamp(rows[-1]['t']),amd_aligned=True,**kwargs)

    def test_ict_silver_and_amd_take_only_current_waiting_plans(self):
        rows=self.rows();now=self.now(rows);s=self.setup(rows)
        for name in ('ict','silver bullet','amd'):
            self.assertEqual(len(candidates(name,'QQQ',rows,now,{'setups':[s]})),1)
            for state in ('win','loss','in trade','in trade (bar simulation)','expired','missed (ran without a retrace)'):
                self.assertEqual(candidates(name,'QQQ',rows,now,{'setups':[dict(s,status=state)]}),[])
        self.assertEqual(candidates('amd','QQQ',rows,now,{'setups':[dict(s,amd_aligned=False)]}),[])

    def test_old_future_wrong_session_missing_and_outside_window_plans_never_signal(self):
        rows=self.rows();now=self.now(rows);s=self.setup(rows)
        for t in (rows[-1]['t']-300,rows[-1]['t']+60,rows[-1]['t']-86400):
            self.assertEqual(candidates('ict','QQQ',rows,now,{'setups':[dict(s,decided_at=ict.stamp(t))]}),[])
        self.assertEqual(candidates('ict','QQQ',rows[:-2]+rows[-1:],now,{'setups':[s]}),[])
        self.assertIsNone(bounds('silver bullet',datetime(2026,10,29,13,tzinfo=ict.NY)))
        self.assertEqual(bounds('silver bullet',datetime(2026,10,29,14,5,tzinfo=ict.NY))[1].hour,15)

    def test_turtle_execution_target_accounts_for_cost_without_reusing_simulated_fill(self):
        rows=self.rows();now=self.now(rows)
        report={'setups':[{'direction':'long','entry':100,'stop':99,'target':102,'confirmed_at':rows[-1]['t']}]}
        plan=candidates('turtle soup','QQQ',rows,now,report)[0]
        # The structural 1-point stop sits inside this fixture's 5-minute noise, so it is widened (never tightened).
        self.assertLess(plan['stop'],99);self.assertEqual(plan['target'],cost_target(100,plan['stop'],'long'))
        self.assertEqual(cost_target(100,101,'short'),98.45)  # 1.5R plus 2.5 x $0.02 costs

    def test_stock_trades_follow_the_days_trend(self):
        from ano.strategy_signals import trend_allows
        rows=self.rows();now=self.now(rows)
        rising=[dict(b,v=1000,c=100+i*.01,h=100.2+i*.01,l=99.8+i*.01) for i,b in enumerate(rows)]
        self.assertTrue(trend_allows({'side':'long'},rising,now))   # last close above VWAP
        self.assertFalse(trend_allows({'side':'short'},rising,now))
        self.assertFalse(trend_allows({'side':'long'},[dict(b,v=0) for b in rising],now))  # no volume, no trade

    def test_smc_requires_later_retest_rejection_and_all_timeframe_alignment(self):
        rows=self.rows();rows[-1].update(o=99.8,l=99.7,h=100.5,c=100.2);now=self.now(rows)
        zone=dict(direction='bullish',low=99,high=100,origin=rows[-6]['t'],confirmed_at=rows[-3]['t']+60,
                  retested_at=rows[-1]['t']+60,status='retested')
        report={'order_blocks':[zone],'higher_timeframes':{'alignment':'bullish'}}
        self.assertEqual(len(candidates('smc','QQQ',rows,now,report)),1)
        self.assertEqual(candidates('smc','QQQ',rows,now,{**report,'higher_timeframes':{'alignment':'mixed'}}),[])
        self.assertEqual(candidates('smc','QQQ',rows,now,{**report,'order_blocks':[dict(zone,confirmed_at=rows[-1]['t']+60)]}),[])

    def test_opening_range_uses_current_first_breakout_not_historical_next_open(self):
        rows=self.rows();rows[-1].update(h=102,c=101.2);now=self.now(rows)
        self.assertEqual(len(candidates('opening range','QQQ',rows,now)),1)
        rows[-3].update(h=102,c=101.1)
        self.assertEqual(candidates('opening range','QQQ',rows,now),[])

    def test_technicals_require_current_ema_crossover_and_rsi_not_context_alone(self):
        rows=self.rows(51);rows[-1].update(h=102,c=101);now=self.now(rows)
        data=dict(stale=False,atr14=.5,ema20=100.1,ema50=100.05,rsi14=60)
        with patch('ano.chart_analysis.indicator_snapshot',return_value=data):
            self.assertEqual(len(candidates('technicals','QQQ',rows,now)),1)
        with patch('ano.chart_analysis.indicator_snapshot',return_value={**data,'rsi14':85}):
            self.assertEqual(candidates('technicals','QQQ',rows,now),[])
