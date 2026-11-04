from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication

from ano.app import Controller
from ano.facts import FactMemoryBrain
from ano.memory import Memory

app = QCoreApplication.instance() or QCoreApplication([])


class SavingsTests(unittest.TestCase):
    def test_settings_reaches_the_ledger_through_the_memory_wrapper(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = Memory(Path(folder) / 'm.sqlite3')
            try:
                paid = Mock(ledger=Mock(savings_summary=lambda: 'Today (UTC): 3 handled free · ~$0.01 estimated saved'))
                controller = Controller(memory)
                controller.provider = FactMemoryBrain(Mock(), paid)
                self.assertIn('3 handled free', controller.savingsSummary)
            finally:
                memory.close()


if __name__ == '__main__':
    unittest.main()
