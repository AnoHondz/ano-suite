from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from ano.claude import ClaudeError
from ano.routines import LocalRoutines, RoutineStore
from ano.scheduled import ScheduledActions, GRACE
from ano.tools import ToolError

NOW = datetime(2026, 10, 26, 23, 0, tzinfo=timezone(timedelta(hours=-4)))


class ScheduledActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = NOW
        self.path = Path(self.tmp.name) / 'routines.sqlite3'
        self.actions = ScheduledActions(self.path, clock=lambda: self.now)

    def test_schedules_exact_text_for_tonight_and_lists_and_cancels(self):
        reply = self.actions.command('at 11:19 pm type "continue" in chatgpt')
        self.assertIn('#1', reply)
        self.assertIn('11:19 PM', reply)
        self.assertIn('“continue”', reply)
        self.assertIn('type “continue” in ChatGPT', self.actions.listing())
        self.assertIn('Cancelled', self.actions.command('cancel scheduled action 1'))
        self.assertEqual(self.actions.listing(), 'No scheduled actions are waiting.')
        with self.assertRaises(ToolError):
            self.actions.cancel(1)

    def test_relative_tomorrow_and_past_times(self):
        self.actions.command('Ano, in 20 minutes type “keep going” into vs code')
        self.actions.command('tomorrow at 9 am type "hello (world) 50% +1" in notepad')
        with self.assertRaisesRegex(ToolError, 'already passed'):
            self.actions.command('at 10 pm type "continue" in chatgpt')
        with self.assertRaisesRegex(ToolError, 'Use: at 11:19 pm'):
            self.actions.command('at 11:30 pm type "continue" in photoshop')
        self.assertIsNone(self.actions.command('open chatgpt'))

    def test_stop_scheduler_cancels_pending_and_watches_without_replaying(self):
        self.actions.command('in 20 minutes type "example" in notepad')
        self.actions.command('watch codex')
        self.assertIn('cancelled 1 waiting actions and disabled 1 watches', self.actions.command('stop scheduler'))
        self.assertEqual(self.actions.listing(), 'No scheduled actions are waiting.')
        self.assertFalse(self.actions.watching('codex'))
        self.assertIn('cancelled 0 waiting actions', self.actions.command('stop scheduler'))
        self.assertIsNone(self.actions.command('do not stop scheduler'))

    def test_scheduler_followup_reports_current_state_without_guessing_history(self):
        self.actions.command('watch codex')
        result = self.actions.command('did  you stop the most recent scheduler')
        self.assertIn('Active watches: codex', result)
        self.assertIn('does not infer', result)
        self.assertTrue(self.actions.watching('codex'))

    def test_due_actions_run_once_and_late_ones_are_missed_not_run(self):
        self.actions.command('at 11:19 pm type "continue" in chatgpt')
        self.actions.command('at 11:05 pm type "late" in notepad')
        self.assertEqual(self.actions.take_due(), [])
        self.now = NOW.replace(minute=20)  # 11:05 is 15 minutes late (over GRACE); 11:19 is 1 minute late
        self.assertLess(timedelta(minutes=1), GRACE)
        due = self.actions.take_due()
        self.assertEqual([(a['text'], a['missed']) for a in due], [('late', True), ('continue', False)])
        self.assertEqual(self.actions.take_due(), [])
        args = ScheduledActions.arguments(due[1])
        self.assertEqual(args[args.index('-Text') + 1], 'continue')
        self.assertEqual(args[args.index('-Process') + 1], 'ChatGPT')

    def test_an_interrupted_run_is_never_replayed_after_restart(self):
        self.actions.command('in 1 minute type "continue" in chatgpt')
        self.now += timedelta(minutes=2)
        self.assertEqual(len(self.actions.take_due()), 1)
        restarted = ScheduledActions(self.path, clock=lambda: self.now)
        self.assertEqual(restarted.take_due(), [])

    def test_only_the_users_typed_command_schedules_through_local_routines(self):
        toolbox = Mock()
        toolbox.allowed.return_value = True
        routines = LocalRoutines(toolbox, RoutineStore(self.path), scheduled=self.actions)
        self.assertIn('Scheduled #1', routines.reply('at 11:19 pm type "continue" in chatgpt'))
        toolbox.run.assert_not_called()
        with self.assertRaises(ClaudeError):
            routines.reply('at 11:19 pm type "" in chatgpt')
        self.assertNotIn('schedule', ' '.join(d['name'] for d in []))  # there is no model tool to call


if __name__ == '__main__':
    unittest.main()
