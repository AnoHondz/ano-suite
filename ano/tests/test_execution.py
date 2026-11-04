from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from ano.execution import RunBudget
from ano.claude import ClaudeClient, ClaudeError
from ano.gpt import GPTClient


class ExecutionTests(TestCase):
    def test_claude_timeout_after_action_preserves_uncertainty_without_retry(self):
        import httpx
        from anthropic import APITimeoutError
        toolbox = self.toolbox(False)
        toolbox.run.return_value = ('Action dispatched', '', False)
        with patch('ano.claude.Anthropic') as factory:
            api = factory.return_value.__enter__.return_value
            api.beta.messages.create.side_effect = [SimpleNamespace(content=[SimpleNamespace(
                type='tool_use', id='write', name='desktop_input', input={})], stop_reason='tool_use'),
                APITimeoutError(request=httpx.Request('POST', 'https://example.org'))]
            with self.assertRaises(ClaudeError) as caught:
                ClaudeClient('fixture', toolbox=toolbox).reply([{'role': 'user', 'body': 'Do it'}])
        self.assertIn('Inspect the current state', str(caught.exception))
        self.assertEqual(toolbox.run.call_count, 1)
        self.assertEqual(api.beta.messages.create.call_count, 2)

    def test_uncertain_input_needs_fresh_inspection_not_unrelated_success(self):
        from ano.tool_media import ToolImage
        budget = RunBudget(self.toolbox(False))
        budget.record('desktop_input', {}, False,
                      ToolImage('{"text_verified":false,"private":"secret"}', b'fixture'))
        budget.record('list_windows', {}, False, 'ok')
        self.assertIn('desktop_input', budget.continuation())
        self.assertNotIn('secret', budget.continuation())
        budget.record('desktop_input', {}, False, 'Action performed.')
        self.assertIn('desktop_input', budget.continuation())
        budget.record('desktop_read', {}, False, 'current result')
        self.assertEqual(budget.continuation(), '')

    def test_failed_tool_remains_unresolved_until_successful_retry(self):
        budget = RunBudget(self.toolbox(False))
        budget.record('read_file', {}, True, 'private error')
        budget.record('list_folder', {}, False)
        self.assertIn('read_file', budget.continuation())
        self.assertNotIn('private error', budget.continuation())
        budget.record('read_file', {}, False)
        self.assertEqual(budget.continuation(), '')

    def test_unfinished_reply_names_the_actual_blocker_for_the_user(self):
        budget = RunBudget(self.toolbox(False))
        budget.record('desktop_read', {}, True, 'Computer control is off, paused, or locked. Enable it from Ano’s menu to begin.')
        text = budget.unfinished()
        self.assertIn('not verified as complete', text)
        self.assertIn('desktop_read: Computer control is off', text)
        self.assertNotIn('Unresolved execution results', text)
        self.assertNotIn('Computer control is off', budget.continuation())
        budget.record('desktop_read', {}, False, 'current result')
        self.assertEqual(budget.continuation(), '')

    def test_providers_inspect_uncertain_typing_before_finishing(self):
        for provider in ('gpt', 'claude'):
            with self.subTest(provider=provider):
                toolbox = self.toolbox(False)
                toolbox.run.side_effect = [('{"text_verified":false}', '', False),
                                           ('Requested result visible', '', False)]
                def gpt_reply(name=None, text='Done'):
                    calls = ([SimpleNamespace(id=name, function=SimpleNamespace(name=name, arguments='{}'))]
                             if name else None)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=text, tool_calls=calls), finish_reason='stop')])
                def claude_reply(name=None, text='Done'):
                    return SimpleNamespace(content=[SimpleNamespace(type='tool_use', id=name, name=name, input={})]
                                           if name else [SimpleNamespace(type='text', text=text, citations=None)],
                                           stop_reason='tool_use' if name else 'end_turn')
                if provider == 'gpt':
                    api = Mock()
                    api.chat.completions.create.side_effect = [gpt_reply('desktop_input'), gpt_reply(),
                                                               gpt_reply('desktop_read'), gpt_reply()]
                    result = GPTClient('fixture', toolbox, connect=lambda: api).reply(
                        [{'role': 'user', 'body': 'Complete fixture'}])
                    self.assertEqual(api.chat.completions.create.call_count, 4)
                else:
                    with patch('ano.claude.Anthropic') as factory:
                        api = factory.return_value.__enter__.return_value
                        api.beta.messages.create.side_effect = [claude_reply('desktop_input'), claude_reply(),
                                                               claude_reply('desktop_read'), claude_reply()]
                        result = ClaudeClient('fixture', toolbox=toolbox).reply(
                            [{'role': 'user', 'body': 'Complete fixture'}])
                        self.assertEqual(api.beta.messages.create.call_count, 4)
                self.assertEqual(result, 'Done')
                self.assertEqual([call.args[0] for call in toolbox.run.call_args_list],
                                 ['desktop_input', 'desktop_read'])

    def test_stubborn_provider_is_bounded_and_cannot_claim_completion(self):
        toolbox = self.toolbox(False)
        toolbox.run.return_value = ('{"text_verified":false}', '', False)
        api = Mock()
        first = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[
            SimpleNamespace(id='input1', function=SimpleNamespace(name='desktop_input', arguments='{}'))]),
            finish_reason='tool_calls')])
        done = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='All done', tool_calls=None),
                                                        finish_reason='stop')])
        api.chat.completions.create.side_effect = [first, done, done, done]
        text = GPTClient('fixture', toolbox, connect=lambda: api).reply([{'role':'user','body':'Complete fixture'}])
        self.assertIn('not verified as complete', text)
        self.assertNotIn('All done', text)
        self.assertEqual(toolbox.run.call_count, 1)
        self.assertEqual(api.chat.completions.create.call_count, 4)

    def toolbox(self, roam=True):
        toolbox=Mock()
        toolbox.free_roam.return_value=roam
        toolbox.allowed.return_value=True
        toolbox.folders.return_value=[]
        toolbox.definitions.return_value=[]
        toolbox.run.return_value=('ok','',False)
        return toolbox

    def test_free_roam_continues_past_normal_limit_but_stop_revokes_it(self):
        toolbox=self.toolbox()
        budget=RunBudget(toolbox)
        for _ in range(1000): self.assertTrue(budget.next())
        toolbox.free_roam.return_value=False
        self.assertFalse(budget.can_act())
        self.assertFalse(budget.next())
        self.assertIn('Stopped',budget.reason)

    def test_normal_budget_and_repeated_failures_remain_bounded(self):
        budget=RunBudget(self.toolbox(False),3)
        self.assertTrue(all(budget.next() for _ in range(3)))
        self.assertFalse(budget.next())
        budget=RunBudget(self.toolbox())
        for _ in range(3): budget.record('read',{'path':'fixture'},True)
        self.assertFalse(budget.next())
        self.assertIn('repeated tool failures',budget.reason)

    def test_both_providers_finish_more_than_24_steps_without_live_api_calls(self):
        toolbox=self.toolbox()
        api=Mock()
        calls=[SimpleNamespace(id=f't{i}',function=SimpleNamespace(name='fixture',arguments='{}')) for i in range(27)]
        api.chat.completions.create.side_effect=[SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=None,tool_calls=[call]),finish_reason='tool_calls')]) for call in calls]+[
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Done',tool_calls=None),finish_reason='stop')])]
        self.assertEqual(GPTClient('fixture',toolbox,connect=lambda:api).reply([{'role':'user','body':'Complete the fixture'}]),'Done')
        self.assertEqual(toolbox.run.call_count,27)
        toolbox.run.reset_mock()
        with patch('ano.claude.Anthropic') as factory:
            client=factory.return_value.__enter__.return_value
            client.beta.messages.create.side_effect=[SimpleNamespace(content=[SimpleNamespace(type='tool_use',name='fixture',input={},id=f't{i}')],stop_reason='tool_use') for i in range(27)]+[
                SimpleNamespace(content=[SimpleNamespace(type='text',text='Done',citations=None)],stop_reason='end_turn')]
            self.assertEqual(ClaudeClient('fixture',toolbox=toolbox).reply([{'role':'user','body':'Complete the fixture'}]),'Done')
        self.assertEqual(toolbox.run.call_count,27)

    def test_paused_turn_never_calls_provider(self):
        toolbox=self.toolbox()
        toolbox.allowed.return_value=False
        api=Mock()
        with self.assertRaises(ClaudeError):
            GPTClient('fixture',toolbox,connect=lambda:api).reply([{'role':'user','body':'Test'}])
        api.chat.completions.create.assert_not_called()
