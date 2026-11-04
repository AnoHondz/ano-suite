from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from ano.routines import natural_reminder, LocalRoutines, RoutineStore
from ano.tools import ToolError


class NaturalCommandsTests(unittest.TestCase):
    def test_natural_arithmetic_is_exact_without_model_or_python_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            local = LocalRoutines(Mock(), RoutineStore(Path(directory) / 'routines.db'))
            for request, result in [('What is 24 times 17?', '408'), ("What's 15% of 80?", '12'),
                                    ('How much is (3 + 4) * 2?', '14'), ('what’s 9 plus 6?', '15')]:
                plan = local.plan(request)
                self.assertEqual(plan.text, result)
                self.assertEqual(plan.steps, [])
            self.assertIsNone(local.plan('what is the price of 5 stocks'))
            self.assertIsNone(local.plan("what is 1 + __import__('os').getcwd()"))
            with self.assertRaises(ToolError):
                local.plan('What is 1 / 0?')
            self.assertEqual(local.plan("what's the processing speed of my laptop?").steps,
                             [('system_overview', {})])
    def test_explicit_day_time_variants(self):
        now = datetime(2026, 10, 26, 10)
        cases = [
            ('remind me tomorrow at 4:45 pm to send the invoice', datetime(2026, 10, 27, 16, 45), 'send the invoice'),
            ('Tomorrow 4:45 pm: remind me to send the invoice', datetime(2026, 10, 27, 16, 45), 'send the invoice'),
            ('on Sunday at 10:30 am: remind me to water the plants', datetime(2026, 11, 1, 10, 30), 'water the plants'),
            ('remind me today at 12 pm to eat lunch', datetime(2026, 10, 26, 12), 'eat lunch'),
        ]
        for request, due, body in cases:
            self.assertEqual(natural_reminder(request, now), (body, due))

    def test_past_invalid_and_ambiguous_are_not_guessed(self):
        now = datetime(2026, 10, 26, 10)
        for request in ('remind me today at 9 am to call', 'remind me tomorrow at 13 pm to call'):
            with self.assertRaises(ToolError):
                natural_reminder(request, now)
        for request in ('do not remind me tomorrow at 4 pm to call', 'remind me at 4 to call'):
            self.assertIsNone(natural_reminder(request, now))

    def test_real_plan_bypasses_model(self):
        with tempfile.TemporaryDirectory() as directory:
            local = LocalRoutines(Mock(), RoutineStore(Path(directory) / 'routines.db'),
                                  clock=lambda: datetime(2026, 10, 26, 10))
            self.assertEqual(local.plan('remind me tomorrow at 4 pm to call Alex').steps,
                             [('add_reminder', {'text': 'call Alex', 'when': '2026-10-27T16:00:00'})])
            self.assertEqual(local.plan('how much ram do i have').steps, [('system_overview', {})])

    def test_compound_preflights_all_steps_and_preserves_literals(self):
        with tempfile.TemporaryDirectory() as directory:
            local = LocalRoutines(Mock(), RoutineStore(Path(directory) / 'routines.db'))
            self.assertEqual(local.plan('open word and then open calculator').steps,
                             [('open_app', {'name': 'Word'}), ('open_app', {'name': 'Calculator'})])
            self.assertIsNone(local.plan('open word and then open imaginary_application'))
            self.assertIsNone(local.plan('do not open word and then open calculator'))
            self.assertEqual(local.plan('write document Notes: First and then open calculator').steps,
                             [('write_document', {'name': 'Notes', 'text': 'First and then open calculator'})])
