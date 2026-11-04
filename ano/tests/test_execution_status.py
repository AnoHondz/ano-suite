from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import Mock
from ano.gpt import GPTClient
from ano.plugins.apps import Apps


class ExecutionStatusTests(TestCase):
    def setUp(self):
        from tests.inference_fixture import isolate_inference
        isolate_inference(self)

    def test_no_action_retry_hands_off_before_any_tool_and_runs_action_once(self):
        api = Mock()
        def result(text='', calls=None):
            return NS(choices=[NS(message=NS(content=text, tool_calls=calls), finish_reason='stop')])
        api.chat.completions.create.side_effect = [result('I cannot help.'),
            result(calls=[NS(id='1', function=NS(name='list_windows', arguments='{}'))]), result('Inspected the app list.')]
        tools = Mock()
        tools.definitions.return_value = [{'name':'list_windows','description':'Read windows','input_schema':{'type':'object'}}]
        tools.allowed.return_value = True
        tools.free_roam.return_value = False
        tools.run.return_value = ('Fixture app list', '', False)
        brain = GPTClient('local', tools, model='small', vision_model='larger',
                          base_url='http://127.0.0.1:11434/v1', connect=lambda:api, profile='fast')
        self.assertEqual(brain.reply([{'role':'user','body':'Make a Spotify playlist'}]), 'Inspected the app list.')
        self.assertEqual([c.kwargs['model'] for c in api.chat.completions.create.call_args_list], ['small','larger','larger'])
        tools.run.assert_called_once_with('list_windows', {})

    def test_no_action_retry_is_bounded_and_does_not_run_tools_for_refusals(self):
        brain = self.brain('I cannot help.')
        brain.reply([{'role':'user','body':'Open Word'}])
        api = brain.connect()
        self.assertEqual(api.chat.completions.create.call_count, 2)
        brain.toolbox.run.assert_not_called()

    def test_completed_action_is_not_replayed_by_no_action_recovery(self):
        api, tools = Mock(), Mock()
        tools.definitions.return_value = Apps().definitions()
        tools.allowed.return_value = True
        tools.free_roam.return_value = False
        tools.run.return_value = ('Opening dispatched', '', False)
        api.chat.completions.create.side_effect = [NS(choices=[NS(message=NS(content='',tool_calls=[
            NS(id='open',function=NS(name='open_app',arguments='{"name":"Word"}'))]),finish_reason='tool_calls')]),
            NS(choices=[NS(message=NS(content='I cannot help.',tool_calls=None),finish_reason='stop')])]
        brain = GPTClient('local',tools,model='small',vision_model='larger',base_url='http://127.0.0.1:11434/v1',
                          connect=lambda:api,profile='fast')
        brain.reply([{'role':'user','body':'Open Word'}])
        self.assertEqual(api.chat.completions.create.call_count, 2)
        tools.run.assert_called_once_with('open_app', {'name':'Word'})

    def brain(self, text, profile='fast'):
        api = Mock()
        api.chat.completions.create.return_value = NS(choices=[
            NS(message=NS(content=text, tool_calls=None), finish_reason='stop')])
        tools = Mock()
        tools.definitions.return_value = Apps().definitions()
        tools.allowed.return_value = True
        tools.free_roam.return_value = False
        return GPTClient('local', tools, model='ano-agent:latest',
                         base_url='http://127.0.0.1:11434/v1',
                         connect=lambda: api, profile=profile)

    def test_action_without_tool_cannot_look_like_verified_success(self):
        response = self.brain('Opened Word.').reply([{'role': 'user', 'body': 'Please open Word'}])
        self.assertTrue(response.startswith('Not completed: no action tool ran.'))
        self.assertIn('Local model response:', response)

    def test_conversation_and_advice_do_not_get_false_execution_warning(self):
        for request in ('Hello', 'How do I open Word?', 'What is a document?'):
            response = self.brain('Example answer').reply([{'role': 'user', 'body': request}])
            self.assertEqual(response, 'Example answer')

    def test_microsoft_app_alias_finds_real_start_menu_name(self):
        launch = Mock()
        apps = Apps(listing=lambda: [{'Name': 'Word', 'AppID': 'example-word'}], launch=launch)
        apps.open_app('Microsoft Word')
        launch.assert_called_once_with('shell:AppsFolder\\example-word')
