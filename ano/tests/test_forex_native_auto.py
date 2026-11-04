from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
from ano.forex_runner import ForexRunner
from ano.forex_shadow import fingerprint,execution_settings
from ano.reliability import atomic_json
from tests.test_forex_runner import Broker
import json


class NativeAutoTests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.state=Path(self.tmp.name)
        self.now=datetime(2026,10,30,8,30,tzinfo=timezone.utc)
        self.broker=Broker(lambda:self.now)
        self.runner=ForexRunner(self.state,enabled=True,api_factory=lambda:self.broker,clock=lambda:self.now,news=lambda now:[])
        self.runner.manage()
        atomic_json(self.state/'forex-native-execution.json',{'version':'fx-native-v2','enabled':True,
            'provider':'oanda_practice','authorization':'explicit_user_practice_auto','engine':fingerprint()})
        def submit(body):
            self.broker.orders[body['clientExtensions']['id']]={**body,'state':'PENDING'}
            return {'orderCreateTransaction':{'id':'1'}}
        self.broker.submit_order_payload=Mock(side_effect=submit)
        self.broker.portfolio=lambda:([],[o for o in self.broker.orders.values() if o['state']=='PENDING'])
        self.broker.cancel_order=Mock(side_effect=TimeoutError())

    def plan(self):
        at=self.now.timestamp()
        return {'version':'fx-native-v2','name':'ict','strategy':'fx_ict_native_v2','pair':'EUR_USD',
            'side':'long','entry':'1.09900','stop':'1.09800','target':'1.10400','entry_type':'LIMIT',
            'session':'london','session_id':'london:2026-10-30','signal_id':'a'*28,
            'confirmed_at':at,'last_closed_at':at,'expires_at':at+1800,'flatten_at':at+3600}

    def test_native_limit_is_queued_at_midpoint_then_verified_as_pending(self):
        p=self.plan();self.runner.dispatch(p);self.runner.dispatch(p)
        self.broker.submit_order_payload.assert_called_once()
        order=self.broker.submit_order_payload.call_args.args[0]
        self.assertEqual(order['type'],'LIMIT');self.assertEqual(order['price'],'1.09900')
        self.assertEqual(order['timeInForce'],'GTD')
        self.runner.manage();self.assertEqual(self.runner.rows()[0]['state'],'pending_verified')
        self.assertTrue(self.runner.ready)

    def test_expired_pending_cancel_timeout_never_repeats(self):
        self.runner.dispatch(self.plan());self.runner.manage();self.now+=timedelta(minutes=31)
        with self.assertRaises(TimeoutError):self.runner.manage()
        self.assertEqual(self.runner.rows()[0]['state'],'pending_cancel_uncertain')
        with self.assertRaisesRegex(ValueError,'GET-only'):self.runner.manage()
        self.broker.cancel_order.assert_called_once()

    def test_source_mismatch_and_no_optin_block_automatic_dispatch(self):
        atomic_json(self.state/'forex-native-execution.json',{'enabled':True,'engine':'old'})
        self.runner.dispatch(self.plan());self.broker.submit_order_payload.assert_not_called()
        self.assertFalse(execution_settings(self.state)['enabled'])

    def test_news_before_expiry_shortens_gtd_and_near_news_blocks(self):
        release=self.now+timedelta(minutes=20)
        self.runner.news=lambda now:[('EUR',release)]
        self.runner.dispatch(self.plan())
        order=self.broker.submit_order_payload.call_args.args[0]
        self.assertEqual(datetime.fromisoformat(order['gtdTime'].replace('Z','+00:00')),release-timedelta(minutes=10))

    def test_manual_pending_order_is_not_adopted(self):
        self.broker.orders['manual']={'state':'PENDING','id':'1','type':'LIMIT'}
        with self.assertRaisesRegex(ValueError,'unknown pending'):self.runner.manage()
        self.runner.ready=False;self.runner.dispatch(self.plan())
        self.broker.submit_order_payload.assert_not_called()

    def test_native_market_bound_is_sized_and_structural_target_unchanged(self):
        p={**self.plan(),'name':'smc','strategy':'fx_smc_native_v2','entry_type':'MARKET',
           'entry':'1.10000','stop':'1.09800','target':'1.10600'}
        self.runner.dispatch(p)
        body=self.broker.submit_order_payload.call_args.args[0]
        self.assertEqual(body['type'],'MARKET');self.assertGreater(Decimal(body['priceBound']),Decimal('1.10010'))
        self.assertEqual(body['takeProfitOnFill']['price'],'1.10600')
        saved=json.loads(self.runner.rows()[0]['plan'])
        self.assertLessEqual(Decimal(saved['planned_risk_usd']),Decimal(250))
