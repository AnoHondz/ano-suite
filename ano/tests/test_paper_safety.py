from unittest.mock import Mock
import unittest
from tests.test_paper_execution import ExecutorTests,bars
from ano.paper_evidence import bracket_evidence
from ano.paper_execution import gap_signal
from ano.tools import ToolError
from ano.paper_execution import ExecutionWorker,fetch_news
from datetime import timedelta


class PaperSafetyTests(ExecutorTests):
    """Reuse the offline broker fixture; inherited lifecycle tests run as well."""
    def test_one_working_leg_never_counts_as_protected(self):
        self.start();self.alpaca.fill('ano-paper-gf20261027qqq',531,legs=('new','canceled'))
        self.at(9,50)
        self.assertEqual(self.calls('close'),[('close','QQQ',531)])
        self.assertEqual(self.state(),'exiting')

    def test_wrong_protective_price_triggers_verified_exit(self):
        self.start();self.alpaca.fill('ano-paper-gf20261027qqq',531)
        self.alpaca.orders['ano-paper-gf20261027qqq']['legs'][1]['stop_price']='999'
        self.at(9,50)
        self.assertEqual(self.state(),'exiting')

    def test_pending_bracket_cancellation_blocks_close_until_confirmed(self):
        self.start();self.alpaca.fill('ano-paper-gf20261027qqq',531)
        self.alpaca.cancel=Mock(return_value=True)
        self.at(11,0)
        self.assertEqual(self.calls('close'),[])
        self.assertFalse(self.executor.management_verified)

    def test_manual_position_overlap_never_closes_or_cancels_unknown_exposure(self):
        self.start();self.alpaca.fill('ano-paper-gf20261027qqq',531)
        self.alpaca.position=lambda symbol:{'qty':-600,'side':'short'}
        self.at(11,0)
        self.assertEqual(self.calls('close'),[]);self.assertEqual(self.calls('cancel'),[])

    def test_uncertain_close_is_reserved_before_dispatch_and_never_replayed(self):
        self.start();self.alpaca.fill('ano-paper-gf20261027qqq',531)
        calls=[]
        def crash(*args):
            calls.append(args)
            self.assertEqual(self.state(),'exit_uncertain')
            raise ToolError('simulated lost close response')
        self.alpaca.close_shares=crash
        self.at(11,0);self.at(11,1);self.executor.stop_everything()
        self.assertEqual(len(calls),1)
        self.assertEqual(self.state(),'exit_uncertain')

    def test_partial_closing_fill_never_means_closed(self):
        self.start();self.alpaca.fill('ano-paper-gf20261027qqq',531)
        self.at(11,0)
        self.alpaca.get_order=lambda identifier:{'symbol':'QQQ','side':'buy','status':'filled','filled_qty':'500'}
        self.at(11,1)
        self.assertEqual(self.state(),'exiting')
        self.assertFalse(self.executor.management_verified)

    def test_other_account_exposure_blocks_new_entry(self):
        self.alpaca.portfolio_snapshot=lambda:{'positions':[{'symbol':'OTHER'}],'orders':[],'orders_complete':True}
        self.start()
        self.assertEqual(self.calls('submit'),[])
        self.assertIn('account exposure',self.journal())

    def test_wrong_day_and_missing_minute_block_without_submission(self):
        self.executor.enabled=True;self.executor.recover()
        broken=bars();broken.pop(2)
        self.executor.decide('QQQ',broken,100)
        self.assertEqual(self.calls('submit'),[])
        self.assertIn('missing',gap_signal(broken,100)[1])

    def test_stop_during_partial_entry_waits_for_entry_cancel_then_closes_remainder(self):
        self.start();self.alpaca.fill('ano-paper-gf20261027qqq',20,status='partially_filled')
        self.at(9,36);self.executor.stop_everything()
        self.assertEqual(self.calls('close'),[])
        self.assertIn(('cancel','broker-1'),self.calls('cancel'))
        self.alpaca.fill('ano-paper-gf20261027qqq',20,status='canceled',legs=('canceled','canceled'))
        self.at(9,37)
        self.assertEqual(self.calls('close'),[('close','QQQ',20)])

    def test_mismatched_child_never_cancels_someone_elses_order(self):
        self.start();self.alpaca.fill('ano-paper-gf20261027qqq',531)
        self.alpaca.orders['ano-paper-gf20261027qqq']['legs'][0]['symbol']='OTHER'
        self.at(11,0)
        self.assertFalse(self.executor.management_verified)
        self.assertEqual(self.calls('cancel'),[]);self.assertEqual(self.calls('close'),[])

    def test_nonfinal_entry_state_is_never_treated_as_closed_or_protected(self):
        self.start();self.alpaca.fill('ano-paper-gf20261027qqq',20,status='done_for_day')
        self.at(11,0)
        self.assertFalse(self.executor.management_verified)
        self.assertEqual(self.calls('close'),[])

    def test_missing_prior_closing_minute_does_not_substitute_an_older_day(self):
        feed=Mock();feed.between.return_value=[{'t':0,'c':100}]
        self.assertIsNone(ExecutionWorker._prior_close(feed,'QQQ',self.now.date()))
        self.assertEqual(feed.between.call_count,1)

    def test_prior_close_uses_exchange_half_day_close(self):
        feed=Mock()
        feed.between.side_effect=lambda symbol,start,end,**kw:[{'t':end.timestamp()-60,'c':99.5}]
        self.assertEqual(ExecutionWorker._prior_close(feed,'QQQ',self.now.date(),lambda day:('09:30','13:00')),99.5)

    def test_invalid_prior_close_fails_as_a_no_trade_signal(self):
        for prior in (0,float('nan'),float('inf'),-1):self.assertIsNone(gap_signal(bars(),prior)[0])

    def test_empty_or_previous_week_news_calendar_never_means_no_news(self):
        client=Mock();client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=False)
        response=client.get.return_value;response.status_code=200;response.content=b'[]'
        for rows in ([],[{'country':'USD','impact':'High','date':(self.now-timedelta(days=8)).isoformat()}]):
            response.json.return_value=rows
            with self.assertRaises(ToolError):fetch_news(now=self.now,client_factory=lambda **kw:client)
