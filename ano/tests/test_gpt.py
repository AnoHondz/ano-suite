from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from ano.claude import ClaudeError
from ano.gpt import Brains, GPTClient


class BrainsTests(unittest.TestCase):
    def test_no_fallback_replays_after_a_tool_has_run(self):
        claude, gpt = Mock(), Mock()
        claude.toolbox = SimpleNamespace(executions=0)
        def failed(history, **kwargs):
            claude.toolbox.executions += 1
            raise ClaudeError('Failed after an action')
        claude.reply.side_effect = failed
        with self.assertRaises(ClaudeError):
            Brains(claude, gpt).reply([])
        gpt.reply.assert_not_called()
    def setUp(self):
        self.claude = Mock()
        self.gpt = Mock()
        self.gpt.name = 'GPT-5.5'
        self.gpt.reply.return_value = 'From GPT'

    def test_auto_falls_back_to_gpt_and_says_so(self):
        self.claude.reply.side_effect = ClaudeError('Claude timed out.')
        brains = Brains(self.claude, self.gpt)
        self.assertEqual(brains.modes, ['auto', 'claude', 'gpt'])
        reply = brains.reply([{'role': 'user', 'body': 'Hi'}], screenshot=b'image')
        self.assertTrue(reply.startswith('From GPT'))
        self.assertIn('because Claude was unavailable: Claude timed out.', reply)
        self.assertEqual(self.gpt.reply.call_args.kwargs, {'screenshot': b'image'})

    def test_claude_only_mode_does_not_fall_back(self):
        self.claude.reply.side_effect = ClaudeError('Claude timed out.')
        with self.assertRaises(ClaudeError):
            Brains(self.claude, self.gpt, mode='claude').reply([])
        self.gpt.reply.assert_not_called()

    def test_single_brain_and_gpt_mode(self):
        self.assertEqual(Brains(None, self.gpt).modes, ['gpt'])
        self.assertEqual(Brains(self.claude, self.gpt, mode='gpt').reply([]), 'From GPT')
        self.claude.reply.assert_not_called()

    def test_economy_routes_simple_to_free_and_hard_to_claude(self):
        local = Mock(); local.name = 'NVIDIA'; local.reply.return_value = 'From free brain'
        self.claude.reply.return_value = 'From Claude'
        brains = Brains(self.claude, self.gpt, local, mode='economy')
        self.assertIn('economy', brains.modes)
        # Simple everyday ask → free brain.
        self.assertEqual(brains.reply([{'role': 'user', 'body': 'tell me a joke'}]), 'From free brain')
        self.claude.reply.assert_not_called()
        # Web / screenshot / complex → Claude.
        self.assertEqual(brains.reply([{'role': 'user', 'body': 'search the news'}]), 'From Claude')
        self.assertEqual(brains.reply([{'role': 'user', 'body': 'hi'}], screenshot=b'img'), 'From Claude')
        self.assertEqual(brains.reply([{'role': 'user', 'body': 'fill in the Therap form'}]), 'From Claude')

    def test_economy_falls_back_to_claude_if_free_brain_fails(self):
        local = Mock(); local.name = 'NVIDIA'; local.reply.side_effect = ClaudeError('free brain down')
        self.claude.reply.return_value = 'From Claude'
        reply = Brains(self.claude, self.gpt, local, mode='economy').reply([{'role': 'user', 'body': 'tell me a joke'}])
        self.assertTrue(reply.startswith('From Claude'))
        self.assertIn('free brain failed', reply)

    def test_local_brain_is_offered_and_used(self):
        local = Mock()
        local.name = 'Local (qwen2.5:7b)'
        local.reply.return_value = 'From local model'
        brains = Brains(self.claude, self.gpt, local)
        self.assertEqual(brains.modes, ['economy', 'auto', 'claude', 'gpt', 'local'])
        self.assertEqual(Brains(self.claude, self.gpt, local, mode='local').reply([]), 'From local model')
        # A local-only setup (no Claude, no GPT) answers with the local model.
        self.assertEqual(Brains(local=local).modes, ['local'])
        self.assertEqual(Brains(local=local).reply([]), 'From local model')

    def test_local_client_points_at_the_local_server(self):
        from types import SimpleNamespace as NS
        done = NS(message=NS(content='hi from local', tool_calls=None), finish_reason='stop')
        api = Mock()
        api.chat.completions.create.return_value = NS(choices=[done])
        client = GPTClient('local', toolbox=None, model='qwen2.5:7b',
                           base_url='http://localhost:11434/v1', name='Local (qwen2.5:7b)',
                           connect=lambda: api)
        self.assertEqual(client.name, 'Local (qwen2.5:7b)')
        self.assertEqual(client.reply([{'role': 'user', 'body': 'hi'}]), 'hi from local')
        self.assertEqual(api.chat.completions.create.call_args.kwargs['model'], 'qwen2.5:7b')


class GPTClientTests(unittest.TestCase):
    def test_empty_responses_are_explicit_errors(self):
        for choices in ([], [SimpleNamespace(message=None)]):
            with self.subTest(choices=choices):
                api = Mock()
                api.chat.completions.create.return_value = SimpleNamespace(choices=choices)
                with self.assertRaisesRegex(ClaudeError, 'empty response'):
                    GPTClient('fixture', connect=lambda: api).reply([{'role': 'user', 'body': 'Hello'}])

    def test_unavailable_tools_are_reported_without_running_or_claiming_success(self):
        api = Mock()
        api.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='Done', tool_calls=[SimpleNamespace(
                id='missing', function=SimpleNamespace(name='invented', arguments='{}'))]),
            finish_reason='tool_calls')])
        text = GPTClient('fixture', connect=lambda: api).reply([{'role': 'user', 'body': 'Hello'}])
        self.assertIn('not verified as complete', text)
        self.assertIn('repeated tool failures', text)
        self.assertNotIn('Done', text)
        self.assertEqual(api.chat.completions.create.call_count, 3)

    def test_new_screenshot_replaces_stale_image_on_next_model_request(self):
        from ano.tool_media import ToolImage
        api = Mock()
        observed = []
        def respond(**request):
            from copy import deepcopy
            observed.append(deepcopy(request['messages']))
            count = len(observed)
            calls = [SimpleNamespace(id=f'view{count}', function=SimpleNamespace(
                name='desktop_observe', arguments='{}'))] if count < 3 else None
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='Observed', tool_calls=calls), finish_reason='stop')])
        api.chat.completions.create.side_effect = respond
        toolbox = Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = []
        toolbox.run.side_effect = [(ToolImage('Old receipt', b'old'), '', False),
                                   (ToolImage('New receipt', b'new'), '', False)]
        GPTClient('fixture', toolbox, connect=lambda: api).reply([{'role': 'user', 'body': 'Inspect'}])
        self.assertEqual(sum(b['type'] == 'image_url' for m in observed[-1]
                             if isinstance(m['content'], list) for b in m['content']), 1)
        self.assertIn('Old receipt', str(observed[-1]))

    def test_timeout_after_tool_use_requires_inspection_and_does_not_retry(self):
        from openai import APITimeoutError
        import httpx
        api = Mock()
        toolbox = Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = []
        toolbox.run.return_value = ('Action dispatched', '', False)
        first = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=None, tool_calls=[SimpleNamespace(id='write', function=SimpleNamespace(
                name='desktop_input', arguments='{}'))]), finish_reason='tool_calls')])
        api.chat.completions.create.side_effect = [first, APITimeoutError(request=httpx.Request('POST', 'http://localhost'))]
        with self.assertRaises(ClaudeError) as caught:
            GPTClient('fixture', toolbox, connect=lambda: api).reply([{'role': 'user', 'body': 'Do it'}])
        self.assertIn('Inspect the current state', str(caught.exception))
        self.assertNotIn('Please try again', str(caught.exception))
        self.assertEqual(toolbox.run.call_count, 1)
        self.assertEqual(api.chat.completions.create.call_count, 2)

    def test_active_browser_task_continues_after_an_early_final_reply(self):
        from ano.tools import Toolbox
        plugin = Mock()
        plugin.definitions.return_value = []
        plugin.continuation.side_effect = ['Still pending. Inspect and verify.', '', '']
        api = Mock()
        def answer(text):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text,tool_calls=None),finish_reason='stop')])
        api.chat.completions.create.side_effect = [answer('Done.'), answer('Verified the requested result.')]
        tools = Toolbox(lambda: [], plugins=[plugin])
        text = GPTClient('test', tools, connect=lambda: api).reply([{'role':'user','body':'Finish the website task'}])
        self.assertEqual(api.chat.completions.create.call_count, 2)
        self.assertEqual(text, 'Verified the requested result.')
        messages = api.chat.completions.create.call_args.kwargs['messages']
        self.assertIn('[Ano task tracker]', messages[-1]['content'])

    def test_repeated_early_final_reply_is_not_reported_as_verified_completion(self):
        from ano.tools import Toolbox
        plugin = Mock()
        plugin.definitions.return_value = []
        plugin.continuation.return_value = '0/1 steps verified.'
        api = Mock()
        api.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='Done.',tool_calls=None),finish_reason='stop')])
        text = GPTClient('test', Toolbox(lambda: [],plugins=[plugin]), connect=lambda: api).reply([{'role':'user','body':'Finish it'}])
        self.assertEqual(api.chat.completions.create.call_count, 3)
        self.assertIn('not verified as complete', text)
        self.assertNotIn('Done.', text)
    def test_tool_image_is_an_image_after_all_tool_results(self):
        from ano.tool_media import ToolImage
        call = SimpleNamespace(id='view1', function=SimpleNamespace(name='desktop_observe', arguments='{"window_id":1}'))
        first = SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]), finish_reason='tool_calls')
        second = SimpleNamespace(message=SimpleNamespace(content='Observed.', tool_calls=None), finish_reason='stop')
        api = Mock()
        api.chat.completions.create.side_effect = [SimpleNamespace(choices=[first]), SimpleNamespace(choices=[second])]
        toolbox = Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = []
        toolbox.run.return_value = (ToolImage('Approved view', b'jpeg'), '', False)
        GPTClient('test', toolbox, connect=lambda: api).reply([{'role': 'user', 'body': 'Look at my app'}])
        messages = api.chat.completions.create.call_args.kwargs['messages']
        self.assertEqual(messages[-2]['role'], 'tool')
        self.assertEqual(messages[-1]['content'][1]['type'], 'image_url')
        self.assertTrue(messages[-1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
    def test_tool_calls_run_and_results_return(self):
        call = SimpleNamespace(id='call-1', function=SimpleNamespace(name='open_website', arguments='{"url": "example.com"}'))
        first = SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]), finish_reason='tool_calls')
        second = SimpleNamespace(message=SimpleNamespace(content='Opened it.', tool_calls=None), finish_reason='stop')
        api = Mock()
        api.chat.completions.create.side_effect = [SimpleNamespace(choices=[first]), SimpleNamespace(choices=[second])]
        toolbox = Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = [{'name': 'open_website', 'description': 'Open', 'input_schema': {'type': 'object'}}]
        toolbox.run.return_value = ('Opened https://example.com', 'Opened https://example.com', False)
        reply = GPTClient('key', toolbox, connect=lambda: api).reply([{'role': 'user', 'body': 'Open example.com'}])
        toolbox.run.assert_called_once_with('open_website', {'url': 'example.com'})
        self.assertEqual(reply, 'Opened it.\n\n· Opened https://example.com')
        request = api.chat.completions.create.call_args.kwargs
        self.assertEqual(request['messages'][0]['role'], 'system')
        self.assertEqual(request['messages'][-1], {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'Opened https://example.com'})
        self.assertEqual(request['tools'][0]['function']['name'], 'open_website')
