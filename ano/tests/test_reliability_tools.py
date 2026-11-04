from unittest import TestCase
from unittest.mock import Mock,patch
from ano.plugins.reliability import ReliabilityTools
from ano.tools import Toolbox,OutboundReview,local_command


class ReliabilityToolTests(TestCase):
    def test_dashboard_is_hidden_from_models_and_reachable_only_by_local_command(self):
        outbound=OutboundReview(Mock());service=Mock()
        plugin=ReliabilityTools(service,authorized=outbound.is_local_command)
        tools=Toolbox(lambda:[],plugins=[plugin],outbound=outbound)
        self.assertNotIn('open_reliability_dashboard',[v['name'] for v in tools.definitions()])
        with patch('ano.plugins.reliability.subprocess.Popen') as launch:
            self.assertTrue(tools.run('open_reliability_dashboard',{})[2]);launch.assert_not_called()
            with local_command(tools):result=tools.run('open_reliability_dashboard',{})
            self.assertFalse(result[2],result[0]);launch.assert_called_once()
            self.assertEqual(launch.call_args.args[0][-2:],['-m','ano.reliability_dashboard'])

    def test_unknown_parameters_or_locked_state_never_reach_external_checks(self):
        service=Mock();plugin=ReliabilityTools(service)
        from ano.tools import ToolError
        with self.assertRaises(ToolError):plugin.run('reliability_status',{'verify_broker':True,'trade':True})
        plugin.allowed=lambda:False
        with self.assertRaises(ToolError):plugin.run('reliability_status',{'verify_broker':True})
        service.snapshot.assert_not_called()
