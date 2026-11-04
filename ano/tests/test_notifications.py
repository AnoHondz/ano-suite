from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ano.monitor import MonitorEngine, Monitors, Sensors, friendly_sender, matches, parse_rule, read_toast
from ano.tools import ToolError


def toast(*texts):
    lines = ''.join(f'<text>{text}</text>' for text in texts)
    return f'<toast><visual><binding template="ToastGeneric">{lines}</binding></visual></toast>'.encode()


class FixtureDatabase:
    """A small wpndatabase.db with the real table layout."""

    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT)')
            db.execute('CREATE TABLE Notification ("Order" INTEGER, Id INTEGER PRIMARY KEY, HandlerId INTEGER, '
                       'Type TEXT, Payload BLOB, ArrivalTime INT64)')
            db.executemany('INSERT INTO NotificationHandler VALUES (?, ?)', [
                (1, 'Microsoft.Office.OUTLOOK.EXE.15'), (2, 'OpenAI.Codex_2p2nqsd0c76g0!App'),
                (3, 'Contoso.MobileBanking_abc!App'), (4, 'Microsoft.YourPhone_8wekyb3d8bbwe!YourPhoneNotifications_1')])
        self.add(1, toast('Weekly digest', 'Nothing new'))

    def add(self, handler, payload, kind='toast'):
        with closing(sqlite3.connect(self.path)) as db, db:
            return db.execute('INSERT INTO Notification(HandlerId, Type, Payload) VALUES (?, ?, ?)', (handler, kind, payload)).lastrowid


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = FixtureDatabase(Path(self.tmp.name) / 'wpndatabase.db')
        self.sensors = Sensors(downloads=self.tmp.name, notifications=self.db.path)

    def events(self, engine):
        self.sensors._notes_stamp = None  # mtime may not move within one test
        return engine.events(self.sensors.snapshot({'notifications'}))

    def test_the_rules_the_user_can_type(self):
        self.assertEqual(parse_rule('when I get a notification from outlook, read it to me'),
                         ('notification', 'outlook', [{'kind': 'read'}], False))
        self.assertEqual(parse_rule('when a notification mentions "interview", tell me every time'),
                         ('notification_text', 'interview', [{'kind': 'notify'}], True))
        self.assertEqual(parse_rule('when codex notifies me, fly around'), ('notification', 'codex', [{'kind': 'fly'}], False))
        self.assertEqual(parse_rule('when I get a notification, tell me')[:2], ('notification', ''))
        with self.assertRaises(ToolError):
            parse_rule('when my download finishes, read it to me')  # reading is for notifications only

    def test_only_new_notifications_fire_and_the_first_read_is_a_baseline(self):
        engine = MonitorEngine()
        self.assertEqual(self.events(engine), [])  # the old digest is the baseline
        self.db.add(1, toast('Interview on Friday', 'Room 4 at 10:00'))
        found = self.events(engine)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].kind, 'notification')
        self.assertEqual(found[0].payload['app'], 'Outlook')
        self.assertEqual(found[0].payload['body'], 'Room 4 at 10:00')
        self.assertEqual(self.events(engine), [])  # already seen

    def test_a_notification_just_before_sensing_starts_still_counts(self):
        # The rule is typed, the notification arrives, then the first read happens: it is recent, so it fires.
        import time
        now = time.time()
        filetime = lambda seconds: int((seconds + 11644473600) * 1e7)
        with closing(sqlite3.connect(self.db.path)) as db, db:
            db.execute('UPDATE Notification SET ArrivalTime=?', (filetime(now - 3600),))  # the old digest
            db.execute('INSERT INTO Notification(HandlerId, Type, Payload, ArrivalTime) VALUES (1, "toast", ?, ?)',
                       (toast('Interview moved', 'Now 3 pm'), filetime(now - 5)))
        found = self.events(MonitorEngine())
        self.assertEqual([event.payload['title'] for event in found], ['Interview moved'])

    def test_banks_password_managers_and_codes_are_dropped_whole(self):
        engine = MonitorEngine()
        self.events(engine)
        self.db.add(3, toast('Account update', 'Your statement is ready'))
        self.db.add(1, toast('Microsoft account', 'Your security code is 482913'))
        self.db.add(4, toast('Mum', '123456 is your WhatsApp code'))
        self.db.add(2, toast('Payment received', 'R500 credited'))
        self.db.add(4, toast('Mum', 'Call me when you are free'))
        found = self.events(engine)
        self.assertEqual([event.payload['title'] for event in found], ['Mum'])
        self.assertEqual(found[0].payload['app'], 'Phone Link')
        # The dropped text is nowhere in what the sensor keeps.
        kept = repr(self.sensors._notes)
        for secret in ('482913', '123456', 'statement', 'R500'):
            self.assertNotIn(secret, kept)

    def test_the_original_database_is_never_changed_or_left_open(self):
        before = self.db.path.read_bytes()
        self.sensors.notifications()
        self.assertEqual(self.db.path.read_bytes(), before)
        self.db.path.rename(self.db.path.with_name('moved.db'))  # Windows refuses this while a handle is open

    def test_rules_match_by_app_and_by_word(self):
        rules = {text: dict(zip(('condition', 'arg'), parse_rule(text)[:2])) for text in (
            'when I get a notification from outlook, tell me', 'when I get a notification from my phone, tell me',
            'when chatgpt notifies me, tell me', 'when a notification mentions "interview", tell me')}
        outlook = read_toast(9, 'Microsoft.Office.OUTLOOK.EXE.15', toast('Interview invite', 'Friday'))
        phone = read_toast(10, 'Microsoft.YourPhone_8wekyb3d8bbwe!YourPhoneNotifications_1', toast('Mum', 'hi'))
        codex = read_toast(11, 'OpenAI.Codex_2p2nqsd0c76g0!App', toast('Task finished', ''))
        from ano.monitor import Event
        event = lambda note: Event('notification', note['title'], arg=note['sender'], payload=note)
        hits = lambda note: sorted(text for text, rule in rules.items() if matches(rule, event(note)))
        self.assertEqual(hits(outlook), ['when I get a notification from outlook, tell me',
                                         'when a notification mentions "interview", tell me'])
        self.assertEqual(hits(phone), ['when I get a notification from my phone, tell me'])
        self.assertEqual(hits(codex), ['when chatgpt notifies me, tell me'])
        self.assertEqual(friendly_sender('AnthropicPBC.Claude_fnn82j28hfe8t!Claude'), 'Claude')

    def test_the_same_rule_twice_is_kept_once(self):
        monitors = Monitors(Path(self.tmp.name) / 'r.sqlite3')
        monitors.command('when chatgpt is ready, say "resume goal"')
        self.assertIn('Monitor #1 already does that', monitors.command('when chatgpt is ready, say "resume goal"'))
        self.assertEqual(len(monitors.active()), 1)

    def test_pause_notifications_stops_only_notification_sensing(self):
        monitors = Monitors(Path(self.tmp.name) / 'r.sqlite3')
        monitors.command('when I get a notification from outlook, read it to me')
        monitors.command('when claude is done, tell me')
        self.assertEqual(monitors.needs(), {'notifications', 'claude'})
        self.assertIn('Notifications paused', monitors.command('pause notifications'))
        self.assertEqual(monitors.needs(), {'claude'})
        self.assertIn('Notifications are paused', monitors.listing())
        self.assertIn('1 notification rule', monitors.command('resume notifications'))
        self.assertIn('notifications', monitors.needs())
        self.assertIn('a notification arrives from outlook → read it to you', monitors.listing())


if __name__ == '__main__':
    unittest.main()
