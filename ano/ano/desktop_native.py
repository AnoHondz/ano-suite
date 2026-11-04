"""Windows input backend. No clipboard, shell, elevation, or credential extraction."""
import ctypes
from ctypes import wintypes
import io
import os
from pathlib import Path
import time

from .plugins.windows import open_windows
from .tools import ToolError

TERMINALS = {'cmd.exe', 'powershell.exe', 'pwsh.exe', 'windowsterminal.exe'}
BLOCKED = {'consent.exe',
           'credentialuibroker.exe', 'logonui.exe', 'lockapp.exe', 'taskmgr.exe', 'keepass.exe',
           'keepassxc.exe', '1password.exe', 'bitwarden.exe', 'lastpass.exe', 'sechealthui.exe',
           'securityhealthsystray.exe'}
BROWSERS = {'chrome.exe', 'brave.exe', 'msedge.exe', 'firefox.exe', 'opera.exe'}
TEXT_ROLES = ('EditControl', 'ComboBoxControl')


def text_field(control):
    """A plain text field: an edit box, or a plain-text editor's document (Windows 11 Notepad's text area is a
    RichEdit "Document"). Other documents, such as Word's, are left out: setting their value would replace everything."""
    role = control.ControlTypeName
    return role in TEXT_ROLES or (role == 'DocumentControl' and (control.ClassName or '').startswith('RichEdit'))


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', wintypes.DWORD), ('u', INPUTUNION)]


class WindowsDesktop:
    def __init__(self):
        self.allow_terminals = False
        self.user = ctypes.windll.user32
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user.IsWindow.argtypes = [wintypes.HWND]
        self.user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        self.user.BringWindowToTop.argtypes = [wintypes.HWND]
        self.user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.kernel = ctypes.windll.kernel32
        self.kernel.GetCurrentThreadId.restype = wintypes.DWORD

    def describe(self, hwnd):
        import psutil
        row = next((row for row in open_windows() if row[0] == int(hwnd)), None)
        if not row or row[1] == os.getpid() or row[3]:
            raise ToolError('Choose an open, restored window belonging to another app.')
        process = psutil.Process(row[1])
        path = process.exe()
        if self.excluded(Path(path).name):
            raise ToolError('This app is excluded from desktop control. Use Ano’s dedicated tools or interact manually.')
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ToolError('The target window is unavailable.')
        bounds = (rect.left, rect.top, rect.right, rect.bottom)
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            raise ToolError('The target window has no visible area.')
        return dict(hwnd=int(hwnd), pid=row[1], title=row[2], app=Path(path).name,
                    path=str(Path(path).resolve()), born=process.create_time(), rect=bounds)

    def excluded(self, app):
        return app.casefold() in BLOCKED or (app.casefold() in TERMINALS and not self.allow_terminals)

    def focus(self, target):
        if self.describe(target['hwnd']) != target:
            raise ToolError('The window changed. Observe it again before continuing.')
        hwnd = target['hwnd']
        # Each try is stronger than the last; Windows' focus rules let different ones through at different times.
        for attempt in (self._force_foreground, self._nudge_then_force, self._focus_by_automation, self._restore_cycle):
            if self.user.GetForegroundWindow() == hwnd:
                return
            try:
                attempt(hwnd)
            except Exception:
                continue
            deadline = time.monotonic() + .40
            while self.user.GetForegroundWindow() != hwnd and time.monotonic() < deadline:
                time.sleep(.01)
        if self.user.GetForegroundWindow() != hwnd:
            raise ToolError(f'Windows did not focus the selected app ({self._front_app()} stayed in front). No input was sent. '
                            'Click the app once, then ask again.')

    def _nudge_then_force(self, hwnd):
        # Windows lets the process that sent the last input change focus. F24 is a key no app uses.
        self.user.keybd_event(0x87, 0, 0, 0)
        self.user.keybd_event(0x87, 0, 2, 0)
        self._force_foreground(hwnd)

    @staticmethod
    def _focus_by_automation(hwnd):
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            auto.ControlFromHandle(hwnd).SetFocus()

    def _restore_cycle(self, hwnd):
        self.user.ShowWindow(hwnd, 6)  # SW_MINIMIZE, then SW_RESTORE: a restored window is activated
        self.user.ShowWindow(hwnd, 9)
        self._force_foreground(hwnd)

    def _front_app(self):
        import psutil
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(self.user.GetForegroundWindow(), ctypes.byref(pid))
        try:
            return psutil.Process(pid.value).name()
        except psutil.Error:
            return 'another window'

    def _force_foreground(self, hwnd):
        # Windows refuses a plain SetForegroundWindow unless the caller is already the foreground
        # thread. Attaching our input queue to the current-foreground and target threads lifts that
        # restriction, so focus returns reliably to the target even after Ano's own window stole it.
        current = self.kernel.GetCurrentThreadId()
        foreground = self.user.GetForegroundWindow()
        threads = []
        for window in (foreground, hwnd):
            thread = self.user.GetWindowThreadProcessId(window, None) if window else 0
            if thread and thread != current and self.user.AttachThreadInput(current, thread, True):
                threads.append(thread)
        try:
            self.user.ShowWindow(hwnd, 9)  # SW_RESTORE, in case the target is minimized
            self.user.BringWindowToTop(hwnd)
            self.user.SetForegroundWindow(hwnd)
        finally:
            for thread in threads:
                self.user.AttachThreadInput(current, thread, False)

    def snapshot(self, hwnd):
        from PIL import ImageGrab
        target = self.describe(hwnd)
        self.focus(target)
        image = ImageGrab.grab(bbox=target['rect'], all_screens=True)
        image.thumbnail((1600, 1600))
        image = image.convert('RGB')
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=85)
        if self.describe(hwnd) != target:
            raise ToolError('The window changed during capture. Try again.')
        return target, image.size, output.getvalue()

    def _send(self, events):
        data = (INPUT * len(events))(*events)
        sent = self.user.SendInput(len(events), data, ctypes.sizeof(INPUT))
        if sent != len(events):
            if sent:
                raise ToolError('Windows accepted only part of the input. Some text or action may already have occurred; inspect before retrying.')
            raise ToolError('Windows blocked input. Ano cannot control elevated or protected windows.')

    def _mouse(self, flags, x=0, y=0, data=0):
        self._send([INPUT(type=0, u=INPUTUNION(mi=MOUSEINPUT(x, y, data & 0xffffffff, flags, 0, 0)))])

    def _point(self, target, size, x, y):
        if not (0 <= x < size[0] and 0 <= y < size[1]):
            raise ToolError('The pointer position is outside the observed image.')
        left, top, right, bottom = target['rect']
        px = left + round(x * (right - left) / size[0])
        py = top + round(y * (bottom - top) / size[1])
        vx, vy = self.user.GetSystemMetrics(76), self.user.GetSystemMetrics(77)
        vw, vh = self.user.GetSystemMetrics(78), self.user.GetSystemMetrics(79)
        self._mouse(0x8000 | 0x4000 | 1, round((px - vx) * 65535 / max(1, vw - 1)),
                    round((py - vy) * 65535 / max(1, vh - 1)))

    def _safe_focus(self, target):
        import uiautomation as auto
        self.focus(target)
        with auto.UIAutomationInitializerInThread():
            focused = auto.GetFocusedControl()
            if focused is None or focused.IsPassword:
                raise ToolError('Password entry is handled only through the approved vault flow.')
            name = (focused.Name or '').casefold()
            if not self.allow_terminals and any(word in name for word in ('terminal', 'powershell', 'command prompt')):
                raise ToolError('Use the reviewed Run fix flow for terminal commands.')

    def _belongs_to(self, control, target):
        # Electron controls can belong to a renderer PID. Bind ancestry to the observed window,
        # rather than assuming every child uses the top-level process ID.
        for _ in range(32):
            if control is None:
                return False
            if int(control.NativeWindowHandle or 0) == target['hwnd']:
                return True
            control = control.GetParentControl()
        return False

    def _editables(self, target):
        import uiautomation as auto
        root = auto.ControlFromHandle(target['hwnd'])
        pending, found, visited = [(root, 0)], [], 0
        deadline = time.monotonic() + 4
        while pending and visited < 500 and len(found) < 20 and time.monotonic() < deadline:
            control, depth = pending.pop(0)
            visited += 1
            try:
                if control.IsPassword:
                    continue
            except Exception:
                # Unknown privacy state is never safe to descend into.
                continue
            try:
                if text_field(control):
                    pattern = control.GetValuePattern()
                    if pattern is not None and not pattern.IsReadOnly:
                        found.append((control, list(control.GetRuntimeId())))
            except Exception:
                pass
            if depth < 22:
                try:
                    pending.extend((child, depth + 1) for child in control.GetChildren()[:100])
                except Exception:
                    pass
        return found

    def inspect_editables(self, target):
        import uiautomation as auto
        if self.describe(target['hwnd']) != target:
            raise ToolError('The window changed before inspecting fields.')
        with auto.UIAutomationInitializerInThread():
            found = []
            for control, runtime in self._editables(target):
                try:
                    found.append(dict(id=str(len(found) + 1), label=(control.Name or '')[:160],
                                      role=control.ControlTypeName, runtime_id=runtime))
                except Exception:
                    continue
            return found

    def inspect_surface(self, target):
        """Bounded labels/roles, never textbox values. IDs are scoped to this inspection."""
        import uiautomation as auto
        if self.describe(target['hwnd']) != target:
            raise ToolError('The window changed before reading its controls.')
        with auto.UIAutomationInitializerInThread():
            pending = [(auto.ControlFromHandle(target['hwnd']), 0, [])]
            rows, editables, actions = [], [], []
            deadline, visited, truncated = time.monotonic() + 4, 0, False
            while pending and visited < 500 and time.monotonic() < deadline:
                control, depth, parents = pending.pop(0)
                visited += 1
                try:
                    if control.IsPassword:
                        continue
                except Exception:
                    truncated = True
                    continue
                label = ''
                try:
                    label, role = (control.Name or '')[:240], control.ControlTypeName
                    identifier = str(visited)
                    offscreen = bool(getattr(control, 'IsOffscreen', False))
                    enabled = bool(getattr(control, 'IsEnabled', True))
                    rows.append(dict(role=role, label=label, depth=depth, offscreen=offscreen, enabled=enabled, parents=parents[-4:]))
                    editable = text_field(control)
                    if not offscreen and enabled and (editable or role in ('ButtonControl', 'MenuItemControl')):
                        item = dict(id=identifier, label=label[:160], role=role, runtime_id=list(control.GetRuntimeId()))
                        if editable:
                            pattern = control.GetValuePattern()
                            if pattern is not None and not pattern.IsReadOnly:
                                editables.append(item)
                        if role in ('ButtonControl', 'MenuItemControl') and control.GetInvokePattern() is not None:
                            actions.append(item)
                except Exception:
                    truncated = True
                try:
                    if depth < 22:
                        children = control.GetChildren()
                        truncated = truncated or len(children) > 100
                        ancestors = parents + ([label[:100]] if label else [])
                        pending.extend((child, depth + 1, ancestors) for child in children[:100])
                    elif control.GetChildren():
                        truncated = True
                except Exception:
                    truncated = True
            return dict(accessibility=rows, editables=editables, actions=actions,
                        truncated=truncated or bool(pending), scope='Accessible labels in this window; hidden or virtualized content may be absent. Textbox values omitted.')

    def _invoke_element(self, target, args, allowed):
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            pending, visited = [auto.ControlFromHandle(target['hwnd'])], 0
            deadline = time.monotonic() + 4
            while pending and visited < 500 and time.monotonic() < deadline:
                control = pending.pop(0)
                visited += 1
                try:
                    if control.IsPassword:
                        continue
                except Exception:
                    continue
                try:
                    pending.extend(control.GetChildren()[:100])
                except Exception:
                    pass
                try:
                    identifier = list(control.GetRuntimeId())
                    if identifier != args.get('_element_runtime_id'):
                        continue
                    if (control.ControlTypeName not in ('ButtonControl', 'MenuItemControl')
                            or control.ControlTypeName != args['_element_role']
                            or (control.Name or '')[:160] != args['_element_label']
                            or bool(getattr(control, 'IsOffscreen', False))
                            or not bool(getattr(control, 'IsEnabled', True))
                            or not self._belongs_to(control, target)):
                        raise ToolError('The observed button changed. No invocation was attempted.')
                    pattern = control.GetInvokePattern()
                    if pattern is None or not allowed() or self.user.GetForegroundWindow() != target['hwnd']:
                        raise ToolError('Control or the selected button is unavailable. No invocation was attempted.')
                    try:
                        accepted = pattern.Invoke(waitTime=0)
                        return dict(action_dispatched=True, action_verified=False, action_dispatch_uncertain=accepted is False,
                                    verification='Invoked the observed button. Read or observe the resulting state; dispatch is not completion.')
                    except Exception:
                        return dict(action_dispatched=True, action_verified=False, action_dispatch_uncertain=True,
                                    verification='Invocation outcome is uncertain. Do not repeat it; use read-only inspection of the result.')
                except ToolError:
                    raise
                except Exception:
                    continue
        raise ToolError('The observed button is no longer available. Observe it again before acting.')

    def _focused_edit_state(self, target):
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            control = auto.GetFocusedControl()
            if (control is None or control.IsPassword or not self._belongs_to(control, target)
                    or not text_field(control)):
                raise ToolError('The selected app does not have a confirmed editable field focused. No text was sent. '
                                'Observe again and use set_text with an observed editable element_id.')
            pattern = control.GetValuePattern()
            if pattern is None or pattern.IsReadOnly or not isinstance(pattern.Value, str):
                raise ToolError('This field does not expose readable text. No text was sent; use a supported field or browser element tools.')
            return list(control.GetRuntimeId()), pattern.Value

    def _set_text(self, target, args, allowed):
        import uiautomation as auto
        runtime = args.get('_element_runtime_id')
        with auto.UIAutomationInitializerInThread():
            control = next((control for control, identifier in self._editables(target) if identifier == runtime), None)
            if control is None or control.IsPassword or not self._belongs_to(control, target):
                raise ToolError('The observed text field changed. Observe it again; no text was sent.')
            if ((control.Name or '')[:160] != args.get('_element_label', (control.Name or '')[:160])
                    or control.ControlTypeName != args.get('_element_role', control.ControlTypeName)):
                raise ToolError('The observed field label or role changed. Observe it again; no text was sent.')
            if not self.allow_terminals and any(word in (control.Name or '').casefold() for word in ('terminal', 'powershell', 'command prompt')):
                raise ToolError('Terminal fields require the user-selected Free roam mode.')
            pattern = control.GetValuePattern()
            if pattern is None or pattern.IsReadOnly:
                raise ToolError('The observed field is no longer editable. No text was sent.')
            if not allowed() or self.user.GetForegroundWindow() != target['hwnd']:
                raise ToolError('Control or window focus changed before setting text. No text was sent.')
            if pattern.Value == args['text']:
                return dict(text_verified=True, text_dispatched=False, verification='The field already contains the exact requested text. It was not entered twice.')
            try:
                pattern.SetValue(args['text'], waitTime=0)
                deadline, verified = time.monotonic() + .5, False
                while allowed():
                    if control.IsPassword or list(control.GetRuntimeId()) != runtime:
                        break
                    if pattern.Value == args['text']:
                        verified = True
                        break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(.025)
            except Exception:
                verified = False
            return dict(text_verified=verified, text_dispatched=True,
                        verification='Exact field value read back.' if verified else 'Field value did not confirm the write. Do not repeat typing; inspect the current screen.')

    def perform(self, target, size, action, args, allowed):
        self._safe_focus(target)
        if not allowed():
            raise ToolError('Control was stopped. No input was sent.')
        if action == 'set_text':
            return self._set_text(target, args, allowed)
        if action == 'invoke':
            return self._invoke_element(target, args, allowed)
        if action in ('click', 'move', 'scroll', 'drag'):
            self._point(target, size, args['x'], args['y'])
            if action == 'click':
                flags = (0x0008, 0x0010) if args.get('button') == 'right' else (0x0002, 0x0004)
                for _ in range(args.get('count', 1)):
                    self._mouse(flags[0])
                    self._mouse(flags[1])
            elif action == 'scroll':
                self._mouse(0x0800, data=args['amount'] * 120)
            elif action == 'drag':
                self._mouse(0x0002)
                try:
                    for step in range(1, 11):
                        if not allowed() or self.user.GetForegroundWindow() != target['hwnd']:
                            raise ToolError('Drag stopped.')
                        self._point(target, size, args['x'] + (args['end_x'] - args['x']) * step / 10,
                                    args['y'] + (args['end_y'] - args['y']) * step / 10)
                        time.sleep(.015)
                finally:
                    self._mouse(0x0004)
        elif action == 'type':
            try:
                runtime, before = self._focused_edit_state(target)
            except ToolError:
                if not self.allow_terminals:
                    raise
                runtime, before = None, ''  # Explicit Free roam retains raw typing for terminal/unsupported controls.
            # UTF-16 packets support Unicode without touching the clipboard.
            for offset in range(0, len(args['text']), 32):
                if not allowed() or self.user.GetForegroundWindow() != target['hwnd']:
                    raise ToolError('Typing stopped because control or focus changed. Some text may already have been entered; do not blindly repeat it.')
                encoded = args['text'][offset:offset + 32].encode('utf-16-le')
                units = [int.from_bytes(encoded[i:i+2], 'little') for i in range(0, len(encoded), 2)]
                events = [INPUT(type=1, u=INPUTUNION(ki=KEYBDINPUT(0, unit, flag, 0, 0)))
                          for unit in units for flag in (4, 6)]
                self._send(events)
            try:
                deadline, verified = time.monotonic() + .5, False
                while runtime is not None and allowed():
                    current, after = self._focused_edit_state(target)
                    if current != runtime:
                        break
                    if after != before and args['text'] in after:
                        verified = True
                        break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(.025)
            except Exception:
                verified = False
            return dict(text_verified=verified, text_dispatched=True,
                        verification='Focused field changed and the requested text was read back.' if verified else
                        'Typing was dispatched but the requested text was not confirmed in the focused field. Do not assume a focus cause or blindly type again.')
        elif action == 'key':
            keys = parse_keys(args['keys'], allow_windows=self.allow_terminals)
            self._send([INPUT(type=1, u=INPUTUNION(ki=KEYBDINPUT(key, 0, 0, 0, 0))) for key in keys]
                       + [INPUT(type=1, u=INPUTUNION(ki=KEYBDINPUT(key, 0, 2, 0, 0))) for key in reversed(keys)])

    def password_target(self, hwnd):
        import uiautomation as auto
        target = self.describe(hwnd)
        if target['app'].casefold() in BROWSERS:
            raise ToolError('Browser passwords require the exact-site extension flow.')
        self.focus(target)
        with auto.UIAutomationInitializerInThread():
            control = auto.GetFocusedControl()
            if control is None or not control.IsPassword or control.ProcessId != target['pid']:
                raise ToolError('Focus a password field in this app, then try again.')
            return target, list(control.GetRuntimeId())

    def fill_password(self, target, runtime_id, password, allowed):
        import uiautomation as auto
        self.focus(target)
        with auto.UIAutomationInitializerInThread():
            control = auto.GetFocusedControl()
            if (control is None or not control.IsPassword or control.ProcessId != target['pid']
                    or list(control.GetRuntimeId()) != runtime_id or not allowed()):
                raise ToolError('The password field or approval changed. Nothing was filled.')
            pattern = control.GetValuePattern()
            if pattern is None or pattern.IsReadOnly:
                raise ToolError('This password field does not support secure value filling. Fill it manually.')
            if not pattern.SetValue(password, waitTime=0):
                raise ToolError('Windows did not confirm the password fill.')


def parse_keys(value, allow_windows=False):
    special = {'ctrl': 0x11, 'alt': 0x12, 'shift': 0x10, 'enter': 0x0d, 'tab': 9, 'escape': 0x1b,
               'backspace': 8, 'delete': 0x2e, 'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
               'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22, 'space': 0x20}
    if allow_windows:
        special['win'] = 0x5b
    tokens = value.casefold().split('+')
    if len(tokens) > 4 or len(set(tokens)) != len(tokens) or set(tokens) >= {'ctrl', 'alt', 'delete'}:
        raise ToolError('That key combination is not supported.')
    keys = []
    for token in tokens:
        key = special.get(token)
        if key is None and len(token) == 1 and token.isascii() and token.isalnum():
            key = ord(token.upper())
        if key is None and token.startswith('f') and token[1:].isdigit() and 1 <= int(token[1:]) <= 12:
            key = 0x70 + int(token[1:]) - 1
        if key is None:
            raise ToolError('Unsupported key. Use named keys or Ctrl/Alt/Shift plus letters.')
        keys.append(key)
    return keys
