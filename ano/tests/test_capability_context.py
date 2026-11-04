import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

from ano.capability_context import integration_context
from ano.claude import build_system_stable
from ano.gpt import GPTClient


class CapabilityContextTests(unittest.TestCase):
    def test_no_mail_tools_reports_disconnected_even_with_browser(self):
        text = integration_context([{'name': 'browser_read'}])
        self.assertIn('Gmail API/IMAP integration is not connected', text)
        self.assertIn('authorized browser workflow', text)
        self.assertIn('Never request', text)

    def test_available_tools_are_not_proof_of_authenticated_access(self):
        text = integration_context([{'name': 'read_email'}, {'name': 'search_email'}])
        self.assertIn('read_email, search_email', text)
        self.assertIn('does not prove', text)
        self.assertNotIn('is not connected', text)

    def test_claude_system_uses_current_advertised_tools(self):
        toolbox = Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = []
        self.assertIn('Gmail API/IMAP integration is not connected', build_system_stable(toolbox))
        toolbox.definitions.return_value = [{'name': 'search_email'}]
        self.assertIn('Gmail tools available: search_email', build_system_stable(toolbox))

    def test_paid_and_local_agent_requests_include_status_without_an_extra_call(self):
        for profile in ('paid', 'fast', 'full'):
            with self.subTest(profile=profile):
                api = Mock()
                api.chat.completions.create.return_value = NS(choices=[NS(
                    message=NS(content='Gmail is not connected.', tool_calls=None), finish_reason='stop')])
                client = GPTClient('fixture', connect=lambda: api, profile=profile)
                client.reply([{'role': 'user', 'body': 'Hello'}])
                self.assertEqual(api.chat.completions.create.call_count, 1)
                system = api.chat.completions.create.call_args.kwargs['messages'][0]['content']
                self.assertIn('Gmail API/IMAP integration is not connected', system)


if __name__ == '__main__':
    unittest.main()
