from pathlib import Path
import tempfile
import threading
import time
import unittest

from PySide6.QtCore import QCoreApplication, QObject, Signal

from ano.background import BackgroundTasks, MAX_RUNNING
from ano.memory import Memory
from ano.tools import Toolbox

app = QCoreApplication.instance() or QCoreApplication([])


class FakeController(QObject):
    replyFinished = Signal(str, bool)
    chatsChanged = Signal()

    def __init__(self):
        super().__init__()
        self.messages = []

    def add_message(self, role, body):
        self.messages.append(body)


class SlowProvider:
    """Answers after a pause; records which threads it ran on and whether they overlapped."""

    def __init__(self):
        self.threads, self.active, self.overlap = [], 0, False
        self.lock = threading.Lock()

    def reply(self, history):
        with self.lock:
            self.active += 1
            self.overlap = self.overlap or self.active > 1
            self.threads.append(threading.current_thread().name)
        time.sleep(0.3)
        with self.lock:
            self.active -= 1
        return 'Done: ' + history[-1]['body']


def settle(condition, seconds=5):
    end = time.time() + seconds
    while time.time() < end and not condition():
        app.processEvents()
        time.sleep(0.02)


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = Memory(Path(self.tmp.name) / 'm.sqlite3')
        self.addCleanup(self.memory.close)
        self.controller, self.provider = FakeController(), SlowProvider()
        self.tasks = BackgroundTasks(self.controller, self.memory, self.provider)

    def test_tasks_run_side_by_side_in_their_own_chats(self):
        self.assertIn('Started in the background', self.tasks.command('in the background: summarise the news'))
        self.assertIn('Started in the background', self.tasks.command('make a budget sheet in the background'))
        self.assertIn(f'{MAX_RUNNING} background tasks are already running', self.tasks.command('in the background: a third'))
        self.assertIsNone(self.tasks.command('what time is it'))
        self.controller.replyFinished.emit('Started…', False)
        settle(lambda: len(self.controller.messages) == 2)
        self.assertTrue(self.provider.overlap)
        self.assertTrue(all(name.startswith('ano-background') for name in self.provider.threads))
        self.assertTrue(all('Background task finished' in message for message in self.controller.messages))
        replies = [chat for chat in self.memory.chats()
                   if any(m['body'].startswith('Done: ') for m in self.memory.recent(chat=chat['id']))]
        self.assertEqual(len(replies), 2)

    def test_a_local_background_task_stays_on_the_free_brain(self):
        self.tasks.command('in the background: list three fruits', local=True)
        self.controller.replyFinished.emit('Started…', False)
        settle(lambda: self.controller.messages)
        asked = [m['body'] for chat in self.memory.chats() for m in self.memory.recent(chat=chat['id']) if m['role'] == 'user']
        self.assertIn('local: list three fruits', asked)

    def test_background_threads_cannot_touch_the_screen(self):
        toolbox = Toolbox(lambda: [], lambda: True)
        result = {}
        thread = threading.Thread(target=lambda: result.update(r=toolbox.run('desktop_input', {})), name='ano-background-9')
        thread.start()
        thread.join()
        self.assertTrue(result['r'][2])
        self.assertIn('cannot use the screen', result['r'][0])


if __name__ == '__main__':
    unittest.main()
