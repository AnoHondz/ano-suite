import tempfile
import unittest
from pathlib import Path

from ano.memory import Memory


class MemoryTests(unittest.TestCase):
    def test_persistence_order_and_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'memory.sqlite3'
            memory = Memory(path)
            memory.add('user', 'first')
            memory.add('assistant', 'second')
            memory.close()
            memory = Memory(path)
            self.assertEqual([m['body'] for m in memory.recent()], ['first', 'second'])
            self.assertEqual(memory.recent(1)[0]['body'], 'second')
            memory.close()

    def test_search_indexes_messages_and_treats_input_as_text(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = Memory(Path(directory) / 'memory.sqlite3')
            memory.add('user', 'Research my Surface battery')
            memory.add('user', 'Buy groceries')
            self.assertEqual(len(memory.search('surface battery')), 1)
            self.assertEqual(memory.search('missing'), [])
            self.assertEqual(memory.search('" OR *'), [])
            self.assertEqual(memory.search(''), [])
            memory.close()


if __name__ == '__main__':
    unittest.main()
