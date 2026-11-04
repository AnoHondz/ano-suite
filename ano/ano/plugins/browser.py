"""Browser tabs through Ano's extension, connected over a local-only WebSocket with a private token."""
import hmac
import json
from threading import Event
import time
import uuid
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketServer

from ..tools import ToolError

PORT = 47613
REPLY_SECONDS = 10
MAX_PAGE = 30000
# The extension version Ano expects; keep in step with ano/extension/manifest.json.
EXTENSION_VERSION = '1.4'
SETUP = 'The Ano browser extension is not connected. The user can run: python -m ano.configure browser'


def health_report(configured, listening, browsers, outdated, control_mode=''):
    """Deterministic browser-connection health, used by both the live UI and the diagnostics tool."""
    browsers = sorted(browsers)
    if not configured:
        state, summary, detail = ('unconfigured', 'Browser not set up',
                                  'Run python -m ano.configure browser, then load the extension in Brave/Chrome/Edge.')
    elif not listening:
        state, summary, detail = ('port_busy', 'Connection port busy',
                                  'Another program (often a second Ano) holds the port. Ano keeps retrying; quit extra '
                                  'Ano copies from the tray if it persists.')
    elif outdated:
        state = 'outdated'
        summary = 'Extension needs updating (' + ', '.join(f'{b.title()} {v}' for b, v in sorted(outdated.items())) + ')'
        detail = f'Run python -m ano.configure browser again and reload the extension (Ano expects {EXTENSION_VERSION}).'
    elif browsers:
        state, summary = 'connected', 'Connected: ' + ', '.join(b.title() for b in browsers)
        detail = 'Ano can list and read your open tabs.'
    else:
        state, summary, detail = ('waiting', 'Waiting for the browser extension',
                                  'Open Brave/Chrome/Edge with the Ano extension loaded. Reload it if it was just added.')
    report = {'state': state, 'summary': summary, 'detail': detail, 'browsers': browsers,
              'outdated': {b: v for b, v in sorted(outdated.items())}}
    if control_mode:
        report['control_mode'] = control_mode
    return report


class BrowserBridge(QObject):
    """Lives on the main thread; call() may be used from the Claude worker thread."""
    _outgoing = Signal(str, str)
    _cancel_form_batches = Signal()

    changed = Signal()

    def __init__(self, token, port=PORT):
        super().__init__()
        self.token = token
        self.port = port
        self._browsers = {}  # name -> socket
        self._versions = {}  # name -> reported extension version
        self._pending = {}  # request id -> [Event, reply]
        self.server = QWebSocketServer('Ano', QWebSocketServer.SslMode.NonSecureMode, self)
        self.listening = self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), port)
        self.server.newConnection.connect(self._accept)
        self._outgoing.connect(self._send)
        self._cancel_form_batches.connect(self._cancel_forms_now)
        self.reconnect = QTimer(self)
        self.reconnect.setInterval(2000)
        self.reconnect.timeout.connect(self._retry_listen)
        if not self.listening:
            self.reconnect.start()
        # Pings keep the extension's service worker awake.
        self.pinger = QTimer(self)
        self.pinger.setInterval(20000)
        self.pinger.timeout.connect(self._ping)
        self.pinger.start()

    def browsers(self):
        return sorted(self._browsers)

    @Slot()
    def _retry_listen(self):
        if self.listening:
            self.reconnect.stop()
            return
        self.listening = self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), self.port)
        if self.listening:
            self.reconnect.stop()
            self.changed.emit()

    def cancel_forms(self):
        self._cancel_form_batches.emit()

    @Slot()
    def _cancel_forms_now(self):
        # Called on the Qt thread by Stop/Pause/lock. The extension checks between chunks.
        for socket in list(self._browsers.values()):
            socket.sendTextMessage(json.dumps({'id': uuid.uuid4().hex, 'action': 'form_cancel'}))

    def call(self, browser, action, _permitted=None, **arguments):
        identifier = uuid.uuid4().hex
        waiter = [Event(), None, browser, _permitted, None]
        self._pending[identifier] = waiter
        self._outgoing.emit(browser, json.dumps({'id': identifier, 'action': action, **arguments}))
        try:
            if not waiter[0].wait(REPLY_SECONDS):
                raise ToolError(f'{browser.title()} did not answer. It may be closed or busy.')
        finally:
            self._pending.pop(identifier, None)
        reply = waiter[1]
        if 'error' in reply:
            raise ToolError(f'{browser.title()} refused: {reply["error"]}')
        return reply.get('result')

    def _accept(self):
        socket = self.server.nextPendingConnection()
        # Web pages can open localhost WebSockets too, but only extensions have this origin.
        if not socket.origin().startswith('chrome-extension://'):
            socket.close()
            return
        socket.textMessageReceived.connect(lambda text, s=socket: self._message(s, text))
        socket.disconnected.connect(lambda s=socket: self._forget(s))

    def _message(self, socket, text):
        try:
            message = json.loads(text)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        if message.get('type') == 'hello':
            if hmac.compare_digest(str(message.get('token', '')), self.token):
                name = str(message.get('browser') or 'browser')[:20]
                self._versions[name] = str(message.get('version') or '')[:20]
                old = self._browsers.get(name)
                self._browsers[name] = socket
                if old is not None and old is not socket:
                    old.close()
                self.changed.emit()
            else:
                socket.close()
            return
        if socket not in self._browsers.values():
            return
        waiter = self._pending.get(message.get('id'))
        if waiter and waiter[4] is socket:
            waiter[1] = message
            waiter[0].set()

    def _forget(self, socket):
        changed = False
        for name, known in list(self._browsers.items()):
            if known is socket:
                del self._browsers[name]
                self._versions.pop(name, None)
                changed = True
        try:
            socket.deleteLater()
        except RuntimeError:
            pass  # Qt may already have destroyed a socket during application shutdown.
        if changed:
            self.changed.emit()

    def outdated(self):
        """Connected browsers whose extension version differs from the one Ano expects."""
        return {name: version for name, version in self._versions.items()
                if version and version != EXTENSION_VERSION}

    @Slot(str, str)
    def _send(self, browser, text):
        socket = self._browsers.get(browser)
        waiter = self._pending.get(json.loads(text).get('id'))
        if waiter and waiter[3] is not None and not waiter[3]():
            waiter[1] = {'error': 'Ano stopped before dispatch. Nothing was sent.'}
            waiter[0].set()
            return
        if socket is not None and waiter:
            waiter[4] = socket
            socket.sendTextMessage(text)

    def _ping(self):
        for socket in self._browsers.values():
            socket.sendTextMessage('{"type":"ping"}')

    def close(self):
        self.reconnect.stop()
        self.pinger.stop()
        for socket in list(self._browsers.values()):
            socket.close()
        self.server.close()


class Browser:
    def __init__(self, bridge, clock=time.time, outbound=None):
        self.bridge = bridge
        self.clock = clock
        self.outbound = outbound

    def definitions(self):
        tab = {'type': 'string', 'description': 'A tab id from list_tabs, e.g. "brave:123"'}
        return [
            {'name': 'open_browser_tab', 'description': 'Open a task website in a connected browser and return its tab ID. Scope the URL to the user’s request. Inspect the resulting page before acting.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {'browser': {'type': 'string'}, 'url': {'type': 'string'}}, 'required': ['browser', 'url']}},
            {'name': 'browser_connection_status',
             'description': 'Diagnose Ano’s own browser connection without reading page contents. Shows port availability and authenticated browser connections. Ano uses its installed extension, not Codex’s in-app browser.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {}}},
            {'name': 'list_tabs',
             'description': 'List open tabs in the connected browsers (Brave, Chrome, Edge) with title, site, '
                            'whether active, playing audio, pinned, and when each was last used.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {}}},
            {'name': 'close_tabs',
             'description': 'Close tabs by id. The user can reopen them with Ctrl+Shift+T. Never close pinned tabs '
                            'or the active tab unless the user asked for those specifically.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                 'tab_ids': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['tab_ids']}},
            {'name': 'switch_to_tab', 'description': 'Bring a tab and its browser window to the front.',
             'input_schema': {'type': 'object', 'additionalProperties': False,
                              'properties': {'tab_id': tab}, 'required': ['tab_id']}},
            {'name': 'mute_tab', 'description': 'Mute or unmute a tab.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                 'tab_id': tab, 'muted': {'type': 'boolean'}}, 'required': ['tab_id', 'muted']}},
            {'name': 'read_tab',
             'description': "Read the visible text of a tab's page (untrusted content). Cannot read browser "
                            'settings pages or the extension store.',
             'input_schema': {'type': 'object', 'additionalProperties': False,
                              'properties': {'tab_id': tab}, 'required': ['tab_id']}},
        ]

    def run(self, name, arguments):
        if name == 'browser_connection_status':
            return self.browser_connection_status()
        if self.bridge is None:
            raise ToolError(SETUP)
        if not self.bridge.listening:
            raise ToolError('Ano could not open its browser connection (port in use). Quit other Ano copies from the tray, then restart one copy.')
        if not self.bridge.browsers():
            raise ToolError(SETUP)
        return getattr(self, name)(**arguments)

    def browser_connection_status(self):
        report = health_report(self.bridge is not None,
                               bool(self.bridge and self.bridge.listening),
                               self.bridge.browsers() if self.bridge else [],
                               self.bridge.outdated() if self.bridge else {})
        report['scope'] = 'Ano’s extension in Chrome/Edge/Brave; Codex’s in-app tabs are separate'
        return json.dumps(report), 'Checked browser connection'

    def open_browser_tab(self, browser, url):
        from ..browser_tasks import clean_location
        clean_location(url)
        if browser not in self.bridge.browsers():
            raise ToolError('Choose a connected browser from browser_connection_status.')
        if self.outbound is not None:
            self.outbound.check([url])
        tab = self.bridge.call(browser, 'open', url=url)
        return json.dumps({'tab_id': f"{browser}:{tab['id']}", 'url': tab.get('url', url)}), 'Opened a task tab'

    def list_tabs(self):
        lines = []
        answered, failed = [], []
        for browser in self.bridge.browsers():
            try:
                tabs = self.bridge.call(browser, 'list')
            except ToolError as error:
                # One slow or unresponsive browser must not hide the tabs of the others.
                failed.append(f'{browser.title()} ({error})')
                continue
            answered.append(browser)
            for tab in tabs:
                flags = [flag for flag, on in (('active', tab['active']), ('playing audio', tab['audible']),
                                               ('muted', tab['muted']), ('pinned', tab['pinned'])) if on]
                used = ''
                if tab.get('lastAccessed'):
                    used = 'last used ' + ago(self.clock() - tab['lastAccessed'] / 1000)
                site = urlparse(tab['url']).netloc or tab['url'][:40]
                lines.append(' · '.join(filter(None, [f'tab {browser}:{tab["id"]}', ', '.join(flags), used,
                                                      tab['title'][:120], site])))
        if failed and not answered:
            raise ToolError('Could not reach ' + '; '.join(failed) + '.')
        body = '\n'.join(lines) or 'No tabs are open.'
        if failed:
            body += '\n\n[Could not reach ' + '; '.join(failed) + '. Tabs there are not shown.]'
        note = f'Looked at your tabs in {", ".join(answered)}' if answered else 'Checked your tabs'
        return body, note

    def close_tabs(self, tab_ids):
        grouped = {}
        for tab_id in tab_ids:
            browser, number = split(tab_id)
            grouped.setdefault(browser, []).append(number)
        for browser, numbers in grouped.items():
            self.bridge.call(browser, 'close', tabIds=numbers)
        count = sum(len(numbers) for numbers in grouped.values())
        return f'Closed {count} tab(s).', f'Closed {count} tab' + ('' if count == 1 else 's') + ' (Ctrl+Shift+T reopens)'

    def switch_to_tab(self, tab_id):
        browser, number = split(tab_id)
        result = self.bridge.call(browser, 'activate', tabId=number)
        return f'Switched to “{result.get("title", "")}”.', f'Switched to “{result.get("title", "")[:60]}”'

    def mute_tab(self, tab_id, muted):
        browser, number = split(tab_id)
        self.bridge.call(browser, 'mute', tabId=number, muted=bool(muted))
        return ('Muted.' if muted else 'Unmuted.'), ('Muted a tab' if muted else 'Unmuted a tab')

    def read_tab(self, tab_id):
        browser, number = split(tab_id)
        page = self.bridge.call(browser, 'read', tabId=number) or {}
        text = page.get('text', '')
        if len(text) > MAX_PAGE:
            text = text[:MAX_PAGE] + f'\n[Showing the first {MAX_PAGE:,} of {len(text):,} characters.]'
        return f'{page.get("title", "")}\n{page.get("url", "")}\n\n{text}', f'Read the tab “{page.get("title", "")[:60]}”'


class BrowserHealth(QObject):
    """Live browser-connection health for the UI, with no AI request. Always present, even with no extension set up."""
    changed = Signal()

    def __init__(self, configured, bridge=None, control_mode=lambda: ''):
        super().__init__()
        self._configured = configured
        self.bridge = bridge
        self._control_mode = control_mode
        self._report = self._compute()
        if bridge is not None:
            bridge.changed.connect(self._refresh)
        # A slow poll also catches the port freeing up between the bridge's own change signals.
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self._refresh)
        self.timer.start()

    def _compute(self):
        return health_report(self._configured,
                             bool(self.bridge and self.bridge.listening),
                             self.bridge.browsers() if self.bridge else [],
                             self.bridge.outdated() if self.bridge else {},
                             self._control_mode() or '')

    @Slot()
    def _refresh(self):
        report = self._compute()
        if report != self._report:
            self._report = report
            self.changed.emit()

    @Property(str, notify=changed)
    def state(self):
        return self._report['state']

    @Property(str, notify=changed)
    def summary(self):
        return self._report['summary']

    @Property(str, notify=changed)
    def detail(self):
        return self._report['detail']

    @Property('QVariantList', notify=changed)
    def browsers(self):
        return self._report['browsers']

    def close(self):
        self.timer.stop()


def split(tab_id):
    browser, _, number = str(tab_id).partition(':')
    if not number.isdigit():
        raise ToolError('Use tab ids from list_tabs, like "brave:123".')
    return browser, int(number)


def ago(seconds):
    if seconds < 90:
        return 'just now'
    if seconds < 5400:
        return f'{seconds / 60:.0f} min ago'
    if seconds < 172800:
        return f'{seconds / 3600:.0f} h ago'
    return f'{seconds / 86400:.0f} days ago'
