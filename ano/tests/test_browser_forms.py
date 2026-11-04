import json
from unittest import TestCase
from unittest.mock import Mock

from ano.browser_forms import BrowserForms
from ano.tools import ToolError


class BrowserFormTests(TestCase):
    def setUp(self):
        self.control=Mock()
        self.control._generation=1
        self.control.guard.return_value=True
        self.bridge=Mock(listening=True)
        self.bridge.browsers.return_value=['chrome']
        self.now=100
        self.forms=BrowserForms(self.bridge,self.control,clock=lambda:self.now)
        self.page={'documentId':'doc-1','url':'https://fixture.test/form','fields':[
            {'id':'f1','tag':'textarea','type':'textarea','label':'Comment','max_length':3000},
            {'id':'f2','tag':'select','label':'Score','options':[{'label':'Monitor','value':'1'}]},
            {'id':'f3','tag':'input','type':'radio','label':'Billable Yes'}]}
        self.bridge.call.return_value=self.page
        self.obs=json.loads(self.forms.inspect_browser_form('chrome:7')[0])['observation_id']
        self.fields=[{'element_id':'f1','operation':'fill','value':'Synthetic care note'},
                     {'element_id':'f2','operation':'select','value':'1'},
                     {'element_id':'f3','operation':'check','checked':True}]
        self.bridge.call.reset_mock()

    def test_batch_routes_all_fields_and_requires_value_verification(self):
        self.bridge.call.return_value={'matched':3,'total':3,'all_matched':True,'submitted':False}
        output,note=self.forms.fill_browser_form(self.obs,'Fill the supplied notes',self.fields)
        receipt=json.loads(output)
        self.assertFalse(receipt['submitted'])
        self.assertEqual(receipt['matched'],3)
        args=self.bridge.call.call_args
        self.assertEqual(args.args,('chrome','form_fill'))
        self.assertEqual(args.kwargs['documentId'],'doc-1')
        self.assertEqual(len(args.kwargs['fields']),3)
        self.assertIn('Synthetic care note',self.control.request_review.call_args.kwargs['detail'])
        self.forms.verify_browser_form(receipt['receipt_id'])
        self.assertEqual(self.bridge.call.call_args.args[1],'form_verify')
        with self.assertRaises(ToolError):
            self.forms.fill_browser_form(self.obs,'Replay',self.fields)

    def test_unknown_duplicate_invalid_option_and_overlength_fail_before_dispatch(self):
        for fields in ([{'element_id':'bad','operation':'fill','value':'x'}],
                       [self.fields[0],self.fields[0]],
                       [{'element_id':'f2','operation':'select','value':'Monitor'}],
                       [{'element_id':'f1','operation':'fill','value':'x'*3001}]):
            self.bridge.call.return_value=self.page
            obs=json.loads(self.forms.inspect_browser_form('chrome:7')[0])['observation_id']
            self.bridge.call.reset_mock()
            with self.assertRaises(ToolError): self.forms.fill_browser_form(obs,'Test',fields)
            self.bridge.call.assert_not_called()

    def test_cancel_or_stop_during_review_prevents_dispatch(self):
        self.control.request_review.side_effect=lambda **kw:self.control.guard.configure_mock(return_value=False)
        with self.assertRaises(ToolError): self.forms.fill_browser_form(self.obs,'Test',self.fields)
        self.bridge.call.assert_not_called()
        self.forms.stop()
        self.bridge.cancel_forms.assert_called_once()
        self.assertIsNone(self.forms.observation)

    def test_uncertain_fill_does_not_replay_and_does_not_claim_matched(self):
        self.bridge.call.side_effect=TimeoutError()
        with self.assertRaisesRegex(ToolError,'Some fields may have changed'):
            self.forms.fill_browser_form(self.obs,'Test',self.fields)
        with self.assertRaises(ToolError): self.forms.fill_browser_form(self.obs,'Replay',self.fields)
        self.assertEqual(self.bridge.call.call_count,1)

    def test_expired_inspection_and_receipt_cannot_be_used(self):
        self.now+=61
        with self.assertRaises(ToolError): self.forms.fill_browser_form(self.obs,'Test',self.fields)
        self.bridge.call.assert_not_called()
        with self.assertRaises(ToolError): self.forms.verify_browser_form('missing')
