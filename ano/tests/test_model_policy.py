import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ano.claude import ClaudeError
from ano.gpt import Brains, GPTClient
from ano.memory import Memory
from ano.model_policy import ModelPolicy, is_loopback_url
from ano.local_runtime import configure_local, local_writer_config


class ModelPolicyTests(unittest.TestCase):
    def test_explicit_model_selection_clears_stale_agent_and_chat_overrides(self):
        self.memory.set_setting('local_agent_model', 'ano-agent:latest')
        self.memory.set_setting('local_chat_model', 'ano-chat:latest')
        with patch('ano.local_runtime.installed_models', return_value=['candidate:latest']), patch(
                'ano.local_runtime.ollama_json', return_value={'capabilities': ['tools']}):
            configure_local('candidate:latest', self.memory)
        self.assertEqual(self.memory.setting('local_model', ''), 'candidate:latest')
        self.assertEqual(self.memory.setting('local_agent_model', 'stale'), '')
        self.assertEqual(self.memory.setting('local_chat_model', 'stale'), '')
    def test_remote_chat_can_keep_drafting_on_the_installed_local_model(self):
        with patch('ano.local_runtime.installed_models', return_value=['ano-local:latest']):
            self.assertEqual(local_writer_config('remote-model', 'https://provider.example/v1'),
                             ('ano-local:latest', 'http://127.0.0.1:11434/v1'))
        with patch('ano.local_runtime.installed_models', return_value=[]):
            self.assertIsNone(local_writer_config('remote-model', 'https://provider.example/v1'))
        with patch('ano.local_runtime.installed_models', side_effect=OSError('offline')):
            self.assertIsNone(local_writer_config('remote-model', 'https://provider.example/v1'))

    def test_local_writer_reuses_a_downloaded_model_endpoint(self):
        self.assertEqual(local_writer_config('ano-local:latest', 'http://127.0.0.1:11434/v1'),
                         ('ano-local:latest', 'http://127.0.0.1:11434/v1'))
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = Memory(Path(self.tmp.name) / 'test.sqlite3')

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def test_legacy_flags_cannot_reenable_paid_brains(self):
        self.memory.set_setting('local_only', False)
        self.memory.set_setting('free_only', False)
        policy = ModelPolicy.load(self.memory, {'ANO_FREE_ONLY': '0', 'ANO_LOCAL_ONLY': '0'})
        self.assertTrue(policy.free_only)
        self.assertTrue(policy.local_only)
        self.assertEqual(policy.model, 'ano-local:latest')

    def test_local_only_disables_paid_providers_and_uses_saved_model(self):
        self.memory.set_setting('local_model', 'qwen3:4b')
        self.memory.set_setting('local_only', True)
        policy = ModelPolicy.load(self.memory, {})
        self.assertTrue(policy.free_only)
        self.assertEqual(policy.output_tokens, 512)
        self.assertEqual(policy.label, 'Local (qwen3:4b)')

    def test_local_only_rejects_remote_and_cloud_models(self):
        for env in [
            {'ANO_LOCAL_URL': 'https://example.com/v1', 'ANO_LOCAL_MODEL': 'qwen3:4b'},
            {'ANO_LOCAL_MODEL': 'qwen3:cloud'},
            {'ANO_LOCAL_MODEL': ''},
        ]:
            with self.subTest(env=env), self.assertRaises(ValueError):
                ModelPolicy.load(self.memory, {'ANO_LOCAL_ONLY': 'true', **env})

    def test_loopback_validation(self):
        for url in ['http://localhost:11434/v1', 'http://127.0.0.1:11434/v1', 'http://[::1]:11434/v1']:
            self.assertTrue(is_loopback_url(url))
        for url in ['http://localhost.evil/v1', 'https://localhost@evil.test/v1',
                    'http://localhost:bad/v1', 'http://localhost/v1?proxy=remote', 'file:///v1']:
            self.assertFalse(is_loopback_url(url))

    def test_remote_endpoint_is_blocked_even_without_local_only_flag(self):
        with self.assertRaisesRegex(ValueError, 'localhost or loopback'):
            ModelPolicy.load(self.memory, {'ANO_LOCAL_URL': 'https://example.com/v1', 'ANO_LOCAL_MODEL': 'test'})

    def test_local_output_limit_is_bounded(self):
        for raw, expected in [('bad', 512), ('0', 256), ('999999', 16000)]:
            self.assertEqual(ModelPolicy.load(self.memory, {'ANO_LOCAL_OUTPUT_TOKENS': raw}).output_tokens, expected)

    @patch('ano.local_runtime.ollama_json')
    def test_configure_requires_installed_tool_capable_model(self, request):
        request.side_effect = [{'models': [{'name': 'qwen3:4b'}]}, {'capabilities': ['completion', 'tools']}]
        self.assertIn('tools', configure_local('qwen3:4b', self.memory))
        self.assertTrue(self.memory.setting('local_only', False))
        self.assertEqual(self.memory.setting('brain', ''), 'local')
        self.assertEqual(request.call_args_list[1].args, ('/api/show', {'model': 'qwen3:4b'}))

    @patch('ano.local_runtime.ollama_json')
    def test_failed_configuration_does_not_switch_providers(self, request):
        request.return_value = {'models': []}
        with self.assertRaises(ValueError):
            configure_local('qwen3:4b', self.memory)
        self.assertFalse(self.memory.setting('local_only', False))


class LocalExecutionTests(unittest.TestCase):
    def test_economy_never_replays_local_actions_through_paid_fallback(self):
        local, paid = Mock(), Mock()
        local.toolbox = SimpleNamespace(executions=0)
        def fail(*args, **kwargs):
            local.toolbox.executions += 1
            raise ClaudeError('Stopped after acting')
        local.reply.side_effect = fail
        with self.assertRaisesRegex(ClaudeError, 'Paid fallback was skipped'):
            Brains(claude=paid, local=local, mode='economy').reply([{'role': 'user', 'body': 'hello'}])
        paid.reply.assert_not_called()

    def test_local_output_budget_uses_compatible_parameter(self):
        api = Mock()
        api.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='Hello', tool_calls=None), finish_reason='stop')])
        GPTClient('local', model='qwen3:4b', base_url='http://localhost:11434/v1',
                  output_tokens=1024, connect=lambda: api).reply([{'role': 'user', 'body': 'hello'}])
        args = api.chat.completions.create.call_args.kwargs
        self.assertEqual(args['max_tokens'], 1024)
        self.assertNotIn('max_completion_tokens', args)
