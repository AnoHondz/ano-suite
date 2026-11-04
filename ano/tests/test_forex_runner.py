from datetime import datetime,timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo
from ano.forex_runner import ForexRunner
from ano.oanda_practice import PracticeAPI,HOST
from ano.strategy_signals import STRATEGIES


class Broker:
    def __init__(self,clock):
        self.clock=clock;self.orders={};self.trades={};self.submitted=[];self.closes=[];self.lost=False;self.close_lost=False
    def account_summary(self):return {'NAV':'100000','marginAvailable':'100000'}
    def portfolio(self):return list(t for t in self.trades.values() if t['state']=='OPEN'),[]
    def instruments(self):return {'EUR_USD':{'displayPrecision':5,'tradeUnitsPrecision':0,'minimumTradeSize':'1','maximumOrderUnits':'1000000','marginRate':'.02'}}
    def quote(self,pair):return {'bid':Decimal('1.10000'),'ask':Decimal('1.10010'),'loss':Decimal(1),'gain':Decimal(1),'at':self.clock()}
    def order(self,identity):return self.orders[identity]
    def trade(self,identity):return self.trades[identity]
    def submit(self,identity,p,units,bound):
        self.submitted.append(identity)
        self.orders[identity]={'instrument':p['pair'],'units':str(units),'clientExtensions':{'id':identity},'state':'FILLED','tradeOpenedID':'42'}
        self.trades['42']={'id':'42','instrument':p['pair'],'initialUnits':str(units),'currentUnits':str(units),'price':str(bound),'state':'OPEN',
            'clientExtensions':{'id':identity},
            'stopLossOrder':{'state':'PENDING','type':'STOP_LOSS','price':p['stop'],'tradeID':'42'},
            'takeProfitOrder':{'state':'PENDING','type':'TAKE_PROFIT','price':p['target'],'tradeID':'42'}}
        if self.lost:raise TimeoutError()
        return {'orderFillTransaction':{'tradeOpened':{'tradeID':'42'}}}
    def close(self,identity):
        self.closes.append(identity)
        if self.close_lost:raise TimeoutError()
        self.trades[identity].update(state='CLOSED',currentUnits='0',realizedPL='12',financing='-1',commission='0.5')


class ForexRunnerTests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=datetime(2026,10,29,14,5,tzinfo=ZoneInfo('America/New_York'))
        self.broker=Broker(lambda:self.now);self.runner=self.make();self.runner.manage()
    def make(self):return ForexRunner(Path(self.tmp.name),enabled=True,api_factory=lambda:self.broker,clock=lambda:self.now,news=lambda now:[])
    def plan(self):return {'pair':'EUR_USD','name':'turtle soup','strategy':'fx_'+STRATEGIES['turtle soup'],'side':'long','entry':'1.10000','stop':'1.09900',
        'target':'1.10400','confirmed_at':self.now.timestamp()-10,'last_closed_at':self.now.timestamp()-10,'signal_id':'one'}
    def test_intent_is_durable_before_dispatch_and_repeat_does_not_replay(self):
        original=self.broker.submit
        def submit(identity,*args):
            self.assertEqual(self.runner.rows()[0]['state'],'submitting')
            return original(identity,*args)
        self.broker.submit=submit
        self.runner.dispatch(self.plan());self.runner.dispatch(self.plan())
        self.assertEqual(len(self.broker.submitted),1)
    def test_lost_response_recovers_fill_without_replay(self):
        self.broker.lost=True
        with self.assertRaises(TimeoutError):self.runner.dispatch(self.plan())
        self.runner=self.make();self.runner.manage();self.runner.dispatch(self.plan())
        self.assertEqual(len(self.broker.submitted),1)
        messages=self.runner.poll();self.assertEqual(len(messages),1)
        self.assertIn('FILLED at',messages[0])
        self.runner.manage();self.assertEqual(self.runner.poll(),[])
    def test_stop_inside_the_spread_noise_is_skipped(self):
        p=self.plan();p['stop']='1.09980'  # 2 pips: closer than 5 pips / 3x the 1-pip spread
        self.runner.dispatch(p);self.assertEqual(self.broker.submitted,[])
    def test_size_follows_risk_not_the_old_100k_cap(self):
        self.runner.dispatch(self.plan())
        units=int(self.broker.orders[self.broker.submitted[0]]['units'])
        self.assertGreater(units,100000)                         # 0.25% of NAV over a 10-pip stop
        self.assertLessEqual(units*1.1001,3*100000)              # never above 3x account notional
    def test_open_trade_is_closed_just_before_high_impact_news(self):
        self.runner.dispatch(self.plan());self.runner.manage();self.assertEqual(self.broker.closes,[])
        self.runner.news=lambda now:[('EUR',self.now+timedelta(minutes=4))];self.runner._news=None
        self.runner.manage();self.assertEqual(self.broker.closes,['42'])
        self.assertIn('news',self.runner.rows()[0]['note'])
    def test_unrelated_or_unavailable_news_leaves_the_trade_to_its_stop(self):
        self.runner.dispatch(self.plan())
        self.runner.news=lambda now:[('JPY',self.now+timedelta(minutes=2))];self.runner._news=None
        self.runner.manage()
        def down(now):raise ValueError('News unavailable')
        self.runner.news=down;self.runner._news=None;self.runner.manage()
        self.assertEqual(self.broker.closes,[])
    def test_stale_and_future_signals_never_send(self):
        for delta in (-100,100):
            p=self.plan();p['confirmed_at']=self.now.timestamp()+delta;self.runner.dispatch(p)
        self.assertEqual(self.broker.submitted,[])
    def test_wrong_strategy_and_paused_never_send(self):
        p=self.plan();p['strategy']='wrong';self.runner.dispatch(p)
        self.runner.enabled=False;self.runner.dispatch(self.plan())
        self.assertEqual(self.broker.submitted,[])
    def test_stale_quote_blocks(self):
        quote=self.broker.quote
        self.broker.quote=lambda pair:{**quote(pair),'at':self.now-timedelta(seconds=30)}
        self.runner.dispatch(self.plan());self.assertEqual(self.broker.submitted,[])
        self.broker.quote=quote
    def test_account_switch_cannot_reuse_another_accounts_ledger(self):
        self.broker.account='101-001-12345678-001';self.runner.api()
        self.broker.account='101-001-87654321-001'
        with self.assertRaises(ValueError):self.runner.api()
    def test_scanner_and_manager_bind_same_account_without_startup_race(self):
        from concurrent.futures import ThreadPoolExecutor
        self.broker.account='101-001-12345678-001'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.runner.api(),range(10)))
        self.assertEqual(results,[self.broker]*10)
    def test_one_feed_failure_does_not_stop_other_pairs_scanning(self):
        def candles(pair,*args):
            if pair=='EUR_USD':raise TimeoutError()
            return [{'t':int(self.now.timestamp())-60,'o':1.1,'h':1.1,'l':1.1,'c':1.1}]
        self.broker.candles=candles
        with patch('ano.forex_runner.scan',return_value=([],{'ict':'0 candidates'})):
            self.runner.scan_once()
        self.assertIn('error',self.runner.observations['EUR_USD'])
        self.assertIn('strategies',self.runner.observations['GBP_USD'])
        self.assertIn('strategies',self.runner.observations['USD_CAD'])
    def test_three_attempts_and_two_losses_block_new_entries(self):
        for n in range(2):
            p=self.plan();p['signal_id']=str(n)
            with self.runner.db() as db:
                db.execute('INSERT INTO orders(id,day,plan,units,state,pnl) VALUES (?,?,?,?,?,?)',
                    (str(n),'2026-10-29',__import__('json').dumps(p),1,'closed','-10'))
        self.runner.dispatch(self.plan());self.assertEqual(self.broker.submitted,[])
        from ano.forex_order_policy import MAX_FILLS_DAY
        with self.runner.db() as db:
            db.execute("UPDATE orders SET pnl='10'")
            for n in range(MAX_FILLS_DAY):  # the runaway guard: wins, but the day's safety limit of entries is used
                db.execute("INSERT INTO orders(id,day,plan,units,state) VALUES (?,'2026-10-29','{}',1,'cancelled')",('r'+str(n),))
        self.runner.dispatch(self.plan());self.assertEqual(self.broker.submitted,[])
    def test_fresh_start_forgives_earlier_losses_only(self):
        for n in range(2):
            p={**self.plan(),'signal_id':str(n),'submitted_at':self.now.timestamp()-600}
            with self.runner.db() as db:
                db.execute('INSERT INTO orders(id,day,plan,units,state,pnl) VALUES (?,?,?,?,?,?)',
                    (str(n),'2026-10-29',__import__('json').dumps(p),1,'closed','-10'))
        (Path(self.tmp.name)/'forex-fresh-start.json').write_text(
            __import__('json').dumps({'since':self.now.timestamp()-300}),encoding='utf-8')
        self.runner.dispatch(self.plan());self.assertEqual(len(self.broker.submitted),1)
    def test_breakeven_arms_at_one_r_then_closes_if_price_comes_back(self):
        self.runner.dispatch(self.plan());self.runner.manage()
        fill=Decimal(self.broker.trades['42']['price']);risk=fill-Decimal('1.09900')
        quote=self.broker.quote
        self.broker.quote=lambda pair:{**quote(pair),'bid':fill+risk+Decimal('.00001'),'ask':fill+risk+Decimal('.00011')}
        self.runner.manage()
        plan=__import__('json').loads(self.runner.rows()[0]['plan'])
        self.assertIn('breakeven_level',plan);self.assertEqual(self.broker.closes,[])  # armed, still open
        self.broker.quote=lambda pair:{**quote(pair),'bid':Decimal(plan['breakeven_level']),'ask':Decimal(plan['breakeven_level'])+Decimal('.0001')}
        self.runner.manage()
        self.assertEqual(self.broker.closes,['42']);self.assertIn('breakeven',self.runner.rows()[0]['note'])
    def candles(self,signal_range=.0004,after=()):
        """20 calm minutes, a signal candle closing at now-60, then `after` candles (o, h, l, c)."""
        start=int(self.now.timestamp())-60*(22+len(after))
        rows=[{'t':start+60*i,'o':1.1,'h':1.1002,'l':1.0998,'c':1.1} for i in range(21)]
        rows.append({'t':start+60*21,'o':1.1,'h':1.1+signal_range/2,'l':1.1-signal_range/2,'c':1.1001})
        rows+=[{'t':start+60*(22+i),'o':o,'h':h,'l':l,'c':c} for i,(o,h,l,c) in enumerate(after)]
        return rows
    def test_legacy_signal_waits_for_a_confirming_candle_and_never_chases_a_spike(self):
        from ano.forex_order_policy import confirmation
        rows=self.candles()
        p={**self.plan(),'confirmed_at':rows[-1]['t']+60}
        self.assertEqual(confirmation(p,rows)[0],'wait')                                       # nothing after it yet
        rows=self.candles(after=[(1.1001,1.1003,1.0999,1.1002)])                               # bullish, holds the low
        p={**self.plan(),'confirmed_at':rows[-2]['t']+60}
        verdict,confirmed=confirmation(p,rows)
        self.assertEqual(verdict,'go');self.assertEqual(confirmed['entry'],'1.10020')
        self.assertEqual(confirmed['confirmed_at'],rows[-1]['t']+60)
        spike=self.candles(signal_range=.0020)                                                 # 5x the usual candle
        self.assertIn('spike',confirmation({**self.plan(),'confirmed_at':spike[-1]['t']+60},spike)[0])
        broke=self.candles(after=[(1.1,1.1,1.0989,1.0989)])                                    # through the stop first
        self.assertIn('invalidated',confirmation({**self.plan(),'confirmed_at':broke[-2]['t']+60},broke)[0])
        slow=self.candles(after=[(1.1,1.1001,1.0999,1.0999)]*5)                                # five red candles
        self.assertIn('no confirmation',confirmation({**self.plan(),'confirmed_at':slow[-6]['t']+60},slow)[0])
    def test_scan_holds_a_legacy_signal_until_a_candle_confirms_it(self):
        plan={**self.plan(),'session':'new_york','session_id':'new_york:2026-10-29'}
        waiting=self.candles();plan['confirmed_at']=waiting[-1]['t']+60
        feed={'rows':waiting}
        self.broker.candles=lambda pair,*args:feed['rows'] if pair=='EUR_USD' else []
        with patch('ano.forex_runner.scan',side_effect=lambda pair,*a,**k:([plan] if pair=='EUR_USD' else [],{})):
            self.runner.scan_once()
            self.assertEqual(list(self.runner.pending),[])                                     # waiting, not chasing
            feed['rows']=waiting+[{'t':waiting[-1]['t']+60,'o':1.1001,'h':1.1003,'l':1.0999,'c':1.1002}]
            self.now+=timedelta(minutes=1);self.runner.scan_once()
        self.assertEqual(len(self.runner.pending),1);self.assertEqual(self.runner.pending[0]['entry'],'1.10020')
        with patch('ano.forex_runner.scan',side_effect=lambda pair,*a,**k:([plan] if pair=='EUR_USD' else [],{})):
            self.runner.scan_once()
        self.assertEqual(len(self.runner.pending),1)                                           # decided once, never re-queued
    def test_liquidity_target_sits_in_front_of_the_pool(self):
        from ano.forex_v2 import objective
        pool={'kind':'high','price':1.1050,'confirmed_at':0,'taken_at':None}
        target,_=objective([],[pool],'long',1.1000,1.0980,100,front=.00015)
        self.assertAlmostEqual(target['price'],1.10485);self.assertEqual(target['pool'],1.1050)
        self.assertIsNotNone(objective([],[pool],'long',1.1000,1.0967,100)[0])                   # 50 pips >= 1.5 x 33
        self.assertIsNone(objective([],[pool],'long',1.1000,1.0967,100,front=.00015)[0])         # 48.5 pips after front-run: < 1.5R
    def test_winner_is_held_past_its_time_exit_with_a_locked_profit(self):
        self.runner.dispatch(self.plan());self.runner.manage()
        fill=Decimal(self.broker.trades['42']['price']);risk=fill-Decimal('1.09900')
        quote=self.broker.quote
        self.broker.quote=lambda pair:{**quote(pair),'bid':fill+risk*Decimal('.7'),'ask':fill+risk*Decimal('.7')+Decimal('.0001')}
        self.now=self.now.replace(hour=12,minute=0)                                            # a noon time exit
        with self.runner.db() as db:
            plan=__import__('json').loads(self.runner.rows()[0]['plan']);plan['flatten_at']=self.now.timestamp()-1
            db.execute('UPDATE orders SET plan=?',(__import__('json').dumps(plan),))
        self.runner.manage()
        self.assertEqual(self.broker.closes,[])                                                # +0.7R: held, not cut
        plan=__import__('json').loads(self.runner.rows()[0]['plan'])
        self.assertTrue(plan['held']);self.assertEqual(Decimal(plan['breakeven_level']),fill+risk*Decimal('.1'))
        self.broker.quote=lambda pair:{**quote(pair),'bid':fill+risk*Decimal('.05'),'ask':fill+risk*Decimal('.05')+Decimal('.0001')}
        self.runner.manage()
        self.assertEqual(self.broker.closes,['42']);self.assertIn('locked-profit',self.runner.rows()[0]['note'])
    def test_losing_trade_is_still_closed_at_its_time_exit(self):
        self.runner.dispatch(self.plan());self.runner.manage()
        with self.runner.db() as db:
            plan=__import__('json').loads(self.runner.rows()[0]['plan']);plan['flatten_at']=self.now.timestamp()-1
            db.execute('UPDATE orders SET plan=?',(__import__('json').dumps(plan),))
        self.runner.manage();self.assertEqual(self.broker.closes,['42'])
    def test_wide_spreads_and_a_fifth_daily_entry_are_refused(self):
        quote=self.broker.quote
        self.broker.quote=lambda pair:{**quote(pair),'ask':Decimal('1.10020')}  # 2-pip spread
        self.runner.dispatch(self.plan());self.assertEqual(self.broker.submitted,[])
        self.broker.quote=quote
        from ano.forex_order_policy import MAX_FILLS_DAY
        with self.runner.db() as db:
            for n in range(MAX_FILLS_DAY):
                db.execute("INSERT INTO orders(id,day,plan,units,state) VALUES (?,'2026-10-29','{}',1,'cancelled')",('r'+str(n),))
        self.runner.dispatch(self.plan());self.assertEqual(self.broker.submitted,[])
    def test_tokyo_jpy_dispatch_uses_yen_tick_and_usd_conversion(self):
        self.now=datetime(2026,10,30,10,tzinfo=ZoneInfo('Asia/Tokyo'))
        self.broker.instruments=lambda:{'USD_JPY':{'displayPrecision':3,'tradeUnitsPrecision':0,'minimumTradeSize':'1','maximumOrderUnits':'1000000','marginRate':'.04'}}
        self.broker.quote=lambda pair:{'bid':Decimal('149.999'),'ask':Decimal('150.001'),
            'loss':Decimal('.0067'),'gain':Decimal('.0066'),'at':self.now}
        p={**self.plan(),'pair':'USD_JPY','entry':'150.000','stop':'149.900','target':'150.400',
            'session':'tokyo','session_id':'tokyo:2026-10-30'}
        self.runner.dispatch(p)
        self.assertEqual(len(self.broker.submitted),1)
        row=self.runner.rows()[0];plan=__import__('json').loads(row['plan'])
        self.assertEqual(Decimal(plan['target'])%Decimal('.001'),0)
        risk=(Decimal(plan['entry'])-Decimal(plan['stop'])+Decimal('.004'))*Decimal('.0067')*row['units']
        self.assertLessEqual(risk,Decimal(250))
    def test_london_session_summary_is_once_even_without_trades(self):
        self.now=datetime(2026,10,29,8,30,tzinfo=ZoneInfo('Europe/London'))
        self.runner.watch_session('london',self.now)
        self.now=self.now.replace(hour=17,minute=4);self.runner.manage();self.assertEqual(self.runner.poll(),[])
        self.now=self.now.replace(minute=5);self.runner.manage()
        notes=self.runner.poll();self.assertEqual(len(notes),1);self.assertIn('london:2026-10-29',notes[0])
        self.runner.manage();self.assertEqual(self.runner.poll(),[])
    def test_news_for_either_currency_blocks(self):
        self.runner.news=lambda now:[('EUR',now)]
        self.runner.dispatch(self.plan());self.assertEqual(self.broker.submitted,[])
    def test_cancelled_entry_reason_is_verified_saved_and_never_retried(self):
        import json
        identity='ano-fx-'+'a'*28
        with self.runner.db() as db:db.execute('INSERT INTO orders(id,day,plan,units,state) VALUES (?,?,?,?,?)',
            (identity,'2026-10-29',json.dumps(self.plan()),100,'cancelled'))
        self.broker.cancellation=Mock(return_value={'reason':'BOUNDS_VIOLATION','transaction_id':'5','order_id':'4','at':self.now.isoformat()})
        self.runner.recover_cancellations();self.runner.recover_cancellations()
        self.broker.cancellation.assert_called_once_with(identity)
        self.assertIn('BOUNDS_VIOLATION',self.runner.command('forex cancellations'))
        self.assertEqual(self.broker.submitted,[])
    def test_summary_reports_cancel_reason_and_skipped_checks_once(self):
        import json
        identity='ano-fx-'+'c'*28;plan={**self.plan(),'session_id':'new_york:2026-10-29'}
        with self.runner.db() as db:db.execute('INSERT INTO orders(id,day,plan,units,state) VALUES (?,?,?,?,?)',
            (identity,'2026-10-29',json.dumps(plan),100,'cancelled'))
        self.broker.cancellation=Mock(return_value={'reason':'BOUNDS_VIOLATION','transaction_id':'5','order_id':'4','at':self.now.isoformat()})
        self.runner.skip(plan,'high-impact currency news');self.runner.watch_session('new_york',self.now)
        self.now=self.now.replace(hour=16,minute=5);self.runner.manage()
        messages=self.runner.poll();self.assertEqual(len(messages),1)
        self.assertIn('BOUNDS_VIOLATION',messages[0]);self.assertIn('high-impact currency news',messages[0])
        self.runner.manage();self.assertEqual(self.runner.poll(),[])
    def test_external_exposure_is_not_adopted_or_closed(self):
        self.broker.trades['external']={'id':'999','state':'OPEN'}
        with self.assertRaises(ValueError):self.runner.manage()
        self.runner.dispatch(self.plan())
        self.assertEqual(self.broker.submitted,[]);self.assertEqual(self.broker.closes,[])
    def test_missing_protection_closes_exact_owned_trade(self):
        self.runner.dispatch(self.plan());self.broker.trades['42'].pop('stopLossOrder')
        self.runner.manage();self.assertEqual(self.broker.closes,['42'])
        self.runner.manage();self.assertEqual(self.runner.rows()[0]['pnl'],'10.5')
    def test_changed_ownership_never_closes(self):
        self.runner.dispatch(self.plan());self.runner.manage()
        self.broker.trades['42']['clientExtensions']={'id':'external'}
        with self.assertRaises(ValueError):self.runner.manage()
        self.assertEqual(self.broker.closes,[])
    def test_uncertain_close_never_repeats(self):
        self.runner.dispatch(self.plan());self.runner.manage();self.runner.enabled=False;self.broker.close_lost=True
        with self.assertRaises(TimeoutError):self.runner.manage()
        with self.assertRaises(ValueError):self.runner.manage()
        self.assertEqual(self.broker.closes,['42'])
    def test_flatten_and_summary_use_verified_pnl_and_once(self):
        self.runner.dispatch(self.plan());self.runner.manage();self.runner.poll()
        self.now=self.now.replace(hour=15,minute=55);self.runner.manage();self.runner.manage()
        self.now=self.now.replace(hour=16,minute=5);self.runner.manage()
        report=self.runner.poll();self.assertEqual(len(report),1);self.assertIn('$+10.50',report[0])
        self.runner.manage();self.assertEqual(self.runner.poll(),[])
    def test_daily_drawdown_halts_entries_and_closes_owned(self):
        self.runner.dispatch(self.plan());self.runner.manage()
        self.broker.account_summary=lambda:{'NAV':'89000','marginAvailable':'89000'}  # down 11%: past the 10% stop
        self.runner.manage();self.assertEqual(self.broker.closes,['42'])
        self.runner.manage();self.runner.dispatch(self.plan());self.assertEqual(len(self.broker.submitted),1)
    def test_transport_is_practice_only_and_market_bracket_bounded(self):
        with self.assertRaises(ValueError):PracticeAPI({'account':'001-001-12345678-001','token':'fake'})
        client=Mock();response=Mock(status_code=201);response.json.return_value={};client.request.return_value=response
        factory=Mock();factory.return_value.__enter__=Mock(return_value=client);factory.return_value.__exit__=Mock(return_value=False)
        api=PracticeAPI({'account':'101-001-12345678-001','token':'fake'},factory)
        api.submit('ano-fx-'+'a'*28,self.plan(),100,Decimal('1.10010'))
        self.assertEqual(factory.call_args.kwargs['base_url'],HOST)
        payload=client.request.call_args.kwargs['json']['order']
        self.assertEqual(payload['timeInForce'],'FOK');self.assertEqual(payload['positionFill'],'OPEN_ONLY')
        self.assertEqual(payload['priceBound'],'1.10010');self.assertIn('stopLossOnFill',payload)
    def test_cancellation_receipt_must_match_order_and_transaction(self):
        identity='ano-fx-'+'b'*28
        api=PracticeAPI({'account':'101-001-12345678-001','token':'fixture'})
        api.order=Mock(return_value={'id':'4','state':'CANCELLED','clientExtensions':{'id':identity},'cancellingTransactionID':'5'})
        api.read=Mock(return_value={'transaction':{'id':'5','type':'ORDER_CANCEL','orderID':'4','clientOrderID':identity,'reason':'BOUNDS_VIOLATION','time':self.now.isoformat()}})
        self.assertEqual(api.cancellation(identity)['reason'],'BOUNDS_VIOLATION')
        api.read.return_value['transaction']['orderID']='99'
        with self.assertRaises(ValueError):api.cancellation(identity)
