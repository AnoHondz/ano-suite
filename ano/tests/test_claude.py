import tempfile
import unittest
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, Mock

from ano.claude import ClaudeClient, ClaudeError, build_system, load_rules, SYSTEM, RULES_MAX


def system_text(system):
    """System may be a plain string or a list of cached text blocks."""
    return system if isinstance(system, str) else ''.join(b['text'] for b in system)


class RulesTests(unittest.TestCase):
    def test_load_rules_reads_caps_and_tolerates_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ano_rules.md'
            self.assertEqual(load_rules(path), '')  # missing file is fine
            path.write_text('  Always greet me as Captain.\nPrefer short answers.  ', encoding='utf-8')
            self.assertEqual(load_rules(path), 'Always greet me as Captain.\nPrefer short answers.')
            path.write_text('x' * (RULES_MAX + 500), encoding='utf-8')
            self.assertEqual(len(load_rules(path)), RULES_MAX)

    def test_rules_are_injected_after_the_safety_prompt(self):
        system = build_system(None, False, None, rules='Prefer short answers.')
        self.assertIn('Prefer short answers.', system)
        self.assertIn('STANDING INSTRUCTIONS', system)
        # Safety text still comes first; rules are framed as not overriding it.
        self.assertTrue(system.startswith(SYSTEM[:40]))
        self.assertIn('conflict with safety', system)
        # No rules -> no standing-instructions section.
        self.assertNotIn('STANDING INSTRUCTIONS', build_system(None, False, None))


class CostTests(unittest.TestCase):
    def _reply(self, factory, history, **kw):
        api = factory.return_value.__enter__.return_value
        api.beta.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type='text', text='ok')], stop_reason='end_turn',
            usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=80,
                                  cache_creation_input_tokens=0))
        ClaudeClient('test', 'main-model', **kw).reply(history)
        return api.beta.messages.create.call_args.kwargs

    @patch('ano.claude.Anthropic')
    def test_system_is_cached_block_plus_uncached_volatile(self, factory):
        toolbox = Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = []
        toolbox.free_roam = lambda: False
        req = self._reply(factory, [{'role': 'user', 'body': 'hi'}], toolbox=toolbox)
        system = req['system']
        self.assertEqual(system[0]['cache_control'], {'type': 'ephemeral'})  # stable block cached
        self.assertEqual(len(system), 2)
        self.assertNotIn('cache_control', system[-1])  # volatile block (local time) not cached
        self.assertIn('Current local time', system_text(system))

    @patch('ano.claude.Anthropic')
    def test_web_tools_only_when_request_points_outward(self, factory):
        self.assertNotIn('tools', self._reply(factory, [{'role': 'user', 'body': 'how are you today?'}], web=True))
        req = self._reply(factory, [{'role': 'user', 'body': 'search the latest news'}], web=True)
        self.assertEqual([t['name'] for t in req['tools']], ['web_search', 'web_fetch'])

    @patch('ano.claude.Anthropic')
    def test_fast_model_routes_simple_requests_only(self, factory):
        import ano.claude as c
        original = c.FAST_MODEL
        c.FAST_MODEL = 'fast-cheap'
        try:
            self.assertEqual(self._reply(factory, [{'role': 'user', 'body': 'hey, how are you?'}])['model'], 'fast-cheap')
            self.assertEqual(self._reply(factory, [{'role': 'user', 'body': 'fill in the Therap form'}])['model'], 'main-model')
            self.assertEqual(self._reply(factory, [{'role': 'user', 'body': 'search the web for prices'}], web=True)['model'], 'main-model')
        finally:
            c.FAST_MODEL = original

    @patch('ano.claude.Anthropic')
    def test_token_usage_is_logged_without_content(self, factory):
        with self.assertLogs('ano.cost', level='INFO') as logs:
            self._reply(factory, [{'role': 'user', 'body': 'hi there'}])
        line = logs.output[0]
        self.assertIn('input=100', line)
        self.assertIn('cache_read=80', line)
        self.assertNotIn('hi there', line)  # never logs message content


class CompactionTests(unittest.TestCase):
    def test_old_results_stubbed_newest_screenshot_kept_blocks_untouched(self):
        from ano.claude import compact_messages
        def user_result(tid, content):
            return {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': tid, 'content': content}]}
        def assistant_call(tid, name):
            return {'role': 'assistant', 'content': [
                {'type': 'thinking', 'thinking': 'secret reasoning'},
                {'type': 'tool_use', 'id': tid, 'name': name, 'input': {}}]}
        messages = []
        for i in range(5):
            messages.append(assistant_call(f'id{i}', 'read_file'))
            messages.append(user_result(f'id{i}', f'file body {i}'))
        # An old screenshot result and a newer one.
        messages.insert(2, {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 'shotA', 'content': [{'type': 'image', 'source': {}}]}]})
        messages.append({'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 'shotB', 'content': [{'type': 'image', 'source': {}}]}]})
        compact_messages(messages)
        stubs = [b['content'] for m in messages if m['role'] == 'user' for b in m['content']
                 if isinstance(b.get('content'), str) and b['content'].startswith('[Earlier')]
        self.assertTrue(stubs)                                   # old results were stubbed
        self.assertIn('read_file', stubs[0])                     # stub names the tool to re-run
        # Oldest screenshot stubbed; newest screenshot kept as an image.
        shot_a = next(b for m in messages for b in m.get('content', []) if isinstance(b, dict) and b.get('tool_use_id') == 'shotA')
        shot_b = next(b for m in messages for b in m.get('content', []) if isinstance(b, dict) and b.get('tool_use_id') == 'shotB')
        self.assertIsInstance(shot_a['content'], str)
        self.assertIsInstance(shot_b['content'], list)
        # Thinking and tool_use blocks are never altered.
        thinking = [b for m in messages if m['role'] == 'assistant' for b in m['content'] if b['type'] == 'thinking']
        self.assertTrue(all(b['thinking'] == 'secret reasoning' for b in thinking))


class ClaudeTests(unittest.TestCase):
    @patch('ano.claude.Anthropic')
    def test_approved_tool_screenshot_is_image_content(self, factory):
        from ano.tool_media import ToolImage
        api = factory.return_value.__enter__.return_value
        call = SimpleNamespace(type='tool_use', id='view1', name='desktop_observe', input={'window_id':1})
        api.beta.messages.create.side_effect = [
            SimpleNamespace(content=[call], stop_reason='tool_use'),
            SimpleNamespace(content=[SimpleNamespace(type='text', text='Observed.')], stop_reason='end_turn')]
        toolbox = Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = []
        toolbox.run.return_value = (ToolImage('Approved view', b'jpeg'), '', False)
        ClaudeClient('test', toolbox=toolbox).reply([{'role':'user','body':'Look at my app'}])
        result = api.beta.messages.create.call_args.kwargs['messages'][-1]['content'][0]
        self.assertEqual(result['content'][1]['type'], 'image')
    @patch('ano.claude.Anthropic')
    def test_activity_context_only_supplied_when_requested(self, factory):
        api = factory.return_value.__enter__.return_value
        api.beta.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type='text', text='You were editing')], stop_reason='end_turn')
        client = ClaudeClient('test', 'test-model')
        history = [{'role': 'user', 'body': 'What was I doing earlier?'}]
        client.reply(history, activity_context='{"records":[{"title":"Example project"}]}')
        system = system_text(api.beta.messages.create.call_args.kwargs['system'])
        self.assertIn('Example project', system)
        self.assertIn('untrusted JSON data', system)
        client.reply([{'role': 'user', 'body': 'Hello'}])
        self.assertNotIn('Example project', system_text(api.beta.messages.create.call_args.kwargs['system']))

    @patch('ano.claude.Anthropic')
    def test_image_only_attaches_to_approved_request(self, factory):
        api = factory.return_value.__enter__.return_value
        api.beta.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type='text', text='An example screen')], stop_reason='end_turn')
        client = ClaudeClient('test', 'test-model')
        history = [{'role': 'user', 'body': 'What am I looking at?'}]
        client.reply(history, screenshot=b'approved-image')
        blocks = api.beta.messages.create.call_args.kwargs['messages'][-1]['content']
        self.assertEqual(blocks[0]['type'], 'image')
        self.assertEqual(blocks[0]['source']['media_type'], 'image/jpeg')
        self.assertEqual(base64.b64decode(blocks[0]['source']['data']), b'approved-image')
        self.assertEqual(blocks[1]['text'], history[0]['body'])
        client.reply(history)
        # Without a screenshot the newest message is a single cached text block carrying the question.
        content = api.beta.messages.create.call_args.kwargs['messages'][-1]['content']
        self.assertEqual(content[-1]['text'], history[0]['body'])

    @patch('ano.claude.Anthropic')
    def test_context_bound_and_text_reply(self, factory):
        api = factory.return_value.__enter__.return_value
        api.beta.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type='text', text='Hello')], stop_reason='end_turn')
        history = [{'role': 'user', 'body': 'x' * 9000} for _ in range(30)]
        self.assertEqual(ClaudeClient('test', 'test-model').reply(history), 'Hello')
        request = api.beta.messages.create.call_args.kwargs
        self.assertLessEqual(sum(len(m['content']) for m in request['messages'][:-1]), 24000+9000)
        self.assertLessEqual(len(request['messages']), 20)
        self.assertEqual(sum(len(m['content'])>8000 for m in request['messages'][:-1]), 1)
        self.assertEqual(request['messages'][-1]['content'][-1]['text'], history[-1]['body'])
        self.assertEqual(request['model'], 'test-model')
        self.assertFalse('tools' in request)

    def test_long_form_source_preserves_start_middle_and_end(self):
        from ano.claude import trim_history
        source='Row 1: first fact\n' + 'important row data\n'*1000 + 'Final row: last fact'
        messages=trim_history([{'role':'user','body':source}])
        self.assertEqual(messages[-1]['content'], source)
        messages=trim_history([{'role':'user','body':source},
                               {'role':'assistant','body':'Please confirm the shift.'},
                               {'role':'user','body':'October 24, 3–10 PM.'}])
        self.assertEqual(messages[0]['content'], source)
        self.assertEqual(messages[-1]['content'],'October 24, 3–10 PM.')
        with self.assertRaises(ClaudeError):
            trim_history([{'role':'user','body':'x'*64001}])

    @patch('ano.claude.Anthropic')
    def test_tool_calls_run_locally_and_results_return_to_claude(self, factory):
        api = factory.return_value.__enter__.return_value
        call = SimpleNamespace(type='tool_use', id='call-1', name='open_website', input={'url': 'example.com'})
        api.beta.messages.create.side_effect = [
            SimpleNamespace(content=[call], stop_reason='tool_use'),
            SimpleNamespace(content=[SimpleNamespace(type='text', text='Opened it.')], stop_reason='end_turn'),
        ]
        toolbox = Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = [{'name': 'open_website'}]
        toolbox.run.return_value = ('Opened https://example.com', 'Opened https://example.com', False)
        reply = ClaudeClient('test', 'test-model', toolbox).reply([{'role': 'user', 'body': 'Open example.com'}])
        toolbox.run.assert_called_once_with('open_website', {'url': 'example.com'})
        self.assertEqual(reply, 'Opened it.\n\n· Opened https://example.com')
        request = api.beta.messages.create.call_args.kwargs
        self.assertEqual(request['tools'][0]['name'], 'open_website')
        self.assertEqual(request['tools'][-1]['cache_control'], {'type': 'ephemeral'})  # cached tool list
        self.assertEqual(request['messages'][1], {'role': 'assistant', 'content': [call]})
        self.assertEqual(request['messages'][2]['content'][0]['tool_use_id'], 'call-1')

    @patch('ano.claude.Anthropic')
    def test_web_research_resumes_paused_turn_and_lists_sources(self, factory):
        api = factory.return_value.__enter__.return_value
        search = SimpleNamespace(type='server_tool_use', name='web_search', input={'query': 'example topic'})
        citation = SimpleNamespace(url='https://example.com/a', title='Example source')
        api.beta.messages.create.side_effect = [
            SimpleNamespace(content=[search], stop_reason='pause_turn'),
            SimpleNamespace(content=[SimpleNamespace(type='text', text='Example answer', citations=[citation]),
                                     SimpleNamespace(type='text', text='.', citations=None)],
                            stop_reason='end_turn'),
        ]
        reply = ClaudeClient('test', 'test-model', web=True).reply([{'role': 'user', 'body': 'Research this'}])
        self.assertEqual(reply, 'Example answer.\n\nSources:\nExample source – https://example.com/a'
                                '\n\n· Searched the web for “example topic”')
        request = api.beta.messages.create.call_args.kwargs
        self.assertEqual([tool['name'] for tool in request['tools']], ['web_search', 'web_fetch'])
        self.assertIn('search the web', system_text(request['system']))
        # The paused turn is resent as-is, with no extra user message.
        self.assertEqual(request['messages'][-1], {'role': 'assistant', 'content': [search]})

    @patch('ano.claude.Anthropic')
    def test_auth_error_does_not_expose_response_or_key(self, factory):
        from anthropic import AuthenticationError
        api = factory.return_value.__enter__.return_value
        response = MagicMock(status_code=401)
        api.beta.messages.create.side_effect = AuthenticationError('secret response', response=response, body={})
        with self.assertRaises(ClaudeError) as caught:
            ClaudeClient('secret-key').reply([{'role': 'user', 'body': 'Hello'}])
        self.assertNotIn('secret', str(caught.exception))
        self.assertIn('rejected the API key', str(caught.exception))
