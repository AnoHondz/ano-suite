from datetime import datetime, timedelta, timezone
import unittest

from ano.brain import AGENT_RULES
from ano.local_profile import stamp


class TimeHeaderTests(unittest.TestCase):
    def test_the_header_names_the_utc_offset_the_right_way_round(self):
        eastern = datetime(2026, 10, 27, 7, 45, tzinfo=timezone(timedelta(hours=-4)))
        self.assertEqual(stamp('hi', eastern), '[Local time: Tuesday 2026-10-27 07:45, UTC-04:00]\nhi')
        india = datetime(2026, 10, 27, 7, 45, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        self.assertIn('UTC+05:30', stamp('hi', india))

    def test_the_agent_is_told_to_check_before_replying(self):
        self.assertIn('Before every final reply', AGENT_RULES)
        self.assertIn('save\nunder a new name', AGENT_RULES)


if __name__ == '__main__':
    unittest.main()
