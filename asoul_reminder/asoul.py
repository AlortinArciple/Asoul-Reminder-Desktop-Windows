from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

from .config import AppConfig
from .http import HttpClient
from .models import LiveEvent, MemberConfig

STATUS_LABELS = {
    "published": "已发布",
    "live": "正在直播",
    "rescheduled": "已改期",
    "cancelled": "已取消",
}

LOGGER = logging.getLogger(__name__)


class AsoulParseError(RuntimeError):
    pass


class AsoulCalendarAccessError(RuntimeError):
    pass


def solid_deserialize(value: Any) -> Any:
    if not isinstance(value, list) or len(value) != 2:
        return value
    tag, payload = value
    if tag == 0:
        if isinstance(payload, dict):
            return {key: solid_deserialize(item) for key, item in payload.items()}
        return payload
    if tag == 1:
        return [solid_deserialize(item) for item in payload]
    if tag == 3:
        return datetime.fromisoformat(str(payload).replace("Z", "+00:00"))
    return payload


def parse_initial_events(document: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(document, "html.parser")
    islands = soup.find_all("astro-island", attrs={"component-export": "FilterableCalendar"})
    for island in islands:
        raw_props = island.get("props")
        if not raw_props:
            continue
        props = json.loads(html.unescape(raw_props))
        decoded = solid_deserialize([0, props])
        events = decoded.get("initialEvents")
        if isinstance(events, list):
            return events
    raise AsoulParseError("未找到 FilterableCalendar initialEvents")


def extract_card_state(document: str) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    soup = BeautifulSoup(document, "html.parser")
    for card in soup.select(".event-card-wrapper > div[style]"):
        style = card.get("style", "")
        color_match = re.search(r"background-color:\s*(#[0-9a-fA-F]{6})", style)
        title = card.get("title", "")
        classes = set(card.get("class", []))
        color = color_match.group(1).upper() if color_match else None
        for link in card.find_all("a", href=True):
            href = link["href"]
            opus_match = re.search(r"/opus/(\d+)", href)
            if opus_match:
                state[opus_match.group(1)] = {
                    "title": title,
                    "color": color,
                    "grayscale": "grayscale" in classes,
                    "classes": sorted(classes),
                }
    return state


def member_for_event(event: dict[str, Any], members: dict[str, MemberConfig]) -> MemberConfig | None:
    host = event.get("host")
    if host in members and members[host].enabled and members[host].monitor_live:
        return members[host]
    for performer in event.get("performers") or []:
        if performer in members and members[performer].enabled and members[performer].monitor_live:
            return members[performer]
    return None


def event_from_payload(
    event: dict[str, Any],
    member: MemberConfig,
    card_state: dict[str, dict[str, Any]],
) -> LiveEvent:
    source_id = str(event.get("sourceId") or "")
    update_source_id = event.get("updateSourceId")
    card = card_state.get(str(update_source_id or source_id), {})
    dynamic_id = update_source_id or source_id
    dynamic_url = f"https://www.bilibili.com/opus/{dynamic_id}" if dynamic_id else None
    start_time = event["startTime"]
    if isinstance(start_time, str):
        start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    return LiveEvent(
        id=str(event["id"]),
        title=str(event.get("title") or ""),
        description=str(event.get("description") or ""),
        type=str(event.get("type") or ""),
        format=str(event.get("format") or ""),
        start_time=start_time,
        host=str(event.get("host") or ""),
        performers=tuple(str(item) for item in event.get("performers") or []),
        status=str(event.get("status") or "published"),
        source_id=source_id,
        update_source_id=str(update_source_id) if update_source_id else None,
        dynamic_url=dynamic_url,
        live_url=member.live_url or None,
        color=member.color.upper(),
    )


class AsoulClient:
    def __init__(self, config: AppConfig, http: HttpClient) -> None:
        self.config = config
        self.http = http

    def fetch_events(self) -> list[LiveEvent]:
        document, source = self.fetch_calendar_document()
        try:
            payload_events = parse_initial_events(document)
        except AsoulParseError as exc:
            if source == "home_fallback":
                raise AsoulCalendarAccessError(
                    "ASOUL 日程接口返回 403，首页兜底页面中也没有找到日程数据。"
                ) from exc
            raise
        card_state = extract_card_state(document)
        members = self.config.member_by_id
        result: list[LiveEvent] = []
        for payload in payload_events:
            if payload.get("format") != "live":
                continue
            member = member_for_event(payload, members)
            if not member:
                continue
            result.append(event_from_payload(payload, member, card_state))
        return sorted(result, key=lambda item: item.start_time)

    def fetch_calendar_document(self) -> tuple[str, str]:
        bypass_proxy = self.config.asoul_bypass_proxy
        LOGGER.info(
            "ASOUL 日程请求网络模式：%s",
            "直连（忽略系统代理）" if bypass_proxy else "跟随系统代理",
        )
        try:
            return (
                self.http.get_text(
                    self.config.asoul_calendar_url,
                    bypass_proxy=bypass_proxy,
                ),
                "calendar",
            )
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code != 403:
                raise
            fallback_url = self.config.asoul_home_url.strip()
            if not fallback_url or fallback_url == self.config.asoul_calendar_url:
                raise AsoulCalendarAccessError(
                    "ASOUL 日程接口返回 403：站点可能启用了访问验证。"
                    "如果正在使用代理/梯子：请确认设置中「日程绕过系统代理」已开启；"
                    "TUN/全局模式需在梯子规则里把 asoul.love 设为 DIRECT。"
                ) from exc
            LOGGER.info("ASOUL 日程接口返回 403，自动改用首页兜底：%s", fallback_url)
            try:
                return (
                    self.http.get_text(
                        fallback_url,
                        bypass_proxy=bypass_proxy,
                    ),
                    "home_fallback",
                )
            except Exception as fallback_exc:
                raise AsoulCalendarAccessError(
                    "ASOUL 日程接口返回 403，首页兜底也失败。"
                    "如果正在使用代理/梯子：请确认设置中「日程绕过系统代理」已开启；"
                    "TUN/全局模式需在梯子规则里把 asoul.love 设为 DIRECT；"
                    "也可以稍后重试，或在设置里更新 ASOUL 日程地址。"
                ) from fallback_exc


def event_to_state(event: LiveEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "type": event.type,
        "format": event.format,
        "start_time": event.start_time.isoformat(),
        "host": event.host,
        "performers": list(event.performers),
        "status": event.status,
        "source_id": event.source_id,
        "update_source_id": event.update_source_id,
        "dynamic_url": event.dynamic_url,
        "live_url": event.live_url,
        "color": event.color,
        "fingerprint": event.fingerprint,
    }


def event_from_state(data: dict[str, Any]) -> LiveEvent:
    return LiveEvent(
        id=data["id"],
        title=data.get("title", ""),
        description=data.get("description", ""),
        type=data.get("type", ""),
        format=data.get("format", "live"),
        start_time=datetime.fromisoformat(data["start_time"]),
        host=data.get("host", ""),
        performers=tuple(data.get("performers") or []),
        status=data.get("status", "published"),
        source_id=data.get("source_id", ""),
        update_source_id=data.get("update_source_id"),
        dynamic_url=data.get("dynamic_url"),
        live_url=data.get("live_url"),
        color=data.get("color"),
    )


def describe_change(old: LiveEvent | None, new: LiveEvent | None) -> str:
    if old is None and new is not None:
        return "新增直播"
    if old is not None and new is None:
        return "删除直播"
    if old is None or new is None:
        return "直播变动"
    if new.status == "cancelled":
        return "直播取消"
    if new.status == "rescheduled":
        return "直播改期"
    if old.start_time != new.start_time:
        return "直播时间变动"
    if old.title != new.title or old.description != new.description:
        return "直播标题变动"
    if old.host != new.host or old.performers != new.performers:
        return "直播成员变动"
    return "直播变动"
