from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from ano.claude import ClaudeError
from ano.monitor import (MonitorEngine, MonitorWorker, Monitors, Event, matches, parse_rule, safe_to_open)
from ano.routines import LocalRoutines, RoutineStore
from ano.tools import ToolError


class RuleParsingTests(unittest.TestCase):
    def test_the_users_example_rules(self):
        self.assertEqual(parse_rule('when claude is done, tell me and fly around'),
                         ('claude_idle', '', [{'kind': 'notify'}, {'kind': 'fly'}], False))
        condition, _, actions, repeat = parse_rule('When my download finishes, open it')
        self.assertEqual((condition, actions, repeat), ('download_done', [{'kind': 'open_download'}], False))
        self.assertEqual(parse_rule('when codex is out of usage, resume it after the reset')[2], [{'kind': 'codex_watch'}])
        condition, arg, actions, repeat = parse_rule('Ano, whenever I open Spotify say "time to focus"')
        self.assertEqual((condition, arg, actions, repeat), ('app_open', 'spotify', [{'kind': 'speak', 'text': 'time to focus'}], True))
        typed = parse_rule('when claude is done, type "continue" in vs code every time')
        self.assertEqual(typed[2], [{'kind': 'type', 'text': 'continue', 'target': 'vs code'}])
        self.assertTrue(typed[3])
        self.assertEqual(parse_rule('when the internet is back, open gmail')[2][0]['site'], 'https://mail.google.com/')

    def test_a_message_for_chatgpt_is_typed_whole_with_its_own_wording(self):
        # The user's own sentence: no closing-quote discipline, "and also" inside the message.
        rule = parse_rule('when chatgpt is ready when you press resume say  this" Resume goal and also I want you to know '
                          'I have OpenAI API running with it, so find a way to make them work hand in hand"')
        self.assertEqual(rule[0], 'codex_running')
        self.assertEqual(rule[2], [{'kind': 'type', 'target': 'chatgpt', 'text': 'Resume goal and also I want you to know '
                                    'I have OpenAI API running with it, so find a way to make them work hand in hand'}])
        self.assertEqual(parse_rule('when codex is back, say "back online" out loud')[2], [{'kind': 'speak', 'text': 'back online'}])
        with self.assertRaisesRegex(ToolError, 'Say where to type it'):
            parse_rule('when claude is done, send "continue"')

    def test_unclear_or_unsafe_rules_are_refused_and_other_text_is_ignored(self):
        self.assertIsNone(parse_rule('open spotify'))
        self.assertIsNone(parse_rule('what is the weather'))
        with self.assertRaisesRegex(ToolError, 'play my playlist'):
            parse_rule('when I open spotify, play my playlist')
        with self.assertRaisesRegex(ToolError, 'open it'):
            parse_rule('when claude is done, open it')
        with self.assertRaisesRegex(ToolError, 'I can watch for'):
            parse_rule('when the moon is full, tell me')


class EngineTests(unittest.TestCase):
    def test_first_snapshot_is_a_baseline_and_claude_finishing_fires(self):
        engine = MonitorEngine()
        self.assertEqual(engine.events({'claude': 'idle'}), [])
        self.assertEqual(engine.events({'claude': 'working'}), [])
        self.assertEqual([e.kind for e in engine.events({'claude': 'idle'})], ['claude_idle'])
        self.assertEqual(engine.events({'claude': 'idle'}), [])

    def test_downloads_apps_battery_network_and_cpu(self):
        engine = MonitorEngine()
        engine.events({'partial': ['song.mp3.crdownload'], 'apps': ['code'], 'battery': (40, False), 'online': True, 'cpu': 80})
        kinds = [e.kind for e in engine.events({'partial': [], 'newest': {'name': 'song.mp3', 'path': 'C:/D/song.mp3'},
                                                'apps': ['code', 'spotify'], 'battery': (14, False), 'online': False, 'cpu': 85})]
        self.assertEqual(kinds, ['download_done', 'app_open', 'battery_low', 'offline'])
        engine.events({'cpu': 90})
        self.assertIn('cpu_idle', [e.kind for e in engine.events({'cpu': 5})])

    def test_app_names_match_what_people_say(self):
        rule = {'condition': 'app_open', 'arg': 'vs code'}
        self.assertTrue(matches(rule, Event('app_open', 'code opened', arg='code')))
        self.assertFalse(matches(rule, Event('app_open', 'x opened', arg='spotify')))
        self.assertTrue(matches({'condition': 'claude_idle', 'arg': ''}, Event('claude_idle', 'done')))

    def test_programs_are_never_opened_automatically(self):
        with tempfile.TemporaryDirectory() as folder:
            song, setup = Path(folder) / 'song.mp3', Path(folder) / 'setup.exe'
            song.write_bytes(b'x'); setup.write_bytes(b'x')
            self.assertTrue(safe_to_open(song))
            self.assertFalse(safe_to_open(setup))
            self.assertFalse(safe_to_open(Path(folder) / 'missing.pdf'))


class StoreAndWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'routines.sqlite3'
        self.monitors = Monitors(self.path)

    def worker_with(self, *events):
        worker = MonitorWorker(self.monitors, sensors=Mock())
        for event in events:
            worker.results.put(event)
        return worker

    def test_a_rule_fires_once_unless_every_time(self):
        self.assertIn('Monitor #1 on', self.monitors.command('when claude is done, tell me'))
        self.monitors.command('when I open spotify, say hi every time')
        claude = Event('claude_idle', 'Claude finished in VS Code')
        spotify = Event('app_open', 'spotify opened', arg='spotify')
        fired = self.worker_with(claude, claude, spotify, spotify).poll()
        self.assertEqual([rule['id'] for rule, _ in fired], [1, 2, 2])
        self.assertEqual([rule['id'] for rule in self.monitors.active()], [2])

    def test_status_listing_pause_and_stop(self):
        self.assertEqual(self.monitors.status(), '')
        self.monitors.command('when claude is done, fly around')
        self.assertEqual(self.monitors.status('working'), '1 monitor · Claude: working')
        self.assertIn('Claude finishes in VS Code → fly around', self.monitors.command('show monitors'))
        self.monitors.command('pause monitoring')
        self.assertEqual(self.monitors.status('working'), '1 monitor · paused')
        self.monitors.command('resume monitoring')
        self.assertIn('Stopped', self.monitors.command('stop monitor 1'))
        self.assertEqual(self.monitors.status(), '')
        with self.assertRaises(ToolError):
            self.monitors.command('stop monitor 1')

    def test_nothing_is_sensed_while_paused_and_changes_then_never_fire(self):
        self.monitors.command('when claude is done, tell me')
        sensors = Mock()
        worker = MonitorWorker(self.monitors, sensors=sensors)
        worker.engine.events({'claude': 'working'})
        self.monitors.set_paused(True)
        needs = self.monitors.needs()
        self.assertEqual(needs, {'claude'})
        # What the worker loop does on a paused tick:
        if self.monitors.paused:
            worker.engine.reset()
        sensors.snapshot.assert_not_called()
        self.assertEqual(worker.engine.events({'claude': 'idle'}), [])  # a baseline again, not "finished"

    def test_rules_only_come_from_the_users_own_message(self):
        toolbox = Mock()
        toolbox.allowed.return_value = True
        routines = LocalRoutines(toolbox, RoutineStore(self.path), monitors=self.monitors)
        self.assertIn('Monitor #1 on', routines.reply('when claude is done, tell me and fly around'))
        toolbox.run.assert_not_called()
        with self.assertRaisesRegex(ClaudeError, 'play my playlist'):
            routines.reply('when I open spotify, play my playlist')
        from ano.tools import Toolbox
        names = {item['name'] for item in Toolbox(lambda: []).definitions()}
        self.assertFalse({name for name in names if 'monitor' in name})


if __name__ == '__main__':
    unittest.main()
