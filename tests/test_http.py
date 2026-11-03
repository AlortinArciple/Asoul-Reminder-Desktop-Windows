import unittest
from unittest.mock import Mock, patch

from asoul_reminder.http import HttpClient


class HttpClientTests(unittest.TestCase):
    @patch("asoul_reminder.http.requests.Session")
    @patch("asoul_reminder.http.requests.get")
    def test_bypass_proxy_uses_session_without_environment(self, get: Mock, session_factory: Mock):
        response = Mock()
        response.content = "日程".encode()
        session = session_factory.return_value
        session.get.return_value = response

        text = HttpClient(timeout_seconds=7).get_text(
            "https://asoul.love/calendar/latest",
            bypass_proxy=True,
        )

        self.assertEqual(text, "日程")
        self.assertIs(session.trust_env, False)
        session.get.assert_called_once()
        _, kwargs = session.get.call_args
        self.assertEqual(kwargs.get("proxies"), {"http": None, "https": None})
        session.close.assert_called_once()
        get.assert_not_called()

    @patch("asoul_reminder.http.requests.get")
    def test_default_request_keeps_normal_proxy_behavior(self, get: Mock):
        response = Mock()
        response.content = b"ok"
        get.return_value = response

        text = HttpClient(timeout_seconds=9).get_text("https://example.test/")

        self.assertEqual(text, "ok")
        get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
