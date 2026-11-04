from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ano.desktop_native import WindowsDesktop
from ano.tools import ToolError


class Pattern:
    IsReadOnly = False
    def __init__(self, value=''):
        self.Value = value
        self.SetValue = Mock(side_effect=self.set)
    def set(self, value, waitTime=0):
        self.Value = value
        return True


class Control:
    IsPassword = False
    Name = 'What do you want to play?'
    ControlTypeName = 'ComboBoxControl'
    NativeWindowHandle = 0
    def __init__(self, parent=None):
        self.parent = parent
        self.pattern = Pattern()
        self.children = []
    def GetRuntimeId(self): return [1, 22]
    def GetParentControl(self): return self.parent
    def GetValuePattern(self): return self.pattern
    def GetChildren(self): return self.children


class NativeTextTests(unittest.TestCase):
    def setUp(self):
        self.root = Control()
        self.root.NativeWindowHandle = 17
        self.root.ControlTypeName = 'PaneControl'
        self.edit = Control(self.root)
        self.root.children = [self.edit]
        self.auto = SimpleNamespace(UIAutomationInitializerInThread=nullcontext,
                                    GetFocusedControl=lambda:self.edit, ControlFromHandle=lambda hwnd:self.root)
        self.patch = patch.dict('sys.modules', {'uiautomation':self.auto})
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.backend = object.__new__(WindowsDesktop)
        self.backend.allow_terminals = False
        self.backend.user = Mock()
        self.backend.user.GetForegroundWindow.return_value = 17
        self.backend.focus = Mock()
        self.target = {'hwnd':17}
        self.backend.describe = Mock(return_value=self.target)
        self.backend._send = Mock()

    def perform(self, action='set_text', **args):
        return self.backend.perform(self.target,(100,100),action,
                                    {'text':'Oliver Mtukudzi','_element_runtime_id':[1,22],**args},lambda:True)

    def test_observation_reports_editable_combo_without_reading_its_value(self):
        fields = self.backend.inspect_editables(self.target)
        self.assertEqual(fields[0]['label'],'What do you want to play?')
        self.assertEqual(fields[0]['role'],'ComboBoxControl')
        self.assertNotIn('Value',fields[0])

    def test_targeted_text_sets_and_reads_back_exact_value(self):
        receipt = self.perform()
        self.assertTrue(receipt['text_verified'])
        self.assertEqual(self.edit.pattern.Value,'Oliver Mtukudzi')
        self.backend._send.assert_not_called()

    def test_identical_value_is_verified_without_duplicate_input(self):
        self.edit.pattern.Value='Oliver Mtukudzi'
        receipt=self.perform()
        self.assertTrue(receipt['text_verified'])
        self.assertFalse(receipt['text_dispatched'])
        self.edit.pattern.SetValue.assert_not_called()

    def test_ignored_value_write_is_reported_unverified_and_not_retried(self):
        self.edit.pattern.SetValue.side_effect=lambda *args,**kwargs:False
        receipt=self.perform()
        self.assertFalse(receipt['text_verified'])
        self.edit.pattern.SetValue.assert_called_once()
        self.backend._send.assert_not_called()

    def test_password_readonly_stale_and_foreign_fields_are_rejected(self):
        self.edit.IsPassword=True
        with self.assertRaises(ToolError):self.perform()
        self.edit.IsPassword=False
        self.edit.pattern.IsReadOnly=True
        with self.assertRaises(ToolError):self.perform()
        self.edit.pattern.IsReadOnly=False
        with self.assertRaises(ToolError):self.perform(_element_runtime_id=[99])
        self.root.NativeWindowHandle=99
        with self.assertRaises(ToolError):self.perform()
        self.edit.pattern.SetValue.assert_not_called()

    def test_plain_type_does_not_dispatch_when_focus_is_not_an_editor(self):
        self.edit.ControlTypeName='ButtonControl'
        with self.assertRaisesRegex(ToolError,'No text was sent'):
            self.perform('type')
        self.backend._send.assert_not_called()

    def test_plain_type_that_was_ignored_is_not_claimed_verified(self):
        receipt=self.perform('type')
        self.assertFalse(receipt['text_verified'])
        self.backend._send.assert_called_once()

    def test_plain_type_verifies_field_change_when_packets_are_received(self):
        self.backend._send.side_effect=lambda events:setattr(self.edit.pattern,'Value','Oliver Mtukudzi')
        self.assertTrue(self.perform('type')['text_verified'])

    def test_user_stop_prevents_value_write(self):
        with self.assertRaises(ToolError):
            self.backend.perform(self.target,(100,100),'set_text',
                                 {'text':'query','_element_runtime_id':[1,22]},lambda:False)
        self.edit.pattern.SetValue.assert_not_called()

    def test_field_relabeling_does_not_reuse_a_runtime_id_for_a_new_purpose(self):
        with self.assertRaisesRegex(ToolError,'label or role changed'):
            self.perform(_element_label='Different field')
        self.edit.pattern.SetValue.assert_not_called()

    def test_delayed_text_readback_is_verified_without_duplicate_packets(self):
        reads=[0]
        def focused():
            reads[0]+=1
            if reads[0]>=4:self.edit.pattern.Value='Oliver Mtukudzi'
            return self.edit
        self.auto.GetFocusedControl=focused
        receipt=self.perform('type')
        self.assertTrue(receipt['text_verified'])
        self.backend._send.assert_called_once()

    def test_partial_sendinput_is_reported_as_uncertain_not_a_completed_write(self):
        from ano.desktop_native import INPUT
        self.backend.user.SendInput.return_value=1
        with self.assertRaisesRegex(ToolError,'only part'):
            WindowsDesktop._send(self.backend,[INPUT(),INPUT()])

    def test_surface_inspection_reads_rows_without_field_values_or_password_labels(self):
        row=Control(self.root);row.Name='1 Todii Oliver Mtukudzi Tuku Music';row.ControlTypeName='DataItemControl'
        password=Control(self.root);password.IsPassword=True;password.Name='Password label must stay hidden'
        self.edit.pattern.Value='private textbox value'
        self.root.children.extend([row,password])
        surface=self.backend.inspect_surface(self.target)
        text=str(surface['accessibility'])
        self.assertIn('1 Todii Oliver Mtukudzi Tuku Music',text)
        self.assertNotIn('private textbox value',text)
        self.assertNotIn('Password label',text)
        self.assertFalse(surface['truncated'])

    def test_native_button_invoke_is_bound_and_never_claims_completion(self):
        button=Control(self.root);button.Name='My Playlist #11';button.ControlTypeName='ButtonControl'
        button.GetRuntimeId=lambda:[1,33]
        pattern=SimpleNamespace(Invoke=Mock(return_value=True))
        button.GetInvokePattern=lambda:pattern
        self.root.children.append(button)
        args={'_element_runtime_id':[1,33],'_element_label':'My Playlist #11','_element_role':'ButtonControl'}
        receipt=self.backend.perform(self.target,(100,100),'invoke',args,lambda:True)
        pattern.Invoke.assert_called_once()
        self.assertFalse(receipt['action_verified'])
        self.backend._send.assert_not_called()
        button.Name='Different destination'
        with self.assertRaisesRegex(ToolError,'changed'):
            self.backend.perform(self.target,(100,100),'invoke',args,lambda:True)
        self.assertEqual(pattern.Invoke.call_count,1)

    def test_uncertain_native_invoke_is_not_repeated(self):
        button=Control(self.root);button.Name='Add to playlist';button.ControlTypeName='MenuItemControl'
        button.GetRuntimeId=lambda:[1,33]
        pattern=SimpleNamespace(Invoke=Mock(side_effect=RuntimeError('Unknown result')))
        button.GetInvokePattern=lambda:pattern
        self.root.children.append(button)
        receipt=self.backend.perform(self.target,(100,100),'invoke',
            {'_element_runtime_id':[1,33],'_element_label':'Add to playlist','_element_role':'MenuItemControl'},lambda:True)
        self.assertTrue(receipt['action_dispatch_uncertain'])
        pattern.Invoke.assert_called_once()

    def test_broken_container_label_does_not_hide_dialog_fields(self):
        class BrokenLabel(Control):
            @property
            def Name(self): raise RuntimeError('UIA label unavailable')
        container = BrokenLabel(self.root)
        container.ControlTypeName = 'PaneControl'
        self.edit.parent = container
        self.edit.Name = 'File name:'
        container.children = [self.edit]
        self.root.children = [container]
        surface = self.backend.inspect_surface(self.target)
        self.assertTrue(surface['truncated'])
        self.assertIn('File name:', str(surface['editables']))
        self.assertTrue(self.perform()['text_verified'])

    def test_broken_editor_does_not_abort_other_editable_labels(self):
        class BrokenLabel(Control):
            @property
            def Name(self): raise RuntimeError('UIA label unavailable')
        self.root.children.insert(0, BrokenLabel(self.root))
        fields = self.backend.inspect_editables(self.target)
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]['label'], self.edit.Name)

    def test_unknown_privacy_state_never_exposes_descendants(self):
        class UnknownPrivacy(Control):
            @property
            def IsPassword(self): raise RuntimeError('Unknown privacy state')
        private = UnknownPrivacy(self.root)
        private.children = [self.edit]
        self.root.children = [private]
        surface = self.backend.inspect_surface(self.target)
        self.assertTrue(surface['truncated'])
        self.assertEqual(surface['editables'], [])
        self.assertEqual(self.backend.inspect_editables(self.target), [])

    def test_bad_container_runtime_id_does_not_hide_verified_save_button(self):
        container = Control(self.root)
        container.ControlTypeName = 'PaneControl'
        container.GetRuntimeId = Mock(side_effect=RuntimeError('No runtime ID'))
        button = Control(container)
        button.Name, button.ControlTypeName = 'Save', 'ButtonControl'
        button.GetRuntimeId = lambda: [1, 44]
        pattern = SimpleNamespace(Invoke=Mock(return_value=True))
        button.GetInvokePattern = lambda: pattern
        container.children = [button]
        self.root.children = [container]
        receipt = self.backend.perform(self.target, (100, 100), 'invoke',
            {'_element_runtime_id': [1, 44], '_element_label': 'Save', '_element_role': 'ButtonControl'}, lambda: True)
        self.assertFalse(receipt['action_verified'])
        pattern.Invoke.assert_called_once()


if __name__=='__main__':unittest.main()
