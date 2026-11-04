from email.message import EmailMessage
from datetime import datetime,timezone
import unittest
from unittest.mock import Mock
from tests import test_sports_mail as fixtures
from ano.sports_mail import RECIPIENT


class DeliveryRecoveryTests(unittest.TestCase):
    desk=fixtures.SportsMailTests.desk

    def add(self,desk,state='submitting',owned=1):
        with desk.db() as db:
            db.execute('INSERT INTO jobs (key,state,command,at,message_id,error,lock_owned) VALUES (?,?,?,?,?,?,?)',
                       ('interrupted',state,'help',datetime.now(timezone.utc).isoformat(),'<test.1@gmail.com>','',owned))

    def state(self,desk):
        with desk.db() as db:return db.execute('SELECT state FROM jobs WHERE key=?',('interrupted',)).fetchone()[0]

    def test_gmail_inbox_copy_confirms_without_resending(self):
        desk,server=self.desk();self.add(desk)
        msg=EmailMessage();msg['From']=RECIPIENT;msg['To']=RECIPIENT
        msg['Message-ID']='<test.1@gmail.com>';msg['X-Ano-Report']='sports-desk-v1'
        imap=Mock();imap.uid.side_effect=[('OK',[b'77']),('OK',[(b'X-GM-LABELS ("\\\\Sent")',msg.as_bytes())])]
        desk.reconcile_delivery(imap)
        self.assertEqual(self.state(desk),'sent');server.send_message.assert_not_called()

    def test_live_job_lock_blocks_recovery(self):
        desk,server=self.desk();self.add(desk,state='preparing')
        imap=Mock()
        with desk.job_lease('interrupted'):desk.reconcile_delivery(imap)
        self.assertEqual(self.state(desk),'preparing');imap.uid.assert_not_called()

    def test_interrupted_preparation_fails_explicitly_without_send(self):
        desk,server=self.desk();self.add(desk,state='preparing')
        desk.reconcile_delivery(Mock())
        self.assertEqual(self.state(desk),'failed_before_submission');server.send_message.assert_not_called()

    def test_absent_gmail_copy_remains_unknown_never_retried(self):
        desk,server=self.desk();self.add(desk)
        imap=Mock();imap.uid.return_value=('OK',[b''])
        desk.reconcile_delivery(imap)
        self.assertEqual(self.state(desk),'delivery_unknown');server.send_message.assert_not_called()

    def test_legacy_unknown_ownership_is_not_treated_as_terminated(self):
        desk,_=self.desk();self.add(desk,owned=0)
        imap=Mock();desk.reconcile_delivery(imap)
        self.assertEqual(self.state(desk),'submitting');imap.uid.assert_not_called()

    def test_spoofed_header_without_server_sent_label_not_confirmed(self):
        desk,_=self.desk();self.add(desk)
        msg=EmailMessage();msg['From']=RECIPIENT;msg['To']=RECIPIENT
        msg['Message-ID']='<test.1@gmail.com>';msg['X-Ano-Report']='sports-desk-v1'
        imap=Mock();imap.uid.side_effect=[('OK',[b'77']),('OK',[(b'X-GM-LABELS (Inbox)',msg.as_bytes())])]
        desk.reconcile_delivery(imap)
        self.assertEqual(self.state(desk),'delivery_unknown')
