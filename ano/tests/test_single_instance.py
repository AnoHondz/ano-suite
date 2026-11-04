import tempfile
import time
import unittest

from PySide6.QtCore import QCoreApplication
from ano.single_instance import SingleInstance


class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def pump(self, predicate, seconds=2):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

    def test_second_launch_is_refused_and_wakes_the_first(self):
        # Closed inside the block so the lock file is released before the temp dir is removed.
        with tempfile.TemporaryDirectory() as directory:
            first, second = SingleInstance(directory), SingleInstance(directory)
            try:
                self.assertTrue(first.acquire())
                woken = []
                first.activated.connect(lambda: woken.append(True))
                self.assertFalse(second.acquire())  # another copy is already running
                self.pump(lambda: woken)
                self.assertTrue(woken, 'the running instance should be raised instead of a second copy starting')
            finally:
                first.close()
                second.close()

    def test_lock_frees_after_close_so_a_fresh_copy_can_start(self):
        with tempfile.TemporaryDirectory() as directory:
            first = SingleInstance(directory)
            self.assertTrue(first.acquire())
            first.close()
            nextone = SingleInstance(directory)
            try:
                self.assertTrue(nextone.acquire())
            finally:
                nextone.close()

    def test_separate_data_directories_run_independently(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            one, two = SingleInstance(a), SingleInstance(b)
            try:
                self.assertTrue(one.acquire())
                self.assertTrue(two.acquire())  # different data dirs are different apps
            finally:
                one.close()
                two.close()
