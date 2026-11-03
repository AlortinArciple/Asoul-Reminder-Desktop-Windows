import importlib.util
import unittest

from asoul_reminder.bilibili_login import (
    FINGER_SPI_URL,
    QRCODE_GENERATE_URL,
    QRCODE_POLL_URL,
    STATUS_EXPIRED,
    STATUS_UNKNOWN,
    STATUS_VALID,
    BilibiliLogin,
    LoginUnavailableError,
    QrLoginSession,
    QrPollResult,
    cookie_dict,
    has_login_cookie,
    login_cookies_from_url,
    merge_cookie,
    normalize_cookie,
    qr_matrix,
)


class FakeResponse:
    def __init__(self, cookies: dict[str, str] | None = None) -> None:
        self.cookies = dict(cookies or {})


class FakeHttp:
    """按 URL 前缀返回固定 payload，记录调用以便断言。"""

    def __init__(self, payloads: dict[str, object], cookies: dict[str, str] | None = None) -> None:
        self.payloads = payloads
        self.cookies = cookies or {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def _payload(self, url: str) -> dict:
        for prefix, payload in self.payloads.items():
            if url.startswith(prefix):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected url: {url}")

    def get_json(self, url: str, headers: dict | None = None, **_kwargs) -> dict:
        self.calls.append((url, dict(headers or {})))
        return self._payload(url)

    def get_json_with_response(self, url: str, headers: dict | None = None, **_kwargs):
        self.calls.append((url, dict(headers or {})))
        return self._payload(url), FakeResponse(self.cookies)


def login_payload(state_code: int, url: str = "") -> dict:
    return {"code": 0, "data": {"code": state_code, "url": url, "message": ""}}


class CookieUtilityTests(unittest.TestCase):
    def test_normalize_strips_prefix_newlines_and_spaces(self):
        raw = "Cookie: SESSDATA=abc%2C123; bili_jct=xyz;\n DedeUserID=42 "
        self.assertEqual(normalize_cookie(raw), "SESSDATA=abc%2C123; bili_jct=xyz; DedeUserID=42")

    def test_cookie_dict_keeps_first_duplicate_and_skips_junk(self):
        pairs = cookie_dict("a=1; a=2; broken; b=; c=3")
        self.assertEqual(pairs, {"a": "1", "b": "", "c": "3"})

    def test_has_login_cookie_requires_sessdata(self):
        self.assertTrue(has_login_cookie("SESSDATA=x; bili_jct=y"))
        self.assertFalse(has_login_cookie("bili_jct=y; buvid3=z"))
        self.assertFalse(has_login_cookie(""))

    def test_merge_cookie_overrides_credentials_and_keeps_old_fingerprint(self):
        existing = "SESSDATA=old; buvid3=keep-me; b_nut=123; bili_jct=old"
        merged = merge_cookie(existing, {"SESSDATA": "new", "DedeUserID": "42"})
        self.assertIn("SESSDATA=new", merged)
        self.assertIn("buvid3=keep-me", merged)
        self.assertIn("b_nut=123", merged)
        self.assertIn("DedeUserID=42", merged)
        self.assertNotIn("SESSDATA=old", merged)

    def test_merge_cookie_ignores_empty_updates(self):
        self.assertEqual(merge_cookie("a=1", {"b": "", " ": "x"}), "a=1")

    def test_login_cookies_from_url_filters_and_decodes(self):
        url = (
            "https://passport.biligame.com/crossDomain?DedeUserID=42&DedeUserID__ckMd5=md5"
            "&SESSDATA=abc%2C1700000000%2Ctoken&bili_jct=csrf&gourl=https%3A%2F%2Fwww.bilibili.com"
        )
        cookies = login_cookies_from_url(url)
        self.assertEqual(cookies["DedeUserID"], "42")
        self.assertEqual(cookies["SESSDATA"], "abc,1700000000,token")
        self.assertEqual(cookies["bili_jct"], "csrf")
        self.assertNotIn("gourl", cookies)

    def test_login_cookies_from_url_tolerates_empty(self):
        self.assertEqual(login_cookies_from_url(""), {})


class LoginFlowTests(unittest.TestCase):
    def test_create_qr_returns_key_and_url(self):
        http = FakeHttp(
            {
                QRCODE_GENERATE_URL: {
                    "code": 0,
                    "data": {"qrcode_key": "key-1", "url": "https://passport.bilibili.com/h5?qrcode_key=key-1"},
                }
            }
        )
        session = BilibiliLogin(http).create_qr()
        self.assertEqual(session.key, "key-1")
        self.assertIn("qrcode_key=key-1", session.url)

    def test_create_qr_raises_on_unexpected_payload(self):
        http = FakeHttp({QRCODE_GENERATE_URL: {"code": -400, "message": "bad"}})
        with self.assertRaises(LoginUnavailableError):
            BilibiliLogin(http).create_qr()

    def test_poll_maps_waiting_scanned_and_expired(self):
        for raw_code, expected in ((86101, "waiting"), (86090, "scanned"), (86038, "expired")):
            http = FakeHttp({QRCODE_POLL_URL: login_payload(raw_code)})
            result = BilibiliLogin(http).poll(QrLoginSession(key="k", url="u"))
            self.assertEqual(result.state, expected)
            self.assertIn("qrcode_key=k", http.calls[0][0])

    def test_poll_ok_reads_set_cookie_jar(self):
        http = FakeHttp({QRCODE_POLL_URL: login_payload(0)}, cookies={"SESSDATA": "s", "bili_jct": "j", "gourl": "x"})
        result = BilibiliLogin(http).poll(QrLoginSession(key="k", url="u"))
        self.assertEqual(result.state, "ok")
        self.assertEqual(result.cookies, {"SESSDATA": "s", "bili_jct": "j"})

    def test_poll_ok_falls_back_to_redirect_query(self):
        http = FakeHttp(
            {
                QRCODE_POLL_URL: login_payload(
                    0, "https://passport.biligame.com/crossDomain?SESSDATA=abc&bili_jct=csrf&DedeUserID=7"
                )
            }
        )
        result = BilibiliLogin(http).poll(QrLoginSession(key="k", url="u"))
        self.assertEqual(login_cookies_from_url(result.redirect_url)["DedeUserID"], "7")

    def test_poll_ok_without_any_cookie_is_an_error(self):
        http = FakeHttp({QRCODE_POLL_URL: login_payload(0)})
        with self.assertRaises(LoginUnavailableError):
            BilibiliLogin(http).poll(QrLoginSession(key="k", url="u"))

    def test_poll_outer_error_raises(self):
        http = FakeHttp({QRCODE_POLL_URL: {"code": -412, "message": "blocked"}})
        with self.assertRaises(LoginUnavailableError):
            BilibiliLogin(http).poll(QrLoginSession(key="k", url="u"))

    def test_verify_cookie_reports_valid_account(self):
        http = FakeHttp({"https://api.bilibili.com/x/web-interface/nav": {"code": 0, "data": {"isLogin": True, "uname": "Alort", "mid": 42}}})
        status = BilibiliLogin(http).verify_cookie("SESSDATA=s")
        self.assertEqual(status.state, STATUS_VALID)
        self.assertEqual(status.uname, "Alort")
        self.assertEqual(status.mid, "42")

    def test_verify_cookie_detects_logged_out(self):
        http = FakeHttp({"https://api.bilibili.com/x/web-interface/nav": {"code": -101, "data": {"isLogin": False}}})
        status = BilibiliLogin(http).verify_cookie("SESSDATA=s")
        self.assertEqual(status.state, STATUS_EXPIRED)

    def test_verify_cookie_network_error_is_unknown(self):
        http = FakeHttp({"https://api.bilibili.com/x/web-interface/nav": RuntimeError("network down")})
        status = BilibiliLogin(http).verify_cookie("SESSDATA=s")
        self.assertEqual(status.state, STATUS_UNKNOWN)
        self.assertIn("network down", status.message)

    def test_verify_cookie_without_sessdata_is_expired(self):
        http = FakeHttp({})
        status = BilibiliLogin(http).verify_cookie("buvid3=x")
        self.assertEqual(status.state, STATUS_EXPIRED)
        self.assertEqual(http.calls, [])

    def test_build_cookie_keeps_existing_buvid(self):
        http = FakeHttp({})
        poll = QrPollResult(
            state="ok",
            redirect_url="https://passport.biligame.com/crossDomain?SESSDATA=new&bili_jct=csrf&DedeUserID=42",
        )
        cookie = BilibiliLogin(http).build_cookie(poll, existing="buvid3=old-buvid; b_nut=1; SESSDATA=old")
        self.assertIn("SESSDATA=new", cookie)
        self.assertIn("buvid3=old-buvid", cookie)
        self.assertIn("b_nut=1", cookie)
        self.assertEqual(http.calls, [])

    def test_build_cookie_fetches_buvid_when_missing(self):
        http = FakeHttp(
            {FINGER_SPI_URL: {"code": 0, "data": {"b_3": "fresh-3", "b_4": "fresh-4"}}}
        )
        poll = QrPollResult(
            state="ok",
            redirect_url="https://passport.biligame.com/crossDomain?SESSDATA=new&bili_jct=csrf",
        )
        cookie = BilibiliLogin(http).build_cookie(poll, existing="")
        pairs = cookie_dict(cookie)
        self.assertEqual(pairs["buvid3"], "fresh-3")
        self.assertEqual(pairs["buvid4"], "fresh-4")
        self.assertEqual(len(http.calls), 1)

    def test_build_cookie_survives_buvid_failure(self):
        http = FakeHttp({FINGER_SPI_URL: RuntimeError("spi down")})
        poll = QrPollResult(state="ok", redirect_url="https://x/crossDomain?SESSDATA=new")
        cookie = BilibiliLogin(http).build_cookie(poll, existing="buvid3=keep")
        self.assertIn("SESSDATA=new", cookie)
        self.assertIn("buvid3=keep", cookie)


@unittest.skipUnless(importlib.util.find_spec("qrcode"), "qrcode 未安装")
class QrMatrixTests(unittest.TestCase):
    def test_matrix_is_square_and_has_quiet_zone(self):
        matrix = qr_matrix("https://passport.bilibili.com/h5-app/passport/login/scan?qrcode_key=k")
        self.assertGreaterEqual(len(matrix), 21)
        self.assertTrue(all(len(row) == len(matrix) for row in matrix))
        self.assertFalse(any(matrix[0]))
        self.assertFalse(any(row[0] for row in matrix))


if __name__ == "__main__":
    unittest.main()
