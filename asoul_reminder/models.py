from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MemberConfig:
    id: str
    name: str
    enabled: bool
    monitor_dynamic: bool
    monitor_live: bool
    color: str
    uid: str
    dynamic_url: str
    live_url: str
    avatar_path: str = ""
    live_sound_enabled: bool = True
    live_sound_file: str = ""
    priority: int = 0


@dataclass(frozen=True)
class LiveEvent:
    id: str
    title: str
    description: str
    type: str
    format: str
    start_time: datetime
    host: str
    performers: tuple[str, ...]
    status: str
    source_id: str
    update_source_id: str | None
    dynamic_url: str | None
    live_url: str | None
    color: str | None

    @property
    def is_changed(self) -> bool:
        return self.status in {"cancelled", "rescheduled"}

    @property
    def fingerprint(self) -> str:
        performers = ",".join(self.performers)
        return "|".join(
            [
                self.id,
                self.title,
                self.description,
                self.start_time.isoformat(),
                self.host,
                performers,
                self.status,
                self.update_source_id or "",
            ]
        )


@dataclass(frozen=True)
class DynamicTextSegment:
    type: str
    text: str
    url: str = ""


@dataclass(frozen=True)
class DynamicItem:
    id: str
    uid: str
    member_name: str
    text: str
    url: str
    published_at: datetime | None
    rich_text: tuple[DynamicTextSegment, ...] = ()


@dataclass(frozen=True)
class LiveRoomStatus:
    member_id: str
    member_name: str
    room_id: str
    live_status: int
    title: str
    live_time: str | None

    @property
    def is_live(self) -> bool:
        return self.live_status == 1
