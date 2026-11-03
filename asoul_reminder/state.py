from __future__ import annotations

import copy
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fileio import atomic_write_text
from .paths import state_path

MEMBER_ID_ALIASES = {
    "sinno": "gladys",
}

MEMBER_KEYED_FIELDS = (
    "last_dynamic_check",
    "last_live_status_check",
    "live_room_status",
    "live_member_open_times",
    "latest_dynamic_id",
    "latest_dynamic_items",
    "seen_dynamic_ids",
    "notified_dynamic_ids",
    "high_frequency_dynamic",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "first_run_completed": False,
        "paused": False,
        "monitor_process": {},
        "last_asoul_check": None,
        "asoul_fail_streak": 0,
        "last_dynamic_check": {},
        "last_live_status_check": {},
        "live_events": {},
        "live_room_status": {},
        "live_event_open_times": {},
        "live_event_end_times": {},
        "live_member_open_times": {},
        "notified_live_reminders": {},
        "notified_live_started": {},
        "notified_live_sessions": {},
        "notified_changes": {},
        "latest_dynamic_id": {},
        "latest_dynamic_items": {},
        "seen_dynamic_ids": {},
        "notified_dynamic_ids": {},
        "suppressed_dynamic_ids": {},
        "high_frequency_dynamic": {},
        "high_frequency_completed_events": {},
        "notification_outbox": {},
        "suppressed_notifications": {},
        "last_errors": {},
        "bilibili_cookie_expired": False,
        "cookie_check": None,
    }


class StateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or state_path()
        self.lock = threading.RLock()
        self.data = self._load()
        self._published_data = copy.deepcopy(self.data)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_state()
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = self.path.with_suffix(".corrupt.json")
            self.path.replace(backup)
            return empty_state()
        base = empty_state()
        base.update(loaded)
        migrate_member_aliases(base)
        return base

    def save(self) -> None:
        with self.lock:
            atomic_write_text(
                self.path,
                json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._published_data = copy.deepcopy(self.data)

    def snapshot(self) -> dict[str, Any]:
        """Return the last fully saved state without exposing mutable service data."""
        with self.lock:
            return copy.deepcopy(self._published_data)

    def set_error(self, key: str, message: str | None) -> None:
        with self.lock:
            if message:
                self.data["last_errors"][key] = {
                    "message": message,
                    "time": utc_now_iso(),
                }
            else:
                self.data["last_errors"].pop(key, None)
            self.save()


def migrate_member_aliases(data: dict[str, Any]) -> None:
    for field in MEMBER_KEYED_FIELDS:
        values = data.get(field)
        if not isinstance(values, dict):
            continue
        for alias, canonical in MEMBER_ID_ALIASES.items():
            if alias in values:
                values.setdefault(canonical, values[alias])
                values.pop(alias, None)

    errors = data.get("last_errors")
    if not isinstance(errors, dict):
        return
    for alias, canonical in MEMBER_ID_ALIASES.items():
        for prefix in ("dynamic", "live_status"):
            old_key = f"{prefix}:{alias}"
            new_key = f"{prefix}:{canonical}"
            if old_key in errors:
                errors.setdefault(new_key, errors[old_key])
                errors.pop(old_key, None)
