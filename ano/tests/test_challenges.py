from datetime import datetime, timedelta, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication, QObject, Signal

from ano import challenges as ch
from ano.monitor import Monitors
from ano.plugins.artifacts import Artifacts
from ano.plugins.word_documents import WordDocuments
from ano.plugins.workshop import Workshop
from ano.scheduled import ScheduledActions

app = QCoreApplication.instance() or QCoreApplication([])


class FakeController(QObject):
    replyFinished = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.sent, self.messages, self.new_chats = [], [], 0

    def send(self, text):
        self.sent.append(text)

    def newChat(self):
        self.new_chats += 1

    def add_message(self, role, body):
        self.messages.append(body)

    def stopTask(self):
        pass


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.workshop = Workshop(root / 'AnoWorkspace', None)
        self.workshop.root.mkdir(parents=True, exist_ok=True)
        log = root / 'action-log.sqlite3'
        with closing(sqlite3.connect(log)) as db, db:
            db.execute('CREATE TABLE actions (id INTEGER PRIMARY KEY, at TEXT, tool TEXT, failed INTEGER, milliseconds INTEGER)')
        notes = root / 'memory.sqlite3'
        with closing(sqlite3.connect(notes)) as db, db:
            db.execute('CREATE TABLE reminders (id INTEGER PRIMARY KEY, body TEXT, due_at TEXT, done INTEGER DEFAULT 0)')
        self.ctx = dict(workspace=self.workshop.root, challenge_dir=self.workshop.root / 'Challenges', notes_db=notes,
                        action_log=log, scheduled=ScheduledActions(root / 'r.sqlite3'), monitors=Monitors(root / 'r.sqlite3'),
                        speech=Mock(available=True), voice=Mock(available=True), ledger=Mock(totals=lambda: (0, 0)),
                        pictures=lambda: root / 'Pictures')
        self.run = dict(tag='AC01', replies=[], last='', baseline=0, started=datetime.now(), secret='123456', monitor_baseline=0)

    def test_commands_are_parsed_and_only_start_after_their_own_reply(self):
        controller = FakeController()
        runner = ch.ChallengeRunner(controller, self.ctx, Path(self.tmp.name) / 'reports')
        self.assertIn('Starting 3 challenge test(s)', runner.command('run challenge tests files'))
        self.assertIn('Choose one of', runner.command('run challenge tests cooking'))
        self.assertIsNone(runner.command('open calculator'))
        self.assertIn('No challenge run has finished', runner.command('challenge results'))
        self.assertEqual(controller.sent, [])  # nothing is sent from the worker thread
        controller.replyFinished.emit('Starting…', False)
        for _ in range(50):
            app.processEvents()
            if controller.sent:
                break
            import time; time.sleep(0.02)
        self.assertTrue(controller.sent and 'Budget' in controller.sent[0])
        self.assertEqual(controller.new_chats, 1)

    def test_a_failed_check_asks_ano_to_find_out_why_and_fix_it(self):
        controller = FakeController()
        runner = ch.ChallengeRunner(controller, self.ctx, Path(self.tmp.name) / 'reports')
        runner.running, runner.results, runner.tag = True, [], 'AC01'
        runner.current = dict(self.run, test=dict(id='reasoning-1', area='reasoning', title='Time arithmetic', steps=['q'],
                                                 check=ch.check_arrival), attempt=1, step=0, replies=['It arrives at 17:25.'])
        runner._advance()
        self.assertIn('Check failed for "Time arithmetic"', controller.sent[-1])
        self.assertIn('Find out why', controller.sent[-1])
        runner.current['replies'].append('Sorry, it arrives at 17:50.')
        runner._advance()
        self.assertEqual(runner.results[-1]['status'], 'fixed on retry')

    def test_local_challenge_keeps_every_step_and_retry_free(self):
        controller = FakeController()
        runner = ch.ChallengeRunner(controller, self.ctx, Path(self.tmp.name) / 'reports')
        self.assertIn('Local-only run', runner.command('run challenge tests reasoning', local=True))
        runner._start()
        self.addCleanup(lambda: runner.timer.stop())
        self.assertTrue(controller.sent[-1].startswith('local: '))
        runner.current['replies'] = ['Wrong answer']
        runner._advance()
        self.assertTrue(controller.sent[-1].startswith('local: Check failed'))
        # A subsequent ordinary run must not inherit the free-only selection.
        runner.running = runner.waiting = False
        runner.command('run challenge tests reasoning')
        runner._start()
        self.assertFalse(controller.sent[-1].startswith('local: '))

    def test_checks_look_at_real_outcomes(self):
        run = dict(self.run, last='It arrives at 17:50')
        self.assertTrue(ch.check_arrival(self.ctx, run)[0])
        # Word report: the numbers and the average must really be in the .docx.
        words = WordDocuments(self.workshop)
        words.write_word_document('AC01 Lab Report', 'Trial\tVolume (mL)\n1\t23.4\n2\t23.1\n3\t23.6\nAverage: 23.37 mL')
        self.assertTrue(ch.check_lab_report(self.ctx, run)[0])
        self.assertFalse(ch.check_revision(self.ctx, run)[0])
        words.write_word_document('AC01 Lab Report revised', 'Run\tVolume (mL)\n1\t23.4')
        self.assertTrue(ch.check_revision(self.ctx, run)[0])
        # Spreadsheet, presentation and PDF are opened and inspected.
        artifacts = Artifacts(self.workshop)
        artifacts.create_spreadsheet('AC01 Budget', ['Item', 'Cost'], [['Rent', 950], ['Food', 320], ['Transport', 85], ['Total', 1355]], 2)
        self.assertTrue(ch.check_budget(self.ctx, run)[0])
        artifacts.create_presentation('AC01 Pitch', 'Ano', [{'title': 'One', 'bullets': ['a']}, {'title': 'Two', 'bullets': ['b']}])
        (self.workshop.root / '~$AC01 Pitch.pptx').write_bytes(b'locked')  # PowerPoint has it open
        self.assertTrue(ch.check_pitch(self.ctx, run)[0])
        artifacts.create_pdf('AC01 Checklist', 'Backup', '\n'.join(f'{n}. Step {n}' for n in range(1, 6)))
        self.assertTrue(ch.check_checklist(self.ctx, run)[0])

    def test_app_reminder_and_project_checks(self):
        run = dict(self.run, last='The result is 7,006,652')
        ok, reason = ch.check_calculator(self.ctx, run)
        self.assertFalse(ok)
        self.assertIn('not actually used', reason)
        with closing(sqlite3.connect(self.ctx['action_log'])) as db, db:
            db.execute("INSERT INTO actions(at, tool, failed, milliseconds) VALUES ('now', 'desktop_input', 0, 5)")
        self.assertTrue(ch.check_calculator(self.ctx, run)[0])
        due = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%dT07:45')
        with closing(sqlite3.connect(self.ctx['notes_db'])) as db, db:
            db.execute('INSERT INTO reminders(body, due_at) VALUES (?, ?)', ('check the AC01 oven', due))
        self.assertTrue(ch.check_reminder(self.ctx, run)[0])
        # The same moment saved in UTC also counts.
        utc = (datetime.now() + timedelta(days=1)).replace(hour=7, minute=45, second=0, microsecond=0).astimezone().astimezone(timezone.utc)
        with closing(sqlite3.connect(self.ctx['notes_db'])) as db, db:
            db.execute('DELETE FROM reminders')
            db.execute('INSERT INTO reminders(body, due_at) VALUES (?, ?)', ('check the AC01 oven', utc.isoformat()))
        self.assertTrue(ch.check_reminder(self.ctx, run)[0])
        self.ctx['scheduled'].command('in 30 minutes type "AC01 still going" in notepad')
        self.assertTrue(ch.check_scheduled(self.ctx, run)[0])
        self.assertFalse(ch.check_monitor(self.ctx, run)[0])
        self.ctx['monitors'].command('when my download finishes, tell me')
        self.assertTrue(ch.check_monitor(self.ctx, run)[0])
        self.assertIsNone(ch.check_voice(self.ctx, run)[0])  # live voice always needs the user

    def test_scorecard_is_posted_and_saved(self):
        controller = FakeController()
        runner = ch.ChallengeRunner(controller, self.ctx, Path(self.tmp.name) / 'reports')
        runner.tag, runner.started, runner.cost_before = 'AC01', datetime.now(), 0
        runner.results = [dict(id='a', area='files', title='PDF', status='passed', attempts=1, check='', explanation='', seconds=3),
                          dict(id='b', area='apps', title='Notepad', status='failed', attempts=3, check='no file',
                               explanation='Computer control is off.', seconds=9)]
        runner._finish()
        self.assertIn('1/2 passed', controller.messages[-1])
        self.assertIn('Notepad: Computer control is off.', controller.messages[-1])
        report = json.loads((Path(self.tmp.name) / 'reports' / 'challenge-AC01.json').read_text(encoding='utf-8'))
        self.assertEqual(len(report['results']), 2)
        self.assertIn('1/2 passed', runner.command('challenge results'))


if __name__ == '__main__':
    unittest.main()
