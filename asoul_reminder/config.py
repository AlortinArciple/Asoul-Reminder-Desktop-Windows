from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fileio import atomic_write_text
from .models import MemberConfig
from .paths import default_config_path, example_config_path

MEMBER_ID_ALIASES = {
    "sinno": "gladys",
}
ASOUL_MIN_CHECK_INTERVAL_MINUTES = 30
# 后加入的成员：老配置里这两项是空的，动态开关会静默失效。
# 读取配置时只在字段为空的情况下补齐，不覆盖用户自己填写的值。
DEFAULT_MEMBER_IDENTITIES: dict[str, tuple[str, str]] = {
    "fiona": (
        "3537115310721181",
        "https://space.bilibili.com/3537115310721181/dynamic",
    ),
    "gladys": (
        "3537115310721781",
        "https://space.bilibili.com/3537115310721781/dynamic",
    ),
}


@dataclass(frozen=True)
class AppConfig:
    asoul_calendar_url: str
    asoul_home_url: str
    asoul_bypass_proxy: bool
    check_interval_minutes: int
    dynamic_check_interval_minutes: int
    dynamic_high_frequency_interval_minutes: int
    dynamic_high_frequency_max_minutes: int
    live_status_open_check_interval_seconds: int
    live_status_close_check_interval_minutes: int
    dynamic_fetch_limit: int
    suppress_live_start_dynamic_window_minutes: int
    write_snapshots: bool
    live_remind_before_minutes: tuple[int, ...]
    auto_start: bool
    notifications_enabled: bool
    bilibili_cookie: str
    request_timeout_seconds: int
    members: tuple[MemberConfig, ...]
    live_sound_file: str = ""
    live_sound_loop_seconds: int = 5

    @property
    def member_by_id(self) -> dict[str, MemberConfig]:
        members = {member.id: member for member in self.members}
        for alias, canonical in MEMBER_ID_ALIASES.items():
            if canonical in members:
                members[alias] = members[canonical]
        return members

    @property
    def enabled_live_members(self) -> tuple[MemberConfig, ...]:
        return tuple(member for member in self.members if member.enabled and member.monitor_live)

    @property
    def enabled_dynamic_members(self) -> tuple[MemberConfig, ...]:
        return tuple(
            member
            for member in self.members
            if member.enabled and member.monitor_dynamic and member.uid
        )


def ensure_config(path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    if not config_path.exists():
        shutil.copyfile(example_config_path(), config_path)
    return config_path


def canonical_member_id(member_id: str) -> str:
    return MEMBER_ID_ALIASES.get(member_id, member_id)


def normalize_member_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    normalized["id"] = canonical_member_id(str(normalized.get("id") or ""))
    normalized["live_sound_enabled"] = bool(normalized.get("live_sound_enabled", True))
    normalized["live_sound_file"] = str(normalized.get("live_sound_file", ""))
    normalized["priority"] = int(normalized.get("priority", 0))
    return normalized


def fill_default_identities(raw: dict[str, Any]) -> bool:
    """给已知成员补上空的 uid / dynamic_url，返回是否发生了改动。

    只填空字符串字段，用户自己填过的值一律保留。
    """
    members = raw.get("members")
    if not isinstance(members, list):
        return False
    changed = False
    for item in members:
        if not isinstance(item, dict):
            continue
        member_id = canonical_member_id(str(item.get("id") or ""))
        identity = DEFAULT_MEMBER_IDENTITIES.get(member_id)
        if identity is None:
            continue
        uid, dynamic_url = identity
        if not str(item.get("uid") or "").strip():
            item["uid"] = uid
            changed = True
        if not str(item.get("dynamic_url") or "").strip():
            item["dynamic_url"] = dynamic_url
            changed = True
    return changed


def read_config_data(path: Path | None = None) -> tuple[Path, dict[str, Any]]:
    config_path = ensure_config(path)
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("config.json 顶层必须是对象")
    if fill_default_identities(raw):
        try:
            write_config_data(raw, config_path)
        except (OSError, ValueError):
            # 配置只读或暂时不可写时仍按补全后的内容运行，不阻断启动。
            pass
    return config_path, raw


def config_from_data(raw: dict[str, Any]) -> AppConfig:
    members_raw = raw.get("members")
    if not isinstance(members_raw, list):
        raise ValueError("config.json 的 members 必须是列表")
    members = tuple(
        MemberConfig(**normalize_member_item(item))
        for item in members_raw
        if isinstance(item, dict)
    )
    if len(members) != len(members_raw):
        raise ValueError("config.json 包含无效成员配置")
    return AppConfig(
        asoul_calendar_url=str(raw["asoul_calendar_url"]),
        asoul_home_url=str(raw["asoul_home_url"]),
        asoul_bypass_proxy=bool(raw.get("asoul_bypass_proxy", True)),
        check_interval_minutes=max(
            ASOUL_MIN_CHECK_INTERVAL_MINUTES,
            int(raw["check_interval_minutes"]),
        ),
        dynamic_check_interval_minutes=int(raw["dynamic_check_interval_minutes"]),
        dynamic_high_frequency_interval_minutes=int(
            raw["dynamic_high_frequency_interval_minutes"]
        ),
        dynamic_high_frequency_max_minutes=int(raw["dynamic_high_frequency_max_minutes"]),
        live_status_open_check_interval_seconds=int(
            raw.get(
                "live_status_open_check_interval_seconds",
                int(raw.get("live_status_open_check_interval_minutes", 1)) * 60,
            )
        ),
        live_status_close_check_interval_minutes=int(
            raw.get(
                "live_status_close_check_interval_minutes",
                raw.get("live_status_check_interval_minutes", 20),
            )
        ),
        dynamic_fetch_limit=int(raw.get("dynamic_fetch_limit", 3)),
        suppress_live_start_dynamic_window_minutes=int(
            raw.get("suppress_live_start_dynamic_window_minutes", 5)
        ),
        write_snapshots=bool(raw.get("write_snapshots", True)),
        live_remind_before_minutes=tuple(int(v) for v in raw["live_remind_before_minutes"]),
        auto_start=bool(raw["auto_start"]),
        notifications_enabled=bool(raw["notifications_enabled"]),
        bilibili_cookie=str(raw.get("bilibili_cookie", "")),
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 20)),
        live_sound_file=str(raw.get("live_sound_file", "")),
        live_sound_loop_seconds=max(0, min(60, int(raw.get("live_sound_loop_seconds", 5)))),
        members=members,
    )


def load_config(path: Path | None = None) -> AppConfig:
    _, raw = read_config_data(path)
    return config_from_data(raw)


def write_config_data(raw: dict[str, Any], path: Path | None = None) -> AppConfig:
    config_path = path or default_config_path()
    config = config_from_data(raw)
    atomic_write_text(
        config_path,
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config


def update_config_values(
    values: dict[str, Any],
    path: Path | None = None,
) -> AppConfig:
    config_path, raw = read_config_data(path)
    updated = dict(raw)
    updated.update(values)
    return write_config_data(updated, config_path)


def update_config_value(key: str, value: Any, path: Path | None = None) -> None:
    update_config_values({key: value}, path)


def update_member_config(
    member_id: str,
    values: dict[str, Any],
    path: Path | None = None,
) -> None:
    config_path, raw = read_config_data(path)
    allowed_keys = {
        "name",
        "enabled",
        "monitor_dynamic",
        "monitor_live",
        "uid",
        "dynamic_url",
        "live_url",
        "avatar_path",
        "live_sound_enabled",
        "live_sound_file",
        "priority",
    }
    member_values = {key: value for key, value in values.items() if key in allowed_keys}
    canonical_id = canonical_member_id(member_id)
    for member in raw.get("members", []):
        if canonical_member_id(str(member.get("id") or "")) == canonical_id:
            member["id"] = canonical_id
            member.update(member_values)
            write_config_data(raw, config_path)
            return
    raise KeyError(f"Unknown member id: {member_id}")
