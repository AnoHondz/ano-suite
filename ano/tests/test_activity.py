from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication
from ano.activity import ActivityTracker, is_activity_question, stamp
from ano.memory import Memory


class ActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'memory.sqlite3'
        self.memory = Memory(self.path)
        self.now = datetime(2026, 10, 25, 15, tzinfo=timezone.utc)
        self.reader = Mock(return_value=('editor.exe', 'Project Alpha'))
        self.desktop = Mock(return_value=True)
        self.tracker = ActivityTracker(self.memory, self.reader, self.desktop, lambda: self.now)

    def tearDown(self):
        self.tracker.close()
        self.memory.close()
        self.directory.cleanup()

    def tick(self, seconds=0):
        self.now += timedelta(seconds=seconds)
        self.tracker.tick()

    def test_coalesces_windows_and_records_switch(self):
        self.tick()
        self.tick(4)
        self.assertEqual(len(self.memory.activities()), 1)
        self.reader.return_value = ('browser.exe', 'Research')
        self.tick(2)
        rows = self.memory.activities()
        self.assertEqual([row['app'] for row in rows], ['browser.exe', 'editor.exe'])
        self.assertEqual(rows[1]['ended_at'], stamp(self.now))

    def test_pause_and_disabled_never_read_titles_or_include_gap(self):
        self.tick()
        self.tick(2)
        self.tracker.set_paused(True)
        count = self.reader.call_count
        self.tick(60)
        self.assertEqual(self.reader.call_count, count)
        self.tracker.set_paused(False)
        self.tick()
        rows = self.memory.activities()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['ended_at'], stamp(self.now - timedelta(seconds=60)))
        self.tracker.setEnabled(False)
        self.tick(2)
        self.assertEqual(self.reader.call_count, count + 1)
        self.assertFalse(self.memory.setting('activity_enabled', True))

    def test_lock_event_and_desktop_guard_block_titles(self):
        self.tick()
        self.tracker.session_changed(True)
        self.tick(2)
        self.assertEqual(self.reader.call_count, 1)
        self.tracker.session_changed(False)
        self.desktop.return_value = False
        self.tick(2)
        self.assertEqual(self.reader.call_count, 1)
        self.desktop.return_value = True
        self.tick(2)
        self.assertEqual(self.reader.call_count, 2)

    def test_startup_locked_and_lock_during_read_discard_sample(self):
        self.desktop.return_value = False
        self.tick()
        self.reader.assert_not_called()
        self.desktop.side_effect = [True, False]
        self.tick(2)
        self.assertEqual(self.memory.activities(), [])

    def test_sleep_gap_is_not_counted(self):
        self.tick()
        self.tick(2)
        previous = self.now
        self.tick(3600)
        rows = self.memory.activities()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['ended_at'], stamp(previous))

    def test_delete_removes_search_and_recall_without_resurrection(self):
        self.tick()
        identifier = self.memory.activities()[0]['id']
        self.assertEqual(len(self.memory.activities('Alpha')), 1)
        self.tracker.deleteEntry(identifier)
        self.tick(2)
        self.assertEqual(self.memory.activities('Alpha'), [])
        context, fallback = self.tracker.recall('What was I working on earlier?')
        self.assertEqual(json.loads(context)['records'], [])
        self.assertIn('no recorded activity', fallback)
        self.reader.return_value = ('browser.exe', 'Research')
        self.tick(2)
        self.tracker.deleteAll()
        self.tick(2)
        self.assertEqual(self.memory.activities(), [])

    def test_recall_limits_records_and_selects_yesterday(self):
        today = self.now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = today - timedelta(hours=12)
        self.memory.start_activity('editor.exe', 'Yesterday project', stamp(yesterday))
        self.memory.start_activity('browser.exe', 'Today research', stamp(self.now - timedelta(minutes=1)))
        context, _ = self.tracker.recall('What was I working on yesterday?')
        records = json.loads(context)['records']
        self.assertEqual([row['title'] for row in records], ['Yesterday project'])
        self.assertEqual(self.memory.activities('" OR *'), [])

    def test_tracking_setting_and_history_persist(self):
        self.tick()
        self.tracker.setEnabled(False)
        other = Memory(self.path)
        self.assertFalse(other.setting('activity_enabled', True))
        self.assertEqual(len(other.activities()), 1)
        other.close()

    def test_question_routing(self):
        for question in ('What was I working on earlier?', 'What did I do yesterday?',
                         'Summarize my activity today', 'Remind me what I was doing earlier'):
            self.assertTrue(is_activity_question(question), question)
        for question in ('Hello', 'What do you think about this app?', 'What was the weather yesterday?'):
            self.assertFalse(is_activity_question(question), question)
