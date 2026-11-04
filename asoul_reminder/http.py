from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

DOWNLOAD_CHUNK_SIZE = 256 * 1024


class DownloadCancelled(RuntimeError):
    """用户中止了下载。"""


@dataclass
class HttpClient:
    timeout_seconds: int = 20

    def _get(
        self,
        url: str,
        headers: dict[str, str] | None,
        *,
        bypass_proxy: bool,
    ) -> requests.Response:
        merged = {**DEFAULT_HEADERS, **(headers or {})}
        if not bypass_proxy:
            return requests.get(url, headers=merged, timeout=self.timeout_seconds)

        # 绕过系统代理：trust_env=False 跳过环境变量/注册表代理，
        # 再显式传 proxies=None 双保险（requests 语义：值为 None 即禁用该协议代理）。
        session = requests.Session()
        session.trust_env = False
        try:
            return session.get(
                url,
                headers=merged,
                timeout=self.timeout_seconds,
                proxies={"http": None, "https": None},
            )
        finally:
            session.close()

    def get_text(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        bypass_proxy: bool = False,
    ) -> str:
        response = self._get(url, headers, bypass_proxy=bypass_proxy)
        response.raise_for_status()
        return response.content.decode("utf-8", errors="replace")

    def get_bytes(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        bypass_proxy: bool = False,
    ) -> bytes:
        response = self._get(url, headers, bypass_proxy=bypass_proxy)
        response.raise_for_status()
        return response.content

    def get_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        bypass_proxy: bool = False,
    ) -> dict:
        payload, _ = self.get_json_with_response(url, headers, bypass_proxy=bypass_proxy)
        return payload

    def get_json_with_response(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        bypass_proxy: bool = False,
    ) -> tuple[dict, requests.Response]:
        """返回 (payload, response)，供需要读取 Set-Cookie 的登录流程使用。"""
        response = self._get(url, headers, bypass_proxy=bypass_proxy)
        try:
            payload = response.json()
        except ValueError:
            response.raise_for_status()
            raise
        if response.status_code >= 400 and not isinstance(payload, dict):
            response.raise_for_status()
        return payload, response

    def download_file(
        self,
        url: str,
        target: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
        cancel: Callable[[], bool] | None = None,
        headers: dict[str, str] | None = None,
        bypass_proxy: bool = False,
    ) -> Path:
        """流式下载到 target，先写 .part 再原子替换，避免留下半个文件。"""
        merged = {**DEFAULT_HEADERS, **(headers or {})}
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")

        session = requests.Session()
        if bypass_proxy:
            session.trust_env = False
        proxies = {"http": None, "https": None} if bypass_proxy else None
        try:
            response = session.get(
                url,
                headers=merged,
                timeout=self.timeout_seconds,
                stream=True,
                proxies=proxies,
            )
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with partial.open("wb") as handle:
                for chunk in response.iter_content(DOWNLOAD_CHUNK_SIZE):
                    if cancel is not None and cancel():
                        raise DownloadCancelled("下载已取消")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
            partial.replace(target)
            return target
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        finally:
            session.close()
