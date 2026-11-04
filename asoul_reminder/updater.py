from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .http import DownloadCancelled, HttpClient


DESKTOP_REPO = "AlortinArciple/Asoul-Reminder-Desktop-Windows"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{DESKTOP_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{DESKTOP_REPO}/releases"
SETUP_ASSET_NAME = "AsoulReminder-Setup.exe"
PORTABLE_ASSET_NAME = "AsoulReminder-portable.zip"
UPDATE_CHECK_INTERVAL_HOURS = 24

__all__ = [
    "DESKTOP_REPO",
    "LATEST_RELEASE_URL",
    "RELEASES_PAGE_URL",
    "SETUP_ASSET_NAME",
    "PORTABLE_ASSET_NAME",
    "UPDATE_CHECK_INTERVAL_HOURS",
    "DownloadCancelled",
    "ReleaseInfo",
    "UpdateError",
    "UpdateProgress",
    "download_release",
    "is_newer",
    "parse_release",
    "parse_version",
]


class UpdateError(RuntimeError):
    """检查或下载更新失败。"""


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    name: str
    notes: str
    asset_name: str
    asset_url: str
    asset_size: int
    html_url: str
    published_at: str = ""

    @property
    def size_text(self) -> str:
        if self.asset_size <= 0:
            return "大小未知"
        return f"{self.asset_size / (1024 * 1024):.1f} MB"


@dataclass(frozen=True)
class UpdateProgress:
    downloaded: int
    total: int

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return max(0, min(100, int(self.downloaded * 100 / self.total)))


def parse_version(text: str) -> tuple[int, ...]:
    """把 `v1.2.3`、`1.2.3-beta` 之类的标签解析成可比较的数字元组。"""
    cleaned = (text or "").strip()
    if cleaned[:1] in ("v", "V"):
        cleaned = cleaned[1:]
    parts: list[int] = []
    for chunk in re.split(r"[.+\-_\s]", cleaned):
        if not chunk:
            continue
        digits = re.match(r"\d+", chunk)
        if digits is None:
            break
        parts.append(int(digits.group()))
    return tuple(parts)


def is_newer(remote: str, current: str) -> bool:
    """远端版本是否比当前版本新；无法解析时一律返回 False（不误报）。"""
    remote_parts = parse_version(remote)
    current_parts = parse_version(current)
    if not remote_parts or not current_parts:
        return False
    width = max(len(remote_parts), len(current_parts))
    padded_remote = remote_parts + (0,) * (width - len(remote_parts))
    padded_current = current_parts + (0,) * (width - len(current_parts))
    return padded_remote > padded_current


def parse_release(payload: dict, asset_name: str = SETUP_ASSET_NAME) -> ReleaseInfo:
    """从 GitHub Release JSON 中取出标签、说明与安装包地址。"""
    if not isinstance(payload, dict):
        raise UpdateError("更新接口返回了意外内容")
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("更新接口没有返回版本号")

    assets = payload.get("assets") or []
    chosen = None
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            if name == asset_name:
                chosen = asset
                break
        if chosen is None:
            # 找不到安装包时退回便携包，至少让用户能下载更新。
            for asset in assets:
                if isinstance(asset, dict) and str(asset.get("name") or "") == PORTABLE_ASSET_NAME:
                    chosen = asset
                    break

    if chosen is None:
        raise UpdateError(f"该版本没有可下载的 {asset_name}")

    return ReleaseInfo(
        tag=tag,
        name=str(payload.get("name") or tag),
        notes=str(payload.get("body") or ""),
        asset_name=str(chosen.get("name") or asset_name),
        asset_url=str(chosen.get("browser_download_url") or ""),
        asset_size=int(chosen.get("size") or 0),
        html_url=str(payload.get("html_url") or RELEASES_PAGE_URL),
        published_at=str(payload.get("published_at") or ""),
    )


class UpdateClient:
    """查询最新 Release；公开仓库不需要任何凭据。"""

    def __init__(self, http: HttpClient, url: str = LATEST_RELEASE_URL) -> None:
        self.http = http
        self.url = url

    def latest(self, asset_name: str = SETUP_ASSET_NAME) -> ReleaseInfo:
        try:
            payload = self.http.get_json(self.url)
        except Exception as exc:  # 网络问题不应当变成崩溃
            raise UpdateError(f"无法连接 GitHub：{exc}") from exc
        return parse_release(payload, asset_name)


def download_release(
    http: HttpClient,
    info: ReleaseInfo,
    target_dir: Path,
    progress: Callable[[UpdateProgress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """把 Release 附件下载到 target_dir，返回最终文件路径。"""
    if not info.asset_url:
        raise UpdateError("该版本没有下载地址")
    target = target_dir / info.asset_name
    try:
        return http.download_file(
            info.asset_url,
            target,
            progress=(lambda done, total: progress(UpdateProgress(done, total))) if progress else None,
            cancel=cancel,
        )
    except DownloadCancelled:
        raise
    except Exception as exc:
        raise UpdateError(f"下载失败：{exc}") from exc
