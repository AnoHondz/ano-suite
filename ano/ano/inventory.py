"""What is on this PC, for Settings → Apps & plugins. Read-only and local; names only.

Start-menu apps come from the shortcut names in the two Start Menu folders, running apps from the
visible top-level windows' processes, and browser extensions from each Chrome/Brave/Edge profile's
manifest files (the extension's name, never its data). Nothing is opened, changed or sent anywhere.
"""
import json
import os
from pathlib import Path
import re

BROWSERS = {'Chrome': Path('Google/Chrome/User Data'), 'Brave': Path('BraveSoftware/Brave-Browser/User Data'),
            'Edge': Path('Microsoft/Edge/User Data')}
# Shortcuts that are not apps a person would pick: uninstallers, help files, readmes.
NOT_APPS = re.compile(r'uninstall|readme|help|manual|documentation|release notes|license|website|\bfaq\b', re.I)
# Extensions every browser installs for itself.
BUILT_IN_EXTENSIONS = re.compile(r'^(?:chrome web store payments|google docs offline|web store|edge relevant text changes|'
                                 r'microsoft clipboard extension|microsoft voices|cryptotoken|.*\bhangouts\b.*)$', re.I)


def start_menu_apps():
    folders = [Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData')) / 'Microsoft/Windows/Start Menu/Programs',
               Path(os.environ.get('APPDATA', '')) / 'Microsoft/Windows/Start Menu/Programs']
    names = set()
    for folder in folders:
        if folder.is_dir():
            for shortcut in folder.rglob('*.lnk'):
                names.add(shortcut.stem)
    # Store apps (WhatsApp, Spotify from the Store, …) have no shortcut file; Windows lists them here.
    import subprocess
    try:
        listed = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 '[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ForEach-Object Name'],
                                capture_output=True, text=True, encoding='utf-8', timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        names.update(line.strip() for line in listed.splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted((name for name in names if not NOT_APPS.search(name)), key=str.lower)


def running_apps():
    """Apps with a visible window (not background services), by process name."""
    import ctypes
    from ctypes import wintypes
    import psutil
    user32, pids = ctypes.windll.user32, set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(window, _):
        if user32.IsWindowVisible(window) and user32.GetWindowTextLengthW(window) and not user32.GetWindow(window, 4):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
            pids.add(pid.value)
        return True
    user32.EnumWindows(visit, 0)
    names = set()
    for pid in pids:
        try:
            name = psutil.Process(pid).name().removesuffix('.exe').removesuffix('.EXE')
        except psutil.Error:
            continue
        if name.lower() not in ('explorer', 'textinputhost', 'applicationframehost', 'systemsettings', 'shellexperiencehost',
                                'python', 'pythonw'):  # python: Ano itself
            names.add(name)
    return sorted(names, key=str.lower)


def _extension_name(version_folder):
    manifest = json.loads((version_folder / 'manifest.json').read_text(encoding='utf-8-sig'))
    name = manifest.get('name', '')
    if match := re.fullmatch(r'__MSG_(\w+)__', name):
        locale = manifest.get('default_locale', 'en')
        messages = json.loads((version_folder / '_locales' / locale / 'messages.json').read_text(encoding='utf-8-sig'))
        found = {key.lower(): value for key, value in messages.items()}.get(match[1].lower(), {})
        name = found.get('message', '')
    return ' '.join(name.split())[:80]


def browser_extensions(local=None):
    """[{browser, name}] for every installed extension, in every profile, without duplicates."""
    local = Path(local or os.environ.get('LOCALAPPDATA', ''))
    found = set()
    for browser, relative in BROWSERS.items():
        root = local / relative
        if not root.is_dir():
            continue
        for extensions in root.glob('*/Extensions'):
            for extension in extensions.iterdir():
                versions = sorted((v for v in extension.iterdir() if (v / 'manifest.json').is_file()), reverse=True) \
                    if extension.is_dir() else []
                try:
                    name = _extension_name(versions[0]) if versions else ''
                except (OSError, ValueError, KeyError, AttributeError):
                    name = ''
                if name and not BUILT_IN_EXTENSIONS.match(name):
                    found.add((browser, name))
    return [dict(browser=browser, name=name) for browser, name in sorted(found, key=lambda item: (item[0], item[1].lower()))]


def ano_plugins():
    folder = Path(__file__).with_name('plugins')
    names = [path.stem.replace('_', ' ').capitalize() for path in folder.glob('*.py') if not path.stem.startswith('_')]
    return sorted(names)


def inventory():
    result = {}
    for key, read in (('apps', start_menu_apps), ('running', running_apps), ('extensions', browser_extensions),
                      ('plugins', ano_plugins)):
        try:
            result[key] = read()
        except Exception:
            result[key] = []
    return result
