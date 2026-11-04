import json
from threading import Thread
import time
import unittest

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtWebSockets import QWebSocket
from ano.plugins.browser import Browser, BrowserBridge, BrowserHealth, health_report, EXTENSION_VERSION
from ano.plugins.windows import Windows
from ano.tools import Toolbox, ToolError

PORT = 47699


class FakeExtension:
    """Plays the extension's part: says hello, then answers list/close requests."""
    def __init__(self, token, origin='chrome-extension://example', browser='brave', mute=False, version=EXTENSION_VERSION):
        self.socket = QWebSocket(origin)
        self.closed = []
        self.browser = browser
        self.mute = mute  # when True, never answers list requests (simulates an unresponsive browser)
        self.socket.connected.connect(lambda: self.socket.sendTextMessage(
            json.dumps({'type': 'hello', 'token': token, 'browser': browser, 'version': version})))
        self.socket.textMessageReceived.connect(self.answer)
        self.socket.open(QUrl(f'ws://127.0.0.1:{PORT}'))

    def answer(self, text):
        message = json.loads(text)
        if message.get('type') == 'ping':
            return
        if self.mute and message.get('action') == 'list':
            return
        if message['action'] == 'list':
            result = [{'id': 7, 'windowId': 1, 'title': 'Example video', 'url': 'https://video.example.com/watch',
                       'active': False, 'audible': True, 'muted': False, 'pinned': False,
                       'lastAccessed': (1000 - 600) * 1000}]
        else:
            self.closed += message['tabIds']
            result = {'closed': len(message['tabIds'])}
        self.socket.sendTextMessage(json.dumps({'id': message['id'], 'result': result}))


class BrowserTests(unittest.TestCase):
    def test_shutdown_tolerates_a_socket_already_destroyed_by_qt(self):
        from unittest.mock import Mock
        socket = Mock()
        socket.deleteLater.side_effect = RuntimeError('Internal C++ object already deleted')
        self.bridge._browsers['chrome'] = socket
        self.bridge._versions['chrome'] = EXTENSION_VERSION
        self.bridge._forget(socket)
        self.assertNotIn('chrome', self.bridge.browsers())

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.bridge = BrowserBridge('secret-token', PORT)
        self.assertTrue(self.bridge.listening)
        self.toolbox = Toolbox(lambda: [], plugins=[Browser(self.bridge, clock=lambda: 1000)])

    def tearDown(self):
        self.bridge.close()
        self.app.processEvents()

    def pump(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)

    def run_tool(self, name, arguments):
        # Tools run on the Claude worker thread while the main thread serves the socket.
        result = []
        worker = Thread(target=lambda: result.append(self.toolbox.run(name, arguments)))
        worker.start()
        self.pump(lambda: not worker.is_alive())
        worker.join()
        return result[0]

    def test_extension_lists_and_closes_tabs(self):
        extension = FakeExtension('secret-token')
        self.pump(lambda: self.bridge.browsers() == ['brave'])
        text, note, failed = self.run_tool('list_tabs', {})
        self.assertFalse(failed)
        self.assertEqual(text, 'tab brave:7 · playing audio · last used 10 min ago · Example video · video.example.com')
        text, note, failed = self.run_tool('close_tabs', {'tab_ids': ['brave:7']})
        self.assertFalse(failed)
        self.assertEqual(extension.closed, [7])
        self.assertIn('Ctrl+Shift+T', note)

    def test_one_unresponsive_browser_still_shows_the_others_tabs(self):
        import ano.plugins.browser as browser_mod
        original = browser_mod.REPLY_SECONDS
        browser_mod.REPLY_SECONDS = 0.3  # keep the unresponsive-browser wait short
        try:
            FakeExtension('secret-token', browser='chrome', mute=True)
            FakeExtension('secret-token', browser='brave')
            self.pump(lambda: self.bridge.browsers() == ['brave', 'chrome'])
            text, note, failed = self.run_tool('list_tabs', {})
        finally:
            browser_mod.REPLY_SECONDS = original
        self.assertFalse(failed)
        self.assertIn('tab brave:7', text)        # Brave's tab is shown
        self.assertIn('Could not reach Chrome', text)  # Chrome is reported as unreachable
        self.assertEqual(note, 'Looked at your tabs in brave')

    def test_outdated_extension_is_detected_and_reported(self):
        FakeExtension('secret-token', browser='brave', version='0.9')
        self.pump(lambda: self.bridge.browsers() == ['brave'])
        self.assertEqual(self.bridge.outdated(), {'brave': '0.9'})
        status, _ = Browser(self.bridge).browser_connection_status()
        self.assertEqual(json.loads(status)['state'], 'outdated')

    def test_current_extension_version_is_not_flagged(self):
        FakeExtension('secret-token', browser='brave', version=EXTENSION_VERSION)
        self.pump(lambda: self.bridge.browsers() == ['brave'])
        self.assertEqual(self.bridge.outdated(), {})

    def test_connection_diagnostics_work_before_extension_setup(self):
        browser = Browser(None)
        status, _ = browser.run('browser_connection_status', {})
        report = json.loads(status)
        self.assertEqual(report['state'], 'unconfigured')
        self.assertIn('configure browser', report['detail'])
        self.assertIn('Codex', report['scope'])
        with self.assertRaisesRegex(ToolError, 'not connected'):
            browser.run('list_tabs', {})

    def test_connection_recovers_after_busy_port_is_released(self):
        waiting=BrowserBridge('fixture-token',PORT)
        try:
            self.assertFalse(waiting.listening)
            self.assertTrue(waiting.reconnect.isActive())
            self.bridge.close()
            waiting._retry_listen()
            self.assertTrue(waiting.listening)
            self.assertFalse(waiting.reconnect.isActive())
        finally:
            waiting.close()

    def test_wrong_token_or_web_page_origin_is_rejected(self):
        FakeExtension('wrong-token')
        FakeExtension('secret-token', origin='https://evil.example')
        self.pump(lambda: False)  # give both connections time to try
        self.assertEqual(self.bridge.browsers(), [])
        text, _, failed = self.toolbox.run('list_tabs', {})
        self.assertTrue(failed)
        self.assertIn('configure browser', text)


class HealthReportTests(unittest.TestCase):
    def test_states_and_actionable_detail(self):
        self.assertEqual(health_report(False, False, [], {})['state'], 'unconfigured')
        self.assertEqual(health_report(True, False, [], {})['state'], 'port_busy')
        self.assertEqual(health_report(True, True, [], {})['state'], 'waiting')
        ok = health_report(True, True, ['brave'], {})
        self.assertEqual(ok['state'], 'connected')
        self.assertEqual(ok['browsers'], ['brave'])
        old = health_report(True, True, ['chrome'], {'chrome': '1.0'})
        self.assertEqual(old['state'], 'outdated')
        self.assertIn('Chrome 1.0', old['summary'])
        self.assertIn(EXTENSION_VERSION, old['detail'])
        # Every state carries actionable guidance, and control mode is included when given.
        for report in (health_report(False, False, [], {}), health_report(True, False, [], {})):
            self.assertTrue(report['detail'])
        self.assertEqual(health_report(True, True, ['brave'], {}, 'full')['control_mode'], 'full')


class BrowserHealthLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_live_object_reflects_bridge_and_emits_changed(self):
        bridge = BrowserBridge('secret-token', PORT + 1)
        health = BrowserHealth(True, bridge)
        try:
            self.assertIn(health.state, ('waiting', 'port_busy'))
            changes = []
            health.changed.connect(lambda: changes.append(health.state))
            ext = QWebSocket('chrome-extension://x')
            ext.connected.connect(lambda: ext.sendTextMessage(json.dumps(
                {'type': 'hello', 'token': 'secret-token', 'browser': 'brave', 'version': EXTENSION_VERSION})))
            ext.open(QUrl(f'ws://127.0.0.1:{PORT + 1}'))
            deadline = time.monotonic() + 3
            while health.state != 'connected' and time.monotonic() < deadline:
                self.app.processEvents(); time.sleep(.005)
            self.assertEqual(health.state, 'connected')
            self.assertEqual(health.browsers, ['brave'])
            self.assertTrue(changes)
        finally:
            health.close()
            bridge.close()
            self.app.processEvents()


class WindowsTests(unittest.TestCase):
    def test_lists_other_apps_and_refuses_stale_ids(self):
        windows = Windows(lambda: [(11, 500, 'Example document - Word', False), (12, 99, 'Ano', False)], own_pid=99)
        text, _ = windows.list_windows()
        self.assertIn('window 11', text)
        self.assertNotIn('window 12', text)
        toolbox = Toolbox(lambda: [], plugins=[windows])
        self.assertTrue(toolbox.run('window_action', {'window_id': 12, 'action': 'close'})[2])

    def test_real_window_listing(self):
        text, _ = Windows().list_windows()
        self.assertTrue(text)
