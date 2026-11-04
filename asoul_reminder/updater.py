from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .http import DownloadCancelled, HttpClient


DESKTOP_REPO = "AlortinArciple/Asoul-Reminder-Desktop-Windows"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{DESKTOP_REPO}/releases/latest"
# releases.atom 是网页版 Release 源，不计入 api.github.com 的每小时 60 次额度。
RELEASES_FEED_URL = f"https://github.com/{DESKTOP_REPO}/releases.atom"
DOWNLOAD_URL_TEMPLATE = (
    f"https://github.com/{DESKTOP_REPO}/releases/download/{{tag}}/{{asset}}"
)
RELEASES_PAGE_URL = f"https://github.com/{DESKTOP_REPO}/releases"
SETUP_ASSET_NAME = "AsoulReminder-Setup.exe"
PORTABLE_ASSET_NAME = "AsoulReminder-portable.zip"
UPDATE_CHECK_INTERVAL_HOURS = 24

__all__ = [
    "DESKTOP_REPO",
    "LATEST_RELEASE_URL",
    "RELEASES_FEED_URL",
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
    "parse_feed",
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


def _html_to_text(markup: str) -> str:
    """把 Release 说明里的 HTML 片段压成可读纯文本。"""
    text = html.unescape(markup or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def parse_feed(
    text: str,
    asset_name: str = SETUP_ASSET_NAME,
    repo: str = DESKTOP_REPO,
) -> ReleaseInfo:
    """从 `releases.atom` 里解析出最新版本；feed 里没有附件大小，先记 0。"""
    if not (text or "").strip():
        raise UpdateError("Release 源返回了空内容")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise UpdateError(f"无法解析 Release 源：{exc}") from exc

    namespace = "{http://www.w3.org/2005/Atom}"
    entries = root.findall(f"{namespace}entry")
    if not entries:
        raise UpdateError("Release 源里没有任何版本")

    entry = entries[0]
    entry_id = (entry.findtext(f"{namespace}id") or "").strip()
    # id 形如 tag:github.com,2008:Repository/1436893255/v1.2.0，末尾即 tag。
    tag = entry_id.rsplit("/", 1)[-1].strip() if "/" in entry_id else ""
    if not tag:
        raise UpdateError("Release 源缺少版本号")

    html_url = RELEASES_PAGE_URL
    for link in entry.findall(f"{namespace}link"):
        if (link.get("rel") or "") == "alternate" and link.get("href"):
            html_url = link.get("href") or html_url
            break

    notes_node = entry.find(f"{namespace}content")
    notes_html = notes_node.text if notes_node is not None else ""
    return ReleaseInfo(
        tag=tag,
        name=(entry.findtext(f"{namespace}title") or tag).strip() or tag,
        notes=_html_to_text(notes_html or ""),
        asset_name=asset_name,
        asset_url=DOWNLOAD_URL_TEMPLATE.format(tag=tag, asset=asset_name),
        asset_size=0,
        html_url=html_url,
        published_at=(entry.findtext(f"{namespace}updated") or "").strip(),
    )


def parse_release(payload: dict, asset_name: str = SETUP_ASSET_NAME) -> ReleaseInfo:
    """从 GitHub Release JSON 中取出标签、说明与安装包地址。"""
    if not isinstance(payload, dict):
        raise UpdateError("更新接口返回了意外内容")
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        # GitHub 出错时返回 {"message": "..."}，例如公开接口按 IP 限流。
        hint = str(payload.get("message") or "").strip()
        if hint:
            raise UpdateError(f"GitHub 返回错误：{hint}")
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
    """查询最新 Release；公开仓库不需要任何凭据。

    优先读网页版 `releases.atom`：它不计入 api.github.com 的每小时 60 次额度，
    也不会因为共享出口 IP 被别人用光而失败。只有 feed 解析不成时才退回 API。
    """

    def __init__(
        self,
        http: HttpClient,
        url: str = LATEST_RELEASE_URL,
        feed_url: str = RELEASES_FEED_URL,
    ) -> None:
        self.http = http
        self.url = url
        self.feed_url = feed_url

    def latest(self, asset_name: str = SETUP_ASSET_NAME) -> ReleaseInfo:
        errors: list[str] = []
        if self.feed_url:
            try:
                info = parse_feed(
                    self.http.get_text(self.feed_url), asset_name=asset_name
                )
            except UpdateError as exc:
                errors.append(str(exc))
            except Exception as exc:  # 网络问题不应当变成崩溃
                errors.append(f"无法连接 GitHub：{exc}")
            else:
                return self._with_size(info)
        try:
            payload = self.http.get_json(self.url)
        except Exception as exc:  # 网络问题不应当变成崩溃
            errors.append(f"无法连接 GitHub：{exc}")
        else:
            try:
                return parse_release(payload, asset_name)
            except UpdateError as exc:
                errors.append(str(exc))
        raise UpdateError(errors[-1] if errors else "检查更新失败")

    def _with_size(self, info: ReleaseInfo) -> ReleaseInfo:
        """feed 没有附件大小，补一次 HEAD；失败就保留「大小未知」。"""
        if info.asset_size > 0:
            return info
        getter = getattr(self.http, "content_length", None)
        if getter is None:
            return info
        try:
            size = getter(info.asset_url)
        except Exception:
            return info
        if not size:
            return info
        return replace(info, asset_size=int(size))


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
