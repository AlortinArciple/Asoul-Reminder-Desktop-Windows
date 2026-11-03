import unittest
from datetime import datetime, timezone

from asoul_reminder.formatting import local_time_text, weekday_text


class FormattingTests(unittest.TestCase):
    def test_weekday_text_maps_beijing_date_to_weekday(self):
        # 2026-06-10 UTC 12:00 == 北京时间 2026-06-10 20:00，星期三。
        wednesday = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(weekday_text(wednesday), "星期三")

        # UTC 2026-06-13 20:00 == 北京时间 2026-06-14 04:00，星期按北京日期取。
        utc_saturday_evening = datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(weekday_text(utc_saturday_evening), "星期日")

    def test_local_time_text_appends_beijing_weekday(self):
        value = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(local_time_text(value), "2026-06-10 20:00 星期三")

    def test_local_time_text_keeps_naive_value_in_beijing_timezone(self):
        value = datetime(2026, 6, 10, 12, 0)
        self.assertEqual(local_time_text(value), "2026-06-10 12:00 星期三")


if __name__ == "__main__":
    unittest.main()
