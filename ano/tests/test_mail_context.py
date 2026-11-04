from datetime import date
from email.message import EmailMessage
import unittest
from unittest.mock import Mock,patch

from ano.mail_context import resolve
from tests import test_sports_mail as fixtures


class MailContextTests(unittest.TestCase):
    def setUp(self):
        from tests.inference_fixture import isolate_inference
        isolate_inference(self)

    desk=fixtures.SportsMailTests.desk
    def test_relative_follow_up_uses_parent_report(self):
        parent={'report_command':'nba picks','day':'2026-10-28'}
        self.assertEqual(resolve('tomorrow instead',parent,date(2026,10,28)),{'command':'nba picks','day':'2026-10-29'})
        self.assertEqual(resolve('props too',parent),{'command':'nba props','day':'2026-10-28'})
        self.assertEqual(resolve('same for nfl',parent),{'command':'nfl picks','day':'2026-10-28'})

    def test_contextless_followup_clarifies_without_model(self):
        self.assertEqual(resolve('tomorrow instead')['command'],'clarify:context')

    def test_explicit_dated_email_request(self):
        self.assertEqual(resolve('please show me nba props tomorrow',today=date(2026,10,28)),
                         {'command':'nba props','day':'2026-10-29'})
        self.assertEqual(resolve('nba picks 2026-02-30')['command'],'clarify:date')
        self.assertEqual(resolve('email me nba props tomorrow',today=date(2026,10,28)),
                         {'command':'nba props','day':'2026-10-29'})

    def test_quoted_history_cannot_dispatch_followup(self):
        self.assertEqual(resolve('> props too',{'report_command':'nba picks'})['command'],'help')
        self.assertTrue(resolve('delete files and nba props')['command'].startswith('ask:'))

    def test_dated_command_reaches_report_builder(self):
        desk,_=self.desk();desk.reports.build.return_value={'title':'test','content':'public report'}
        desk.content('nba props',day='2026-10-29')
        desk.reports.build.assert_called_once_with('nba',props=True,day='2026-10-29')

    def test_only_known_sent_reply_context_is_used(self):
        desk,server=self.desk();desk.reports.build.return_value={'title':'NBA','content':'NBA public report'}
        desk.deliver('first','nba picks',day='2026-10-29')
        outbound=server.send_message.call_args.args[0]['Message-ID']
        reply=EmailMessage();reply['In-Reply-To']=outbound
        parent=desk.parent_context(reply)
        self.assertEqual(parent['report_command'],'nba picks')
        self.assertEqual(parent['day'],'2026-10-29')
        other=EmailMessage();other['In-Reply-To']='<unknown@example.com>'
        self.assertIsNone(desk.parent_context(other))

    def test_uncertain_delivery_is_not_trusted_context(self):
        desk,server=self.desk();server.send_message.side_effect=OSError()
        desk.deliver('first','help')
        reply=EmailMessage();reply['In-Reply-To']=server.send_message.call_args.args[0]['Message-ID']
        self.assertIsNone(desk.parent_context(reply))

    def test_explanation_receives_only_matching_report_and_previous_exchange(self):
        desk,_=self.desk()
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"message":{"content":"Answer"}}'
        opener=Mock();opener.open.return_value=response
        import json
        with patch('ano.sports_mail.build_opener',return_value=opener):
            desk.answer('Why that player?',parent={'public_context':'MATCHED REPORT ONLY','question':'Previous question','answer':'Previous answer'})
        body=json.loads(opener.open.call_args.args[0].data)
        self.assertIn('MATCHED REPORT ONLY',body['messages'][0]['content'])
        self.assertEqual(body['messages'][-3]['content'],'Previous question')
        self.assertNotIn('tools',body)
