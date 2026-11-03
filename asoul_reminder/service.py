from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from queue import Empty, Queue
from time import monotonic
from typing import Any

from .asoul import (
    AsoulClient,
    describe_change,
    event_from_state,
    event_to_state,
)
from .bilibili import BilibiliClient, BilibiliError
from .bilibili_login import (
    COOKIE_EXPIRED_MESSAGE,
    STATUS_EXPIRED,
    STATUS_UNKNOWN,
    STATUS_VALID,
    BilibiliLogin,
    CookieStatus,
)
from .config import ASOUL_MIN_CHECK_INTERVAL_MINUTES, AppConfig
from .fileio import atomic_write_text
from .formatting import live_event_summary, local_time_text, truncate_text
from .models import DynamicItem, LiveEvent, LiveRoomStatus, MemberConfig
from .notifications import Notifier
from .paths import asoul_snapshot_path, dynamic_snapshot_path
from .state import StateStore, utc_now_iso
from .version import version_text

LOGGER = logging.getLogger(__name__)
LIVE_STATUS_OPEN_WINDOW_BEFORE = timedelta(minutes=10)
LIVE_STATUS_OPEN_WINDOW_AFTER = timedelta(minutes=2)
CLOCK_SKEW_TOLERANCE = timedelta(minutes=1)
# Cookie 失效检测只做低频校验，避免每次动态检查都多打一次 nav。
COOKIE_CHECK_INTERVAL = timedelta(minutes=30)
# 日程「新增/变动」提醒只针对还没结束的日程。站点每周重建日程页时会把几周前的日程
# 重新放回 initialEvents，若不加这条过滤，这些历史日程会被当成新日程逐条弹窗。
SCHEDULE_NOTIFY_GRACE = timedelta(minutes=30)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class ReminderService:
    def __init__(
        self,
        config: AppConfig,
        state: StateStore,
        asoul: AsoulClient,
        bilibili: BilibiliClient,
        notifier: Notifier,
    ) -> None:
        self.config = config
        self.state = state
        self.asoul = asoul
        self.bilibili = bilibili
        self.notifier = notifier
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.command_queue: Queue[tuple[str, Any]] = Queue()
        self.pending_commands: set[str] = set()
        self.command_lock = threading.Lock()
        self._owner_thread_id: int | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, name="AsoulReminderService", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.command_queue.put(("stop", None))
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)

    def run(self) -> None:
        self._owner_thread_id = threading.get_ident()
        try:
            self.update_heartbeat(starting=True)
            self.restore_events_from_snapshot_if_needed()
            try:
                self.check_asoul()
                self.check_due_live_status(now_utc(), force=True)
                self.check_due_dynamics(force=True)
            except Exception:
                LOGGER.exception("Service startup check failed")

            next_tick = monotonic() + 30
            while not self.stop_event.is_set():
                timeout = max(0.0, min(30.0, next_tick - monotonic()))
                command: tuple[str, Any] | None = None
                try:
                    command = self.command_queue.get(timeout=timeout)
                except Empty:
                    pass
                if command is not None:
                    self._handle_command(*command)
                if self.stop_event.is_set():
                    break
                if monotonic() >= next_tick:
                    try:
                        self.update_heartbeat()
                        self.tick()
                    except Exception:
                        LOGGER.exception("Service tick failed")
                    next_tick = monotonic() + 30
        finally:
            self.mark_stopped()
            self._owner_thread_id = None

    def update_heartbeat(self, starting: bool = False) -> None:
        monitor = self.state.data.setdefault("monitor_process", {})
        monitor["pid"] = os.getpid()
        monitor["heartbeat_at"] = utc_now_iso()
        if starting:
            monitor["started_at"] = utc_now_iso()
            monitor.pop("stopped_at", None)
        self.state.save()

    def mark_stopped(self) -> None:
        monitor = self.state.data.setdefault("monitor_process", {})
        monitor["stopped_at"] = utc_now_iso()
        monitor["heartbeat_at"] = utc_now_iso()
        self.state.save()

    def tick(self) -> None:
        current = now_utc()
        if self.is_asoul_check_due(current):
            self.check_asoul(current=current, force=True)
        self.check_live_reminders(current)
        self.check_due_live_status(current)
        self.ensure_high_frequency_windows(current)
        self.check_due_dynamics()
        self.retry_pending_notifications(current)

    def _handle_command(self, name: str, payload: Any) -> None:
        try:
            if name == "check_all":
                self.check_all()
            elif name == "refresh_schedule":
                self.check_asoul(force=True)
            elif name == "set_paused":
                self._set_paused_now(bool(payload))
            elif name != "stop":
                LOGGER.warning("未知后台命令：%s", name)
        except Exception:
            LOGGER.exception("后台命令执行失败：%s", name)
        finally:
            with self.command_lock:
                self.pending_commands.discard(name)

    def _enqueue_command(self, name: str, payload: Any = None) -> bool:
        if name in {"check_all", "refresh_schedule"}:
            with self.command_lock:
                if name in self.pending_commands:
                    LOGGER.info("后台命令已在队列中，忽略重复请求：%s", name)
                    return False
                self.pending_commands.add(name)
        self.command_queue.put((name, payload))
        return True

    def check_all_async(self) -> bool:
        if not self.thread or not self.thread.is_alive():
            self.start()
        return self._enqueue_command("check_all")

    def refresh_schedule_async(self) -> bool:
        if not self.thread or not self.thread.is_alive():
            self.start()
        return self._enqueue_command("refresh_schedule")

    def check_all(self) -> None:
        with self.lock:
            self.restore_events_from_snapshot_if_needed()
            current = now_utc()
            self.check_asoul(current=current)
            self.check_due_live_status(current, force=True)
            self.ensure_high_frequency_windows(current)
            self.check_due_dynamics(force=True)
            self.retry_pending_notifications(current)

    def apply_config(self, config: AppConfig) -> None:
        self.config = config
        self.asoul.config = config
        self.bilibili.config = config
        self.notifier.enabled = config.notifications_enabled
        self.notifier.live_sound_file = config.live_sound_file
        self.notifier.live_sound_loop_seconds = config.live_sound_loop_seconds

    def member_name(self, member_id: str) -> str:
        member = self.config.member_by_id.get(member_id)
        return member.name if member else member_id

    def member_for_event(self, event: LiveEvent) -> MemberConfig | None:
        members = self.config.member_by_id
        if event.host in members:
            return members[event.host]
        for performer in event.performers:
            if performer in members:
                return members[performer]
        return None

    def asoul_check_interval(self) -> timedelta:
        minutes = max(ASOUL_MIN_CHECK_INTERVAL_MINUTES, self.config.check_interval_minutes)
        # 连续失败退避：首次失败仍按基础间隔重试，连续 2 次起逐级拉长（封顶 4 倍）。
        streak = int(self.state.data.get("asoul_fail_streak") or 0)
        if streak >= 2:
            minutes *= min(streak, 4)
        return timedelta(minutes=minutes)

    def is_asoul_check_due(self, current: datetime) -> bool:
        last_asoul = parse_time(self.state.data.get("last_asoul_check"))
        if last_asoul is None:
            return True
        if last_asoul - current > CLOCK_SKEW_TOLERANCE:
            LOGGER.warning("ASOUL 日程检查时间位于未来，将立即重新检查")
            return True
        return current - last_asoul >= self.asoul_check_interval()

    def check_asoul(self, current: datetime | None = None, force: bool = False) -> bool:
        with self.lock:
            current = current or now_utc()
            if not force and not self.is_asoul_check_due(current):
                LOGGER.info(
                    "ASOUL 日程检查跳过：距上次检查不足 %d 分钟",
                    int(self.asoul_check_interval().total_seconds() // 60),
                )
                return False
            self.state.data["last_asoul_check"] = current.isoformat()
            try:
                events = self.asoul.fetch_events()
            except Exception as exc:
                LOGGER.exception("ASOUL check failed")
                self.state.data["asoul_fail_streak"] = (
                    int(self.state.data.get("asoul_fail_streak") or 0) + 1
                )
                self.state.set_error("asoul", str(exc))
                cached_count = len(self.current_events())
                if cached_count:
                    LOGGER.warning("ASOUL 日程获取失败，继续使用本地缓存中的 %d 条日程", cached_count)
                return False
            self.state.data["asoul_fail_streak"] = 0
            self.state.set_error("asoul", None)
            old_raw: dict[str, Any] = self.state.data.get("live_events") or {}
            old_events = {
                event_id: event_from_state(item)
                for event_id, item in old_raw.items()
                if isinstance(item, dict) and item.get("start_time")
            }
            new_events = {event.id: event for event in events}
            if old_events:
                self.notify_schedule_changes(old_events, new_events)
            self.state.data["live_events"] = {
                event_id: event_to_state(event) for event_id, event in new_events.items()
            }
            self.state.save()
            if self.config.write_snapshots:
                self.write_asoul_snapshot(events)
            LOGGER.info("ASOUL 日程检查完成：识别到 %d 条启用成员相关直播", len(events))
            for event in events:
                LOGGER.info(
                    "ASOUL 日程：%s | %s | %s | %s | %s",
                    local_time_text(event.start_time),
                    self.member_name(event.host),
                    event.status,
                    event.id,
                    event.title,
                )
            return True

    def write_asoul_snapshot(self, events: list[LiveEvent]) -> None:
        atomic_write_text(
            asoul_snapshot_path(),
            json.dumps([event_to_state(event) for event in events], ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8-sig",
        )

    def notify_schedule_changes(
        self,
        old_events: dict[str, LiveEvent],
        new_events: dict[str, LiveEvent],
    ) -> None:
        old_ids = set(old_events)
        new_ids = set(new_events)
        for event_id in sorted(new_ids - old_ids):
            self.notify_schedule_change(None, new_events[event_id])
        for event_id in sorted(old_ids - new_ids):
            self.notify_schedule_change(old_events[event_id], None)
        for event_id in sorted(old_ids & new_ids):
            old = old_events[event_id]
            new = new_events[event_id]
            if old.fingerprint != new.fingerprint or new.is_changed:
                self.notify_schedule_change(old, new)

    def notify_schedule_change(self, old: LiveEvent | None, new: LiveEvent | None) -> None:
        event = new or old
        if event is None:
            return
        # 开始超过宽限期的日程视为历史条目，不再提醒（站点重发页面时它们会重新出现）。
        if event.start_time + SCHEDULE_NOTIFY_GRACE < now_utc():
            return
        key = f"{event.id}:{event.fingerprint}:{'removed' if new is None else 'current'}"
        notified = self.state.data.setdefault("notified_changes", {})
        if key in notified:
            return
        title = describe_change(old, new)
        lines = []
        if old and new and old.start_time != new.start_time:
            lines.append(f"原时间：{local_time_text(old.start_time)}")
            lines.append(f"新时间：{local_time_text(new.start_time)}")
        lines.append(live_event_summary(event, self.member_name(event.host)))
        self.dispatch_notification(
            "notified_changes",
            key,
            title,
            "\n".join(lines),
            self.config.asoul_home_url,
        )

    def check_live_reminders(self, current: datetime) -> None:
        for event in self.current_events():
            if event.status in {"cancelled", "rescheduled"}:
                continue
            member = self.member_for_event(event)
            if not member or not member.enabled or not member.monitor_live:
                continue
            for minutes in self.config.live_remind_before_minutes:
                due_at = event.start_time - timedelta(minutes=minutes)
                if due_at <= current < event.start_time:
                    self.notify_live_reminder(event, member, minutes)

    def notify_live_reminder(self, event: LiveEvent, member: MemberConfig, minutes: int) -> None:
        reminders = self.state.data.setdefault("notified_live_reminders", {})
        key = f"{event.id}:{minutes}"
        if key in reminders:
            return
        message = f"{member.name} {event.title}\n{local_time_text(event.start_time)}，还有 {minutes} 分钟"
        if event.description:
            message += f"\n{truncate_text(event.description, 80)}"
        self.dispatch_notification(
            "notified_live_reminders",
            key,
            "直播即将开始",
            message,
            member.live_url or self.config.asoul_home_url,
            sound=bool(member.live_sound_enabled),
            sound_file=member.live_sound_file,
        )
        self.state.save()

    def load_asoul_snapshot_events(self) -> list[LiveEvent]:
        path = asoul_snapshot_path()
        if not path.exists():
            return []
        try:
            rows = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("读取 ASOUL 日程快照失败")
            return []
        if not isinstance(rows, list):
            LOGGER.warning("ASOUL 日程快照格式无效：顶层不是列表")
            return []
        events: list[LiveEvent] = []
        for item in rows:
            if not isinstance(item, dict) or not item.get("start_time"):
                continue
            try:
                events.append(event_from_state(item))
            except Exception:
                LOGGER.exception("ASOUL 日程快照中存在无效事件")
        return events

    def restore_events_from_snapshot_if_needed(self) -> bool:
        if self.state.data.get("live_events"):
            return False
        events = self.load_asoul_snapshot_events()
        if not events:
            return False
        self.state.data["live_events"] = {event.id: event_to_state(event) for event in events}
        self.state.save()
        LOGGER.warning("后台已从本地快照恢复 %d 条 ASOUL 日程", len(events))
        return True

    def current_events(self) -> list[LiveEvent]:
        if self._owner_thread_id is None or threading.get_ident() == self._owner_thread_id:
            raw = self.state.data.get("live_events") or {}
        else:
            raw = self.state.snapshot().get("live_events") or {}
        events: list[LiveEvent] = []
        for item in raw.values():
            if isinstance(item, dict) and item.get("start_time"):
                try:
                    events.append(event_from_state(item))
                except Exception:
                    LOGGER.exception("状态中存在无效日程")
        if events:
            return events
        return self.load_asoul_snapshot_events()

    def dispatch_notification(
        self,
        state_field: str,
        key: str,
        title: str,
        message: str,
        url: str | None,
        *,
        sound: bool = False,
        sound_file: str = "",
    ) -> bool:
        delivered = self.state.data.setdefault(state_field, {})
        if key in delivered:
            return True

        outbox_key = f"{state_field}|{key}"
        outbox = self.state.data.setdefault("notification_outbox", {})
        if not self.should_notify():
            now_iso = utc_now_iso()
            delivered[key] = now_iso
            outbox.pop(outbox_key, None)
            self.state.data.setdefault("suppressed_notifications", {})[outbox_key] = {
                "title": title,
                "time": now_iso,
                "reason": "paused" if self.state.data.get("paused") else "disabled",
            }
            LOGGER.info(
                "提醒已抑制，不会在恢复后补发：category=%s key=%s title=%r",
                state_field,
                key,
                title,
            )
            return False

        sent = self.notifier.notify(
            title,
            message,
            url,
            sound=bool(sound),
            sound_file=str(sound_file or ""),
            tag=f"{state_field}|{key}",
        )
        now_iso = utc_now_iso()
        if sent:
            delivered[key] = now_iso
            outbox.pop(outbox_key, None)
            return True

        previous = outbox.get(outbox_key) or {}
        outbox[outbox_key] = {
            "state_field": state_field,
            "state_key": key,
            "title": title,
            "message": message,
            "url": url or "",
            "sound": bool(sound),
            "sound_file": str(sound_file or ""),
            "attempts": int(previous.get("attempts") or 0) + 1,
            "last_attempt_at": now_iso,
        }
        LOGGER.warning("Windows 通知进入重试队列：category=%s key=%s", state_field, key)
        return False

    def retry_pending_notifications(
        self,
        current: datetime | None = None,
        *,
        force: bool = False,
    ) -> None:
        current = current or now_utc()
        outbox = self.state.data.setdefault("notification_outbox", {})
        changed = False
        for outbox_key, item in list(outbox.items()):
            if not isinstance(item, dict):
                outbox.pop(outbox_key, None)
                changed = True
                continue
            last_attempt = parse_time(item.get("last_attempt_at"))
            if (
                not force
                and last_attempt is not None
                and current - self.time_as_utc(last_attempt) < timedelta(minutes=1)
            ):
                continue
            state_field = str(item.get("state_field") or "")
            state_key = str(item.get("state_key") or "")
            if not state_field or not state_key:
                outbox.pop(outbox_key, None)
                changed = True
                continue
            delivered = self.state.data.setdefault(state_field, {})
            if not self.should_notify():
                now_iso = utc_now_iso()
                delivered[state_key] = now_iso
                self.state.data.setdefault("suppressed_notifications", {})[outbox_key] = {
                    "title": str(item.get("title") or ""),
                    "time": now_iso,
                    "reason": "paused" if self.state.data.get("paused") else "disabled",
                }
                outbox.pop(outbox_key, None)
                changed = True
                continue
            sent = self.notifier.notify(
                str(item.get("title") or ""),
                str(item.get("message") or ""),
                str(item.get("url") or "") or None,
                sound=bool(item.get("sound")),
                sound_file=str(item.get("sound_file") or ""),
                tag=f"{state_field}|{state_key}",
            )
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["last_attempt_at"] = utc_now_iso()
            changed = True
            if sent:
                delivered[state_key] = item["last_attempt_at"]
                outbox.pop(outbox_key, None)
                LOGGER.info(
                    "Windows 通知重试成功：category=%s key=%s",
                    state_field,
                    state_key,
                )
        if changed:
            self.state.save()

    def events_requiring_live_status(self, current: datetime) -> list[LiveEvent]:
        result = []
        for event in self.current_events():
            if event.status == "rescheduled":
                continue
            if event.id in (self.state.data.get("high_frequency_completed_events") or {}):
                continue
            member = self.member_for_event(event)
            if not member or not member.enabled or not member.monitor_live or not member.live_url:
                continue
            if self.event_needs_live_status_watch(event, member, current):
                result.append(event)
        return result

    def event_needs_live_status_watch(
        self, event: LiveEvent, member: MemberConfig, current: datetime
    ) -> bool:
        start_time = self.time_as_utc(event.start_time)
        current = self.time_as_utc(current)
        if event.id in (self.state.data.get("live_event_end_times") or {}):
            return False
        if event.id in (self.state.data.get("live_event_open_times") or {}):
            return True
        return start_time - LIVE_STATUS_OPEN_WINDOW_BEFORE <= current <= start_time + LIVE_STATUS_OPEN_WINDOW_AFTER

    def live_status_interval_for_member(
        self, member: MemberConfig, candidates: list[LiveEvent]
    ) -> timedelta:
        open_times = self.state.data.get("live_event_open_times") or {}
        if any(event.id not in open_times for event in candidates):
            return timedelta(seconds=max(1, self.config.live_status_open_check_interval_seconds))
        return timedelta(minutes=max(1, self.config.live_status_close_check_interval_minutes))

    def should_check_live_status_now(
        self,
        current: datetime,
        last: datetime | None,
        interval: timedelta,
        force: bool = False,
    ) -> bool:
        if force:
            return True
        current = self.time_as_utc(current)
        if last is None:
            return True
        last = self.time_as_utc(last)
        if last - current > CLOCK_SKEW_TOLERANCE:
            return True
        return current - last >= interval

    def check_due_live_status(self, current: datetime, force: bool = False) -> None:
        candidates = self.events_requiring_live_status(current)
        members: dict[str, MemberConfig] = {}
        candidates_by_member: dict[str, list[LiveEvent]] = {}
        for event in candidates:
            member = self.member_for_event(event)
            if member:
                members[member.id] = member
                candidates_by_member.setdefault(member.id, []).append(event)
        for member in self.config.enabled_live_members:
            if member.live_url:
                members.setdefault(member.id, member)
        if not members:
            return
        last_checks = self.state.data.setdefault("last_live_status_check", {})
        due: list[tuple[MemberConfig, list[LiveEvent]]] = []
        for member in members.values():
            candidates_for_member = candidates_by_member.get(member.id, [])
            interval = self.live_status_interval_for_member(member, candidates_for_member)
            last = parse_time(last_checks.get(member.id))
            if self.should_check_live_status_now(current, last, interval, force):
                due.append((member, candidates_for_member))
        if not due:
            return
        # 低优先级先发通知、高优先级后发：Windows 通知中心按时间倒序，高优先级显示在顶部。
        # 提示音只给本轮最高优先级的成员，避免多个提示音互相打断；若其未开播则顺延给实际开播者。
        due.sort(key=lambda item: item[0].priority)
        top_priority = max(member.priority for member, _ in due)
        sound_used = False
        for member, candidates_for_member in due:
            allow_sound = member.priority == top_priority and not sound_used
            if self.check_member_live_status(member, candidates_for_member, allow_sound=allow_sound):
                sound_used = True

    def check_member_live_status(
        self,
        member: MemberConfig,
        candidates: list[LiveEvent],
        *,
        allow_sound: bool = True,
    ) -> bool:
        """返回本次检查是否实际发出了带提示音的开播通知。"""
        attempted_at = utc_now_iso()
        self.state.data.setdefault("last_live_status_check", {})[member.id] = attempted_at
        try:
            status = self.bilibili.fetch_live_room_status(member)
        except BilibiliError as exc:
            LOGGER.warning("直播间状态检查失败：%s: %s", member.name, exc)
            self.state.set_error(f"live_status:{member.id}", str(exc))
            return False
        except Exception as exc:
            LOGGER.exception("直播间状态检查失败：%s", member.name)
            self.state.set_error(f"live_status:{member.id}", str(exc))
            return False
        if not status:
            self.state.save()
            return False
        self.state.set_error(f"live_status:{member.id}", None)
        now_iso = utc_now_iso()
        live_statuses = self.state.data.setdefault("live_room_status", {})
        previous = live_statuses.get(member.id) or {}
        previous_status = previous.get("live_status")
        session_key = str(previous.get("session_key") or "")
        if status.live_status == 1 and (previous_status != 1 or not session_key):
            session_key = str(status.live_time or now_iso)
        live_statuses[member.id] = {
            "room_id": status.room_id,
            "live_status": status.live_status,
            "is_live": status.is_live,
            "title": status.title,
            "live_time": status.live_time,
            "session_key": session_key if status.live_status == 1 else "",
            "checked_at": now_iso,
        }
        LOGGER.info(
            "直播间状态：%s live_status=%s title=%s",
            member.name,
            status.live_status,
            status.title,
        )
        member_open_times = self.state.data.setdefault("live_member_open_times", {})
        played_sound = False
        if status.live_status == 1:
            opened_event = self.find_event_for_live_start(member, candidates)
            if opened_event:
                open_times = self.state.data.setdefault("live_event_open_times", {})
                if opened_event.id not in open_times:
                    open_times[opened_event.id] = now_iso
                    LOGGER.info("检测到直播开播：%s %s", member.name, opened_event.id)
                member_open_times[member.id] = {
                    "event_id": opened_event.id,
                    "session_key": session_key,
                    "opened_at": open_times.get(opened_event.id) or now_iso,
                }
                played_sound = (
                    self.notify_live_started(
                        opened_event, member, status, allow_sound=allow_sound
                    )
                    or played_sound
                )
            else:
                synthetic_event_id = f"room:{member.id}:{session_key}"
                member_open_times[member.id] = {
                    "event_id": synthetic_event_id,
                    "session_key": session_key,
                    "opened_at": now_iso,
                }
                if previous_status != 1:
                    LOGGER.info("检测到无匹配日程的直播开播：%s %s", member.name, session_key)
                played_sound = (
                    self.notify_unscheduled_live_started(
                        member, status, session_key, allow_sound=allow_sound
                    )
                    or played_sound
                )
        if previous_status == 1 and status.live_status != 1:
            ended_event = self.find_event_for_live_end(member, candidates)
            open_info = member_open_times.pop(member.id, {}) or {}
            event_id = ended_event.id if ended_event else str(
                open_info.get("event_id") or f"room:{member.id}:{previous.get('session_key') or now_iso}"
            )
            self.state.data.setdefault("live_event_end_times", {})[event_id] = now_iso
            LOGGER.info("检测到直播结束：%s %s", member.name, event_id)
            self.refresh_dynamic_after_live_end(
                member,
                ended_event or event_id,
                parse_time(now_iso) or now_utc(),
            )
        self.state.save()
        return played_sound

    def notify_live_started(
        self,
        event: LiveEvent,
        member: MemberConfig,
        status: LiveRoomStatus,
        *,
        allow_sound: bool = True,
    ) -> bool:
        """返回是否实际发出了带提示音的通知(供本轮提示音仲裁使用)。"""
        notifications = self.state.data.setdefault("notified_live_started", {})
        if event.id in notifications:
            return False
        lines = [f"{member.name} {event.title}", local_time_text(event.start_time)]
        if status.title:
            lines.append(f"直播间：{truncate_text(status.title, 80)}")
        if event.status == "cancelled":
            lines.append("原日程状态：已取消")
        sound = bool(member.live_sound_enabled) and allow_sound
        self.dispatch_notification(
            "notified_live_started",
            event.id,
            "直播已开始",
            "\n".join(lines),
            member.live_url or event.live_url or self.config.asoul_home_url,
            sound=sound,
            sound_file=member.live_sound_file,
        )
        return sound

    def notify_unscheduled_live_started(
        self,
        member: MemberConfig,
        status: LiveRoomStatus,
        session_key: str,
        *,
        allow_sound: bool = True,
    ) -> bool:
        """返回是否实际发出了带提示音的通知(供本轮提示音仲裁使用)。"""
        key = f"{member.id}:{session_key}"
        notifications = self.state.data.setdefault("notified_live_sessions", {})
        if key in notifications:
            return False
        lines = [f"{member.name} 的直播已开始"]
        if status.title:
            lines.append(f"直播间：{truncate_text(status.title, 80)}")
        sound = bool(member.live_sound_enabled) and allow_sound
        self.dispatch_notification(
            "notified_live_sessions",
            key,
            "直播已开始",
            "\n".join(lines),
            member.live_url or self.config.asoul_home_url,
            sound=sound,
            sound_file=member.live_sound_file,
        )
        return sound

    def refresh_dynamic_after_live_end(
        self, member: MemberConfig, event: LiveEvent | str, ended_at: datetime
    ) -> None:
        if not member.enabled or not member.monitor_dynamic or not member.uid:
            return
        if not self.config.bilibili_cookie.strip():
            self.mark_missing_bilibili_cookie()
            return
        event_id = event.id if isinstance(event, LiveEvent) else event
        new_items = self.check_member_dynamic(member)
        if self.dynamic_items_at_or_after(new_items, ended_at):
            self.state.data.setdefault("high_frequency_completed_events", {})[event_id] = utc_now_iso()
            return
        self.start_high_frequency_window(member, event_id, ended_at, "live_end")

    def start_high_frequency_window(
        self,
        member: MemberConfig,
        event: LiveEvent | str,
        started_at: datetime,
        trigger: str,
    ) -> None:
        event_id = event.id if isinstance(event, LiveEvent) else event
        active = self.state.data.setdefault("high_frequency_dynamic", {})
        completed = self.state.data.setdefault("high_frequency_completed_events", {})
        if event_id in completed or member.id in active:
            return
        started_at = self.time_as_utc(started_at)
        expires_at = started_at + timedelta(minutes=self.config.dynamic_high_frequency_max_minutes)
        active[member.id] = {
            "event_id": event_id,
            "started_at": started_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "trigger": trigger,
        }
        LOGGER.info("动态高频检查开始：%s，事件 %s，触发方式 %s", member.name, event_id, trigger)
        self.state.save()

    def find_event_for_live_start(
        self, member: MemberConfig, candidates: list[LiveEvent]
    ) -> LiveEvent | None:
        current = now_utc()
        ended = self.state.data.get("live_event_end_times") or {}
        member_events = [
            event
            for event in candidates
            if self.member_for_event(event) and self.member_for_event(event).id == member.id
            and event.id not in ended
            and event.start_time - timedelta(minutes=10) <= current
        ]
        if not member_events:
            return None
        member_events.sort(key=lambda event: event.start_time, reverse=True)
        return member_events[0]

    def find_event_for_live_end(
        self, member: MemberConfig, candidates: list[LiveEvent]
    ) -> LiveEvent | None:
        current = now_utc()
        member_events = [
            event
            for event in candidates
            if self.member_for_event(event) and self.member_for_event(event).id == member.id
            and event.start_time <= current
        ]
        if not member_events:
            return None
        member_events.sort(key=lambda event: event.start_time, reverse=True)
        return member_events[0]

    def ensure_high_frequency_windows(self, current: datetime) -> None:
        active = self.state.data.setdefault("high_frequency_dynamic", {})
        completed = self.state.data.setdefault("high_frequency_completed_events", {})
        changed = False
        for member_id, window in list(active.items()):
            expires_at = parse_time(window.get("expires_at"))
            if expires_at and current >= expires_at:
                event_id = window.get("event_id")
                if event_id:
                    completed[event_id] = utc_now_iso()
                active.pop(member_id, None)
                changed = True
        for event in self.current_events():
            if event.status in {"cancelled", "rescheduled"}:
                continue
            member = self.member_for_event(event)
            if not member or not member.enabled or not member.monitor_dynamic or not member.uid:
                continue
            if event.id in completed or member.id in active:
                continue
            ended_at = parse_time((self.state.data.get("live_event_end_times") or {}).get(event.id))
            if not ended_at:
                continue
            start_at = ended_at
            trigger = "live_end"
            expires_at = start_at + timedelta(minutes=self.config.dynamic_high_frequency_max_minutes)
            if start_at <= current < expires_at:
                active[member.id] = {
                    "event_id": event.id,
                    "started_at": self.time_as_utc(start_at).isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "trigger": trigger,
                }
                LOGGER.info("动态高频检查开始：%s，事件 %s，触发方式 %s", member.name, event.id, trigger)
                changed = True
        if changed:
            self.state.save()

    def check_due_dynamics(self, force: bool = False) -> None:
        if not self.ensure_cookie_usable(force=force):
            return
        self.state.set_error("bilibili_cookie", None)
        current = now_utc()
        for member in self.config.enabled_dynamic_members:
            interval = self.dynamic_interval_for_member(member, current)
            last = parse_time((self.state.data.get("last_dynamic_check") or {}).get(member.id))
            future_timestamp = last is not None and last - current > CLOCK_SKEW_TOLERANCE
            if future_timestamp:
                LOGGER.warning("%s 动态检查时间位于未来，将立即重新检查", member.name)
            if force or last is None or future_timestamp or current - last >= timedelta(minutes=interval):
                self.check_member_dynamic(member)

    def mark_missing_bilibili_cookie(self) -> None:
        message = "未配置 Bilibili Cookie，已跳过动态检查。填入 config.json 后会从最新动态开始记录。"
        last_message = self.state.data.get("last_errors", {}).get("bilibili_cookie", {}).get("message")
        if last_message != message:
            LOGGER.warning(message)
        self.state.set_error("bilibili_cookie", message)

    def cookie_check_due(self, current: datetime) -> bool:
        checked_at = parse_time((self.state.data.get("cookie_check") or {}).get("checked_at"))
        return checked_at is None or current - checked_at >= COOKIE_CHECK_INTERVAL

    def ensure_cookie_usable(self, *, force: bool = False) -> bool:
        """动态检查前的 Cookie 体检：有效或无法判断时放行，确认失效时跳过一次。"""
        cookie = self.config.bilibili_cookie.strip()
        if not cookie:
            self.mark_missing_bilibili_cookie()
            return False
        expired = bool(self.state.data.get("bilibili_cookie_expired"))
        if not force and not self.cookie_check_due(now_utc()):
            return not expired
        status = self.verify_bilibili_cookie(cookie)
        self.state.data["cookie_check"] = {
            "checked_at": utc_now_iso(),
            "state": status.state,
        }
        if status.state == STATUS_EXPIRED:
            self.mark_bilibili_cookie_expired(status.message or COOKIE_EXPIRED_MESSAGE)
            return False
        if status.state == STATUS_VALID:
            LOGGER.info(
                "B 站登录状态有效：%s（UID %s）",
                status.uname or "未知账号",
                status.mid or "-",
            )
            self.clear_bilibili_cookie_expired()
            self.state.save()
            return True
        self.state.save()
        return True

    def verify_bilibili_cookie(self, cookie: str) -> CookieStatus:
        verifier = getattr(self.bilibili, "verify_cookie", None)
        if callable(verifier):
            return verifier(cookie)
        http = getattr(self.bilibili, "http", None)
        if http is None:
            return CookieStatus(STATUS_UNKNOWN, "当前客户端不支持登录状态校验")
        return BilibiliLogin(http).verify_cookie(cookie)

    def mark_bilibili_cookie_expired(self, message: str | None = None) -> None:
        text = message or COOKIE_EXPIRED_MESSAGE
        last_message = (
            (self.state.data.get("last_errors") or {}).get("bilibili_cookie", {}).get("message")
        )
        if last_message != text:
            LOGGER.warning(text)
        self.state.data["bilibili_cookie_expired"] = True
        self.state.set_error("bilibili_cookie", text)

    def clear_bilibili_cookie_expired(self) -> None:
        changed = bool(self.state.data.get("bilibili_cookie_expired"))
        self.state.data["bilibili_cookie_expired"] = False
        if (self.state.data.get("last_errors") or {}).get("bilibili_cookie"):
            self.state.set_error("bilibili_cookie", None)
        elif changed:
            self.state.save()

    def mark_cookie_updated(self) -> None:
        """扫码登录写入新 Cookie 后调用：清掉失效标记并允许立刻重新校验。"""
        self.state.data["cookie_check"] = None
        self.clear_bilibili_cookie_expired()
        self.state.save()

    def dynamic_interval_for_member(self, member: MemberConfig, current: datetime) -> int:
        active = (self.state.data.get("high_frequency_dynamic") or {}).get(member.id)
        if active:
            expires_at = parse_time(active.get("expires_at"))
            if expires_at and current < expires_at:
                return self.config.dynamic_high_frequency_interval_minutes
        return self.config.dynamic_check_interval_minutes

    @staticmethod
    def time_as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def dynamic_items_at_or_after(
        self, items: list[DynamicItem], cutoff: datetime
    ) -> list[DynamicItem]:
        cutoff = self.time_as_utc(cutoff)
        return [
            item
            for item in items
            if item.published_at and self.time_as_utc(item.published_at) >= cutoff
        ]

    def should_suppress_live_start_dynamic(self, member: MemberConfig, item: DynamicItem) -> bool:
        window_minutes = max(0, self.config.suppress_live_start_dynamic_window_minutes)
        if window_minutes <= 0 or not item.published_at:
            return False

        published_at = self.time_as_utc(item.published_at)
        window = timedelta(minutes=window_minutes)
        for event in self.current_events():
            if event.status in {"cancelled", "rescheduled"}:
                continue
            event_member = self.member_for_event(event)
            if not event_member or event_member.id != member.id:
                continue
            start_time = self.time_as_utc(event.start_time)
            if abs(published_at - start_time) <= window:
                return True
        return False

    def check_member_dynamic(self, member: MemberConfig) -> list[DynamicItem]:
        try:
            items = self.bilibili.fetch_recent_dynamics(member, self.config.dynamic_fetch_limit)
        except BilibiliError as exc:
            LOGGER.warning("Dynamic check failed for %s: %s", member.name, exc)
            if "-101" in str(exc):
                # 动态接口也会在 Cookie 失效时返回 -101，这里顺手标记，等下一轮弹扫码。
                self.mark_bilibili_cookie_expired()
            self.state.set_error(f"dynamic:{member.id}", str(exc))
            self.state.data.setdefault("last_dynamic_check", {})[member.id] = utc_now_iso()
            self.state.save()
            return []
        except Exception as exc:
            LOGGER.exception("Dynamic check failed for %s", member.name)
            self.state.set_error(f"dynamic:{member.id}", str(exc))
            self.state.data.setdefault("last_dynamic_check", {})[member.id] = utc_now_iso()
            self.state.save()
            return []
        self.state.set_error(f"dynamic:{member.id}", None)
        self.state.data.setdefault("last_dynamic_check", {})[member.id] = utc_now_iso()
        if not items:
            LOGGER.info("B 站动态检查完成：%s 未返回动态", member.name)
            self.state.save()
            return []
        self.record_dynamic_snapshot(member, items)
        latest = items[0]
        latest_ids = self.state.data.setdefault("latest_dynamic_id", {})
        latest_items = self.state.data.setdefault("latest_dynamic_items", {})
        seen_ids_by_member = self.state.data.setdefault("seen_dynamic_ids", {})
        seen_ids = set(seen_ids_by_member.get(member.id) or [])
        latest_ids[member.id] = latest.id
        latest_items[member.id] = self.dynamic_item_to_state(latest)
        if not seen_ids:
            seen_ids_by_member[member.id] = [item.id for item in items]
            self.state.save()
            LOGGER.info(
                "B 站动态初始化完成：%s 记录 %d 条，最新 %s，首次不提醒历史动态",
                member.name,
                len(items),
                latest.id,
            )
            return []
        new_items = [item for item in reversed(items) if item.id not in seen_ids]
        notified = self.state.data.setdefault("notified_dynamic_ids", {})
        suppressed = self.state.data.setdefault("suppressed_dynamic_ids", {})
        for item in new_items:
            if item.id not in notified:
                if self.should_suppress_live_start_dynamic(member, item):
                    now_iso = utc_now_iso()
                    suppressed[item.id] = now_iso
                    notified[item.id] = now_iso
                    LOGGER.info(
                        "B 站动态已忽略开播同步提醒：%s %s %s",
                        member.name,
                        item.id,
                        truncate_text(item.text, 60),
                    )
                else:
                    self.dispatch_notification(
                        "notified_dynamic_ids",
                        item.id,
                        "发现新动态",
                        f"{member.name}：{truncate_text(item.text, 100)}",
                        item.url or member.dynamic_url,
                    )
            LOGGER.info("B 站动态发现更新：%s %s %s", member.name, item.id, truncate_text(item.text, 60))
        merged_seen = [item.id for item in items] + list(seen_ids)
        deduped_seen = []
        for item_id in merged_seen:
            if item_id not in deduped_seen:
                deduped_seen.append(item_id)
        seen_ids_by_member[member.id] = deduped_seen[:200]
        if new_items:
            self.complete_high_frequency_if_active(member, new_items)
        else:
            LOGGER.info(
                "B 站动态检查完成：%s 无新动态，读取 %d 条，最新 %s %s",
                member.name,
                len(items),
                latest.id,
                truncate_text(latest.text, 60),
            )
        self.state.save()
        return new_items

    @staticmethod
    def dynamic_item_to_state(item: DynamicItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "uid": item.uid,
            "member_name": item.member_name,
            "text": item.text,
            "url": item.url,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "rich_text": [
                {
                    "type": segment.type,
                    "text": segment.text,
                    "url": segment.url,
                }
                for segment in item.rich_text
            ],
        }

    def record_dynamic_snapshot(self, member: MemberConfig, items: list[DynamicItem]) -> None:
        if not self.config.write_snapshots:
            return
        path = dynamic_snapshot_path()
        if path.exists():
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                snapshot = {}
        else:
            snapshot = {}
        snapshot[member.id] = [self.dynamic_item_to_state(item) for item in items]
        atomic_write_text(
            path,
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8-sig",
        )

    def complete_high_frequency_if_active(
        self, member: MemberConfig, new_items: list[DynamicItem]
    ) -> None:
        active = self.state.data.setdefault("high_frequency_dynamic", {})
        completed = self.state.data.setdefault("high_frequency_completed_events", {})
        window = active.get(member.id)
        if not window:
            return
        started_at = parse_time(window.get("started_at"))
        if started_at and not self.dynamic_items_at_or_after(new_items, started_at):
            return
        window = active.pop(member.id, None)
        if window and window.get("event_id"):
            completed[window["event_id"]] = utc_now_iso()

    def should_notify(self) -> bool:
        return self.config.notifications_enabled and not bool(self.state.data.get("paused"))

    def set_paused(self, paused: bool) -> None:
        if (
            self.thread
            and self.thread.is_alive()
            and threading.get_ident() != self._owner_thread_id
        ):
            self._enqueue_command("set_paused", paused)
            return
        self._set_paused_now(paused)

    def _set_paused_now(self, paused: bool) -> None:
        self.state.data["paused"] = paused
        self.state.save()
        LOGGER.info("提醒状态已切换：%s", "暂停" if paused else "启用")

    def status_text(self) -> str:
        state = self.state.snapshot()
        lines = []
        lines.append(f"程序版本：{version_text()}")
        lines.append("提醒状态：已暂停" if state.get("paused") else "提醒状态：未暂停")
        monitor = state.get("monitor_process") or {}
        heartbeat = parse_time(monitor.get("heartbeat_at"))
        stopped = parse_time(monitor.get("stopped_at"))
        if heartbeat and (not stopped or heartbeat > stopped):
            age = now_utc() - heartbeat
            if age <= timedelta(seconds=120):
                lines.append(f"监控进程：运行中，PID {monitor.get('pid')}")
            else:
                lines.append(f"监控进程：可能未运行，上次心跳 {local_time_text(heartbeat)}")
        else:
            lines.append("监控进程：未检测到运行中的后台监控")
        last_asoul = parse_time(state.get("last_asoul_check"))
        if last_asoul:
            lines.append(f"ASOUL：上次检查 {local_time_text(last_asoul)}")
        else:
            lines.append("ASOUL：尚未完成检查")
        room_status = state.get("live_room_status") or {}
        for member in self.config.enabled_live_members:
            status = room_status.get(member.id)
            if status:
                live_status = status.get("live_status")
                if live_status == 1:
                    live_text = "直播中"
                elif live_status in (0, 2):
                    live_text = "未开播"
                else:
                    live_text = f"未知({live_status})"
                lines.append(f"{member.name}直播间：{live_text}，上次检查 {status.get('checked_at', '')}")
        last_dynamic = state.get("last_dynamic_check") or {}
        snapshot = self.load_dynamic_snapshot()
        for member in self.config.enabled_dynamic_members:
            checked_at = parse_time(last_dynamic.get(member.id))
            latest_id = (state.get("latest_dynamic_id") or {}).get(member.id)
            latest_item = (state.get("latest_dynamic_items") or {}).get(member.id) or {}
            if checked_at:
                suffix = f"，最新 {latest_id}" if latest_id else ""
                text = truncate_text(latest_item.get("text", ""), 30)
                if text:
                    suffix += f"：{text}"
                lines.append(f"{member.name}动态：{local_time_text(checked_at)}{suffix}")
                for item in (snapshot.get(member.id) or [])[: self.config.dynamic_fetch_limit]:
                    lines.append(
                        f"  - {item.get('id')}：{truncate_text(item.get('text', ''), 50)}"
                    )
            else:
                lines.append(f"{member.name}动态：尚未完成检查")
        errors = state.get("last_errors") or {}
        if errors:
            lines.append(f"最近状态：{next(iter(errors.values())).get('message', '')}")
        lines.append(f"ASOUL 快照：{asoul_snapshot_path()}")
        lines.append(f"动态快照：{dynamic_snapshot_path()}")
        return "\n".join(lines)

    @staticmethod
    def load_dynamic_snapshot() -> dict[str, Any]:
        path = dynamic_snapshot_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return {}
