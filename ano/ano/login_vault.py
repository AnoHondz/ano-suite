"""User-managed credentials. Only approved local fillers ever receive passwords."""
import json
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import hashlib
from pathlib import Path
import threading
import sys
import uuid
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl

from .credentials import vault
from .desktop_native import BROWSERS
from .plugins.browser import split
from .tools import ToolError

SERVICE = 'Ano/Logins'


def origin(value):
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or any(char.isspace() for char in value)):
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        if ':' in host:
            host = '[' + host + ']'
        return 'https://' + host + (f':{port}' if port not in (None, 443) else '')
    except (ValueError, UnicodeError):
        raise ToolError('Use a valid HTTPS website address without embedded credentials.') from None


def app_path(value):
    if value.startswith('file:'):
        value = QUrl(value).toLocalFile()
    path = Path(value).expanduser()
    if not path.is_absolute() or path.suffix.lower() != '.exe' or not path.is_file():
        raise ToolError('Choose the full path of an existing Windows .exe.')
    if path.name.casefold() in BROWSERS:
        raise ToolError('Save browser logins as websites so the exact origin can be checked.')
    return str(path.resolve())


class LoginStore:
    def __init__(self, backend=None):
        self.backend = backend if backend is not None else vault()
        self.lock = threading.RLock()

    @contextmanager
    def locked(self):
        with self.lock:
            if sys.platform != 'win32':
                yield
                return
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            name = 'Local\\AnoVault-' + hashlib.sha256(SERVICE.encode()).hexdigest()[:20]
            handle = kernel.CreateMutexW(None, False, name)
            if not handle:
                raise ToolError('Windows could not lock the vault for this operation.')
            acquired = False
            try:
                acquired = kernel.WaitForSingleObject(handle, 5000) in (0, 0x80)
                if not acquired:
                    raise ToolError('Another Ano instance is using the vault. Try again shortly.')
                yield
            finally:
                if acquired:
                    kernel.ReleaseMutex(handle)
                kernel.CloseHandle(handle)

    def entries(self):
        with self.locked():
            text = self.backend.get_password(SERVICE, 'index')
            header = json.loads(text) if text else []
            if isinstance(header, list):  # Initial prototype index, migrated on the next write.
                return header
            if not isinstance(header, dict) or not 0 <= header.get('pages', -1) <= 10000:
                raise ToolError('The vault index could not be read.')
            rows = []
            for page in range(header['pages']):
                values = json.loads(self.backend.get_password(SERVICE, f'index-{header["generation"]}-{page}') or '[]')
                for identifier in values:
                    if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
                        raise ToolError('The vault index could not be read.')
                    meta = self.backend.get_password(SERVICE, identifier + '-meta')
                    if meta:
                        rows.append(json.loads(meta))
                    else:
                        legacy = self.backend.get_password(SERVICE, identifier)
                        if legacy:
                            record = json.loads(legacy)
                            rows.append({key: record[key] for key in ('id', 'label', 'kind', 'target', 'username')})
            return rows

    def _write_index(self, identifiers):
        # WinCred blobs are small. Versioned pages keep many logins from overflowing one credential.
        old_text = self.backend.get_password(SERVICE, 'index')
        old = json.loads(old_text) if old_text else []
        generation = uuid.uuid4().hex
        pages = [identifiers[i:i+20] for i in range(0, len(identifiers), 20)]
        written = []
        try:
            for number, page in enumerate(pages):
                account = f'index-{generation}-{number}'
                self.backend.set_password(SERVICE, account, json.dumps(page))
                written.append(account)
            self.backend.set_password(SERVICE, 'index', json.dumps(dict(generation=generation, pages=len(pages))))
        except Exception:
            for account in written:
                self.backend.delete_password(SERVICE, account)
            raise
        if isinstance(old, dict):
            for number in range(old.get('pages', 0)):
                try:
                    self.backend.delete_password(SERVICE, f'index-{old["generation"]}-{number}')
                except Exception:
                    pass  # Old manifests contain only opaque ids, never secrets.

    def save(self, label, kind, target, username, password):
        if kind not in ('website', 'app') or not label.strip() or not username.strip() or not password:
            raise ToolError('Enter a name, username, password, and login type.')
        if len(label) > 100 or len(username) > 320 or len(password) > 4096:
            raise ToolError('One of the login fields is too long.')
        target = origin(target) if kind == 'website' else app_path(target)
        identifier = uuid.uuid4().hex
        metadata = dict(id=identifier, label=label.strip(), kind=kind, target=target, username=username.strip())
        payload = json.dumps(metadata, ensure_ascii=False)
        if len(payload.encode('utf-16-le')) > 2400 or len(password.encode('utf-16-le')) > 2400:
            raise ToolError('This login exceeds Windows Credential Manager’s entry size. Shorten the fields.')
        with self.locked():
            rows = self.entries()
            try:
                self.backend.set_password(SERVICE, identifier, password)
                self.backend.set_password(SERVICE, identifier + '-meta', payload)
                self._write_index([row['id'] for row in rows] + [identifier])
            except Exception:
                for account in (identifier, identifier + '-meta'):
                    try:
                        self.backend.delete_password(SERVICE, account)
                    except Exception:
                        pass
                raise
        return identifier

    def secret(self, metadata):
        with self.locked():
            meta = self.backend.get_password(SERVICE, metadata['id'] + '-meta')
            raw = self.backend.get_password(SERVICE, metadata['id'])
            row = json.loads(meta) if meta else json.loads(raw or '{}')
            password = raw if meta else row.get('password')
            if any(row.get(key) != value for key, value in metadata.items()) or not password:
                raise ToolError('The saved login changed. Request a new approval.')
            return password

    def delete(self, identifier):
        with self.locked():
            rows = self.entries()
            if not any(row['id'] == identifier for row in rows):
                return
            self.backend.delete_password(SERVICE, identifier)
            if self.backend.get_password(SERVICE, identifier + '-meta') is not None:
                self.backend.delete_password(SERVICE, identifier + '-meta')
            self._write_index([row['id'] for row in rows if row['id'] != identifier])


class LoginVault(QObject):
    changed = Signal()
    privateChanged = Signal()
    _begin = Signal(object)
    _end = Signal()

    def __init__(self, store, reviews, desktop_backend, allowed, stop_control, bridge=None):
        super().__init__()
        self.store = store
        self.reviews = reviews
        self.backend = desktop_backend
        self.allowed = allowed
        self.stop_control = stop_control
        self.bridge = bridge
        self._visible = False
        self._busy = False
        self._hold = False
        self._status = 'Passwords stay in Windows Credential Manager. Approval is required each time.'
        self._begin.connect(self._activate)
        self._end.connect(self._finish)

    @Property(bool, notify=privateChanged)
    def private(self):
        return self._visible or self._busy or self._hold

    @Property(bool, notify=privateChanged)
    def hold(self):
        return self._hold

    @Slot()
    def done(self):
        if not self._busy and self.allowed():
            self._hold = False
            self.privateChanged.emit()

    @Property(bool, notify=privateChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property('QVariantList', notify=changed)
    def entries(self):
        try:
            return self.store.entries() if self.store else []
        except Exception:
            return []

    @Slot(bool)
    def setVisible(self, value):
        self._visible = value
        if value:
            self.stop_control()
        self.privateChanged.emit()

    @Slot(str, str, str, str, str, result=bool)
    def save(self, label, kind, target, username, password):
        try:
            if not self.allowed() or self.store is None:
                raise ToolError('Unlock and resume Ano before saving a login.')
            self.store.save(label, kind, target, username, password)
            self._status = 'Login saved in Windows Credential Manager.'
            self.changed.emit()
            return True
        except ToolError as error:
            self._status = str(error)
        except Exception:
            self._status = 'Windows Credential Manager could not save the login. Nothing was put in chat.'
        self.changed.emit()
        return False

    @Slot(str)
    def delete(self, identifier):
        try:
            if not self.allowed() or self._busy or self.store is None:
                raise ToolError('Resume Ano and wait for the current login request to finish.')
            self.store.delete(identifier)
            self._status = 'Saved login removed.'
        except Exception:
            self._status = 'The login could not be removed.'
        self.changed.emit()

    @Slot(object)
    def _activate(self, ticket):
        if not ticket.get('cancelled') and self.allowed() and not self._visible and not self._busy:
            self.stop_control()
            self._busy = True
            ticket['accepted'] = True
            self.privateChanged.emit()
        ticket['event'].set()

    @Slot()
    def _finish(self):
        self._busy = False
        self.privateChanged.emit()

    def definitions(self):
        return [
            {'name': 'list_saved_logins', 'description': 'List saved login labels, targets, and ids. No passwords or usernames are returned.',
             'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
            {'name': 'request_saved_login', 'description': 'Ask the user to approve ONE local use of a saved login. '
             'For websites supply a tab_id from list_tabs; exact HTTPS origin and document identity are enforced by the extension. '
             'For native apps supply a window_id from list_windows; the user must first focus a password field in the saved executable. '
             'Passwords never come back to you. This fills but never submits; computer control is stopped for the login.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                 'login_id': {'type': 'string'}, 'tab_id': {'type': 'string'}, 'window_id': {'type': 'integer'}},
                 'required': ['login_id']}},
            {'name': 'sign_in_saved_website', 'description': 'Ask the user to approve filling AND submitting one ordinary saved website login. Exact HTTPS origin, document, same-origin POST form, and one submit control are required. No passwords are returned. MFA/CAPTCHA/SSO or unsupported forms require the user. Private mode stays on until the user finishes authentication and clears it; then resume the saved task.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                 'login_id': {'type': 'string'}, 'tab_id': {'type': 'string'}}, 'required': ['login_id', 'tab_id']}},
        ]

    def run(self, name, arguments):
        if not self.allowed() or self.store is None:
            raise ToolError('The credential vault is unavailable, paused, or locked.')
        if name == 'list_saved_logins':
            rows = [{key: row[key] for key in ('id', 'label', 'kind', 'target')} for row in self.store.entries()]
            return json.dumps(rows), 'Listed saved login names (no secrets)'
        if name == 'sign_in_saved_website':
            return self.use(**arguments, submit=True)
        if name != 'request_saved_login':
            raise ToolError('Unknown saved-login tool.')
        return self.use(**arguments)

    def use(self, login_id, tab_id='', window_id=0, submit=False):
        metadata = next((row for row in self.store.entries() if row['id'] == login_id), None)
        if metadata is None:
            raise ToolError('That saved login no longer exists.')
        if submit and metadata['kind'] != 'website':
            raise ToolError('Automatic sign-in is supported only for approved website forms.')
        ticket = dict(event=threading.Event(), accepted=False, cancelled=False)
        self._begin.emit(ticket)
        if not ticket['event'].wait(5):
            ticket['cancelled'] = True
            if ticket['accepted']:
                self._end.emit()
            raise ToolError('The login review could not start. Try again.')
        if not ticket['accepted']:
            raise ToolError('Close the vault editor before requesting a login.')
        secret_phase = False
        try:
            if metadata['kind'] == 'website':
                if self.bridge is None:
                    raise ToolError('Connect or reload Ano’s browser extension before filling a website login.')
                browser, number = split(tab_id)
                context = self.bridge.call(browser, 'login_context', tabId=number, **({'submit': True} if submit else {}))
                if origin(context.get('origin', '')) != metadata['target'] or not context.get('documentId'):
                    raise ToolError('The browser tab does not match this saved login’s exact website.')
                if submit and context.get('canSubmit') is not True:
                    raise ToolError('This website requires a manual login or an extension reload. No secret was loaded.')
            else:
                target, runtime_id = self.backend.password_target(window_id)
                if str(Path(target['path']).resolve()).casefold() != metadata['target'].casefold():
                    raise ToolError('That window is not the application saved for this login.')
            action_detail = ('Fill and submit this one sign-in form. The password never goes to the AI. '
                             'Sign-in success is not guaranteed. Finish any MFA/CAPTCHA yourself, clear private mode, '
                             're-enable Computer control, then ask Ano to continue the saved task.' if submit else
                             'Fill locally once. The password is never shown to the AI. '
                             'Nothing is submitted, and computer control stays stopped. Finish logging in yourself.')
            self.reviews.request(kind='login', title='Sign in with this saved login?' if submit else 'Use this saved login?', target=metadata['target'],
                                 detail=f'{metadata["label"]}\nUsername: {metadata["username"]}\n\n' + action_detail,
                                 preview='', button='Sign in once' if submit else 'Allow this login once')
            if not self.allowed():
                raise ToolError('Login cancelled because Ano was paused or locked.')
            self._hold = True
            self.privateChanged.emit()
            secret_phase = True
            password = self.store.secret(metadata)  # Only reached after the human approval.
            try:
                if not self.allowed():
                    raise ToolError('Login cancelled.')
                if metadata['kind'] == 'website':
                    result = self.bridge.call(browser, 'fill_login', tabId=number, origin=metadata['target'],
                                              documentId=context['documentId'], username=metadata['username'], password=password,
                                              **({'submit': True} if submit else {}))
                    if not result or result.get('filled') is not True:
                        raise ToolError('The browser did not confirm a successful local fill.')
                    if submit and result.get('submitted') is not True:
                        raise ToolError('The sign-in submission was not confirmed. Do not automatically retry it.')
                else:
                    self.backend.fill_password(target, runtime_id, password, self.allowed)
            finally:
                password = None
            if submit:
                return ('The approved sign-in was submitted locally. Authentication is not yet verified. '
                        'Finish any MFA/CAPTCHA, clear private mode and re-enable control; then ask Ano to resume the saved task.',
                        'Submitted the approved sign-in once; authentication not yet verified')
            return 'Approved credentials were filled locally, not submitted. Control is stopped. The user finishes login manually.', 'Filled the approved login locally (not submitted)'
        except ToolError:
            if secret_phase:
                if submit:
                    raise ToolError('The sign-in result is uncertain. Finish or inspect it manually; do not automatically submit again. No secret details were returned.') from None
                raise ToolError('The approved login could not be filled. Finish it manually; no secret details were returned.') from None
            raise
        except Exception:
            raise ToolError('The saved login could not be used. No secret details were returned.') from None
        finally:
            self._end.emit()
