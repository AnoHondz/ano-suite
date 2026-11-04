from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from ano.execution import RunBudget
from ano.gpt import GPTClient
from ano.local_limits import wait_allowance, turn_allowance
from ano.local_request import request


class LocalLimitsTests(unittest.TestCase):
    def setUp(self):
        from tests.inference_fixture import isolate_inference
        isolate_inference(self)

    def test_live_gpu_question_uses_inspection_without_hijacking_general_definitions(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from ano.routines import LocalRoutines, RoutineStore
        from ano.local_profile import task_tools
        with TemporaryDirectory() as folder:
            routines = LocalRoutines(Mock(), RoutineStore(Path(folder) / 'routines.db'))
            plan = routines.plan('Which GPU do you have, and how much VRAM? (You can check in Task Manager.)')
            self.assertEqual(plan.steps[0][0], 'inspect_system')
            self.assertIn('Win32_VideoController', plan.steps[0][1]['command'])
            self.assertIn('not a verified dedicated-VRAM', plan.text)
            self.assertIsNone(routines.plan('What is a GPU?'))
        definitions = [{'name': 'inspect_system', 'description': 'Read-only inspection', 'input_schema': {'type': 'object'}}]
        self.assertIn('inspect_system', {tool['function']['name'] for tool in task_tools(definitions, 'Which GPU do I have?')})

    def test_cold_prompt_and_output_get_bounded_allowances(self):
        short = {'messages': [{'content': 'Hello'}], 'max_tokens': 64}
        cold = {'messages': [{'content': 'public fixture ' * 2000}], 'max_tokens': 512}
        self.assertEqual(wait_allowance(short), 180)
        self.assertGreater(wait_allowance(cold), 120)
        self.assertLessEqual(wait_allowance(cold), 900)
        self.assertGreaterEqual(turn_allowance(cold), wait_allowance(cold))
        self.assertLessEqual(turn_allowance(cold), 1800)
        self.assertEqual(turn_allowance(short, conversation=True), wait_allowance(short))

    def test_image_encoding_does_not_inflate_wait_allowance(self):
        def arguments(size):
            return {'messages': [{'content': [{'type': 'image_url', 'image_url': {'url': 'x' * size}}]}], 'max_tokens': 256}
        self.assertEqual(wait_allowance(arguments(20)), wait_allowance(arguments(200000)))

    def test_actual_gpt_local_route_reserves_more_than_two_minutes_without_paid_change(self):
        api = Mock()
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Answer', tool_calls=None), finish_reason='stop')])
        for profile, expected in [('chat', True), ('paid', False)]:
            captured = []
            def reply(api, arguments, budget, timeout, progress, use_gate):
                self.assertEqual(use_gate,expected)
                captured.append((budget.deadline - budget.clock(), timeout, progress))
                return response
            client = GPTClient('local', model='fixture', base_url='http://127.0.0.1:11434/v1',
                               profile=profile, connect=lambda: api, output_tokens=512)
            with patch('ano.local_request.request', side_effect=reply):
                client.reply([{'role': 'user', 'body': 'A synthetic conversation question'}])
            if expected:
                self.assertGreater(captured[0][0], 120)
                self.assertGreater(captured[0][1], 180)
            else:
                self.assertGreater(captured[0][0], 590)
                self.assertLessEqual(captured[0][0], 600)
                self.assertEqual(captured[0][1], 180)

    def test_wait_past_120_seconds_can_complete_once_with_visible_progress(self):
        release = Event()
        api, toolbox = Mock(), Mock()
        toolbox.allowed.return_value = True
        toolbox.free_roam.return_value = False
        def complete(**kwargs):
            release.wait(2)
            return 'synthetic response'
        api.chat.completions.create.side_effect = complete
        now = [0]
        budget = RunBudget(toolbox, max_seconds=600, clock=lambda: now[0])
        times = iter([0, 125])
        def clock():
            now[0] = next(times, now[0])
            return now[0]
        reports = []
        def progress(text):
            reports.append(text)
            release.set()
        try:
            self.assertEqual(request(api, {}, budget, timeout=300, progress=progress, clock=clock), 'synthetic response')
            self.assertTrue(any('125s elapsed' in r for r in reports))
            api.chat.completions.create.assert_called_once()
            toolbox.run.assert_not_called()
        finally:
            release.set()
