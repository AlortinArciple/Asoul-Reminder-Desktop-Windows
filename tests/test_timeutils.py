import unittest
from datetime import datetime, timezone

from asoul_reminder.timeutils import BEIJING_TIMEZONE, as_beijing_time


class TimeUtilsTests(unittest.TestCase):
    def test_utc_time_is_displayed_as_beijing_time(self):
        value = datetime(2026, 7, 17, 6, 32, tzinfo=timezone.utc)

        converted = as_beijing_time(value)

        self.assertEqual(converted.strftime("%Y-%m-%d %H:%M"), "2026-07-17 14:32")
        self.assertEqual(converted.utcoffset(), BEIJING_TIMEZONE.utcoffset(None))

    def test_naive_chinese_service_time_is_treated_as_beijing_time(self):
        value = datetime(2026, 7, 17, 14, 32)

        converted = as_beijing_time(value)

        self.assertEqual(converted.strftime("%Y-%m-%d %H:%M"), "2026-07-17 14:32")


if __name__ == "__main__":
    unittest.main()
