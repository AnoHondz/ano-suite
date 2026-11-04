"""Direct, local startup controls; never bulk-disable unrelated Windows components."""
import os
from pathlib import Path

from .. import autostart
from ..tools import ToolError


def registered_entries():
    import winreg
    entries = set()
    for hive, scope in ((winreg.HKEY_CURRENT_USER, 'your account'), (winreg.HKEY_LOCAL_MACHINE, 'all users')):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Run', 0,
                                    winreg.KEY_READ | view) as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        name, _, _ = winreg.EnumValue(key, index)
                        entries.add(f'{name} ({scope})')
            except FileNotFoundError:
                pass
    for variable, scope in (('APPDATA', 'your Startup folder'), ('PROGRAMDATA', 'shared Startup folder')):
        if os.environ.get(variable):
            folder = Path(os.environ[variable]) / 'Microsoft/Windows/Start Menu/Programs/Startup'
            if folder.is_dir():
                entries.update(f'{path.stem} ({scope})' for path in folder.iterdir() if path.suffix.lower() == '.lnk')
    return sorted(entries, key=str.casefold)


class Startup:
    def __init__(self, entries=registered_entries, enable=autostart.enable, disable=autostart.disable,
                 open_path=getattr(os, 'startfile', None)):
        self.entries, self.enable, self.disable, self.open_path = entries, enable, disable, open_path

    def definitions(self):
        return [
            {'name': 'list_startup_apps', 'description': 'List registered Windows sign-in apps locally. Registration alone does not prove enabled status. No changes.',
             'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
            {'name': 'set_ano_autostart', 'description': 'Enable or disable only Ano at Windows sign-in when the user asks. Does not change other apps or services.',
             'input_schema': {'type': 'object', 'properties': {'enabled': {'type': 'boolean'}}, 'required': ['enabled'], 'additionalProperties': False}},
            {'name': 'open_startup_settings', 'description': 'Open Windows Startup Apps so the user can choose which other optional apps start. Does not disable anything itself. Never claim other apps were disabled.',
             'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]

    def run(self, name, arguments):
        return getattr(self, name)(**arguments)

    def list_startup_apps(self):
        names = self.entries()
        return (f'Registered sign-in apps ({len(names)} entries; enabled status must be checked in Windows Startup Apps):\n' +
                '\n'.join(names) if names else 'No Run-key or Startup-folder entries found.'), ''

    def set_ano_autostart(self, enabled):
        if type(enabled) is not bool:
            raise ToolError('Give enabled as true or false.')
        (self.enable if enabled else self.disable)()
        return ('Ano will start at Windows sign-in.' if enabled else 'Ano will start only when opened.') + \
               ' Other applications were not changed.', ''

    def open_startup_settings(self):
        if self.open_path is None:
            raise ToolError('Windows Startup Apps settings are unavailable.')
        self.open_path('ms-settings:startupapps')
        return ('Opened Windows Startup Apps. Choose the other optional apps to turn off there. '
                'Opening this page did not change their startup settings.'), ''
