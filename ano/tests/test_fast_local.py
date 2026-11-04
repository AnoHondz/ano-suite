import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from ano.brain import AnoBrain
from ano.brain import LocalConversationRouter
from ano.claude import ClaudeError
from ano.execution import RunBudget
from ano.local_profile import task_tools
from ano.local_request import request
from ano.context import fast_history


def answer(text='', calls=None):
    return NS(choices=[NS(message=NS(content=text, tool_calls=calls), finish_reason='stop')])


class FastLocalTests(unittest.TestCase):
    def setUp(self):
        from tests.inference_fixture import isolate_inference
        isolate_inference(self)

    def test_wrong_format_write_never_dispatches_and_word_retry_resolves_it(self):
        api, toolbox = Mock(), Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = [
            {'name': n, 'description': n, 'input_schema': {'type': 'object'}}
            for n in ('write_document', 'write_word_document')]
        toolbox.run.return_value = ('Saved and verified Fixture.docx', 'Created Word document', False)
        api.chat.completions.create.side_effect = [
            answer(calls=[NS(id='wrong', function=NS(name='write_document', arguments='{}'))]),
            answer(calls=[NS(id='right', function=NS(name='write_word_document', arguments='{}'))]),
            answer('Created Fixture.docx')]
        result = AnoBrain(toolbox=toolbox, model='ano-agent:latest', profile='fast', connect=lambda: api).reply(
            [{'role': 'user', 'body': 'Create a Word document named Fixture containing supplied text.'}])
        toolbox.run.assert_called_once_with('write_word_document', {})
        self.assertNotIn('not verified', result)
        self.assertIn('Created Fixture.docx', result)

    def test_explicit_word_creation_excludes_markdown_writers(self):
        from ano.local_profile import word_creation_request
        names = ('write_document', 'draft_document', 'write_word_document', 'read_word_document')
        definitions = [{'name': n, 'description': n, 'input_schema': {'type': 'object'}} for n in names]
        request = 'Create a Word document named "Fixture" containing exactly this text: "Public fixture."'
        selected = {t['function']['name'] for t in task_tools(definitions, request)}
        self.assertIn('write_word_document', selected)
        self.assertNotIn('write_document', selected)
        self.assertNotIn('draft_document', selected)
        self.assertFalse(word_creation_request('write document Note: Word document creation is useful'))
        self.assertFalse(word_creation_request('Create a Markdown guide about Word documents'))
        self.assertFalse(word_creation_request('Create a Word document and a PDF'))

    def test_free_roam_removes_local_task_deadline_but_not_paid_limit(self):
        for profile, roaming, unlimited in [('fast', False, False), ('fast', True, True), ('paid', True, False)]:
            toolbox, api = Mock(), Mock()
            toolbox.definitions.return_value = []
            toolbox.allowed.return_value = True
            toolbox.free_roam.return_value = roaming
            brain = AnoBrain(toolbox=toolbox, profile=profile, connect=lambda: api)
            with patch('ano.local_request.request', return_value=answer('Hello')) as request_mock:
                brain.reply([{'role': 'user', 'body': 'Hello'}])
                budget = request_mock.call_args.args[2]
                self.assertEqual(budget.deadline is None, unlimited)

    def test_clock_context_does_not_override_fiction_in_chat_or_drafts(self):
        for profile in ('chat', 'writer'):
            api = Mock()
            api.chat.completions.create.return_value = answer('Thursday, 10 am.')
            prompt = "In a fictional story, Mira's picnic starts Thursday at 10 am. Reply with its day and time."
            brain = AnoBrain(profile=profile, connect=lambda: api)
            brain.reply([{'role': 'user', 'body': prompt}])
            sent = api.chat.completions.create.call_args.kwargs['messages'][-1]['content']
            self.assertEqual(sent, prompt)
            self.assertNotIn('[Local time:', sent)

    def test_playlist_request_exposes_creation_tools_not_only_playback(self):
        names = ('media_control', 'browser_connection_status', 'list_tabs', 'read_tab',
                 'start_browser_task', 'browser_task_action', 'verify_browser_task',
                 'list_windows', 'desktop_observe', 'desktop_read', 'desktop_input')
        definitions = [{'name': n, 'description': n, 'input_schema': {'type': 'object'}} for n in names]
        for text in ('can you make me a bugzymalone 11 song playlist for the gym',
                     'create playlists for running', 'add a track to my Spotify playlist'):
            selected = {t['function']['name'] for t in task_tools(definitions, text)}
            self.assertTrue({'browser_connection_status', 'list_tabs', 'start_browser_task',
                             'browser_task_action', 'verify_browser_task', 'list_windows',
                             'desktop_observe', 'desktop_read', 'desktop_input'} <= selected)
        # Ordinary playback does not need all the creation/inspection schemas.
        self.assertNotIn('desktop_input', {t['function']['name'] for t in task_tools(definitions, 'mute music')})

    def test_canvas_login_followup_uses_agent_and_saved_login_tools(self):
        api, toolbox = Mock(), Mock()
        toolbox.definitions.return_value = [
            {'name': n, 'description': n, 'input_schema': {'type': 'object'}}
            for n in ('browser_connection_status', 'list_tabs', 'read_tab', 'list_saved_logins', 'sign_in_saved_website')]
        api.chat.completions.create.return_value = answer('An exact login blocker')
        agent = AnoBrain(toolbox=toolbox, profile='fast', connect=lambda: api)
        chat = Mock()
        router = LocalConversationRouter(agent, chat)
        for text in ('log in and pull up my week 4 to do list',
                     'Log in to the platform and pull up your Week 4 to-do list.'):
            with self.subTest(text=text):
                router.reply([{'role': 'user', 'body': 'open canvas on chrome'},
                              {'role': 'assistant', 'body': 'Opened Canvas'},
                              {'role': 'user', 'body': text}])
                names = {t['function']['name'] for t in api.chat.completions.create.call_args.kwargs['tools']}
                self.assertTrue({'list_tabs', 'read_tab', 'list_saved_logins', 'sign_in_saved_website'} <= names)
        chat.reply.assert_not_called()

    def test_real_followup_retains_browser_tools_and_stays_out_of_chat(self):
        api, toolbox = Mock(), Mock()
        definitions = [{'name': n, 'description': n, 'input_schema': {'type': 'object'}}
                       for n in ('list_tabs', 'open_browser_tab', 'list_startup_apps')]
        toolbox.definitions.return_value = definitions
        api.chat.completions.create.return_value = answer('Inspecting the browser')
        agent = AnoBrain(toolbox=toolbox, profile='fast', connect=lambda: api)
        chat = Mock()
        router = LocalConversationRouter(agent, chat)
        history = [{'role': 'user', 'body': 'open canvas on chrome'},
                   {'role': 'assistant', 'body': 'No relevant tools'},
                   {'role': 'user', 'body': 'try again'}]
        router.reply(history)
        chat.reply.assert_not_called()
        names = {t['function']['name'] for t in api.chat.completions.create.call_args.kwargs['tools']}
        self.assertIn('open_browser_tab', names)
        self.assertIn('list_tabs', names)
        # A new unrelated request must not inherit browser work.
        router.reply(history + [{'role': 'user', 'body': 'Hello'}])
        chat.reply.assert_called_once()

    def test_completion_reasoning_never_goes_to_the_tiny_chat_role(self):
        agent, chat = Mock(), Mock()
        agent.toolbox.definitions.return_value = []
        router = LocalConversationRouter(agent, chat)
        router.reply([{'role':'user','body':'Only one required step finished. Is the task complete?'}])
        agent.reply.assert_called_once()
        chat.reply.assert_not_called()

    def test_laptop_startup_language_gets_startup_capabilities(self):
        definitions = [{'name': 'list_startup_apps', 'description': 'List', 'input_schema': {'type': 'object'}}]
        for text in ('when the laptop starts why did a bunch more apps start with it?',
                     'okay only Ano should start with the laptop'):
            self.assertIn('list_startup_apps', {t['function']['name'] for t in task_tools(definitions, text)})

    def test_inventory_answer_keeps_every_entry_without_a_second_generation(self):
        api, toolbox = Mock(), Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = [{'name': 'list_startup_apps', 'description': 'List registered startup apps',
                                            'input_schema': {'type': 'object'}}]
        evidence = 'Registered sign-in apps: Example1, Example2, SecurityHealth'
        toolbox.run.return_value = (evidence, '', False)
        api.chat.completions.create.return_value = answer(calls=[NS(id='list', function=NS(
            name='list_startup_apps', arguments='{}'))])
        result = AnoBrain(toolbox=toolbox, model='ano-agent:latest', profile='fast', connect=lambda: api).reply(
            [{'role': 'user', 'body': 'List the registered startup apps. Do not change any settings.'}])
        self.assertEqual(result, evidence)
        self.assertEqual(api.chat.completions.create.call_count, 1)

    def test_text_planner_hands_off_tool_images_without_repeating_the_tool(self):
        from ano.tool_media import ToolImage
        api, toolbox = Mock(), Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = [{'name': 'desktop_observe', 'description': 'Inspect screen',
                                            'input_schema': {'type': 'object'}}]
        toolbox.run.return_value = (ToolImage('Fixture screen', b'fixture'), '', False)
        api.chat.completions.create.side_effect = [answer(calls=[NS(id='view', function=NS(
            name='desktop_observe', arguments='{}'))]), answer('Observed')]
        brain = AnoBrain(toolbox=toolbox, model='ano-agent:latest', vision_model='ano-local:latest',
                         profile='fast', connect=lambda: api)
        self.assertEqual(brain.reply([{'role': 'user', 'body': 'Inspect the screen'}]), 'Observed')
        self.assertEqual([call.kwargs['model'] for call in api.chat.completions.create.call_args_list],
                         ['ano-agent:latest', 'ano-local:latest'])
        toolbox.run.assert_called_once()

    def test_chat_profile_has_no_tools_or_tool_execution(self):
        api, toolbox = Mock(), Mock()
        toolbox.definitions.return_value = [{'name': 'open_app', 'description': 'Open', 'input_schema': {'type': 'object'}}]
        api.chat.completions.create.return_value = answer('I run locally.')
        text = AnoBrain(toolbox=toolbox, model='ano-chat:latest', profile='chat', connect=lambda: api).reply(
            [{'role': 'user', 'body': 'Which brain are you using?'}])
        self.assertEqual(text, 'I run locally.')
        self.assertNotIn('tools', api.chat.completions.create.call_args.kwargs)
        toolbox.run.assert_not_called()

    def test_lightweight_conversation_never_handles_actions_or_images(self):
        agent, conversation = Mock(), Mock()
        agent.toolbox.definitions.return_value = [{'name': 'open_app', 'description': 'Open', 'input_schema': {'type': 'object'}}]
        agent.reply.return_value = 'agent'
        conversation.reply.return_value = 'chat'
        router = LocalConversationRouter(agent, conversation)
        self.assertEqual(router.reply([{'role': 'user', 'body': 'Hello'}]), 'chat')
        for body, kwargs in [('Open Calculator', {}), ('yes', {}), ('Which chart?', {}), ('Look', {'screenshot': b'image'})]:
            self.assertEqual(router.reply([{'role': 'user', 'body': body}], **kwargs), 'agent')

    def test_short_history_preserves_full_long_source_and_latest_correction(self):
        source = 'row data ' * 1200
        messages = [{'role': 'user', 'content': source}]
        for i in range(8):
            messages.extend([{'role': 'assistant', 'content': 'answer'},
                             {'role': 'user', 'content': f'follow-up {i}'}])
        result = fast_history(messages)
        self.assertEqual(result[0]['content'], source)
        self.assertEqual(result[-1]['content'], 'follow-up 7')
        self.assertLessEqual(len(result), 7)

    def test_brain_rejects_remote_or_cloud_configuration_before_client_creation(self):
        for options in ({'base_url': 'https://api.openai.com/v1'},
                        {'base_url': None}, {'model': 'large:cloud'}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'Cloud AI is blocked'):
                AnoBrain(**options)
        brain = AnoBrain()
        self.assertEqual(brain.base_url, 'http://127.0.0.1:11434/v1')
        self.assertEqual(brain.model, 'ano-local:latest')

    def test_casual_chat_does_not_receive_the_whole_tool_catalog(self):
        definitions = [{'name': f'fixture_{i}', 'description': 'Unused tool', 'input_schema': {'type': 'object'}}
                       for i in range(100)]
        self.assertEqual([t['function']['name'] for t in task_tools(definitions, 'hello')], ['find_tool'])

    def test_image_and_desktop_capabilities_are_available_when_requested(self):
        definitions = [{'name': name, 'description': name, 'input_schema': {'type': 'object'}}
                       for name in ('generate_image', 'desktop_read', 'desktop_input', 'list_windows')]
        names = {t['function']['name'] for t in task_tools(definitions, 'generate a picture')}
        self.assertIn('generate_image', names)
        names = {t['function']['name'] for t in task_tools(definitions, 'click the desktop')}
        self.assertIn('desktop_input', names)

    def test_tool_discovery_adds_real_schema_then_executes_once(self):
        api, toolbox = Mock(), Mock()
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = [{'name': 'fixture_probe', 'description': 'Read fixture readiness',
                                            'input_schema': {'type': 'object'}}]
        toolbox.run.return_value = ('ready', '', False)
        snapshots = []
        def respond(**kwargs):
            snapshots.append([t['function']['name'] for t in kwargs['tools']])
            if len(snapshots) == 1:
                return answer(calls=[NS(id='find', function=NS(name='find_tool', arguments='{"query":"fixture probe"}'))])
            if len(snapshots) == 2:
                return answer(calls=[NS(id='probe', function=NS(name='fixture_probe', arguments='{}'))])
            return answer('ready')
        api.chat.completions.create.side_effect = respond
        text = AnoBrain(toolbox=toolbox, profile='fast', connect=lambda: api).reply([{'role': 'user', 'body': 'Check readiness'}])
        self.assertEqual(text, 'ready')
        self.assertEqual(snapshots[0], ['find_tool'])
        self.assertIn('fixture_probe', snapshots[1])
        toolbox.run.assert_called_once_with('fixture_probe', {})

    def test_pause_cancels_wait_and_late_response_never_runs_tools(self):
        api, toolbox = Mock(), Mock()
        toolbox.allowed.return_value = True
        released = threading.Event()
        def slow(**kwargs):
            toolbox.allowed.return_value = False
            released.wait(1)
            return answer(calls=[NS(id='late', function=NS(name='desktop_input', arguments='{}'))])
        api.chat.completions.create.side_effect = slow
        started = time.monotonic()
        try:
            with self.assertRaises(ClaudeError):
                request(api, {}, RunBudget(toolbox))
            self.assertLess(time.monotonic() - started, 0.7)
            toolbox.run.assert_not_called()
        finally:
            released.set()
