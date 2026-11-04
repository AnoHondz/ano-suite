from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import httpx

from ano.paper_feed import AlpacaPaperFeed
from ano.plugins.paper_trading import PaperTrading
from ano.plugins.workshop import Workshop
from ano.tools import ToolError


class PaperFeedTests(unittest.TestCase):
    def feed(self, handler):
        self.calls=[]
        def route(request):
            self.calls.append(request)
            return handler(request)
        def factory(**kwargs):
            self.assertFalse(kwargs['follow_redirects'])
            return httpx.Client(transport=httpx.MockTransport(route),**kwargs)
        return AlpacaPaperFeed('fixture-key','fixture-secret',client_factory=factory)

    def test_only_paper_account_get_is_used_and_private_fields_are_omitted(self):
        feed=self.feed(lambda r:httpx.Response(200,json={'status':'ACTIVE','trading_blocked':False,
            'id':'private-account','account_number':'private-number','equity':'100000'}))
        status=feed.verify()
        self.assertFalse(status['execution_enabled'])
        self.assertTrue(status['paper_endpoint_verified'])
        self.assertNotIn('private',json.dumps(status))
        self.assertEqual(str(self.calls[0].url),'https://paper-api.alpaca.markets/v2/account')
        self.assertEqual(self.calls[0].method,'GET')

    def test_redirect_and_auth_errors_never_follow_or_leak_secret(self):
        for code in (302,401,403,429):
            feed=self.feed(lambda r:httpx.Response(code,headers={'Location':'https://example.org/'},
                json={'message':'fixture-secret'}))
            with self.assertRaises(ToolError) as caught: feed.verify()
            self.assertNotIn('fixture-secret',str(caught.exception))
            self.assertEqual(len(self.calls),1)

    def test_feed_keeps_the_session_through_quiet_minutes_and_cuts_at_a_session_break(self):
        # 13:00 is the previous session; 14:00–14:04 has one quiet minute (14:01) with no IEX bar.
        bars=[dict(t=f'2026-10-28T{stamp}:00Z',o=10,h=11,l=9,c=10)
              for stamp in ('14:04','14:03','14:02','14:00','13:00')]
        feed=self.feed(lambda r:httpx.Response(200,json={'bars':{'QQQ':bars}}))
        data=feed.candles('QQQ',now=datetime(2026,10,28,15,tzinfo=timezone.utc))
        self.assertEqual(data['omitted_before_last_gap'],1)
        self.assertEqual(len(data['candles']),4)
        self.assertEqual(data['missing_minutes'],1)
        self.assertEqual(self.calls[0].url.host,'data.alpaca.markets')
        self.assertEqual(self.calls[0].url.params['feed'],'iex')
        with self.assertRaises(ToolError): feed.candles('NQ')
        self.assertEqual(len(self.calls),1)

    def test_missing_credentials_never_call_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            factory=Mock()
            plugin=PaperTrading(Workshop(Path(tmp),None),key_loader=lambda:None,feed_factory=factory)
            with self.assertRaisesRegex(ToolError,'not configured'):
                plugin.run('paper_connection_status',{})
            factory.assert_not_called()

    def test_higher_timeframes_use_fixed_get_and_filter_forming_bars(self):
        feed=self.feed(lambda r:httpx.Response(200,json={'bars':{'QQQ':[
            dict(t='2026-10-28T04:00:00Z',o=100,h=103,l=99,c=102),
            dict(t='2026-10-29T15:00:00Z',o=102,h=104,l=100,c=103)]}}))
        result=feed.higher_timeframes('QQQ',now=datetime(2026,10,29,15,1,tzinfo=timezone.utc))
        self.assertEqual(set(result),{'daily','4h','1h'})
        self.assertTrue(all(len(rows)==1 for rows in result.values()))
        self.assertEqual([r.url.params['timeframe'] for r in self.calls],['1Day','4Hour','1Hour'])
        self.assertTrue(all(r.method=='GET' and r.url.host=='data.alpaca.markets' for r in self.calls))

    def test_watchlist_pipeline_writes_actual_files_with_mocked_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed=Mock()
            feed.candles.return_value=dict(omitted_before_last_gap=1,candles=[
                dict(t=1000+i*60,o=10+i,h=11+i,l=9+i,c=10+i) for i in range(6)])
            plugin=PaperTrading(Workshop(Path(tmp),None),clock=lambda:1360,
                key_loader=lambda:('fixture-key','fixture-secret'),feed_factory=lambda *keys:feed)
            text,_=plugin.run('analyze_paper_watchlist',{'symbol':'QQQ'})
            result=json.loads(text)
            self.assertTrue(Path(result['chart']).exists())
            self.assertIn('1 earlier bars excluded',result['report']['source'])
            self.assertNotIn('fixture-secret',text)
            self.assertFalse(result['report']['execution_authorized'])
            feed.verify.assert_called_once()


if __name__=='__main__': unittest.main()
