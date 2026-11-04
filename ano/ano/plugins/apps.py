"""Open installed apps by their Start menu name. No arguments are ever passed to an app."""
import json
import os
import subprocess

from ..tools import ToolError

CREATE_NO_WINDOW = 0x08000000


def start_apps():
    output = subprocess.run(
        ['powershell', '-NoProfile', '-Command', 'Get-StartApps | ConvertTo-Json -Compress'],
        capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW).stdout
    apps = json.loads(output or '[]')
    return [apps] if isinstance(apps, dict) else apps


class Apps:
    def __init__(self, listing=start_apps, launch=getattr(os, 'startfile', None)):
        self.listing = listing
        self.launch = launch
        self._apps = None

    def definitions(self):
        return [{'name': 'open_app',
                 'description': 'Open an installed Windows app by its Start menu name, e.g. "Spotify", "Word", '
                                '"Calculator". If several apps match, the names are returned to choose from.',
                 'input_schema': {'type': 'object', 'additionalProperties': False,
                                  'properties': {'name': {'type': 'string'}}, 'required': ['name']}}]

    def run(self, name, arguments):
        return self.open_app(**arguments)

    def open_app(self, name):
        wanted = name.casefold().strip()
        if not wanted:
            raise ToolError('Name an app to open.')
        apps = [app for app in self._list() if app.get('AppID') and app.get('Name')
                and app['Name'] != 'Ano' and 'uninstall' not in app['Name'].casefold()]
        exact = [app for app in apps if app['Name'].casefold() == wanted]
        if not exact and wanted in ('microsoft word', 'ms word', 'microsoft excel', 'microsoft powerpoint', 'microsoft outlook'):
            wanted = wanted.split(' ', 1)[1]
            exact = [app for app in apps if app['Name'].casefold() == wanted]
        words = wanted.split()
        matches = exact or [app for app in apps if all(word in app['Name'].casefold() for word in words)]
        if not matches:
            raise ToolError(f'No installed app is called “{name}”.')
        if len(matches) > 1:
            starts = [app for app in matches if app['Name'].casefold().startswith(wanted)]
            if len(starts) != 1:
                names = ', '.join(sorted({app['Name'] for app in matches})[:15])
                raise ToolError(f'Several apps match: {names}. Ask which one, or call again with the exact name.')
            matches = starts
        app = matches[0]
        self.launch('shell:AppsFolder\\' + app['AppID'])
        return f'Opened {app["Name"]}.', f'Opened {app["Name"]}'

    def _list(self):
        if self._apps is None:
            try:
                self._apps = self.listing()
            except (OSError, ValueError, subprocess.SubprocessError):
                raise ToolError('Could not read the list of installed apps.') from None
        return self._apps
