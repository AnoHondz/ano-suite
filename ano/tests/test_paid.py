from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from ano.claude import ClaudeError
from ano.paid import GuardedAPI, SpendLedger, PaidTasks, MODEL, TOTAL_USD, DAILY_USD, TASK_USD, MAX_CALLS


class PaidTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = SpendLedger(Path(self.tmp.name) / 'spend.sqlite3')
        self.client = Mock()
        self.client.chat.completions.create.return_value = NS(usage=NS(prompt_tokens=100, completion_tokens=10),
            choices=[NS(message=NS(content='Verified answer', tool_calls=None), finish_reason='stop')])

    def test_no_key_load_or_client_on_normal_message_and_usage_report(self):
        local, key, factory = Mock(), Mock(), Mock()
        router = PaidTasks(local, Mock(), self.ledger, key_loader=key, client_factory=factory)
        history = [{'role': 'user', 'body': 'log in to Canvas'}]
        router.reply(history)
        local.reply.assert_called_once_with(history)
        report = router.reply([{'role': 'user', 'body': 'paid usage'}])
        self.assertIn('$0.00000', report)
        key.assert_not_called()
        factory.assert_not_called()

    def test_opt_in_is_one_turn_fixed_model_endpoint_and_no_retries(self):
        local, factory, toolbox = Mock(), Mock(return_value=self.client), Mock()
        toolbox.definitions.return_value = []
        router = PaidTasks(local, toolbox, self.ledger, key_loader=lambda: 'secret', client_factory=factory)
        history = [{'role': 'user', 'body': 'paid: Give a brief answer'}]
        result = router.reply(history)
        self.assertIn('Verified answer', result)
        self.assertEqual(history[-1]['body'], 'paid: Give a brief answer')
        factory.assert_called_once_with(api_key='secret', base_url='https://api.openai.com/v1', timeout=45, max_retries=0)
        args = self.client.chat.completions.create.call_args.kwargs
        self.assertEqual(args['model'], MODEL)
        self.assertEqual(args['reasoning_effort'], 'low')
        self.assertNotIn('parallel_tool_calls', args)  # no tools were supplied
        self.assertNotIn('temperature', args)
        self.assertNotIn('max_tokens', args)
        router.reply([{'role': 'user', 'body': 'continue'}])
        local.reply.assert_called_once()
        self.assertEqual(factory.call_count, 1)

    def test_reservations_survive_restarts_and_network_failure(self):
        guard = GuardedAPI(self.client, self.ledger)
        self.client.chat.completions.create.side_effect = RuntimeError('network')
        with self.assertRaises(RuntimeError):
            guard.create(model=MODEL, messages=[])
        self.assertEqual(self.client.chat.completions.create.call_count, 1)
        with self.ledger.connect() as db:
            self.assertGreater(db.execute('select sum(usd) from charges').fetchone()[0], 0)
        # Exhaust total across prior days, including pending reservations.
        with self.ledger.connect() as db:
            db.execute('insert into charges(day,usd) values (?,?)', ('2020-01-01', TOTAL_USD))
        fresh = GuardedAPI(self.client, SpendLedger(self.ledger.path))
        with self.assertRaisesRegex(ClaudeError, 'budget reached'):
            fresh.create(model=MODEL, messages=[])
        self.assertEqual(self.client.chat.completions.create.call_count, 1)

    def test_daily_task_step_context_and_model_limits_before_network(self):
        guard = GuardedAPI(self.client, self.ledger)
        for args in (dict(model='gpt-5.5', messages=[]),
                     dict(model=MODEL, messages=[{'role': 'user', 'content': 'x' * 700000}])):
            with self.assertRaises(ClaudeError):
                guard.create(**args)
        guard.spent = TASK_USD
        with self.assertRaisesRegex(ClaudeError, 'task estimate limit'):
            guard.create(model=MODEL, messages=[])
        guard.spent, guard.calls = 0, MAX_CALLS
        with self.assertRaisesRegex(ClaudeError, f'{MAX_CALLS} paid API calls'):
            guard.create(model=MODEL, messages=[])
        self.client.chat.completions.create.assert_not_called()
        with self.ledger.connect() as db:
            from datetime import datetime, timezone
            db.execute('insert into charges(day,usd) values (?,?)', (datetime.now(timezone.utc).date().isoformat(), DAILY_USD))
        with self.assertRaisesRegex(ClaudeError, 'budget reached'):
            GuardedAPI(self.client, self.ledger).create(model=MODEL, messages=[])

    def test_missing_key_fails_without_local_or_paid_fallback(self):
        local, factory = Mock(), Mock()
        router = PaidTasks(local, Mock(), self.ledger, key_loader=lambda: '', client_factory=factory)
        with self.assertRaisesRegex(ClaudeError, 'No OpenAI key'):
            router.reply([{'role': 'user', 'body': 'paid: hard task'}])
        factory.assert_not_called()
        local.reply.assert_not_called()

    def test_openai_mode_pays_every_message_except_local_prefix(self):
        local, factory, toolbox = Mock(), Mock(return_value=self.client), Mock()
        toolbox.definitions.return_value = []
        router = PaidTasks(local, toolbox, self.ledger, key_loader=lambda: 'secret', client_factory=factory, mode='openai')
        self.assertIn('Verified answer', router.reply([{'role': 'user', 'body': 'Give a brief answer'}]))
        self.assertTrue(router.last_paid)
        history = [{'role': 'user', 'body': 'local: hello'}]
        router.reply(history)
        self.assertEqual(local.reply.call_args.args[0][-1]['body'], 'hello')
        self.assertEqual(history[-1]['body'], 'local: hello')
        self.assertFalse(router.last_paid)
        self.assertEqual(factory.call_count, 1)

    def test_openai_mode_runs_builtin_commands_free_and_gives_model_every_tool(self):
        local, factory, toolbox = Mock(), Mock(return_value=self.client), Mock()
        local.routines.reply.side_effect = lambda text: 'Opened Calculator.' if text == 'open calculator' else None
        toolbox.definitions.return_value = [
            {'name': name, 'description': 'A tool.', 'input_schema': {'type': 'object', 'properties': {}}}
            for name in ('open_browser_tab', 'desktop_input', 'media_control')]
        router = PaidTasks(local, toolbox, self.ledger, key_loader=lambda: 'secret', client_factory=factory, mode='openai')
        self.assertEqual(router.reply([{'role': 'user', 'body': 'open calculator'}]), 'Opened Calculator.')
        factory.assert_not_called()
        router.reply([{'role': 'user', 'body': 'make a song on suno for me'}])
        tools = [tool['function']['name'] for tool in self.client.chat.completions.create.call_args.kwargs['tools']]
        self.assertEqual(tools, ['open_browser_tab', 'desktop_input', 'media_control'])

    def test_cached_prompt_tokens_are_settled_at_the_discounted_rate(self):
        self.client.chat.completions.create.return_value.usage = NS(
            prompt_tokens=1_000_000, completion_tokens=0, prompt_tokens_details=NS(cached_tokens=1_000_000))
        guard = GuardedAPI(self.client, self.ledger)
        guard.create(model=MODEL, messages=[])
        self.assertAlmostEqual(guard.spent, .005)

    def test_reasoning_model_uses_low_effort_and_unknown_model_falls_back(self):
        nano = 'gpt-5-nano-2025-08-07'
        GuardedAPI(self.client, self.ledger, nano).create(model=nano, messages=[])
        self.assertEqual(self.client.chat.completions.create.call_args.kwargs['reasoning_effort'], 'low')
        self.assertEqual(PaidTasks(Mock(), Mock(), self.ledger, model='gpt-4').model, MODEL)
        self.assertEqual(PaidTasks(Mock(), Mock(), self.ledger, mode='auto').mode, 'local')


if __name__ == '__main__':
    unittest.main()
