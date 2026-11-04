from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asoul_reminder.http import DownloadCancelled
from asoul_reminder.updater import (
    PORTABLE_ASSET_NAME,
    SETUP_ASSET_NAME,
    ReleaseInfo,
    UpdateClient,
    UpdateError,
    download_release,
    is_newer,
    parse_release,
    parse_version,
)


def release_payload(*, tag="v1.2.0", assets=None, body="更新说明", html_url="https://example/rel"):
    if assets is None:
        assets = [
            {"name": SETUP_ASSET_NAME, "size": 42 * 1024 * 1024, "browser_download_url": "https://example/setup.exe"},
            {"name": PORTABLE_ASSET_NAME, "size": 60 * 1024 * 1024, "browser_download_url": "https://example/portable.zip"},
        ]
    return {
        "tag_name": tag,
        "name": f"Asoul Reminder {tag}",
        "body": body,
        "html_url": html_url,
        "published_at": "2026-11-04T00:00:00Z",
        "assets": assets,
    }


class VersionTests(unittest.TestCase):
    def test_parse_version_strips_prefix_and_suffix(self):
        self.assertEqual(parse_version("v1.2.0"), (1, 2, 0))
        self.assertEqual(parse_version("1.2.3-beta"), (1, 2, 3))
        self.assertEqual(parse_version("V2"), (2,))
        self.assertEqual(parse_version(""), ())

    def test_is_newer_compares_numerically(self):
        self.assertTrue(is_newer("v1.10.0", "1.9.0"))
        self.assertTrue(is_newer("v1.2.1", "1.2.0"))
        self.assertFalse(is_newer("v1.2.0", "1.2.0"))
        self.assertFalse(is_newer("v1.1.0", "1.2.0"))

    def test_is_newer_pads_short_versions(self):
        self.assertTrue(is_newer("v1.3", "1.2.9"))
        self.assertFalse(is_newer("v1.2", "1.2.0"))

    def test_is_newer_never_raises_on_garbage(self):
        self.assertFalse(is_newer("", "1.2.0"))
        self.assertFalse(is_newer("nightly", "1.2.0"))
        self.assertFalse(is_newer("v1.2.0", "dev"))


class ParseReleaseTests(unittest.TestCase):
    def test_picks_setup_asset(self):
        info = parse_release(release_payload())
        self.assertEqual(info.tag, "v1.2.0")
        self.assertEqual(info.asset_name, SETUP_ASSET_NAME)
        self.assertEqual(info.asset_url, "https://example/setup.exe")
        self.assertEqual(info.asset_size, 42 * 1024 * 1024)
        self.assertEqual(info.size_text, "42.0 MB")

    def test_falls_back_to_portable_when_setup_missing(self):
        payload = release_payload(assets=[{"name": PORTABLE_ASSET_NAME, "size": 1, "browser_download_url": "https://example/p.zip"}])
        info = parse_release(payload)
        self.assertEqual(info.asset_name, PORTABLE_ASSET_NAME)

    def test_missing_assets_raises(self):
        with self.assertRaises(UpdateError):
            parse_release(release_payload(assets=[]))

    def test_missing_tag_raises(self):
        with self.assertRaises(UpdateError):
            parse_release({"assets": []})

    def test_non_dict_payload_raises(self):
        with self.assertRaises(UpdateError):
            parse_release([])  # type: ignore[arg-type]

    def test_size_unknown_when_zero(self):
        payload = release_payload(assets=[{"name": SETUP_ASSET_NAME, "size": 0, "browser_download_url": "https://example/s.exe"}])
        self.assertEqual(parse_release(payload).size_text, "大小未知")


class FakeHttp:
    def __init__(self, payload=None, error: Exception | None = None, data: bytes = b"binary"):
        self.payload = payload
        self.error = error
        self.data = data
        self.calls: list[str] = []
        self.download_calls: list[str] = []

    def get_json(self, url, headers=None, *, bypass_proxy=False):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.payload or {}

    def download_file(self, url, target, *, progress=None, cancel=None, headers=None, bypass_proxy=False):
        self.download_calls.append(url)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.data)
        if progress is not None:
            progress(len(self.data), len(self.data))
        return target


class UpdateClientTests(unittest.TestCase):
    def test_latest_returns_release(self):
        client = UpdateClient(FakeHttp(release_payload()))
        info = client.latest()
        self.assertEqual(info.tag, "v1.2.0")

    def test_network_error_becomes_update_error(self):
        client = UpdateClient(FakeHttp(error=RuntimeError("network down")))
        with self.assertRaises(UpdateError) as ctx:
            client.latest()
        self.assertIn("GitHub", str(ctx.exception))
        self.assertIn("network down", str(ctx.exception))


class DownloadTests(unittest.TestCase):
    def test_download_writes_file_and_reports_progress(self):
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            info = ReleaseInfo(
                tag="v1.2.0",
                name="rel",
                notes="",
                asset_name=SETUP_ASSET_NAME,
                asset_url="https://example/setup.exe",
                asset_size=6,
                html_url="https://example",
            )
            path = download_release(FakeHttp(data=b"abc123"), info, Path(tmp), progress=seen.append)
            self.assertEqual(path.read_bytes(), b"abc123")
            self.assertEqual(seen[-1].downloaded, 6)
            self.assertEqual(seen[-1].percent, 100)

    def test_missing_url_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = ReleaseInfo("v1", "n", "", SETUP_ASSET_NAME, "", 0, "https://example")
            with self.assertRaises(UpdateError):
                download_release(FakeHttp(), info, Path(tmp))


class DownloadCancelledTests(unittest.TestCase):
    def test_cancel_propagates(self):
        class CancelHttp(FakeHttp):
            def download_file(self, url, target, *, progress=None, cancel=None, headers=None, bypass_proxy=False):
                raise DownloadCancelled("cancelled")

        with tempfile.TemporaryDirectory() as tmp:
            info = ReleaseInfo("v1", "n", "", SETUP_ASSET_NAME, "https://example/s.exe", 1, "https://example")
            with self.assertRaises(DownloadCancelled):
                download_release(CancelHttp(), info, Path(tmp))


if __name__ == "__main__":
    unittest.main()
