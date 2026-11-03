from __future__ import annotations

from datetime import datetime

from .asoul import STATUS_LABELS
from .models import LiveEvent
from .timeutils import as_beijing_time

WEEKDAY_TEXT = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def weekday_text(value: datetime) -> str:
    return WEEKDAY_TEXT[as_beijing_time(value).weekday()]


def local_time_text(value: datetime) -> str:
    return f"{as_beijing_time(value).strftime('%Y-%m-%d %H:%M')} {weekday_text(value)}"


def live_event_summary(event: LiveEvent, member_name: str | None = None) -> str:
    name = member_name or event.host
    desc = f"：{event.description}" if event.description else ""
    status = STATUS_LABELS.get(event.status, event.status)
    return f"{name} {event.title}{desc}\n{local_time_text(event.start_time)} · {status}"


def truncate_text(value: str, max_len: int = 100) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max_len - 1] + "..."

