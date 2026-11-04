from copy import deepcopy
import unittest

from ano.context import compact_compatible_messages, RESULT_HEAD, RESULT_TAIL
from ano.claude import compact_messages


class ContextTests(unittest.TestCase):
    def test_long_task_keeps_recent_results_call_ids_and_original_instructions(self):
        source = 'First row\n' + 'source data\n' * 2000 + 'Last row'
        messages = [{'role': 'system', 'content': 'rules'},
                    {'role': 'user', 'content': source}]
        for i in range(6):
            messages += [
                {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'id': f'call{i}', 'type': 'function',
                     'function': {'name': 'write_document', 'arguments': '{"name":"notes"}'}}]},
                {'role': 'tool', 'tool_call_id': f'call{i}',
                 'content': f'receipt{i}\n' + 'x' * 8000 + f'\nend{i}'}]
        calls = deepcopy([m for m in messages if m.get('tool_calls')])
        recent = deepcopy(messages[-6:])
        compact_compatible_messages(messages)
        self.assertEqual(messages[1]['content'], source)
        self.assertEqual([m for m in messages if m.get('tool_calls')], calls)
        self.assertEqual(messages[-6:], recent)
        results = [m for m in messages if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in results], [f'call{i}' for i in range(6)])
        for i, result in enumerate(results[:3]):
            self.assertIn(f'receipt{i}', result['content'])
            self.assertTrue(result['content'].endswith(f'end{i}'))
            self.assertIn('do not replay', result['content'])
            self.assertLess(len(result['content']), RESULT_HEAD + RESULT_TAIL + 300)
        once = deepcopy(messages)
        compact_compatible_messages(messages)
        self.assertEqual(messages, once)

    def test_old_screenshots_removed_without_losing_text_receipts(self):
        messages = []
        for i in range(3):
            messages.append({'role': 'user', 'content': [
                {'type': 'text', 'text': f'Observation {i}: text_verified=false'},
                {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,fixture{i}'}}]})
        newest = deepcopy(messages[-1])
        compact_compatible_messages(messages)
        self.assertEqual(messages[-1], newest)
        self.assertEqual(sum(b['type'] == 'image_url' for m in messages for b in m['content']), 1)
        self.assertIn('Observation 0: text_verified=false', str(messages[0]))
        self.assertNotIn('base64,fixture0', str(messages))

    def test_claude_compaction_keeps_error_flag_and_receipt_without_replay_advice(self):
        messages = [{'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': 'write1', 'name': 'write_document', 'input': {}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'write1',
             'content': 'Action may have happened; receipt=fixture123', 'is_error': True}]}]
        for i in range(4):
            messages.append({'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': f'read{i}', 'content': 'evidence'}]})
        compact_messages(messages)
        result = messages[1]['content'][0]
        self.assertTrue(result['is_error'])
        self.assertIn('receipt=fixture123', result['content'])
        self.assertIn('do not replay', result['content'])
        self.assertNotIn('Re-run', result['content'])
