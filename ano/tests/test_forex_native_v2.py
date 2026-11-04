from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock,patch
from contextlib import closing
from ano import forex_v2 as fx
from ano.forex_order_policy import budget,prepare,payload
from ano.forex_v2_execution import Lifecycle,IntentStore
from ano.forex_replay import replay,chronological_review
from ano.forex_shadow import ShadowStore,validation_status
import json

NOW=datetime(2026,10,30,8,3,tzinfo=timezone.utc)


def ict_rows():
    start=int(NOW.replace(hour=7,minute=0).timestamp())
    rows=[dict(t=start+i*60,o=1.1005,h=1.1006,l=1.1004,c=1.10051) for i in range(63)]
    rows[20].update(l=1.0980)
    rows[50].update(h=1.1010)
    rows[54].update(l=1.1000)
    rows[60].update(o=1.1007,h=1.1012,l=1.10065,c=1.1010)
    rows[61].update(o=1.1010,h=1.10102,l=1.1002,c=1.1003)
    rows[62].update(o=1.1003,h=1.1005,l=1.0998,c=1.0999)
    return rows


def plan(kind='LIMIT'):
    return {'version':fx.VERSION,'name':'ict' if kind=='LIMIT' else 'smc','pair':'EUR_USD','session':'london',
            'session_id':'london:2026-10-30','side':'long','entry':'1.09900' if kind=='LIMIT' else '1.10000',
            'stop':'1.09800','target':'1.10400' if kind=='LIMIT' else '1.10600','confirmed_at':NOW.timestamp(),
            'expires_at':NOW.timestamp()+1800,'flatten_at':NOW.timestamp()+3600,
            'entry_type':kind,'signal_id':'fixture'}


def prepared(kind='LIMIT'):
    return prepare(plan(kind),{'bid':Decimal('1.10000'),'ask':Decimal('1.10010'),'loss':1,'gain':1},
                   {'minimumTradeSize':'1','maximumOrderUnits':'100000','marginRate':'.02'},
                   {'NAV':'100000','marginAvailable':'100000'},{'nav':'100000'})


class NativeTests(TestCase):
    def test_native_local_sessions_follow_dst_without_synthetic_timestamps(self):
        london=fx.session_context('london',NOW)
        self.assertEqual(london['open'],NOW.replace(minute=0).timestamp())
        summer=fx.session_context('london',datetime(2026,7,15,7,5,tzinfo=timezone.utc))
        self.assertTrue(summer['active'])
        self.assertEqual(datetime.fromtimestamp(summer['open'],timezone.utc).hour,7)
        tokyo=fx.session_context('tokyo',datetime(2026,10,30,0,5,tzinfo=timezone.utc))
        self.assertEqual(datetime.fromtimestamp(tokyo['open'],timezone.utc).hour,0)
        self.assertFalse(fx.session_context('london',datetime(2026,10,31,9,tzinfo=timezone.utc))['active'])

    def test_actual_ict_pattern_uses_midpoint_limit_and_30minute_expiry(self):
        result=fx.scan('EUR_USD',ict_rows(),{},NOW,'london')
        choices=[p for p in result['plans'] if p['name']=='ict']
        self.assertTrue(choices,result['diagnostics'])
        p=choices[0]
        self.assertEqual(p['entry_type'],'LIMIT')
        self.assertEqual(p['confirmed_at'],NOW.timestamp())
        self.assertEqual(p['expires_at'],NOW.timestamp()+1800)
        self.assertEqual(p['side'],'short')
        self.assertIsNone(p['probability'])
        self.assertEqual(Decimal(p['entry']),Decimal('1.10058'))

    def test_future_source_candles_cannot_change_current_signal(self):
        rows=ict_rows();original=fx.scan('EUR_USD',rows,{},NOW,'london')
        future=[dict(t=int(NOW.timestamp()),o=5,h=8,l=1,c=7)]
        self.assertEqual(original,fx.scan('EUR_USD',rows+future,{},NOW,'london'))

    def test_incomplete_prior_day_not_treated_as_verified_high_low(self):
        rows=ict_rows();c=fx.session_context('london',NOW)
        self.assertFalse(any(p['source']=='prior local day' for p in fx.known_levels(rows,c,NOW)))

    def test_nearest_obstacle_cannot_be_skipped_for_farther_two_r_target(self):
        levels=[{'kind':'high','price':1.101,'confirmed_at':1,'taken_at':None},
                {'kind':'high','price':1.105,'confirmed_at':1,'taken_at':None}]
        objective,reason=fx.objective([],levels,'long',1.1,1.099,10)
        self.assertIsNone(objective);self.assertIn('less than 1.5R',reason)  # the nearer 1R pool is not skipped

    def test_opposing_order_block_blocks_otherwise_distant_target(self):
        levels=[{'kind':'high','price':1.105,'confirmed_at':1,'taken_at':None}]
        _,reason=fx.objective([],levels,'long',1.1,1.099,10,
            [{'direction':'bearish','status':'active','low':1.102,'high':1.103}])
        self.assertEqual(reason,'opposing block before target')
        _,reason=fx.objective([],levels,'long',1.1,1.099,10,
            [{'direction':'bearish','status':'active','low':1.0995,'high':1.1005}])
        self.assertEqual(reason,'entry inside opposing block')

    def test_weekend_closure_does_not_make_fridays_4h_structure_stale(self):
        from zoneinfo import ZoneInfo
        ny=ZoneInfo('America/New_York');friday_close=datetime(2026,10,30,17,0,tzinfo=ny)
        swing=lambda i:100+(i%7)-(i%5)
        rows=[dict(t=int(friday_close.timestamp())-(40-i)*14400,o=swing(i),c=swing(i)+.5,h=swing(i)+1.5,l=swing(i)-1) for i in range(40)]
        sunday_evening=datetime(2026,11,1,19,20,tzinfo=ny)
        self.assertNotEqual(fx.structural_bias(rows,sunday_evening,14400)['state'],'unavailable')
        # Friday 17:00 to Sunday 17:00 New York; 49 real hours this weekend because US clocks fell back on Nov 1.
        reopen=datetime(2026,11,1,17,0,tzinfo=ny)
        self.assertEqual(fx.weekend_closed_seconds(friday_close.timestamp(),sunday_evening.timestamp()),reopen.timestamp()-friday_close.timestamp())
        midweek=datetime(2026,11,4,19,20,tzinfo=ny)                 # genuinely days old: still stale
        self.assertEqual(fx.structural_bias(rows,midweek,14400)['state'],'unavailable')

    def test_orders_carry_the_strategy_name_for_the_broker_record(self):
        body=payload('ano-fx-'+'a'*28,prepared())
        self.assertEqual(body['clientExtensions']['id'],'ano-fx-'+'a'*28)
        self.assertEqual(body['clientExtensions']['tag'],'ano ict');self.assertIn('london',body['tradeClientExtensions']['comment'])

    def test_native_stop_must_clear_the_spread_noise(self):
        tight={**plan('MARKET'),'stop':'1.09980'}
        with self.assertRaisesRegex(ValueError,'3x spread or 5 pips'):
            prepare(tight,{'bid':Decimal('1.10000'),'ask':Decimal('1.10010'),'loss':1,'gain':1},
                    {'minimumTradeSize':'1','maximumOrderUnits':'1000000','marginRate':'.02'},
                    {'NAV':'100000','marginAvailable':'100000'},{'nav':'100000'})

    def test_bias_requires_pivot_break_not_just_rising_closes(self):
        rows=[dict(t=int(NOW.timestamp())-(30-i)*3600,o=100+i,h=102+i,l=99+i,c=101+i) for i in range(30)]
        self.assertEqual(fx.structural_bias(rows,NOW,3600)['state'],'unconfirmed')

    def test_shadow_capture_is_immutable_and_never_promotes(self):
        import sqlite3
        with TemporaryDirectory() as directory:
            store=ShadowStore(directory);store.observe('EUR_USD',ict_rows(),{},NOW,'london')
            with closing(sqlite3.connect(store.path)) as db, db:
                with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE candles SET sha='changed'")
            self.assertFalse(validation_status(directory)['execution_enabled'])


class OrderPolicyTests(TestCase):
    def test_verified_cancels_release_fill_slots_but_not_session_submission_cap(self):
        from ano.forex_order_policy import MAX_SUBMISSIONS_SESSION as n
        rows=[{'id':str(i),'state':'cancelled','day':'2026-10-29','trade_id':None,
               'plan':json.dumps({'session_id':'new_york:2026-10-29'})} for i in range(n)]
        self.assertEqual(budget(rows,'tokyo:2026-10-30')['fills_or_reserved'],n)
        verified={str(i) for i in range(n)}
        b=budget(rows,'tokyo:2026-10-30',verified)
        self.assertIsNone(b['reason']);self.assertEqual(b['fills_or_reserved'],0)
        self.assertEqual(budget(rows,'new_york:2026-10-29',verified)['reason'],'bounded session submission cap')

    def test_two_trades_at_once_on_different_pairs_and_never_beside_foreign_exposure(self):
        from ano.forex_order_policy import exposure,open_risk
        mine={'id':'ano-fx-1','state':'filled','trade_id':'42'}
        trade={'id':'42','instrument':'GBP_USD'}
        protective=[{'type':'STOP_LOSS','tradeID':'42'},{'type':'TAKE_PROFIT','tradeID':'42'}]
        self.assertIsNone(exposure('USD_CAD',[mine],[trade],protective))  # a trade's own stop/target are not entries
        self.assertEqual(exposure('GBP_USD',[mine],[trade],protective),'already exposed on this pair')
        second={'id':'ano-fx-2','state':'filled','trade_id':'43'}
        self.assertIn('already open',exposure('EUR_USD',[mine,second],[trade,{'id':'43','instrument':'USD_CAD'}],[]))
        self.assertEqual(exposure('EUR_USD',[],[{'id':'999','instrument':'AUD_USD'}],[]),'account exposure not opened by Ano')
        self.assertEqual(exposure('EUR_USD',[{'id':'x','state':'submitting'}],[],[]),'unresolved order intent')
        self.assertEqual(open_risk('100000',[trade],protective),Decimal('250.0000'))

    def test_market_bound_is_adverse_and_included_in_risk_budget(self):
        p=prepared('MARKET')
        self.assertGreater(Decimal(p['bound']),Decimal('1.10010'))
        self.assertLessEqual(Decimal(p['planned_risk_usd']),Decimal(250))

    def test_limit_keeps_exact_price_and_expiry_without_market_price_bound(self):
        p=prepared();body=payload('ano-fx-'+'a'*28,p)
        self.assertEqual(body['type'],'LIMIT');self.assertEqual(body['price'],'1.09900')
        self.assertEqual(body['timeInForce'],'GTD');self.assertNotIn('priceBound',body)
        self.assertEqual(body['triggerCondition'],'DEFAULT')

    def test_costs_can_reject_structural_minimum_r_without_moving_target(self):
        p=plan();p['target']='1.10050'  # exactly 1.5R gross; costs leave it short of 1.5R net
        with self.assertRaisesRegex(ValueError,'net risk check'):
            prepare(p,{'bid':1.1,'ask':1.1001,'loss':1,'gain':1},
                {'minimumTradeSize':'1','maximumOrderUnits':'100000','marginRate':'.02'},
                {'NAV':'100000','marginAvailable':'100000'},{'nav':'100000'})
        self.assertEqual(p['target'],'1.10050')

    def test_restart_or_timeout_never_resubmits_durable_order(self):
        with TemporaryDirectory() as directory:
            store=IntentStore(str(Path(directory)/'intents.db'));api=Mock()
            api.submit_order_payload.side_effect=TimeoutError()
            identity='ano-fx-'+'b'*28
            with self.assertRaises(TimeoutError):Lifecycle(api,store,lambda:NOW).submit(identity,prepared())
            self.assertEqual(store.read(identity)['state'],'uncertain')
            with self.assertRaisesRegex(ValueError,'durable intent'):
                Lifecycle(api,store,lambda:NOW).submit(identity,prepared())
            api.submit_order_payload.assert_called_once()

    def test_pending_limit_uses_broker_expiry_and_unknown_cancel_is_not_retried(self):
        with TemporaryDirectory() as directory:
            store=IntentStore(str(Path(directory)/'intents.db'));api=Mock();p=prepared();identity='ano-fx-'+'c'*28
            lifecycle=Lifecycle(api,store,lambda:NOW);lifecycle.submit(identity,p)
            api.order.return_value={**payload(identity,p),'state':'PENDING'}
            self.assertEqual(lifecycle.reconcile(store.read(identity)),'pending_verified')
            api.cancel_order.side_effect=TimeoutError()
            with self.assertRaises(TimeoutError):lifecycle.reconcile(store.read(identity),allow_pending=False)
            with self.assertRaisesRegex(ValueError,'cancel outcome uncertain'):
                lifecycle.reconcile(store.read(identity),allow_pending=False)
            api.cancel_order.assert_called_once()

    def test_limit_transport_uses_practice_endpoint_and_gtd_bracket(self):
        from ano.oanda_practice import PracticeAPI
        api=PracticeAPI({'account':'101-001-12345678-001','token':'fixture'})
        api.request=Mock(return_value={'orderCreateTransaction':{'id':'1'}})
        p=prepared();p['expires_at']=datetime.now(timezone.utc).timestamp()+600
        body=payload('ano-fx-'+'d'*28,p);api.submit_order_payload(body)
        args=api.request.call_args
        self.assertEqual(args.args[0],'POST');self.assertEqual(args.kwargs['payload']['order']['price'],p['entry'])
        self.assertEqual(args.kwargs['payload']['order']['type'],'LIMIT')
        body['instrument']='BTC_USD'
        with self.assertRaises(ValueError):api.submit_order_payload(body)

    def test_fill_must_match_owned_trade_and_sized_bound(self):
        with TemporaryDirectory() as directory:
            store=IntentStore(str(Path(directory)/'intents.db'));api=Mock();p=prepared();identity='ano-fx-'+'e'*28
            lifecycle=Lifecycle(api,store,lambda:NOW);lifecycle.submit(identity,p)
            api.order.return_value={**payload(identity,p),'state':'FILLED','tradeOpenedID':'42'}
            api.trade.return_value={'id':'42','instrument':'EUR_USD','initialUnits':str(p['units']),
                'clientExtensions':{'id':identity},'price':'1.09910','state':'OPEN'}
            with self.assertRaisesRegex(ValueError,'worst price'):lifecycle.reconcile(store.read(identity))


class ReplayTests(TestCase):
    def candles(self):
        return [dict(t=int(NOW.timestamp())+i*60,
            bid={'o':1.1000,'h':1.1002,'l':1.0999,'c':1.1000},
            ask={'o':1.1001,'h':1.1003,'l':1.1000,'c':1.1001}) for i in range(4)]

    def scanner(self,pair,rows,higher,now,session):
        if len(rows)!=1:return {'plans':[],'diagnostics':[]}
        p=plan();p['confirmed_at']=now.timestamp()
        return {'plans':[p],'diagnostics':[]}

    def test_midpoint_is_not_a_market_fill_and_news_missing_blocks(self):
        r=replay('EUR_USD','london',self.candles(),{},news=[],scanner=self.scanner)
        self.assertEqual(r['trades'],[]);self.assertTrue(r['assumptions']['unfilled_end_of_data'])
        missing=replay('EUR_USD','london',self.candles(),{},scanner=self.scanner)
        self.assertEqual(missing['diagnostics']['historical news unavailable'],1)

    def test_future_higher_bars_are_not_visible_to_scanner(self):
        seen=[]
        def scanner(pair,rows,higher,now,session):
            seen.extend(higher['1h']);return {'plans':[],'diagnostics':[]}
        replay('EUR_USD','london',self.candles(),{'1h':[dict(t=int(NOW.timestamp()),o=1,h=2,l=1,c=2)]},scanner=scanner)
        self.assertEqual(seen,[])

    def test_market_same_bar_stop_and_target_is_a_loss_with_adverse_slippage(self):
        def scanner(pair,rows,higher,now,session):
            if len(rows)!=1:return {'plans':[],'diagnostics':[]}
            p=plan('MARKET');p['confirmed_at']=now.timestamp()
            return {'plans':[p],'diagnostics':[]}
        bars=self.candles();bars[1]['bid'].update(l=1.097,h=1.107)
        bars[1]['ask'].update(l=1.0971,h=1.1071)
        result=replay('EUR_USD','london',bars,{},news=[],scanner=scanner)
        self.assertEqual(result['trades'][0]['status'],'loss')
        self.assertLess(result['trades'][0]['net_r'],-1)

    def test_holdout_does_not_count_training_or_unresolved_as_wins(self):
        r={'trades':[{'confirmed_at':1,'entered_at':2,'net_r':3,'session_id':'a'},
                     {'confirmed_at':10,'entered_at':11,'net_r':None,'session_id':'b'},
                     {'confirmed_at':12,'entered_at':13,'net_r':-1,'session_id':'b'}]}
        result=chronological_review(r,5)
        self.assertEqual(result['holdout_closed'],1);self.assertEqual(result['unresolved'],1)
        self.assertEqual(result['mean_net_r'],-1);self.assertFalse(result['execution_enabled'])
