import unittest
from types import SimpleNamespace

from asoul_reminder.bilibili import (
    BilibiliClient,
    sign_wbi_params,
    wbi_encode,
    wbi_key_from_url,
    wbi_mixin_key,
)
from asoul_reminder.models import MemberConfig

IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"
MIXIN_KEY = "ea1db124af3c7062474693fa704f4ff8"


def make_member(uid: str = "672353429") -> MemberConfig:
    return MemberConfig(
        id="bella",
        name="贝拉",
        enabled=True,
        monitor_dynamic=True,
        monitor_live=False,
        color="#DB7D74",
        uid=uid,
        dynamic_url=f"https://space.bilibili.com/{uid}/dynamic",
        live_url="",
    )


class FakeHttp:
    def __init__(self, nav_payload):
        self.nav_payload = nav_payload
        self.urls = []

    def get_json(self, url, headers=None, **kwargs):
        self.urls.append(url)
        if "x/web-interface/nav" in url:
            return self.nav_payload
        return {"code": 0, "data": {"items": []}}


def make_nav_payload() -> dict:
    return {
        "code": -101,
        "data": {
            "wbi_img": {
                "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
                "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.jpg",
            }
        },
    }


class WbiSignTests(unittest.TestCase):
    def test_mixin_key_matches_documented_example(self):
        self.assertEqual(wbi_mixin_key(IMG_KEY, SUB_KEY), MIXIN_KEY)

    def test_key_extracted_from_nav_url(self):
        self.assertEqual(
            wbi_key_from_url(f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png"),
            IMG_KEY,
        )
        self.assertEqual(wbi_key_from_url(""), "")
        self.assertEqual(wbi_key_from_url(None), "")

    def test_sign_matches_documented_vector(self):
        signed = sign_wbi_params(
            {"foo": "114", "bar": "514", "zab": "1919810"},
            MIXIN_KEY,
            1702204169,
        )
        self.assertEqual(
            signed,
            {
                "bar": "514",
                "foo": "114",
                "wts": "1702204169",
                "zab": "1919810",
                "w_rid": "8f6f2b5b3d485fe1886cec6a0be8c5d4",
            },
        )

    def test_filter_strips_risky_characters(self):
        self.assertEqual(wbi_encode("a!'()*b"), "ab")
        self.assertEqual(wbi_encode("[]"), "%5B%5D")

    def test_dynamic_url_is_signed_and_cached(self):
        http = FakeHttp(make_nav_payload())
        client = BilibiliClient(
            SimpleNamespace(bilibili_cookie="", dynamic_fetch_limit=3), http
        )
        url = client.dynamic_url(make_member())
        self.assertIn("w_rid=", url)
        self.assertIn("wts=", url)
        self.assertIn("host_mid=672353429", url)
        client.dynamic_url(make_member())
        nav_calls = [item for item in http.urls if "x/web-interface/nav" in item]
        self.assertEqual(len(nav_calls), 1)

    def test_dynamic_url_falls_back_without_signature_when_nav_fails(self):
        class FailingHttp:
            def __init__(self):
                self.urls = []

            def get_json(self, url, headers=None, **kwargs):
                self.urls.append(url)
                raise RuntimeError("network down")

        http = FailingHttp()
        client = BilibiliClient(
            SimpleNamespace(bilibili_cookie="", dynamic_fetch_limit=3), http
        )
        url = client.dynamic_url(make_member())
        self.assertNotIn("w_rid=", url)
        self.assertIn("host_mid=672353429", url)


if __name__ == "__main__":
    unittest.main()
