from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx

from ano.paper_orders import PaperOrderStore, PaperOrderTransport, bracket_payload
from ano.tools import ToolError


class PaperOrderTests(unittest.TestCase):
    def setUp(self):
        # Transport mechanics use MockTransport only. Production remains held.
        hold_patch=patch('ano.paper_orders.PAPER_EXECUTION_HOLD',False)
        hold_patch.start()
        self.addCleanup(hold_patch.stop)
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=PaperOrderStore(Path(self.temp.name)/'orders.sqlite3')
        self.plan=dict(client_id='ano-paper-fixture1',symbol='QQQ',side='buy',quantity=1,entry=100,stop=99,target=103)
        self.requests=[]

    def transport(self,handler):
        def route(request):
            self.requests.append(request)
            return handler(request)
        def factory(**kwargs):
            self.assertFalse(kwargs['follow_redirects'])
            return httpx.Client(transport=httpx.MockTransport(route),**kwargs)
        return PaperOrderTransport('fixture-key','fixture-secret',self.store,client_factory=factory)

    def accepted(self,request):
        return httpx.Response(200,json=dict(id='broker-fixture',client_order_id=self.plan['client_id'],status='new'))

    def test_fixed_paper_bracket_and_acknowledgement_is_not_fill_or_protection(self):
        transport=self.transport(self.accepted)
        result=transport.submit_once(**self.plan)
        self.assertEqual(result['state'],'acknowledged')
        self.assertFalse(result['fill_verified'])
        self.assertFalse(result['protection_verified'])
        self.assertEqual(str(self.requests[0].url),'https://paper-api.alpaca.markets/v2/orders')
        import json
        payload=json.loads(self.requests[0].content)
        self.assertEqual(payload['order_class'],'bracket')
        self.assertFalse(payload['extended_hours'])
        self.assertEqual(payload['stop_loss'],{'stop_price':'99'})

    def test_research_hold_blocks_before_journal_claim_or_network(self):
        transport=self.transport(self.accepted)
        with patch('ano.paper_orders.PAPER_EXECUTION_HOLD',True):
            with self.assertRaisesRegex(ToolError,'research hold'):
                transport.submit_once(**self.plan)
        self.assertEqual(self.requests,[])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM paper_orders').fetchone()[0],0)

    def test_explicitly_authorized_adapters_use_only_fixed_paper_host(self):
        import json
        from ano.strategy_signals import STRATEGIES
        def accept(request):
            body=json.loads(request.content)
            return httpx.Response(200,json={'id':'broker-fixture','client_order_id':body['client_order_id'],'status':'new'})
        transport=self.transport(accept)
        with patch('ano.paper_orders.PAPER_EXECUTION_HOLD',True):
            for i,strategy in enumerate(STRATEGIES.values()):
                result=transport.submit_once(**{**self.plan,'client_id':'ano-paper-authorized'+str(i),'strategy':strategy})
                self.assertEqual(result['state'],'acknowledged')
        self.assertEqual(len(self.requests),len(STRATEGIES))
        self.assertTrue(all(str(r.url)=='https://paper-api.alpaca.markets/v2/orders' for r in self.requests))

    def test_permission_or_freshness_expiring_while_claiming_never_sends_order(self):
        transport=self.transport(self.accepted)
        for i,kwargs in enumerate(({'dispatch_deadline':1},{'can_dispatch':lambda:False})):
            result=transport.submit_once(**{**self.plan,'client_id':'ano-paper-expired'+str(i),**kwargs})
            self.assertEqual(result['state'],'not_sent');self.assertFalse(result['dispatched'])
        self.assertEqual(self.requests,[])

    def test_timeout_never_repeats_post_even_after_reopening_store(self):
        def timeout(request): raise httpx.ReadTimeout('fixture-secret')
        transport=self.transport(timeout)
        result=transport.submit_once(**self.plan)
        self.assertEqual(result['state'],'dispatch_uncertain')
        self.assertNotIn('fixture-secret',str(result))
        transport.store=PaperOrderStore(self.store.path)
        self.assertFalse(transport.submit_once(**self.plan)['dispatched'])
        self.assertEqual(len(self.requests),1)

    def test_concurrent_identical_requests_dispatch_at_most_once(self):
        transport=self.transport(self.accepted)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:transport.submit_once(**self.plan),range(2)))
        self.assertEqual(sum(result['dispatched'] for result in results),1)
        self.assertEqual(len(self.requests),1)

    def test_identifier_reuse_with_different_payload_is_rejected(self):
        transport=self.transport(self.accepted)
        transport.submit_once(**self.plan)
        with self.assertRaises(ToolError): transport.submit_once(**(self.plan|{'quantity':2}))
        self.assertEqual(len(self.requests),1)

    def test_bad_scope_unprotected_direction_fractional_and_nan_never_dispatch(self):
        transport=self.transport(self.accepted)
        for change in ({'symbol':'NQ'},{'quantity':'.5'},{'entry':'NaN'},
                       {'stop':101},{'target':101},{'client_id':'https://example.org/'}):
            with self.subTest(change=change),self.assertRaises(ToolError):
                transport.submit_once(**(self.plan|change))
        self.assertEqual(self.requests,[])

    def test_redirect_server_error_and_wrong_ack_stay_uncertain(self):
        for response in (httpx.Response(302,headers={'Location':'https://example.org/'}),
                         httpx.Response(500),httpx.Response(200,json={'id':'x','client_order_id':'wrong'})):
            plan=self.plan|{'client_id':self.plan['client_id']+str(len(self.requests))}
            transport=self.transport(lambda request:response)
            self.assertEqual(transport.submit_once(**plan)['state'],'dispatch_uncertain')
        self.assertEqual(len(self.requests),3)

    def test_reconciliation_reads_existing_order_without_resubmission(self):
        transport=self.transport(self.accepted)
        transport.submit_once(**self.plan)
        transport.reconcile(self.plan['client_id'])
        self.assertEqual([request.method for request in self.requests],['POST','GET'])
        self.assertTrue(self.requests[-1].url.path.endswith('orders:by_client_order_id'))
        self.assertFalse(transport.submit_once(**self.plan)['dispatched'])


if __name__=='__main__': unittest.main()
