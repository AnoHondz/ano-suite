from datetime import datetime
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from ano.brain import AnoBrain
from ano.local_profile import LOCAL_TOOLS, compact_system, compact_tools, stamp
from ano.model_policy import ModelPolicy

DEFINITIONS = [
    {'name': 'add_reminder', 'description': 'Remind the user at a local time. Works while Ano runs.',
     'input_schema': {'type': 'object', 'additionalProperties': False, 'required': ['text', 'when'],
                      'properties': {'text': {'type': 'string'},
                                     'when': {'type': 'string', 'description': 'Local YYYY-MM-DDTHH:MM'}}}},
    {'name': 'desktop_input', 'description': 'Click or type in another app.', 'input_schema': {'type': 'object'}},
]


class Memory:
    def __init__(self, values):
        self.values = values

    def setting(self, key, default):
        return self.values.get(key, default)


class CompactProfileTests(unittest.TestCase):
    def test_tools_are_filtered_and_keep_their_constraints(self):
        tools = compact_tools(DEFINITIONS)
        self.assertEqual([tool['function']['name'] for tool in tools], ['add_reminder'])
        function = tools[0]['function']
        self.assertEqual(function['description'], 'Remind the user at a local time.')
        self.assertEqual(function['parameters']['required'], ['text', 'when'])
        self.assertIn('when', function['parameters']['properties'])
        self.assertNotIn('desktop_input', LOCAL_TOOLS)

    def test_system_prompt_is_fixed_and_time_rides_on_the_user_message(self):
        text = compact_system('﻿Answer briefly\n')
        self.assertTrue(text.endswith("User's standing instructions:\nAnswer briefly"))
        self.assertNotIn('﻿', text)
        self.assertNotIn('[Local time:', text)
        self.assertEqual(text, compact_system('Answer briefly'))
        self.assertEqual(stamp('hi', datetime(2026, 10, 27, 9, 5)), '[Local time: Tuesday 2026-10-27 09:05]\nhi')

    def test_requests_share_a_cacheable_prefix_and_warm_up_matches_it(self):
        api = Mock()
        api.chat.completions.create.return_value = NS(choices=[NS(message=NS(content='Ok', tool_calls=None),
                                                                  finish_reason='stop')])
        toolbox = Mock()
        toolbox.definitions.return_value = DEFINITIONS
        brain = AnoBrain('local', toolbox, model='ano-local:latest', rules='Answer briefly',
                         base_url='http://127.0.0.1:11434/v1', connect=lambda: api, profile='compact')
        brain.warm()
        brain.reply([{'role': 'user', 'body': 'first'}])
        brain.reply([{'role': 'user', 'body': 'second'}], activity_context='[]')
        calls = [call.kwargs for call in api.chat.completions.create.call_args_list]
        self.assertEqual(len({json.dumps([c['messages'][0], c['tools']], sort_keys=True) for c in calls}), 1)
        self.assertTrue(calls[1]['messages'][-1]['content'].startswith('[Local time: '))
        self.assertIn('[Local activity records', calls[2]['messages'][-1]['content'])
        self.assertEqual(calls[0]['max_tokens'], 1)

    def test_policy_reads_profile_and_defaults_to_fast(self):
        self.assertEqual(ModelPolicy.load(Memory({}), {}).profile, 'fast')
        self.assertEqual(ModelPolicy.load(Memory({'local_profile': 'compact'}), {}).profile, 'compact')
        self.assertEqual(ModelPolicy.load(Memory({}), {'ANO_LOCAL_PROFILE': 'COMPACT'}).profile, 'compact')

    def test_compact_brain_sends_the_trained_prompt_tools_and_no_thinking(self):
        api = Mock()
        api.chat.completions.create.return_value = NS(choices=[NS(message=NS(content='Hi!', tool_calls=None),
                                                                  finish_reason='stop')])
        toolbox = Mock()
        toolbox.definitions.return_value = DEFINITIONS
        toolbox.folders.return_value = []
        brain = AnoBrain('local', toolbox, model='ano-local:latest', rules='Answer briefly',
                         base_url='http://127.0.0.1:11434/v1', connect=lambda: api, profile='compact')
        self.assertEqual(brain.reply([{'role': 'user', 'body': 'hi'}]), 'Hi!')
        request = api.chat.completions.create.call_args.kwargs
        system = request['messages'][0]['content']
        self.assertTrue(system.startswith("You are Ano, a warm, brief assistant"))
        self.assertIn('Answer briefly', system)
        self.assertNotIn("operating as Ano's web-connected computer agent", system)  # AGENT_RULES left out
        self.assertEqual([tool['function']['name'] for tool in request['tools']], ['add_reminder'])
        self.assertEqual(request['reasoning_effort'], 'none')
        self.assertEqual(request['temperature'], 0.2)


if __name__ == '__main__':
    unittest.main()
