from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock
import json
from ano.sports_mail import resume_configured_service


class MailServiceRecoveryTests(TestCase):
    def test_opted_in_service_resumes_existing_cursor_without_enable_send_or_bootstrap(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp);launch=Mock()
            for name,value in [('config.json',{'enabled':True,'token':'fixture'}),('cursor.json',{'validity':'123','last_uid':45})]:
                (path/name).write_text(json.dumps(value))
            before=(path/'cursor.json').read_bytes()
            self.assertTrue(resume_configured_service(path,launch))
            self.assertEqual(launch.call_args.args[0][-3:],['-m','ano.sports_mail','serve'])
            self.assertIn('.venv',launch.call_args.args[0][0])
            self.assertEqual(before,(path/'cursor.json').read_bytes())

    def test_disabled_uninitialized_and_bad_cursor_never_launch(self):
        for cfg,cursor in [({},{}),({'enabled':False,'token':'fixture'},{'validity':'1','last_uid':4}),
                           ({'enabled':True,'token':'fixture'},{}),({'enabled':True,'token':'fixture'},{'validity':'1','last_uid':-1})]:
            with TemporaryDirectory() as tmp:
                path=Path(tmp);launch=Mock()
                (path/'config.json').write_text(json.dumps(cfg));(path/'cursor.json').write_text(json.dumps(cursor))
                self.assertFalse(resume_configured_service(path,launch));launch.assert_not_called()
