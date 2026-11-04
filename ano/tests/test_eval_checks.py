import unittest
from finetune.evaluate import check_call, same


class EvaluationChecksTests(unittest.TestCase):
    def test_sensitive_arguments_are_exact_not_similar(self):
        for expected, actual in [('CaseSensitiveId', 'casesensitiveid'),
                                 ('Pay Alex, not Sam.', 'Pay Alex, Sam.'),
                                 ('https://example.org/a', 'https://example.org/A'),
                                 (1, True), (1, '1')]:
            self.assertFalse(same(expected, actual))

    def test_extra_calls_or_arguments_fail(self):
        wanted = {'name': 'add_note', 'arguments': {'text': 'example'}}
        call = {'function': wanted}
        self.assertTrue(check_call(wanted, {'tool_calls': [call]})[0])
        self.assertFalse(check_call(wanted, {'tool_calls': [call, call]})[0])
        self.assertFalse(check_call(wanted, {'tool_calls': [{'function': {
            'name': 'add_note', 'arguments': {'text': 'example', 'send': True}}}]})[0])
