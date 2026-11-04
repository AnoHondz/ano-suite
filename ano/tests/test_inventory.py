import json
from pathlib import Path
import tempfile
import unittest

from ano.inventory import ano_plugins, browser_extensions
from ano.monitor import sender_matches


def extension(root, profile, identifier, manifest, messages=None):
    folder = root / profile / 'Extensions' / identifier / '1.0.0_0'
    folder.mkdir(parents=True)
    (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    if messages:
        (folder / '_locales' / 'en').mkdir(parents=True)
        (folder / '_locales' / 'en' / 'messages.json').write_text(json.dumps(messages), encoding='utf-8')


class InventoryTests(unittest.TestCase):
    def test_extension_names_from_every_profile_without_built_ins(self):
        with tempfile.TemporaryDirectory() as local:
            chrome = Path(local) / 'Google/Chrome/User Data'
            extension(chrome, 'Default', 'aaa', {'name': 'uBlock Origin Lite'})
            extension(chrome, 'Profile 1', 'aaa', {'name': 'uBlock Origin Lite'})  # same one twice
            extension(chrome, 'Default', 'bbb', {'name': '__MSG_appName__', 'default_locale': 'en'},
                      {'APPNAME': {'message': 'Grammar Helper'}})
            extension(chrome, 'Default', 'ccc', {'name': 'Google Docs Offline'})
            extension(Path(local) / 'Microsoft/Edge/User Data', 'Default', 'ddd', {'name': 'Dark Reader'})
            (chrome / 'Default' / 'Extensions' / 'broken' / '1.0').mkdir(parents=True)  # no manifest: skipped
            self.assertEqual(browser_extensions(local), [dict(browser='Chrome', name='Grammar Helper'),
                                                         dict(browser='Chrome', name='uBlock Origin Lite'),
                                                         dict(browser='Edge', name='Dark Reader')])

    def test_plugins_are_listed_by_name(self):
        self.assertIn('Word documents', ano_plugins())

    def test_full_app_names_match_notification_senders(self):
        self.assertTrue(sender_matches('google chrome', 'Chrome'))
        self.assertTrue(sender_matches('microsoft teams', 'MSTeams_8wekyb3d8bbwe!MSTeams'))
        self.assertTrue(sender_matches('phone link', 'Microsoft.YourPhone_8wekyb3d8bbwe!App'))
        self.assertFalse(sender_matches('microsoft store', 'Microsoft.Office.OUTLOOK.EXE.15'))


if __name__ == '__main__':
    unittest.main()
