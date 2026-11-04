import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ano.browser_tasks import BrowserTasks
from ano.tools import ToolError


class Control:
    _generation = 1
    enabled = True

    def __init__(self, reviews):
        self.reviews = reviews
        self.full = False

    def request_review(self, **details):
        if not self.full:
            self.reviews.request(**details)

    def permitted(self):
        return self.enabled

    def guard(self, generation):
        return self.enabled and generation == self._generation


class Reviews:
    def __init__(self):
        self.callback = lambda: None
        self.details = []

    def request(self, **details):
        self.details.append(details)
        self.callback()


class Bridge:
    def __init__(self):
        self.text = 'Search for a song'
        self.actions = []
        self.fail = False

    def call(self, browser, action, _permitted=None, **args):
        if _permitted and not _permitted():
            raise ToolError('Stopped before dispatch')
        if action == 'workflow_inspect':
            return dict(documentId='doc1', url='https://fixture.test/search', title='Search', text=self.text,
                        elements=[dict(id='1', tag='input', type='search', label='Song search', href='')])
        self.actions.append(args)
        if self.fail:
            raise ToolError('Timeout after possible click')
        return {'performed': True}


class BrowserTaskTests(unittest.TestCase):
    def test_progress_snapshot_uses_verified_steps_not_attempts(self):
        seen = []
        self.tasks.progress_changed = lambda: seen.append(self.tasks.progress_snapshot())
        before = self.tasks.progress_snapshot()
        self.assertEqual((before['step'], before['total'], before['verified_steps']), (1, 1, 0))
        self.act()
        self.assertEqual(self.tasks.progress_snapshot()['verified_steps'], 0)
        self.bridge.text = 'Found one song'
        self.tasks.verify_browser_task(self.id)
        self.assertEqual(self.tasks.progress_snapshot()['verified_steps'], 1)
        self.assertEqual(self.tasks.progress_snapshot()['state'], 'running')
        self.tasks.finish_browser_task(self.id, 'completed', 'Exact observed result')
        self.assertEqual(self.tasks.progress_snapshot()['state'], 'completed')
        self.assertTrue(seen)
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'memory.sqlite3'
        self.bridge, self.reviews = Bridge(), Reviews()
        self.control = Control(self.reviews)
        self.now = 100
        self.tasks = BrowserTasks(self.path, self.bridge, self.reviews, self.control, clock=lambda: self.now)
        self.id = json.loads(self.tasks.start_browser_task('Find a song', 'chrome:7', [
            dict(instruction='Search for the song', expected_text='Found one song')])[0])['id']

    def tearDown(self):
        self.temp.cleanup()

    def observe(self):
        return json.loads(self.tasks.inspect_browser_task(self.id)[0])['observation_id']

    def act(self, observation=None):
        self.tasks.browser_task_action(self.id, observation or self.observe(), '1', 'fill', 'Search the catalog', 'fixture query')

    def test_complete_only_after_new_observed_result_and_no_typed_text_in_ledger(self):
        with self.assertRaises(ToolError):
            self.tasks.finish_browser_task(self.id, 'completed', 'Done')
        self.act()
        self.assertIn('Not verified', self.tasks.verify_browser_task(self.id)[0])
        self.bridge.text = 'Found one song'
        self.tasks.verify_browser_task(self.id)
        record = json.loads(self.tasks.finish_browser_task(self.id, 'completed', 'Found the song')[0])
        self.assertEqual(record['current_step'], 1)
        self.assertEqual(record['evidence'][0]['origin'], 'fixture.test')
        self.assertNotIn('fixture query', self.tasks.browser_task_status(self.id)[0])
        self.assertIn('fixture query', self.reviews.details[0]['detail'])

    def test_old_success_text_and_no_action_cannot_verify(self):
        self.bridge.text = 'Found one song'
        self.assertIn('Not verified', self.tasks.verify_browser_task(self.id)[0])
        self.act()
        self.assertIn('Not verified', self.tasks.verify_browser_task(self.id)[0])

    def test_single_use_expiring_observation(self):
        obs = self.observe()
        self.act(obs)
        with self.assertRaises(ToolError):
            self.act(obs)
        obs = self.observe()
        self.now += 61
        with self.assertRaises(ToolError):
            self.act(obs)
        self.assertEqual(len(self.bridge.actions), 1)

    def test_stop_during_approval_and_reenable_cannot_revive_task(self):
        def stop():
            self.control._generation += 1
            self.tasks.stop()
        self.reviews.callback = stop
        with self.assertRaises(ToolError):
            self.act()
        self.assertEqual(self.bridge.actions, [])
        self.assertEqual(json.loads(self.tasks.browser_task_status(self.id)[0])['status'], 'blocked')
        with self.assertRaises(ToolError):
            self.observe()

    def test_denial_stops_task_without_dispatch(self):
        def deny():
            raise ToolError('Denied')
        self.reviews.callback = deny
        with self.assertRaises(ToolError):
            self.act()
        self.assertFalse(self.bridge.actions)
        self.assertEqual(json.loads(self.tasks.browser_task_status(self.id)[0])['status'], 'blocked')

    def test_uncertain_dispatch_blocks_and_is_not_replayed_after_restart(self):
        self.bridge.fail = True
        with self.assertRaises(ToolError):
            self.act()
        with self.assertRaises(ToolError):
            self.act()
        restarted = BrowserTasks(self.path, self.bridge, self.reviews, self.control)
        self.assertEqual(json.loads(restarted.browser_task_status(self.id)[0])['status'], 'blocked')
        self.assertEqual(len(self.bridge.actions), 1)

    def test_refusal_before_acting_keeps_task_running_and_retry_is_allowed(self):
        call = self.bridge.call
        def refuse(browser, action, _permitted=None, **args):
            if action == 'workflow_act':
                raise ToolError('Chrome refused: The observed element changed. Inspect again.')
            return call(browser, action, _permitted, **args)
        self.bridge.call = refuse
        with self.assertRaisesRegex(ToolError, 'Nothing was clicked'):
            self.act()
        record = json.loads(self.tasks.browser_task_status(self.id)[0])
        self.assertEqual(record['status'], 'running')
        self.assertFalse(record['pending_dispatch'])
        self.assertNotIn('acted', record['steps'][0])
        self.bridge.call = call
        self.act()
        self.bridge.text = 'found ONE\nsong'  # case and line wrapping do not matter
        self.assertIn('Verified', self.tasks.verify_browser_task(self.id)[1])

    def test_revise_replaces_only_unverified_steps(self):
        self.act()
        self.bridge.text = 'Found one song'
        self.tasks.verify_browser_task(self.id)
        with self.assertRaises(ToolError):
            self.tasks.revise_browser_task(self.id, [])
        record = json.loads(self.tasks.revise_browser_task(self.id, [
            dict(instruction='Open the song', expected_text='Now playing')])[0])
        self.assertEqual([s['expected_text'] for s in record['steps']], ['Found one song', 'Now playing'])
        self.assertTrue(record['steps'][0]['verified'])
        self.assertEqual(record['current_step'], 1)

    def test_restart_marks_interrupted_plan_blocked(self):
        restarted = BrowserTasks(self.path, self.bridge, self.reviews, self.control)
        record = json.loads(restarted.browser_task_status(self.id)[0])
        self.assertEqual(record['status'], 'blocked')
        self.assertIn('restarted', record['reason'])

    def test_reasoning_turn_end_closes_unfinished_task(self):
        self.tasks.turn_finished()
        self.assertEqual(json.loads(self.tasks.browser_task_status(self.id)[0])['status'], 'blocked')
        with self.assertRaises(ToolError):
            self.observe()

    def test_full_permission_browser_action_skips_review_and_still_requires_verification(self):
        self.control.full = True
        self.act()
        self.assertFalse(self.reviews.details)
        self.assertEqual(len(self.bridge.actions), 1)
        with self.assertRaises(ToolError):
            self.tasks.finish_browser_task(self.id, 'completed', 'Clicked')
        self.bridge.text = 'Found one song'
        self.tasks.verify_browser_task(self.id)
        self.assertEqual(json.loads(self.tasks.finish_browser_task(self.id, 'completed', 'Found it')[0])['status'], 'completed')

    def test_roaming_removes_browser_attempt_and_plan_caps(self):
        self.control.permissionMode='roam'
        self.control.full=True
        self.tasks.task['attempts']=24
        self.act()
        self.assertEqual(len(self.bridge.actions),1)
        self.tasks.stop()
        self.tasks.start_browser_task('Longer plan','chrome:7',[
            {'instruction':f'Action {i}','expected_text':f'Result {i}'} for i in range(12)])
        schema=self.tasks.definitions()[0]['input_schema']['properties']['steps']
        self.assertNotIn('maxItems',schema)

    def test_resume_after_handoff_preserves_verified_steps_and_requires_fresh_observation(self):
        self.act()
        self.bridge.text = 'Found one song'
        self.tasks.verify_browser_task(self.id)
        self.tasks.stop()
        self.control._generation += 1
        resumed = json.loads(self.tasks.resume_browser_task(self.id)[0])
        self.assertEqual(resumed['current_step'], 1)
        self.assertTrue(resumed['steps'][0]['verified'])
        self.assertEqual(len(resumed['evidence']), 1)
        self.assertIsNone(self.tasks.observation)
        self.assertEqual(len(self.bridge.actions), 1)

    def test_resume_requires_control_and_refuses_uncertain_dispatch(self):
        self.tasks.stop()
        self.control.enabled = False
        with self.assertRaisesRegex(ToolError, 'Enable Computer control'):
            self.tasks.resume_browser_task(self.id)
        self.control.enabled = True
        self.tasks.task['pending_dispatch'] = True
        self.tasks._save()
        with self.assertRaisesRegex(ToolError, 'uncertain action'):
            self.tasks.resume_browser_task(self.id)

    def test_denied_action_cannot_resume_via_another_tool(self):
        self.reviews.callback = lambda: (_ for _ in ()).throw(ToolError('Denied'))
        with self.assertRaises(ToolError):
            self.act()
        with self.assertRaisesRegex(ToolError, 'denied'):
            self.tasks.resume_browser_task(self.id)
        self.assertFalse(self.bridge.actions)

    def test_navigation_observation_is_single_use_and_requires_verification(self):
        observed = self.observe()
        self.tasks.navigate_browser_task(self.id, observed, 'https://fixture.test/results', 'Open the requested results')
        self.assertEqual(self.bridge.actions[-1]['url'], 'https://fixture.test/results')
        self.assertFalse(self.tasks.task['pending_dispatch'])
        self.assertIn('Not verified', self.tasks.verify_browser_task(self.id)[0])
        with self.assertRaises(ToolError):
            self.tasks.navigate_browser_task(self.id, observed, 'https://fixture.test/again', 'Repeat navigation')

    def test_continuation_exists_only_for_an_active_permitted_task(self):
        self.assertIn('0/1 steps verified', self.tasks.continuation())
        self.control.enabled = False
        self.assertEqual(self.tasks.continuation(), '')
        self.control.enabled = True
        self.tasks.stop()
        self.assertEqual(self.tasks.continuation(), '')

    def test_list_tasks_exposes_saved_progress_without_page_or_fill_contents(self):
        self.act()
        text, _ = self.tasks.list_browser_tasks()
        self.assertIn(self.id, text)
        self.assertNotIn('fixture query', text)
        self.assertNotIn('Song search', text)

