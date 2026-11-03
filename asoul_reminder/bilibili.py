from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .config import AppConfig
from .emoji import emoji_cache_path, normalize_emoji_url
from .http import HttpClient
from .models import DynamicItem, DynamicTextSegment, LiveRoomStatus, MemberConfig
from .timeutils import BEIJING_TIMEZONE, beijing_now

LOGGER = logging.getLogger(__name__)

DYNAMIC_FEED_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
WBI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
WBI_KEY_TTL_SECONDS = 1800.0
WBI_MIXIN_TABLE = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)


class BilibiliError(RuntimeError):
    pass


def wbi_key_from_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split("?")[0].rstrip("/").split("/")[-1].split(".")[0]


def wbi_mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[index] for index in WBI_MIXIN_TABLE if index < len(raw))[:32]


def wbi_filter_value(value: str) -> str:
    return "".join(ch for ch in value if ch not in "!'()*")


def wbi_encode(value: str) -> str:
    # 与 JS encodeURIComponent 对齐：空格编为 %20；!'()* 已被 wbi_filter_value 移除。
    return quote(wbi_filter_value(value), safe="")


def sign_wbi_params(params: dict[str, Any], mixin_key: str, wts: int) -> dict[str, str]:
    """按 B 站 wbi 规则签名查询参数，返回按 key 排序、末尾附加 w_rid 的参数。"""
    merged = {str(key): str(value) for key, value in params.items()}
    merged["wts"] = str(int(wts))
    query = "&".join(f"{wbi_encode(key)}={wbi_encode(value)}" for key, value in sorted(merged.items()))
    merged["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    return merged


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if isinstance(value, dict):
        direct_text = value.get("text")
        if isinstance(direct_text, str):
            return normalize_text(direct_text)
        nodes = value.get("rich_text_nodes")
        if isinstance(nodes, list):
            return "".join(
                str(node.get("orig_text") or node.get("text") or "")
                for node in nodes
                if isinstance(node, dict)
            ).strip()
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := normalize_text(item))).strip()
    return str(value).strip()


def compact_text(value: Any) -> str:
    return " ".join(normalize_text(value).split())


def summarize_dynamic(item: dict[str, Any]) -> str:
    modules = item.get("modules") or {}
    dynamic = modules.get("module_dynamic") or {}
    desc = dynamic.get("desc") or {}
    text = normalize_text(desc) if desc else ""
    if text:
        return text
    major = dynamic.get("major") or {}
    for key in ("opus", "archive", "article", "draw", "live_rcmd"):
        block = major.get(key)
        if isinstance(block, dict):
            title = compact_text(block.get("title") or block.get("desc") or block.get("summary"))
            if title:
                return title
    return "新动态"


def extract_dynamic_rich_text(item: dict[str, Any]) -> tuple[DynamicTextSegment, ...]:
    modules = item.get("modules") or {}
    dynamic = modules.get("module_dynamic") or {}
    for source in dynamic_text_sources(dynamic):
        segments = extract_rich_text_segments(source)
        if segments:
            return segments
    text = summarize_dynamic(item)
    return (DynamicTextSegment(type="text", text=text),) if text else ()


def dynamic_text_sources(dynamic: dict[str, Any]) -> tuple[Any, ...]:
    sources = [dynamic.get("desc")]
    major = dynamic.get("major") or {}
    if isinstance(major, dict):
        opus = major.get("opus") or {}
        if isinstance(opus, dict):
            sources.append(opus.get("summary"))
        for key in ("archive", "article", "draw", "live_rcmd"):
            block = major.get(key)
            if isinstance(block, dict):
                sources.extend((block.get("desc"), block.get("summary"), block.get("title")))
    return tuple(source for source in sources if source)


def extract_rich_text_segments(desc: Any) -> tuple[DynamicTextSegment, ...]:
    if not isinstance(desc, dict):
        text = normalize_text(desc)
        return (DynamicTextSegment(type="text", text=text),) if text else ()
    nodes = desc.get("rich_text_nodes")
    if not isinstance(nodes, list):
        text = normalize_text(desc)
        return (DynamicTextSegment(type="text", text=text),) if text else ()

    segments: list[DynamicTextSegment] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        text = str(node.get("orig_text") or node.get("text") or "")
        emoji = node.get("emoji")
        node_type = str(node.get("type") or "")
        is_emoji = "EMOJI" in node_type or (text.startswith("[") and text.endswith("]"))
        image_url = ""
        if isinstance(emoji, dict):
            image_url = normalize_emoji_url(
                str(emoji.get("gif_url") or emoji.get("icon_url") or emoji.get("url") or "")
            )
        if not image_url:
            image_url = normalize_emoji_url(str(node.get("icon_url") or node.get("url") or ""))
        if image_url and is_emoji:
            segments.append(DynamicTextSegment(type="emoji", text=text, url=image_url))
        elif text:
            segments.append(DynamicTextSegment(type="text", text=text))
    return merge_text_segments(segments)


def merge_text_segments(segments: list[DynamicTextSegment]) -> tuple[DynamicTextSegment, ...]:
    merged: list[DynamicTextSegment] = []
    for segment in segments:
        if segment.type == "text" and merged and merged[-1].type == "text":
            previous = merged[-1]
            merged[-1] = DynamicTextSegment(type="text", text=previous.text + segment.text)
        else:
            merged.append(segment)
    return tuple(merged)


def parse_publish_time(item: dict[str, Any]) -> datetime | None:
    modules = item.get("modules") or {}
    author = modules.get("module_author") or {}
    for key in ("pub_ts", "pub_time"):
        parsed = parse_publish_time_value(author.get(key))
        if parsed:
            return parsed
    return None


def parse_publish_time_value(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc)

    local_tz = BEIJING_TIMEZONE
    now = beijing_now()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=local_tz).astimezone(timezone.utc)
        except ValueError:
            pass

    month_day = re.fullmatch(r"(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})", text)
    if month_day:
        month, day, hour, minute = (int(part) for part in month_day.groups())
        try:
            parsed = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if parsed - now > timedelta(days=2):
                parsed = parsed.replace(year=parsed.year - 1)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    relative_day = re.fullmatch(r"(今天|昨天)\s*(\d{1,2}):(\d{2})", text)
    if relative_day:
        day_text, hour_text, minute_text = relative_day.groups()
        days_back = 1 if day_text == "昨天" else 0
        parsed = (now - timedelta(days=days_back)).replace(
            hour=int(hour_text),
            minute=int(minute_text),
            second=0,
            microsecond=0,
        )
        return parsed.astimezone(timezone.utc)

    relative = re.fullmatch(r"(\d+)\s*(分钟|小时)前", text)
    if relative:
        amount_text, unit = relative.groups()
        amount = int(amount_text)
        delta = timedelta(minutes=amount) if unit == "分钟" else timedelta(hours=amount)
        return (now - delta).astimezone(timezone.utc)

    if text == "刚刚":
        return now.astimezone(timezone.utc)
    return None


class BilibiliClient:
    def __init__(self, config: AppConfig, http: HttpClient) -> None:
        self.config = config
        self.http = http
        self._wbi_lock = threading.Lock()
        self._wbi_mixin: str | None = None
        self._wbi_fetched_at: float = 0.0

    def ensure_wbi_mixin(self) -> str | None:
        """获取缓存的 wbi mixin 密钥；nav 每日轮换密钥，这里最多缓存 30 分钟。"""
        with self._wbi_lock:
            now = time.monotonic()
            if self._wbi_mixin and now - self._wbi_fetched_at < WBI_KEY_TTL_SECONDS:
                return self._wbi_mixin
            try:
                # nav 未登录时 code=-101，但 data.wbi_img 照常返回，因此不检查 code。
                payload = self.http.get_json(WBI_NAV_URL)
                wbi_img = (payload.get("data") or {}).get("wbi_img") or {}
                img_key = wbi_key_from_url(wbi_img.get("img_url"))
                sub_key = wbi_key_from_url(wbi_img.get("sub_url"))
                if not img_key or not sub_key:
                    raise BilibiliError("nav 未返回 wbi_img 密钥")
                self._wbi_mixin = wbi_mixin_key(img_key, sub_key)
                self._wbi_fetched_at = now
                LOGGER.info("已获取 B 站 wbi 签名密钥")
                return self._wbi_mixin
            except Exception as exc:
                LOGGER.warning("获取 wbi 签名密钥失败，本次动态请求不签名：%s", exc)
                return None

    def dynamic_url(self, member: MemberConfig) -> str:
        params: dict[str, str] = {
            "host_mid": member.uid,
            "timezone_offset": "-480",
            "features": (
                "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,"
                "decorationCard,forwardListHidden,ugcDelete,onlyfansQaCard"
            ),
        }
        mixin = self.ensure_wbi_mixin()
        if mixin:
            params = sign_wbi_params(params, mixin, int(time.time()))
        query = "&".join(f"{wbi_encode(key)}={wbi_encode(value)}" for key, value in params.items())
        return f"{DYNAMIC_FEED_URL}?{query}"

    def dynamic_headers(self, member: MemberConfig) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://space.bilibili.com",
            "Referer": member.dynamic_url,
        }
        if self.config.bilibili_cookie:
            headers["Cookie"] = self.config.bilibili_cookie
        return headers

    def fetch_recent_dynamics(self, member: MemberConfig, limit: int | None = None) -> list[DynamicItem]:
        if not member.uid:
            return []
        payload = self.http.get_json(self.dynamic_url(member), headers=self.dynamic_headers(member))
        code = payload.get("code")
        if code != 0:
            message = payload.get("message") or payload.get("msg") or "unknown"
            raise BilibiliError(f"Bilibili dynamic API failed: code={code}, message={message}")
        items = ((payload.get("data") or {}).get("items") or [])
        result = []
        for item in items[: limit or self.config.dynamic_fetch_limit]:
            parsed = self.parse_dynamic_item(member, item)
            if parsed:
                self.cache_dynamic_emojis(parsed.rich_text)
                result.append(parsed)
        return result

    def fetch_latest(self, member: MemberConfig) -> DynamicItem | None:
        items = self.fetch_recent_dynamics(member, limit=1)
        return items[0] if items else None

    @staticmethod
    def parse_dynamic_item(member: MemberConfig, item: dict[str, Any]) -> DynamicItem | None:
        dynamic_id = str(item.get("id_str") or item.get("id") or "")
        if not dynamic_id:
            return None
        return DynamicItem(
            id=dynamic_id,
            uid=member.uid,
            member_name=member.name,
            text=summarize_dynamic(item),
            url=f"https://www.bilibili.com/opus/{dynamic_id}",
            published_at=parse_publish_time(item),
            rich_text=extract_dynamic_rich_text(item),
        )

    def cache_dynamic_emojis(self, segments: tuple[DynamicTextSegment, ...]) -> None:
        for segment in segments:
            if segment.type != "emoji" or not segment.url:
                continue
            path = emoji_cache_path(segment.url)
            if path.exists():
                continue
            try:
                data = self.http.get_bytes(
                    segment.url,
                    headers={
                        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                        "Referer": "https://www.bilibili.com/",
                    },
                )
                path.write_bytes(data)
            except Exception as exc:
                LOGGER.debug("Bilibili emoji cache failed: %s", exc)

    @staticmethod
    def room_id_from_url(url: str) -> str | None:
        match = re.search(r"live\.bilibili\.com/(\d+)", url)
        return match.group(1) if match else None

    def fetch_live_room_status(self, member: MemberConfig) -> LiveRoomStatus | None:
        room_id = self.room_id_from_url(member.live_url)
        if not room_id:
            return None
        url = f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}"
        payload = self.http.get_json(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": member.live_url,
            },
        )
        code = payload.get("code")
        if code != 0:
            message = payload.get("message") or payload.get("msg") or "unknown"
            raise BilibiliError(f"Bilibili live API failed: code={code}, message={message}")
        data = payload.get("data") or {}
        return LiveRoomStatus(
            member_id=member.id,
            member_name=member.name,
            room_id=room_id,
            live_status=int(data.get("live_status") or 0),
            title=str(data.get("title") or ""),
            live_time=str(data.get("live_time") or "") or None,
        )
