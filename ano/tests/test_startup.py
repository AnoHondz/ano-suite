import unittest
from unittest.mock import Mock

from ano.plugins.startup import Startup
from ano.tools import ToolError
from ano.execution import RunBudget


class StartupTests(unittest.TestCase):
    def test_only_ano_is_changed_and_other_apps_are_not_claimed_disabled(self):
        enable, disable = Mock(), Mock()
        plugin = Startup(enable=enable, disable=disable)
        result, _ = plugin.set_ano_autostart(True)
        enable.assert_called_once()
        disable.assert_not_called()
        self.assertIn('Other applications were not changed', result)
        with self.assertRaises(ToolError):
            plugin.set_ano_autostart('true')

    def test_listing_and_settings_open_do_not_change_any_startup_entries(self):
        enable, disable, opened = Mock(), Mock(), Mock()
        plugin = Startup(entries=lambda: ['Example app'], enable=enable, disable=disable, open_path=opened)
        self.assertIn('Example app', plugin.list_startup_apps()[0])
        self.assertIn('did not change', plugin.open_startup_settings()[0])
        opened.assert_called_once_with('ms-settings:startupapps')
        enable.assert_not_called()
        disable.assert_not_called()

    def test_local_task_deadline_cannot_be_reset_by_a_successful_step(self):
        now = [0]
        tools = Mock()
        tools.allowed.return_value = True
        tools.free_roam.return_value = False
        budget = RunBudget(tools, max_seconds=10, clock=lambda: now[0])
        self.assertTrue(budget.next())
        now[0] = 8
        budget.record('read_file', {}, False)
        now[0] = 11
        self.assertFalse(budget.next())
        self.assertIn('time limit', budget.reason)
