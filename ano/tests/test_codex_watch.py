from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from ano.codex_watch import CodexWatch, reset_time, CHECK_EVERY, RETRY_EVERY, AFTER_RESET
from ano.scheduled import ScheduledActions

NOW = datetime(2026, 10, 26, 23, 30, tzinfo=timezone(timedelta(hours=-4)))


class ResetTimeTests(unittest.TestCase):
    def test_reads_the_reset_messages_codex_shows(self):
        self.assertEqual(reset_time(['You’re out of Codex and Work usage. Add credits or upgrade your plan — '
                                     'or wait for usage to reset on 1:17 AM'], NOW), NOW.replace(hour=1, minute=17) + timedelta(days=1))
        self.assertEqual(reset_time(['Usage resets Oct 28 at 9:05 PM'], NOW), NOW.replace(day=28, hour=21, minute=5))
        self.assertEqual(reset_time(['Try again in 2h 10m'], NOW), NOW + timedelta(hours=2, minutes=10))

    def test_no_time_or_an_old_message_gives_none(self):
        self.assertIsNone(reset_time(["You've hit your usage limit. Upgrade your plan or add credits to continue, "
                                      'or try again later.'], NOW))
        self.assertIsNone(reset_time(['wait for usage to reset on 11:17 PM'], NOW))  # 13 minutes ago, not tomorrow
        self.assertIsNone(reset_time(['Usage resets Oct 20 at 9:05 PM'], NOW))


class CodexWatchTests(unittest.TestCase):
    def test_unknown_reset_notifies_once_while_read_only_checks_continue(self):
        watch = CodexWatch(NOW)
        result = {'found': True, 'limited': True, 'reset': None}
        self.assertIn('hourly', watch.checked(result, NOW))
        first_resume = watch.resume_at
        for n in range(1, 6):
            self.assertIsNone(watch.checked(result, NOW + CHECK_EVERY * n))
            self.assertEqual(watch.resume_at, first_resume)
        self.assertEqual(watch.due(first_resume), 'resume')
        self.assertIn('Pressed', watch.resumed({'pressed': True}, first_resume))
        # If still limited after an attempted resume, announce the new wait once.
        self.assertIn('hourly', watch.checked(result, first_resume + AFTER_RESET))
        self.assertIsNone(watch.checked(result, first_resume + AFTER_RESET + CHECK_EVERY))

    def test_a_new_limit_episode_can_announce_again(self):
        watch = CodexWatch(NOW)
        limited = {'found': True, 'limited': True}
        self.assertIsNotNone(watch.checked(limited, NOW))
        self.assertEqual(watch.checked({'found': True, 'limited': False}, NOW + CHECK_EVERY), 'Codex is running again.')
        self.assertIsNotNone(watch.checked(limited, NOW + CHECK_EVERY * 2))
    def test_waits_for_the_reset_then_resumes_and_verifies(self):
        watch = CodexWatch(NOW)
        self.assertEqual(watch.due(NOW), 'check')
        reset = NOW + timedelta(minutes=40)
        message = watch.checked({'found': True, 'limited': True, 'reset': reset.isoformat()}, NOW)
        self.assertIn('Resume goal', message)
        self.assertIsNone(watch.checked({'found': True, 'limited': True, 'reset': reset.isoformat()}, NOW + CHECK_EVERY))
        self.assertEqual(watch.due(reset), 'check')  # read-only checks continue while waiting; no press yet
        self.assertEqual(watch.due(reset + AFTER_RESET), 'resume')
        self.assertIn('Pressed', watch.resumed({'pressed': True}, reset + AFTER_RESET))
        self.assertIsNone(watch.due(reset + AFTER_RESET + timedelta(minutes=1)))
        self.assertEqual(watch.due(reset + AFTER_RESET + timedelta(minutes=2)), 'check')
        self.assertEqual(watch.checked({'found': True, 'limited': False}, reset + timedelta(minutes=5)), 'Codex is running again.')

    def test_without_a_reset_time_it_retries_hourly(self):
        watch = CodexWatch(NOW)
        self.assertIn('hourly', watch.checked({'found': True, 'limited': True, 'reset': None}, NOW))
        self.assertEqual(watch.due(NOW + RETRY_EVERY - timedelta(minutes=1)), 'check')
        self.assertEqual(watch.due(NOW + RETRY_EVERY), 'resume')
        self.assertIn('try again in an hour', watch.resumed({'pressed': False, 'error': 'no Resume goal button is showing'},
                                                            NOW + RETRY_EVERY))

    def test_running_or_closed_app_needs_no_action(self):
        watch = CodexWatch(NOW)
        self.assertIsNone(watch.checked({'found': True, 'limited': False}, NOW))
        self.assertIsNone(watch.checked({'found': False}, NOW + CHECK_EVERY))
        self.assertIsNone(watch.resume_at)


class WatchCommandTests(unittest.TestCase):
    def test_user_commands_turn_the_watch_on_report_and_off(self):
        with tempfile.TemporaryDirectory() as folder:
            actions = ScheduledActions(Path(folder) / 'r.sqlite3', clock=lambda: NOW)
            self.assertIn('not being watched', actions.command('codex watch status'))
            self.assertIn('Watching Codex', actions.command('watch codex overnight'))
            self.assertTrue(actions.watching('codex'))
            actions.note('codex', 'Limited; waiting to resume.')
            self.assertIn('Limited; waiting', actions.command('codex watch status'))
            self.assertIn('Stopped', actions.command('stop watching codex'))
            self.assertFalse(actions.watching('codex'))


if __name__ == '__main__':
    unittest.main()
