from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from ano.memory import Memory


class RequestHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = Memory(Path(self.tmp.name) / 'memory.sqlite3')
        self.addCleanup(self.memory.close)
        self.chat = self.memory.latest_chat()

    def test_queued_commands_cannot_replace_the_active_request(self):
        first = self.memory.add('user', 'First request', self.chat)
        second = self.memory.add('user', 'Second request', self.chat)
        self.memory.add('user', 'Third request', self.chat)
        self.memory.add('assistant', 'First result', self.chat, reply_to=first)
        self.assertEqual([m['body'] for m in self.memory.history_for_request(first, self.chat)], ['First request'])
        self.assertEqual([m['body'] for m in self.memory.history_for_request(second, self.chat)],
                         ['First request', 'First result', 'Second request'])

    def test_legacy_history_and_chat_boundaries_are_preserved(self):
        self.memory.add('user', 'Legacy request', self.chat)
        self.memory.add('assistant', 'Legacy result', self.chat)
        request = self.memory.add('user', 'Current request', self.chat)
        other = self.memory.new_chat()
        with self.assertRaises(ValueError):
            self.memory.add('assistant', 'Wrong destination', other, reply_to=request)
        self.assertEqual([m['body'] for m in self.memory.history_for_request(request, self.chat)],
                         ['Legacy request', 'Legacy result', 'Current request'])
        self.assertEqual(self.memory.recent(chat=other), [])
