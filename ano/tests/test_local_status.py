from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import Mock
from ano.plugins.local_status import LocalStatus


class LocalStatusTests(TestCase):
    def test_checks_only_read_only_local_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            query = Mock(side_effect=[{'models': [{'name': 'example'}]}, {'models': []}])
            text, _ = LocalStatus(Path(directory), query).run('local_ai_status', {})
            self.assertEqual([call.args[0] for call in query.call_args_list], ['/api/tags', '/api/ps'])
            self.assertIn('reachable on this PC', text)
            self.assertIn('No model generation or paid API request', text)

    def test_server_failure_is_specific_without_generation_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            query = Mock(side_effect=OSError('Disconnected'))
            text, _ = LocalStatus(Path(directory), query).run('local_ai_status', {})
            self.assertIn('could not be verified', text)
            query.assert_called_once_with('/api/tags')
