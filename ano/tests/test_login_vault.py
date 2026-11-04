import json
from pathlib import Path
import tempfile
from threading import Thread
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication
from ano.login_vault import LoginStore, LoginVault, origin, SERVICE
from ano.tools import ToolError


class FakeCredentialBackend:
    def __init__(self): self.data = {}
    def get_password(self, service, account): return self.data.get((service, account))
    def set_password(self, service, account, value): self.data[(service, account)] = value
    def delete_password(self, service, account): self.data.pop((service, account), None)


class VaultTests(unittest.TestCase):
    def test_two_store_instances_do_not_lose_concurrent_saves(self):
        backend = FakeCredentialBackend()
        stores = [LoginStore(backend), LoginStore(backend)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: stores[i % 2].save('Example ' + str(i), 'website', 'https://example.com', 'someone', 'dummy'), range(20)))
        self.assertEqual(len(stores[0].entries()), 20)
    def test_many_logins_fit_windows_blob_limits(self):
        backend = FakeCredentialBackend()
        store = LoginStore(backend)
        identifiers = [store.save('Example ' + str(i), 'website', 'https://example.com', 'someone', 'dummy') for i in range(45)]
        self.assertEqual(len(store.entries()), 45)
        self.assertTrue(all(len(value.encode('utf-16-le')) <= 2400 for value in backend.data.values()))
        store.delete(identifiers[20])
        self.assertEqual(len(store.entries()), 44)
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.store = LoginStore(FakeCredentialBackend())
        self.identifier = self.store.save('Example', 'website', 'https://example.com/login', 'person@example.com', 'dummy-test-secret')
        self.review = Mock()
        self.backend = Mock()
        self.bridge = Mock()
        self.bridge.call.side_effect = lambda browser, action, **args: ({'origin': 'https://example.com', 'documentId': 'doc1'} if action == 'login_context' else {'filled': True})
        self.allowed = True
        self.vault = LoginVault(self.store, self.review, self.backend, lambda: self.allowed, Mock(), self.bridge)

    def use(self, **kwargs):
        results, errors = [], []
        def run():
            try: results.append(self.vault.use(self.identifier, **kwargs))
            except Exception as error: errors.append(error)
        worker = Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 2
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        worker.join(.2)
        self.app.processEvents()
        self.assertFalse(worker.is_alive())
        return results, errors

    def test_store_and_model_listing_never_return_password_or_username(self):
        index = self.store.backend.data[(SERVICE, 'index')]
        self.assertNotIn('dummy-test-secret', index)
        result, _ = self.vault.run('list_saved_logins', {})
        self.assertNotIn('dummy-test-secret', result)
        self.assertNotIn('person@example.com', result)
        self.store.delete(self.identifier)
        self.assertEqual(self.store.entries(), [])

    def test_origin_normalization_is_exact_https(self):
        self.assertEqual(origin('https://EXAMPLE.com:443/login'), 'https://example.com')
        self.assertNotEqual(origin('https://example.com.evil.test'), 'https://example.com')
        self.assertNotEqual(origin('https://sub.example.com'), 'https://example.com')
        for value in ('http://example.com', 'https://me:secret@example.com', 'javascript:alert(1)', 'https://example.com:bad'):
            with self.assertRaises(ToolError): origin(value)

    def test_denial_and_origin_mismatch_never_load_secret(self):
        original = self.store.secret
        self.store.secret = Mock(wraps=original)
        self.review.request.side_effect = ToolError('Denied')
        results, errors = self.use(tab_id='chrome:3')
        self.assertTrue(errors)
        self.store.secret.assert_not_called()
        self.assertEqual(self.bridge.call.call_count, 1)
        self.review.request.side_effect = None
        self.bridge.call.return_value = {'origin': 'https://evil.test', 'documentId': 'doc1'}
        self.bridge.call.side_effect = None
        results, errors = self.use(tab_id='chrome:3')
        self.assertTrue(errors)
        self.store.secret.assert_not_called()

    def test_approval_fills_once_and_keeps_private_mode_until_done(self):
        events = []
        original = self.store.secret
        self.store.secret = lambda row: events.append('secret') or original(row)
        self.review.request.side_effect = lambda **kwargs: events.append('approval')
        results, errors = self.use(tab_id='chrome:3')
        self.assertFalse(errors)
        self.assertEqual(events, ['approval', 'secret'])
        self.assertNotIn('dummy-test-secret', str(results))
        fill = self.bridge.call.call_args
        self.assertEqual(fill.args, ('chrome', 'fill_login'))
        self.assertEqual(fill.kwargs['origin'], 'https://example.com')
        self.assertEqual(fill.kwargs['documentId'], 'doc1')
        self.assertTrue(self.vault.private)
        self.assertTrue(self.vault.hold)
        self.vault.done()
        self.assertFalse(self.vault.private)

    def test_pause_after_approval_and_errors_cannot_leak_secret(self):
        original = self.store.secret
        self.store.secret = Mock(wraps=original)
        self.review.request.side_effect = lambda **kwargs: setattr(self, 'allowed', False)
        results, errors = self.use(tab_id='chrome:3')
        self.assertTrue(errors)
        self.store.secret.assert_not_called()
        self.allowed = True
        self.review.request.side_effect = None
        self.bridge.call.side_effect = lambda browser, action, **args: ({'origin': 'https://example.com', 'documentId': 'doc1'} if action == 'login_context' else (_ for _ in ()).throw(ToolError('dummy-test-secret')))
        results, errors = self.use(tab_id='chrome:3')
        self.assertTrue(errors)
        self.assertNotIn('dummy-test-secret', str(errors))

    def test_native_app_binding_and_password_target_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'example.exe'
            app.touch()
            self.identifier = self.store.save('Native', 'app', str(app), 'someone', 'dummy-test-secret')
            self.backend.password_target.return_value = ({'path': str(app.resolve())}, [1, 2, 3])
            results, errors = self.use(window_id=20)
            self.assertFalse(errors)
            self.backend.fill_password.assert_called_once()
            self.assertEqual(self.backend.fill_password.call_args.args[1], [1, 2, 3])
            self.assertNotIn('dummy-test-secret', str(results))

    def test_sign_in_submission_requires_supported_form_and_explicit_review(self):
        self.bridge.call.side_effect = lambda browser, action, **args: (
            {'origin':'https://example.com','documentId':'doc1','canSubmit':True} if action=='login_context'
            else {'filled':True,'submitted':True})
        results, errors = self.use(tab_id='chrome:3', submit=True)
        self.assertFalse(errors)
        self.assertEqual(self.review.request.call_args.kwargs['button'], 'Sign in once')
        self.assertTrue(self.bridge.call.call_args.kwargs['submit'])
        self.assertNotIn('dummy-test-secret', str(results))
        self.assertIn('not yet verified', str(results))
        self.assertTrue(self.vault.hold)

    def test_unsupported_sign_in_form_never_loads_secret(self):
        self.store.secret = Mock()
        _, errors = self.use(tab_id='chrome:3', submit=True)
        self.assertTrue(errors)
        self.store.secret.assert_not_called()

    def test_uncertain_sign_in_submission_is_not_retried_or_exposed(self):
        self.bridge.call.side_effect = lambda browser, action, **args: (
            {'origin':'https://example.com','documentId':'doc1','canSubmit':True} if action=='login_context'
            else {'filled':True,'submitted':False})
        _, errors = self.use(tab_id='chrome:3', submit=True)
        self.assertIn('do not automatically submit again', str(errors))
        self.assertNotIn('dummy-test-secret', str(errors))
        self.assertEqual(self.bridge.call.call_count, 2)
