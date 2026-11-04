import base64
import json
from types import SimpleNamespace
from threading import Thread
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage, QColor

from ano.desktop import DesktopControl, DesktopFrames, validate_input
from ano.desktop_native import WindowsDesktop, parse_keys
from ano.review import Reviews
from ano.screen import PreviewProvider, encode_image
from ano.tools import ToolError
from ano.tool_media import ToolImage


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def spin(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_nonce_approval_and_cancel(self):
        review = Reviews(lambda: True, timeout=1)
        results = []
        def ask():
            try:
                review.request(title='Synthetic action')
                results.append('approved')
            except ToolError:
                results.append('cancelled')
        worker = Thread(target=ask)
        worker.start()
        self.spin(lambda: review.ready)
        review.approve('wrong-nonce')
        self.assertEqual(results, [])
        review.approve(review.review['id'])
        worker.join(1)
        self.assertEqual(results, ['approved'])
        worker = Thread(target=ask)
        worker.start()
        self.spin(lambda: review.ready)
        review.cancel()
        worker.join(1)
        self.assertEqual(results, ['approved', 'cancelled'])
        self.app.processEvents()

    def test_timeout_and_blocked_state_cannot_approve(self):
        review = Reviews(lambda: False, timeout=.01)
        with self.assertRaises(ToolError):
            review.request(title='Blocked')
        review = Reviews(lambda: True, timeout=.01)
        with self.assertRaises(ToolError):
            review.request(title='Timed out')
        self.app.processEvents()
        self.assertFalse(review.ready)


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        image = QImage(200, 100, QImage.Format_RGB32)
        image.fill(QColor('white'))
        self.target = dict(hwnd=1, pid=50, title='Synthetic editor', app='editor.exe', rect=(0, 0, 200, 100))
        self.backend = Mock()
        self.backend.describe.return_value = self.target
        self.review = Mock()
        self.now = 0
        self.allowed = True
        frames = SimpleNamespace(take=Mock(return_value=(self.target, (200, 100), encode_image(image))))
        self.control = DesktopControl(self.backend, self.review, lambda: self.allowed, PreviewProvider(), frames, lambda: self.now)
        self.control._enabled = True

    def test_approved_observation_is_image_and_input_consumes_it(self):
        result, note = self.control.observe(1)
        self.assertIsInstance(result, ToolImage)
        self.assertEqual(result.claude()[1]['type'], 'image')
        self.assertEqual(base64.b64decode(result.claude()[1]['source']['data']), result.data)
        observation = self.control._observation['id']
        self.control.input(observation, 'Write the requested example', 'type', text='hello')
        self.backend.perform.assert_called_once()
        with self.assertRaises(ToolError):
            self.control.input(observation, 'Repeat', 'type', text='hello')
        self.assertEqual(self.review.request.call_count, 2)

    def test_targeted_text_uses_observed_field_without_exposing_runtime_binding(self):
        self.backend.inspect_editables.return_value=[{'id':'1','label':'Search','role':'ComboBoxControl','runtime_id':[1,22]}]
        self.backend.perform.return_value={'text_verified':True,'text_dispatched':True}
        image,_=self.control.observe(1)
        metadata=json.loads(image.text)
        self.assertEqual(metadata['editable_elements'][0]['label'],'Search')
        self.assertNotIn('runtime_id',metadata['editable_elements'][0])
        result,note=self.control.input(metadata['observation_id'],'Set the requested search','set_text',element_id='1',text='query')
        self.assertEqual(self.backend.perform.call_args.args[3]['_element_runtime_id'],[1,22])
        self.assertIn('Verified text',note)
        self.assertTrue(json.loads(result.split('\n')[0])['text_verified'])

    def test_unobserved_text_target_is_not_dispatched(self):
        self.control.observe(1)
        with self.assertRaisesRegex(ToolError,'fresh observation'):
            self.control.input(self.control._observation['id'],'Set text','set_text',element_id='unknown',text='query')
        self.backend.perform.assert_not_called()

    def test_unverified_typing_receipt_survives_followup_capture_failure(self):
        self.control._permission_mode='full'
        image,_=self.control.observe(1)
        self.backend.perform.return_value={'text_verified':False,'text_dispatched':True,'verification':'No matching field value.'}
        self.control.frames.take.side_effect=ToolError('Capture unavailable')
        text,note=self.control.input(json.loads(image.text)['observation_id'],'Enter query','type',text='query')
        self.assertIn('"text_verified": false',text)
        self.assertIn('not verified',note)
        self.assertIn('Do not repeat',text)

    def test_read_only_result_inspection_is_available_after_the_input_cap(self):
        self.control._steps=50
        self.backend.inspect_surface.return_value={'accessibility':[{'role':'DataItemControl','label':'1 Todii Oliver Mtukudzi','depth':2}],
                                                   'truncated':False,'scope':'Current playlist rows'}
        text,_=self.control.run('desktop_read',{'window_id':1})
        self.assertIn('Todii',text)
        self.backend.perform.assert_not_called()
        self.assertIsNone(self.control._observation)

    def test_stop_during_accessibility_inspection_discards_the_observation(self):
        self.backend.inspect_surface.side_effect=lambda target:self.control.stop()
        with self.assertRaisesRegex(ToolError,'during control inspection'):
            self.control.observe(1)
        self.assertIsNone(self.control._observation)

    def test_observed_button_invocation_uses_bound_control_and_preserves_uncertainty(self):
        self.backend.inspect_surface.return_value={'editables':[], 'actions':[{'id':'3','label':'My Playlist #11','role':'ButtonControl','runtime_id':[1,33]}]}
        self.backend.perform.return_value={'action_dispatched':True,'action_verified':False,'action_dispatch_uncertain':True}
        image,_=self.control.observe(1)
        self.assertNotIn('runtime_id',json.loads(image.text)['actionable_elements'][0])
        text,note=self.control.input(json.loads(image.text)['observation_id'],'Open the destination playlist','invoke',element_id='3')
        self.assertEqual(self.backend.perform.call_args.args[3]['_element_runtime_id'],[1,33])
        self.assertIn('uncertain',note)
        self.assertFalse(json.loads(text.split('\n')[0])['action_verified'])

    def test_denial_stale_frame_stop_and_window_change_prevent_input(self):
        self.control.observe(1)
        identifier = self.control._observation['id']
        self.review.request.side_effect = ToolError('Denied')
        with self.assertRaises(ToolError):
            self.control.input(identifier, 'Test', 'click', x=10, y=10)
        self.backend.perform.assert_not_called()
        self.review.request.side_effect = None
        self.control.observe(1)
        self.now = 61
        with self.assertRaises(ToolError):
            self.control.input(self.control._observation['id'], 'Test', 'key', keys='enter')
        self.control.stop()
        with self.assertRaises(ToolError):
            self.control.run('desktop_observe', {'window_id': 1})
        self.backend.perform.assert_not_called()

    def test_stop_during_approval_prevents_native_input(self):
        self.control.observe(1)
        identifier = self.control._observation['id']
        self.review.request.side_effect = lambda **kwargs: self.control.stop()
        with self.assertRaises(ToolError):
            self.control.input(identifier, 'Test', 'type', text='hello')
        self.backend.perform.assert_not_called()

    def test_coordinate_text_and_key_validation(self):
        for action, arguments in [('click', {'x': -1, 'y': 5}), ('drag', {'x': 1, 'y': 1, 'end_x': 200, 'end_y': 0}),
                                  ('type', {'text': 'x' * 2001}), ('key', {'keys': 'ctrl+alt+delete'}),
                                  ('key', {'keys': 'win+r'}), ('scroll', {'x': 1, 'y': 1, 'amount': 99})]:
            with self.assertRaises(ToolError):
                validate_input(action, arguments, (200, 100))
        self.assertEqual(parse_keys('ctrl+a'), [0x11, 65])
        validate_input('drag', {'x': 1, 'y': 2, 'end_x': 20, 'end_y': 30}, (200, 100))

    def test_full_session_skips_views_and_inputs_but_retains_single_use_and_stop(self):
        self.control._available = True
        self.control.startSession('full')
        self.assertEqual(self.control.permissionMode, 'full')
        self.control.observe(1)
        identifier = self.control._observation['id']
        result, _ = self.control.input(identifier, 'Type the requested text', 'type', text='hello')
        self.assertIsInstance(result, ToolImage)
        next_id = json.loads(result.text)['observation_id']
        self.assertNotEqual(next_id, identifier)
        self.assertTrue(json.loads(result.text)['action_performed'])
        self.assertEqual(self.control._observation['id'], next_id)
        self.assertEqual(self.control.frames.take.call_count, 2)
        self.review.request.assert_not_called()
        self.backend.perform.assert_called_once()
        with self.assertRaises(ToolError):
            self.control.input(identifier, 'Repeat', 'type', text='hello')
        self.control.stop()
        self.assertEqual(self.control.permissionMode, 'reviewed')
        with self.assertRaises(ToolError):
            self.control.request_review(title='Stopped action')

    def test_mode_change_invalidates_old_observation_and_blocked_session_cannot_enable(self):
        self.control._available = True
        self.control.observe(1)
        identifier = self.control._observation['id']
        self.control.startSession('full')
        with self.assertRaises(ToolError):
            self.control.input(identifier, 'Old view', 'type', text='hello')
        self.control.startSession('reviewed')
        self.control.observe(1)
        self.assertEqual(self.review.request.call_count, 2)
        self.control.stop()
        self.allowed = False
        self.control.startSession('full')
        self.assertFalse(self.control.enabled)

    def test_roaming_removes_input_cap_until_stop(self):
        self.control._available=True
        self.control._steps=50
        self.control.run('desktop_observe',{'window_id':1})
        with self.assertRaises(ToolError): self.control.run('desktop_input',{'observation_id':self.control._observation['id'],'purpose':'Test the input cap','action':'key','keys':'enter'})
        self.control.startSession('roam')
        self.assertTrue(self.control.freeRoam)
        self.assertTrue(self.backend.allow_terminals)
        self.control._steps=500
        image,_=self.control.run('desktop_observe',{'window_id':1})
        image,_=self.control.run('desktop_input',{'observation_id':json.loads(image.text)['observation_id'],
                                'purpose':'Use the requested Windows shortcut','action':'key','keys':'win+d'})
        self.assertIsInstance(image,ToolImage)
        self.assertEqual(self.control._steps,501)
        self.control.stop()
        self.assertFalse(self.control.freeRoam)
        self.assertFalse(self.backend.allow_terminals)
        self.assertEqual(self.control.permissionMode, 'reviewed')
        self.control.startSession('invalid')
        self.assertFalse(self.control.enabled)

    def test_full_mode_followup_capture_failure_does_not_replay_or_leave_old_observation(self):
        self.control._permission_mode = 'full'
        self.control.observe(1)
        identifier = self.control._observation['id']
        self.control.frames.take.side_effect = ToolError('App navigated')
        result, _ = self.control.input(identifier, 'Test', 'click', x=10, y=10)
        self.assertIn('Action performed', result)
        self.assertIn('Do not repeat', result)
        self.assertIsNone(self.control._observation)
        self.backend.perform.assert_called_once()

    def test_stop_after_input_prevents_automatic_capture(self):
        self.control._permission_mode = 'full'
        self.control.observe(1)
        identifier = self.control._observation['id']
        self.backend.perform.side_effect = lambda *args: self.control.stop()
        result, _ = self.control.input(identifier, 'Test', 'click', x=10, y=10)
        self.assertIn('Action performed', result)
        self.assertEqual(self.control.frames.take.call_count, 1)
        self.assertIsNone(self.control._observation)


class CaptureAndFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_terminal_permission_is_separate_from_security_windows(self):
        backend=object.__new__(WindowsDesktop)
        backend.allow_terminals=False
        self.assertTrue(backend.excluded('powershell.exe'))
        backend.allow_terminals=True
        self.assertFalse(backend.excluded('powershell.exe'))
        self.assertTrue(backend.excluded('credentialuibroker.exe'))
        self.assertEqual(parse_keys('win+r',allow_windows=True),[0x5b,ord('R')])
        with self.assertRaises(ToolError): parse_keys('win+r')

    def test_hidden_panel_capture_has_no_compositor_wait_and_pause_still_blocks(self):
        backend = Mock()
        frames = DesktopFrames(backend, lambda: True)
        request = dict(cancelled=False)
        with patch('ano.desktop.QTimer.singleShot') as timer:
            frames._hide(request)
            self.assertEqual(timer.call_args.args[0], 0)
        denied = DesktopFrames(backend, lambda: False)
        from threading import Event
        request = dict(cancelled=False, event=Event())
        denied._hide(request)
        self.assertTrue(request['event'].is_set())
        backend.snapshot.assert_not_called()

    def test_already_focused_window_has_no_sleep_and_wrong_focus_still_blocks(self):
        backend = object.__new__(WindowsDesktop)
        backend.user = Mock()
        backend.kernel = Mock()
        backend.kernel.GetCurrentThreadId.return_value = 1
        backend.user.GetWindowThreadProcessId.return_value = 2
        target = dict(hwnd=17)
        backend.describe = Mock(return_value=target)
        backend.user.GetForegroundWindow.return_value = 17
        with patch('ano.desktop_native.time.sleep') as sleep:
            backend.focus(target)
            sleep.assert_not_called()
        backend.user.SetForegroundWindow.assert_not_called()
        backend.user.GetForegroundWindow.return_value = 99
        with self.assertRaises(ToolError):
            backend.focus(target)
