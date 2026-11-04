import unittest
from unittest.mock import patch

from ano import configure
from ano.gpt import Brains


class LocalOnlySetupTests(unittest.TestCase):
    def test_removed_cloud_setup_commands_cannot_change_configuration(self):
        for command in ('cloud', 'claude'):
            with self.subTest(command=command), patch('sys.argv', ['ano.configure', command]), \
                    patch('ano.configure.local') as local:
                with self.assertRaisesRegex(SystemExit, 'Cloud AI brains have been removed'):
                    configure.main()
                local.assert_not_called()

    def test_openai_setup_saves_key_without_changing_default_or_calling_api(self):
        with patch('sys.argv', ['ano.configure', 'openai']), \
                patch('ano.configure.getpass', return_value='test-secret'), \
                patch('ano.credentials.save_openai_key') as save, \
                patch('ano.configure.local') as local:
            configure.main()
        save.assert_called_once_with('test-secret')
        local.assert_not_called()

    def test_setup_defaults_to_local_brain(self):
        with patch('sys.argv', ['ano.configure']), patch('ano.configure.local') as local:
            configure.main()
        local.assert_called_once_with()

    def test_local_only_selector_has_no_paid_choices_or_fallback(self):
        from unittest.mock import Mock
        from ano.claude import ClaudeError
        local = Mock()
        local.reply.side_effect = ClaudeError('Local server is offline')
        brains = Brains(local=local, mode='local')
        self.assertEqual(brains.modes, ['local'])
        self.assertIsNone(brains.claude)
        self.assertIsNone(brains.gpt)
        with self.assertRaisesRegex(ClaudeError, 'Local server is offline'):
            brains.reply([{'role': 'user', 'body': 'Hello'}])
        local.reply.assert_called_once()
