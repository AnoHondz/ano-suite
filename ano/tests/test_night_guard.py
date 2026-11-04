from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from ano.night_guard import NightGuard, PROTECTED, morning_summary

LONDON_OPEN = datetime(2026, 11, 2, 8, 5, tzinfo=timezone.utc)  # 3:05 AM New York


class NightGuardTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.state = Path(self.tmp.name)
        self.now = LONDON_OPEN;self.ram = 95.0;self.closed = []
        self.apps = {'chrome': (3000 * 1024 ** 2, ['c1', 'c2']), 'code': (2600 * 1024 ** 2, ['v'])}
        self.away = 3600;self.front = ''
        self.guard = NightGuard(self.state, lambda: (self.ram, 1), apps=lambda: dict(self.apps),
                                close=self.closed.append, clock=lambda: self.now, idle=lambda: self.away, foreground=lambda: self.front)

    def tick(self, seconds=15):
        result = self.guard.check();self.now += timedelta(seconds=seconds);return result

    def test_overnight_pressure_closes_the_biggest_other_app_after_two_readings(self):
        self.assertIsNone(self.tick())
        entry = self.tick()
        self.assertEqual(entry['closed'], 'chrome');self.assertEqual(self.closed, [['c1', 'c2']])
        self.assertTrue((self.state / 'overnight-trading.json').exists())
        self.assertEqual([r['closed'] for r in morning_summary(self.state, LONDON_OPEN)], ['chrome'])

    def test_waits_a_minute_between_closes_and_stops_once_ram_recovers(self):
        self.tick();self.tick()
        del self.apps['chrome']
        for _ in range(3):self.assertIsNone(self.tick())  # within the minute
        self.ram = 70.0
        for _ in range(4):self.assertIsNone(self.tick())
        self.assertEqual(len(self.closed), 1)

    def test_daytime_brief_spikes_and_disabled_setting_close_nothing(self):
        self.now = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)  # 1 PM New York: user awake
        for _ in range(4):self.tick()
        self.now = LONDON_OPEN;self.ram = 95.0;self.tick();self.ram = 60.0;self.tick();self.ram = 95.0;self.tick()
        self.assertEqual(self.closed, [])
        (self.state / 'overnight-trading.json').write_text('{"enabled": false}', encoding='utf-8')
        for _ in range(4):self.tick()
        self.assertEqual(self.closed, [])

    def test_ano_its_services_and_windows_are_never_candidates(self):
        for name in ('python', 'pythonw', 'node', 'ollama', 'explorer', 'svchost', 'lively'):
            self.assertIn(name, PROTECTED)

    def test_nothing_closes_while_the_user_is_at_the_pc(self):
        self.away = 45   # touched the mouse 45 seconds ago, inside the overnight window, RAM at 95%
        for _ in range(6):self.assertIsNone(self.tick())
        self.assertEqual(self.closed, [])

    def test_the_app_on_screen_is_never_the_one_closed(self):
        self.front = 'chrome'
        self.tick();entry = self.tick()
        self.assertEqual(entry['closed'], 'code')

    def test_vs_code_is_never_closed(self):
        self.assertIn('code', PROTECTED)
