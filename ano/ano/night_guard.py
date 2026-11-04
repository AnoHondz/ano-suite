"""Overnight trading guard: while the user sleeps, keep enough RAM free for Ano's trading bots.

The user authorized this on 2026-11-01: during the overnight window, other desktop apps may be closed
when memory stays critically low, so the London session keeps trading. Ano, ScorPred, Ollama, the
wallpaper and Windows itself are never touched. Settings live in overnight-trading.json in Ano's data
folder; set "enabled" to false there (or say "stop overnight guard") to turn it off.
"""
from datetime import datetime, time, timezone
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
DEFAULTS = {'enabled': True, 'start': '01:00', 'end': '07:00', 'ram_percent': 90,
            'authorized': '2026-11-01: user allowed any other app to be closed overnight so trading continues'}
# Never closed: Ano and its services, the model server, the wallpaper, shells, and Windows components.
PROTECTED = {
    'python', 'pythonw', 'py', 'node', 'ollama', 'ollama app', 'ollama_llama_server', 'lively', 'livelywpf',
    'explorer', 'dwm', 'csrss', 'winlogon', 'svchost', 'system', 'registry', 'smss', 'lsass', 'services',
    'wininit', 'fontdrvhost', 'sihost', 'ctfmon', 'taskhostw', 'searchhost', 'searchindexer',
    'startmenuexperiencehost', 'shellexperiencehost', 'runtimebroker', 'textinputhost', 'securityhealthsystray',
    'securityhealthservice', 'msmpeng', 'nissrv', 'audiodg', 'conhost', 'powershell', 'pwsh', 'cmd',
    'msedgewebview2',  # shared web engine inside other apps (including the wallpaper); closing it breaks them unpredictably
    'windowsterminal', 'openconsole', 'memory compression', 'spoolsv', 'dllhost', 'wudfhost', 'lockapp',
    'code',  # VS Code: the user asked on 2026-11-02 that it never be closed
}


def user_apps():
    """Desktop apps owned by this user, grouped by name: {name: (total_bytes, [processes])}."""
    import psutil
    me = (os.environ.get('USERNAME') or '').lower()
    windows = (os.environ.get('WINDIR') or r'C:\Windows').lower()
    groups = {}
    for p in psutil.process_iter(['name', 'username', 'exe', 'memory_info']):
        try:
            info = p.info
            name = (info['name'] or '').lower().removesuffix('.exe')
            if not name or name in PROTECTED or not info['memory_info']:
                continue
            if not (info['username'] or '').lower().endswith('\\' + me) or (info['exe'] or '').lower().startswith(windows):
                continue
            total, procs = groups.get(name, (0, []))
            groups[name] = (total + info['memory_info'].rss, procs + [p])
        except Exception:
            continue
    return groups


AWAY_SECONDS = 600  # no mouse or keyboard input for this long counts as away


def idle_seconds():
    """Seconds since the last mouse or keyboard input on this PC (Windows); 0 when unknown, so nothing closes."""
    try:
        import ctypes
        class LastInput(ctypes.Structure):
            _fields_ = [('size', ctypes.c_uint), ('tick', ctypes.c_uint)]
        info = LastInput(ctypes.sizeof(LastInput), 0)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):return 0
        return ((ctypes.windll.kernel32.GetTickCount() - info.tick) & 0xFFFFFFFF) / 1000
    except Exception:
        return 0


def foreground_app():
    """Name of the app in the window the user is looking at ('' when unknown)."""
    try:
        import ctypes, psutil
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(ctypes.windll.user32.GetForegroundWindow(), ctypes.byref(pid))
        return psutil.Process(pid.value).name().lower().removesuffix('.exe')
    except Exception:
        return ''


def close_app(procs):
    import psutil
    for p in procs:
        try:p.terminate()
        except psutil.Error:pass
    _, alive = psutil.wait_procs(procs, timeout=10)
    for p in alive:
        try:p.kill()
        except psutil.Error:pass


class NightGuard:
    def __init__(self, state, sample, *, apps=user_apps, close=close_app, clock=lambda: datetime.now(timezone.utc),
                 idle=idle_seconds, foreground=foreground_app):
        self.state = Path(state);self.sample, self.apps, self.close, self.clock = sample, apps, close, clock
        self.idle, self.foreground = idle, foreground
        self.streak = 0;self.last_close = None

    def settings(self):
        path = self.state / 'overnight-trading.json'
        try:saved = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = dict(DEFAULTS)
            from .reliability import atomic_json
            atomic_json(path, saved)
        return {**DEFAULTS, **saved}

    def in_window(self, s, now):
        local = now.astimezone(NY).time()
        start, end = time.fromisoformat(s['start']), time.fromisoformat(s['end'])
        return start <= local < end if start < end else local >= start or local < end

    def check(self):
        s = self.settings();now = self.clock();pressure = self.sample()
        log = self.state / 'overnight-trading.log.jsonl'
        # Only while the user is away: the clock alone closed apps as they were being opened on the morning of 2026-11-02.
        if not s['enabled'] or not self.in_window(s, now) or not pressure or self.idle() < AWAY_SECONDS:
            self.streak = 0;return None
        self.streak = self.streak + 1 if pressure[0] >= s['ram_percent'] else 0
        # Two readings in a row (about 30 s) and a minute between closes, so a brief spike closes nothing.
        if self.streak < 2 or (self.last_close and (now - self.last_close).total_seconds() < 60):
            return None
        groups = self.apps()
        groups.pop(self.foreground(), None)  # never the app on screen
        if not groups:
            return None
        name, (size, procs) = max(groups.items(), key=lambda kv: kv[1][0])
        self.close(procs);self.last_close = now;self.streak = 0
        entry = {'at': now.isoformat(), 'closed': name, 'freed_mb': round(size / 1024 ** 2), 'ram_percent': pressure[0]}
        with log.open('a', encoding='utf-8') as f:f.write(json.dumps(entry) + '\n')
        return entry


def morning_summary(state, since):
    """Apps the guard closed since a moment, for the user's morning check."""
    path = Path(state) / 'overnight-trading.log.jsonl'
    try:rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    except (OSError, ValueError):return []
    return [r for r in rows if datetime.fromisoformat(r['at']) >= since]
