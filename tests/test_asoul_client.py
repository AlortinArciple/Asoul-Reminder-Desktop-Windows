import html as html_lib
import json
import unittest
from datetime import datetime, timezone

import requests

from asoul_reminder.asoul import AsoulClient
from asoul_reminder.config import AppConfig
from asoul_reminder.models import MemberConfig


CALENDAR_URL = "https://asoul.love/calendar/latest"
HOME_URL = "https://asoul.love/"


def make_config() -> AppConfig:
    return AppConfig(
        asoul_calendar_url=CALENDAR_URL,
        asoul_home_url=HOME_URL,
        asoul_bypass_proxy=True,
        check_interval_minutes=20,
        dynamic_check_interval_minutes=10,
        dynamic_high_frequency_interval_minutes=3,
        dynamic_high_frequency_max_minutes=60,
        live_status_open_check_interval_seconds=30,
        live_status_close_check_interval_minutes=20,
        dynamic_fetch_limit=3,
        suppress_live_start_dynamic_window_minutes=5,
        write_snapshots=False,
        live_remind_before_minutes=(10, 5),
        auto_start=False,
        notifications_enabled=True,
        bilibili_cookie="",
        request_timeout_seconds=20,
        members=(
            MemberConfig(
                id="diana",
                name="嘉然",
                enabled=True,
                monitor_dynamic=True,
                monitor_live=True,
                color="#E799B0",
                uid="672328094",
                dynamic_url="https://space.bilibili.com/672328094/dynamic",
                live_url="https://live.bilibili.com/22637261",
            ),
        ),
    )


def make_calendar_document() -> str:
    props = {
        "initialEvents": [
            1,
            [
                [
                    0,
                    {
                        "id": "event-1",
                        "title": "嘉然直播",
                        "description": "测试日程",
                        "type": "live",
                        "format": "live",
                        "startTime": "2026-06-11T12:00:00+00:00",
                        "host": "diana",
                        "performers": [],
                        "status": "published",
                        "sourceId": "121",
                    },
                ]
            ],
        ]
    }
    escaped = html_lib.escape(json.dumps(props), quote=True)
    return (
        '<html><body><astro-island component-export="FilterableCalendar" '
        f'props="{escaped}"></astro-island></body></html>'
    )


def http_error(status_code: int, url: str) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    response.url = url
    return requests.HTTPError(f"{status_code} Client Error for url: {url}", response=response)


class FakeHttp:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.bypass_modes: list[bool] = []

    def get_text(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        bypass_proxy: bool = False,
    ) -> str:
        self.calls.append(url)
        self.bypass_modes.append(bypass_proxy)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return str(response)


class AsoulClientTests(unittest.TestCase):
    def test_falls_back_to_home_page_when_latest_calendar_is_forbidden(self):
        http = FakeHttp(
            {
                CALENDAR_URL: http_error(403, CALENDAR_URL),
                HOME_URL: make_calendar_document(),
            }
        )
        client = AsoulClient(make_config(), http)

        with self.assertLogs("asoul_reminder.asoul", level="INFO") as logs:
            events = client.fetch_events()

        self.assertEqual(http.calls, [CALENDAR_URL, HOME_URL])
        self.assertEqual(http.bypass_modes, [True, True])
        self.assertTrue(any("ASOUL 日程接口返回 403" in entry for entry in logs.output))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "嘉然直播")
        self.assertEqual(events[0].start_time, datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
