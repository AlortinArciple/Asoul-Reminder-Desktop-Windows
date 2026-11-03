import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from asoul_reminder.asoul import event_to_state
from asoul_reminder.bilibili import BilibiliError
from asoul_reminder.bilibili_login import (
    STATUS_EXPIRED,
    STATUS_UNKNOWN,
    STATUS_VALID,
    CookieStatus,
)
from asoul_reminder.config import AppConfig
from asoul_reminder.models import DynamicItem, LiveEvent, LiveRoomStatus, MemberConfig
from asoul_reminder.service import ReminderService, parse_time
from asoul_reminder.state import StateStore


def make_member(member_id: str = "diana") -> MemberConfig:
    return MemberConfig(
        id=member_id,
        name=member_id.title(),
        enabled=True,
        monitor_dynamic=True,
        monitor_live=True,
        color="#E799B0",
        uid="123",
        dynamic_url="https://space.bilibili.com/123/dynamic",
        live_url="https://live.bilibili.com/123",
    )


def make_config(
    *members: MemberConfig,
    suppress_minutes: int = 5,
    live_open_interval_seconds: int = 30,
    live_close_interval_minutes: int = 20,
    bilibili_cookie: str = "cookie",
) -> AppConfig:
    return AppConfig(
        asoul_calendar_url="https://asoul.love/calendar/latest",
        asoul_home_url="https://asoul.love/",
        asoul_bypass_proxy=True,
        check_interval_minutes=20,
        dynamic_check_interval_minutes=10,
        dynamic_high_frequency_interval_minutes=3,
        dynamic_high_frequency_max_minutes=60,
        live_status_open_check_interval_seconds=live_open_interval_seconds,
        live_status_close_check_interval_minutes=live_close_interval_minutes,
        dynamic_fetch_limit=3,
        suppress_live_start_dynamic_window_minutes=suppress_minutes,
        write_snapshots=False,
        live_remind_before_minutes=(10, 5),
        auto_start=False,
        notifications_enabled=True,
        bilibili_cookie=bilibili_cookie,
        request_timeout_seconds=20,
        members=members,
    )


def make_event(member: MemberConfig, start_time: datetime) -> LiveEvent:
    return LiveEvent(
        id="event-1",
        title="Live",
        description="",
        type="live",
        format="live",
        start_time=start_time,
        host=member.id,
        performers=(),
        status="published",
        source_id="source-1",
        update_source_id=None,
        dynamic_url=None,
        live_url=member.live_url,
        color=member.color,
    )


def make_item(item_id: str, member: MemberConfig, published_at: datetime) -> DynamicItem:
    return DynamicItem(
        id=item_id,
        uid=member.uid,
        member_name=member.name,
        text="live reminder",
        url=member.dynamic_url,
        published_at=published_at,
    )


class FakeBilibili:
    def __init__(
        self,
        status: LiveRoomStatus | None = None,
        dynamics: list[DynamicItem] | None = None,
        statuses: dict[str, LiveRoomStatus] | None = None,
    ) -> None:
        self.status = status
        self.statuses = statuses or {}
        self.dynamics = dynamics or []
        self.dynamic_fetch_count = 0

    def fetch_live_room_status(self, member: MemberConfig) -> LiveRoomStatus:
        if member.id in self.statuses:
            return self.statuses[member.id]
        if self.status is None:
            raise RuntimeError("live status not configured")
        return self.status

    def fetch_recent_dynamics(
        self, member: MemberConfig, limit: int | None = None
    ) -> list[DynamicItem]:
        self.dynamic_fetch_count += 1
        return self.dynamics[:limit]


class FakeAsoul:
    def __init__(
        self,
        events: list[LiveEvent] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = events or []
        self.error = error
        self.fetch_count = 0

    def fetch_events(self) -> list[LiveEvent]:
        self.fetch_count += 1
        if self.error is not None:
            raise self.error
        return self.events


class ZeroJitter:
    """固定返回 0 的抖动源：让「30 分钟/60 分钟」这类精确断言不受随机偏移影响。"""

    def uniform(self, low: float, high: float) -> float:  # noqa: ARG002 - 接口对齐 random.Random
        return 0.0


class FakeNotifier:
    def __init__(self, succeed: bool = True) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self.sounds: list[bool] = []
        self.sound_files: list[str] = []
        self.tags: list[str] = []
        self.succeed = succeed

    def notify(
        self,
        title: str,
        message: str,
        url: str = "",
        *,
        sound: bool = False,
        sound_file: str = "",
        tag: str = "",
    ) -> bool:
        self.messages.append((title, message, url))
        self.sounds.append(bool(sound))
        self.sound_files.append(str(sound_file))
        self.tags.append(str(tag))
        return self.succeed


class ServiceTests(unittest.TestCase):
    """时间相关的断言都基于「无抖动」节拍，统一注入固定为 0 的抖动源。"""

    def make_service(
        self,
        config: AppConfig,
        event: LiveEvent,
        bilibili: object | None = None,
        asoul: object | None = None,
    ) -> ReminderService:
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.json")
            state.data["live_events"] = {event.id: event_to_state(event)}
            service = ReminderService(
                config,
                state,
                asoul or object(),
                bilibili or object(),
                FakeNotifier(),
            )
            service._jitter_source = ZeroJitter()
            return service

    def test_suppresses_same_member_dynamic_near_live_start(self):
        member = make_member("diana")
        start = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        service = self.make_service(make_config(member, suppress_minutes=5), make_event(member, start))
        item = make_item("dynamic-1", member, start + timedelta(minutes=3))

        self.assertTrue(service.should_suppress_live_start_dynamic(member, item))

    def test_does_not_suppress_other_member_or_outside_window(self):
        member = make_member("diana")
        other = make_member("bella")
        start = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        service = self.make_service(make_config(member, other, suppress_minutes=5), make_event(member, start))

        self.assertFalse(
            service.should_suppress_live_start_dynamic(
                member,
                make_item("dynamic-2", member, start + timedelta(minutes=10)),
            )
        )
        self.assertFalse(
            service.should_suppress_live_start_dynamic(
                other,
                make_item("dynamic-3", other, start + timedelta(minutes=1)),
            )
        )

    def test_live_status_candidate_stops_two_minutes_after_start_without_open(self):
        member = make_member("diana")
        start = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        event = make_event(member, start)
        service = self.make_service(
            make_config(member),
            event,
        )

        candidates = service.events_requiring_live_status(start + timedelta(minutes=2))

        self.assertEqual([candidate.id for candidate in candidates], [event.id])
        self.assertEqual(
            service.events_requiring_live_status(start + timedelta(minutes=2, seconds=1)),
            [],
        )

    def test_cancelled_live_status_candidate_is_checked_in_open_window(self):
        member = make_member("diana")
        start = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        event = make_event(member, start)
        event = LiveEvent(**{**event.__dict__, "status": "cancelled"})
        service = self.make_service(make_config(member), event)

        candidates = service.events_requiring_live_status(start - timedelta(minutes=5))

        self.assertEqual([candidate.id for candidate in candidates], [event.id])

    def test_live_status_checks_every_half_minute_before_open(self):
        member = make_member("diana")
        start = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        service = self.make_service(
            make_config(member, live_open_interval_seconds=30),
            make_event(member, start),
        )
        current = datetime(2026, 6, 10, 11, 55, tzinfo=timezone.utc)
        candidates = service.events_requiring_live_status(current)
        interval = service.live_status_interval_for_member(member, candidates)

        self.assertEqual(interval, timedelta(seconds=30))
        self.assertTrue(
            service.should_check_live_status_now(
                current,
                datetime(2026, 6, 10, 11, 54, 30, tzinfo=timezone.utc),
                interval,
            )
        )
        self.assertFalse(
            service.should_check_live_status_now(
                current,
                datetime(2026, 6, 10, 11, 54, 31, tzinfo=timezone.utc),
                interval,
            )
        )

    def test_live_status_checks_every_twenty_minutes_after_open(self):
        member = make_member("diana")
        start = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        event = make_event(member, start)
        service = self.make_service(
            make_config(member, live_close_interval_minutes=20),
            event,
        )
        service.state.data["live_event_open_times"] = {event.id: start.isoformat()}
        current = datetime(2026, 6, 10, 12, 40, tzinfo=timezone.utc)
        candidates = service.events_requiring_live_status(current)
        interval = service.live_status_interval_for_member(member, candidates)

        self.assertEqual(interval, timedelta(minutes=20))
        self.assertFalse(
            service.should_check_live_status_now(
                current,
                datetime(2026, 6, 10, 12, 21, tzinfo=timezone.utc),
                interval,
            )
        )
        self.assertTrue(
            service.should_check_live_status_now(
                current,
                datetime(2026, 6, 10, 12, 20, tzinfo=timezone.utc),
                interval,
            )
        )

    def test_records_live_event_open_time(self):
        member = make_member("diana")
        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        event = make_event(member, start)
        status = LiveRoomStatus(
            member_id=member.id,
            member_name=member.name,
            room_id="123",
            live_status=1,
            title="Live",
            live_time="2026-06-10 12:00:00",
        )
        service = self.make_service(make_config(member), event, FakeBilibili(status))

        service.check_member_live_status(member, [event])

        self.assertIn(event.id, service.state.data["live_event_open_times"])
        self.assertEqual(service.notifier.messages[0][0], "直播已开始")

    def test_no_time_based_high_frequency_without_confirmed_end(self):
        member = make_member("diana")
        start = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        event = make_event(member, start)
        service = self.make_service(make_config(member), event)
        current = start + timedelta(minutes=80)

        service.ensure_high_frequency_windows(current)

        self.assertNotIn(member.id, service.state.data["high_frequency_dynamic"])
        service.state.data["live_event_open_times"] = {event.id: start.isoformat()}
        service.ensure_high_frequency_windows(current)

        self.assertNotIn(member.id, service.state.data["high_frequency_dynamic"])

    def test_live_end_refreshes_dynamics_and_starts_high_frequency_when_no_new_item(self):
        member = make_member("diana")
        start = datetime.now(timezone.utc) - timedelta(minutes=30)
        event = make_event(member, start)
        old_item = make_item("old", member, start + timedelta(minutes=5))
        status = LiveRoomStatus(
            member_id=member.id,
            member_name=member.name,
            room_id="123",
            live_status=0,
            title="",
            live_time=None,
        )
        bilibili = FakeBilibili(status, [old_item])
        service = self.make_service(make_config(member), event, bilibili)
        service.state.data["live_room_status"] = {member.id: {"live_status": 1}}
        service.state.data["seen_dynamic_ids"] = {member.id: ["old"]}

        service.check_member_live_status(member, [event])

        self.assertEqual(bilibili.dynamic_fetch_count, 1)
        self.assertIn(member.id, service.state.data["high_frequency_dynamic"])
        self.assertEqual(service.dynamic_interval_for_member(member, datetime.now(timezone.utc)), 3)

    def test_live_end_keeps_high_frequency_when_only_pre_end_new_item_exists(self):
        member = make_member("diana")
        start = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        event = make_event(member, start)
        old_item = make_item("old", member, start + timedelta(minutes=5))
        pre_end_item = make_item("pre-end", member, start + timedelta(minutes=30))
        ended_at = start + timedelta(minutes=40)
        bilibili = FakeBilibili(dynamics=[pre_end_item, old_item])
        service = self.make_service(make_config(member), event, bilibili)
        service.state.data["seen_dynamic_ids"] = {member.id: ["old"]}

        service.refresh_dynamic_after_live_end(member, event, ended_at)

        self.assertEqual(bilibili.dynamic_fetch_count, 1)
        self.assertIn(member.id, service.state.data["high_frequency_dynamic"])
        self.assertNotIn(event.id, service.state.data["high_frequency_completed_events"])

    def test_live_end_refreshes_dynamics_and_skips_high_frequency_when_post_end_item_exists(self):
        member = make_member("diana")
        start = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        event = make_event(member, start)
        old_item = make_item("old", member, start + timedelta(minutes=5))
        post_end_item = make_item("post-end", member, start + timedelta(minutes=41))
        ended_at = start + timedelta(minutes=40)
        bilibili = FakeBilibili(dynamics=[post_end_item, old_item])
        service = self.make_service(make_config(member), event, bilibili)
        service.state.data["seen_dynamic_ids"] = {member.id: ["old"]}

        service.refresh_dynamic_after_live_end(member, event, ended_at)

        self.assertEqual(bilibili.dynamic_fetch_count, 1)
        self.assertNotIn(member.id, service.state.data["high_frequency_dynamic"])
        self.assertIn(event.id, service.state.data["high_frequency_completed_events"])

    def test_active_high_frequency_completes_only_for_post_window_dynamic(self):
        member = make_member("diana")
        start = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        event = make_event(member, start)
        old_item = make_item("old", member, start + timedelta(minutes=5))
        pre_end_item = make_item("pre-end", member, start + timedelta(minutes=30))
        post_end_item = make_item("post-end", member, start + timedelta(minutes=41))
        ended_at = start + timedelta(minutes=40)
        bilibili = FakeBilibili(dynamics=[pre_end_item, old_item])
        service = self.make_service(make_config(member), event, bilibili)
        service.state.data["seen_dynamic_ids"] = {member.id: ["old"]}
        service.start_high_frequency_window(member, event, ended_at, "live_end")

        service.check_member_dynamic(member)

        self.assertIn(member.id, service.state.data["high_frequency_dynamic"])
        self.assertNotIn(event.id, service.state.data["high_frequency_completed_events"])

        bilibili.dynamics = [post_end_item, pre_end_item, old_item]
        service.check_member_dynamic(member)

        self.assertNotIn(member.id, service.state.data["high_frequency_dynamic"])
        self.assertIn(event.id, service.state.data["high_frequency_completed_events"])

    def test_force_live_status_check_ignores_interval(self):
        member = make_member("diana")
        service = self.make_service(make_config(member), make_event(member, datetime.now(timezone.utc)))

        self.assertTrue(
            service.should_check_live_status_now(
                datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 6, 10, 11, 59, 45, tzinfo=timezone.utc),
                timedelta(minutes=20),
                force=True,
            )
        )

    def test_asoul_checks_have_half_hour_minimum_interval(self):
        member = make_member("diana")
        service = self.make_service(make_config(member), make_event(member, datetime.now(timezone.utc)))

        self.assertEqual(service.asoul_check_interval(), timedelta(minutes=30))

    def test_failed_asoul_check_is_retried_after_half_hour(self):
        member = make_member("diana")
        current = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        asoul = FakeAsoul(error=RuntimeError("forbidden"))
        service = self.make_service(
            make_config(member),
            make_event(member, current),
            asoul=asoul,
        )

        with self.assertLogs("asoul_reminder.service", level="ERROR") as logs:
            service.check_asoul(current=current)
            service.check_asoul(current=current + timedelta(minutes=29, seconds=59))
            service.check_asoul(current=current + timedelta(minutes=30))

        self.assertEqual(len(logs.output), 2)
        self.assertEqual(asoul.fetch_count, 2)
        self.assertEqual(
            parse_time(service.state.data["last_asoul_check"]),
            current + timedelta(minutes=30),
        )

    def test_repeated_asoul_failures_back_off_and_success_resets(self):
        member = make_member("diana")
        current = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        asoul = FakeAsoul(error=RuntimeError("forbidden"))
        service = self.make_service(make_config(member), make_event(member, current), asoul=asoul)

        with self.assertLogs("asoul_reminder.service", level="ERROR"):
            service.check_asoul(current=current)
            service.check_asoul(current=current + timedelta(minutes=30))
        self.assertEqual(service.asoul_check_interval(), timedelta(minutes=60))

        service.check_asoul(current=current + timedelta(minutes=89))
        self.assertEqual(asoul.fetch_count, 2)

        asoul.error = None
        service.check_asoul(current=current + timedelta(minutes=90))
        self.assertEqual(asoul.fetch_count, 3)
        self.assertEqual(service.asoul_check_interval(), timedelta(minutes=30))

    def test_asoul_backoff_is_capped_at_four_times(self):
        member = make_member("diana")
        current = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        asoul = FakeAsoul(error=RuntimeError("forbidden"))
        service = self.make_service(make_config(member), make_event(member, current), asoul=asoul)

        with self.assertLogs("asoul_reminder.service", level="ERROR"):
            moment = current
            for _ in range(6):
                service.check_asoul(current=moment)
                moment += service.asoul_check_interval()

        self.assertEqual(service.asoul_check_interval(), timedelta(minutes=120))

    def test_failed_notification_is_retried_before_being_marked_delivered(self):
        member = make_member("diana")
        event = make_event(member, datetime.now(timezone.utc) + timedelta(minutes=10))
        service = self.make_service(make_config(member), event)
        service.notifier.succeed = False

        service.notify_live_reminder(event, member, 10)

        key = f"{event.id}:10"
        self.assertNotIn(key, service.state.data["notified_live_reminders"])
        self.assertTrue(service.state.data["notification_outbox"])

        service.notifier.succeed = True
        service.retry_pending_notifications(force=True)

        self.assertIn(key, service.state.data["notified_live_reminders"])
        self.assertFalse(service.state.data["notification_outbox"])
        self.assertEqual(len(service.notifier.messages), 2)

    def test_live_notifications_carry_member_sound_setting(self):
        member_on = MemberConfig(**{**make_member("diana").__dict__, "live_sound_enabled": True})
        member_off = MemberConfig(**{**make_member("bella").__dict__, "live_sound_enabled": False})
        start = datetime.now(timezone.utc) + timedelta(minutes=10)
        service = self.make_service(make_config(member_on, member_off), make_event(member_on, start))
        event_on = make_event(member_on, start)
        event_off = LiveEvent(**{**make_event(member_off, start).__dict__, "id": "event-2"})

        service.notify_live_reminder(event_on, member_on, 10)
        service.notify_live_reminder(event_off, member_off, 10)

        self.assertTrue(service.notifier.sounds[-2])
        self.assertFalse(service.notifier.sounds[-1])

    def test_live_notifications_carry_member_sound_file(self):
        member = MemberConfig(
            **{**make_member("diana").__dict__, "live_sound_file": "sounds/diana.wav"}
        )
        start = datetime.now(timezone.utc) + timedelta(minutes=10)
        event = make_event(member, start)
        service = self.make_service(make_config(member), event)

        service.notify_live_reminder(event, member, 10)

        self.assertEqual(service.notifier.sound_files[-1], "sounds/diana.wav")

    def test_retry_keeps_sound_file_from_outbox(self):
        member = MemberConfig(
            **{**make_member("diana").__dict__, "live_sound_file": "sounds/diana.wav"}
        )
        event = make_event(member, datetime.now(timezone.utc) + timedelta(minutes=10))
        service = self.make_service(make_config(member), event)
        service.notifier.succeed = False

        service.notify_live_reminder(event, member, 10)

        outbox_item = next(iter(service.state.data["notification_outbox"].values()))
        self.assertEqual(outbox_item["sound_file"], "sounds/diana.wav")

        service.notifier.succeed = True
        service.retry_pending_notifications(force=True)

        self.assertEqual(service.notifier.sound_files[-1], "sounds/diana.wav")
        self.assertFalse(service.state.data["notification_outbox"])

    def test_retry_keeps_sound_from_outbox(self):
        member = MemberConfig(**{**make_member("diana").__dict__, "live_sound_enabled": True})
        event = make_event(member, datetime.now(timezone.utc) + timedelta(minutes=10))
        service = self.make_service(make_config(member), event)
        service.notifier.succeed = False

        service.notify_live_reminder(event, member, 10)

        self.assertTrue(service.notifier.sounds[-1])
        outbox = service.state.data["notification_outbox"]
        self.assertTrue(next(iter(outbox.values()))["sound"])

        service.notifier.succeed = True
        service.retry_pending_notifications(force=True)

        self.assertTrue(service.notifier.sounds[-1])
        self.assertFalse(service.state.data["notification_outbox"])

    def test_dynamic_notifications_stay_silent(self):
        member = make_member("diana")
        item = make_item("dynamic-1", member, datetime.now(timezone.utc))
        bilibili = FakeBilibili(dynamics=[item])
        service = self.make_service(
            make_config(member),
            make_event(member, datetime.now(timezone.utc) + timedelta(hours=3)),
            bilibili=bilibili,
        )
        service.state.data["seen_dynamic_ids"] = {member.id: ["old"]}

        service.check_member_dynamic(member)

        self.assertFalse(service.notifier.sounds[-1])

    def test_live_start_without_schedule_still_notifies_once(self):
        member = make_member("diana")
        event = make_event(member, datetime.now(timezone.utc) + timedelta(hours=3))
        status = LiveRoomStatus(
            member_id=member.id,
            member_name=member.name,
            room_id="123",
            live_status=1,
            title="Unexpected live",
            live_time="2026-07-16 19:23:06",
        )
        service = self.make_service(make_config(member), event, FakeBilibili(status))
        service.state.data["live_events"] = {}
        service.state.save()

        service.check_member_live_status(member, [])
        service.check_member_live_status(member, [])

        self.assertEqual(len(service.notifier.messages), 1)
        self.assertTrue(service.state.data["notified_live_sessions"])

    def test_unscheduled_live_start_carries_member_sound_file(self):
        member = MemberConfig(
            **{**make_member("diana").__dict__, "live_sound_file": "sounds/diana.wav"}
        )
        status = LiveRoomStatus(
            member_id=member.id,
            member_name=member.name,
            room_id="123",
            live_status=1,
            title="Unexpected live",
            live_time="2026-07-16 19:23:06",
        )
        service = self.make_service(
            make_config(member),
            make_event(member, datetime.now(timezone.utc) + timedelta(hours=5)),
            FakeBilibili(status),
        )
        service.state.data["live_events"] = {}

        service.check_member_live_status(member, [])

        self.assertEqual(service.notifier.sound_files[-1], "sounds/diana.wav")
        self.assertTrue(service.notifier.sounds[-1])

    def test_simultaneous_live_starts_keep_both_notifications_in_priority_order(self):
        low = MemberConfig(**{**make_member("diana").__dict__, "priority": 0})
        high = MemberConfig(**{**make_member("bella").__dict__, "priority": 10})
        start = datetime.now(timezone.utc)
        event_low = make_event(low, start)
        event_high = LiveEvent(
            **{**make_event(high, start).__dict__, "id": "event-2", "host": high.id}
        )
        statuses = {
            low.id: LiveRoomStatus(
                member_id=low.id,
                member_name=low.name,
                room_id="1",
                live_status=1,
                title="low",
                live_time="2026-07-16 19:00:00",
            ),
            high.id: LiveRoomStatus(
                member_id=high.id,
                member_name=high.name,
                room_id="2",
                live_status=1,
                title="high",
                live_time="2026-07-16 20:00:00",
            ),
        }
        service = self.make_service(
            make_config(low, high),
            event_low,
            bilibili=FakeBilibili(statuses=statuses),
        )
        service.state.data["live_events"] = {
            event_low.id: event_to_state(event_low),
            event_high.id: event_to_state(event_high),
        }

        service.check_due_live_status(datetime.now(timezone.utc), force=True)

        self.assertEqual([message[0] for message in service.notifier.messages], ["直播已开始", "直播已开始"])
        # 低优先级先发、高优先级后发（通知中心按时间倒序，高优先级显示在顶部）
        self.assertIn("Diana", service.notifier.messages[0][1])
        self.assertIn("Bella", service.notifier.messages[1][1])
        # 两条通知的 tag 不同，不再互相覆盖
        self.assertEqual(len(service.notifier.tags), 2)
        self.assertNotEqual(service.notifier.tags[0], service.notifier.tags[1])
        # 本轮只有最高优先级成员播放提示音
        self.assertEqual(service.notifier.sounds, [False, True])

    def test_restores_asoul_snapshot_when_state_has_no_events(self):
        member = make_member("diana")
        event = make_event(member, datetime.now(timezone.utc) + timedelta(hours=1))
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.json")
            snapshot = Path(tmp) / "asoul_events.json"
            snapshot.write_text(
                json.dumps([event_to_state(event)], ensure_ascii=False),
                encoding="utf-8",
            )
            service = ReminderService(
                make_config(member),
                state,
                FakeAsoul(),
                FakeBilibili(),
                FakeNotifier(),
            )
            with patch("asoul_reminder.service.asoul_snapshot_path", return_value=snapshot):
                restored = service.restore_events_from_snapshot_if_needed()

        self.assertTrue(restored)
        self.assertEqual([item.id for item in service.current_events()], [event.id])

    def test_refresh_schedule_command_forces_request_inside_interval(self):
        member = make_member("diana")
        event = make_event(member, datetime.now(timezone.utc) + timedelta(hours=1))
        asoul = FakeAsoul(events=[event])
        service = self.make_service(make_config(member), event, asoul=asoul)
        service.state.data["last_asoul_check"] = datetime.now(timezone.utc).isoformat()

        service._handle_command("refresh_schedule", None)

        self.assertEqual(asoul.fetch_count, 1)

    def test_future_dynamic_check_time_is_checked_immediately(self):
        member = make_member("diana")
        bilibili = FakeBilibili(dynamics=[])
        service = self.make_service(
            make_config(member),
            make_event(member, datetime.now(timezone.utc) + timedelta(hours=1)),
            bilibili=bilibili,
        )
        service.state.data["last_dynamic_check"] = {
            member.id: (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        }

        with self.assertLogs("asoul_reminder.service", level="WARNING"):
            service.check_due_dynamics()

        self.assertEqual(bilibili.dynamic_fetch_count, 1)

    def test_future_live_check_time_is_checked_immediately(self):
        member = make_member("diana")
        service = self.make_service(
            make_config(member),
            make_event(member, datetime.now(timezone.utc) + timedelta(hours=1)),
        )
        current = datetime(2026, 7, 17, 6, 32, tzinfo=timezone.utc)

        self.assertTrue(
            service.should_check_live_status_now(
                current,
                current + timedelta(hours=1),
                timedelta(minutes=20),
            )
        )

class FakeVerifyingBilibili(FakeBilibili):
    """带登录状态校验的 B 站替身；state 可切换以模拟 Cookie 失效/恢复。"""

    def __init__(self, state: str = "valid", dynamics: list[DynamicItem] | None = None) -> None:
        super().__init__(dynamics=dynamics or [])
        self.state = state
        self.verify_calls = 0

    def verify_cookie(self, cookie: str) -> CookieStatus:
        self.verify_calls += 1
        return CookieStatus(self.state)


class CookieExpiryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make_service(
        self,
        bilibili: object,
        *,
        cookie: str = "SESSDATA=abc; bili_jct=csrf",
    ) -> tuple[ReminderService, StateStore]:
        member = make_member("diana")
        config = make_config(member, bilibili_cookie=cookie)
        state = StateStore(Path(self.tmp.name) / "state.json")
        event = make_event(member, datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc))
        state.data["live_events"] = {event.id: event_to_state(event)}
        service = ReminderService(config, state, object(), bilibili, FakeNotifier())
        return service, state

    def test_expired_cookie_skips_dynamics_and_marks_state(self):
        member = make_member("diana")
        bilibili = FakeVerifyingBilibili(STATUS_EXPIRED, dynamics=[make_item("d1", member, datetime.now(timezone.utc))])
        service, state = self.make_service(bilibili)

        service.check_due_dynamics()

        self.assertEqual(bilibili.dynamic_fetch_count, 0)
        self.assertEqual(bilibili.verify_calls, 1)
        self.assertTrue(state.data["bilibili_cookie_expired"])
        self.assertIn("失效", state.data["last_errors"]["bilibili_cookie"]["message"])

    def test_valid_cookie_keeps_checking_dynamics(self):
        member = make_member("diana")
        bilibili = FakeVerifyingBilibili(STATUS_VALID, dynamics=[make_item("d1", member, datetime.now(timezone.utc))])
        service, state = self.make_service(bilibili)

        service.check_due_dynamics()

        self.assertEqual(bilibili.dynamic_fetch_count, 1)
        self.assertFalse(state.data["bilibili_cookie_expired"])
        self.assertEqual(state.data["cookie_check"]["state"], STATUS_VALID)

    def test_unknown_status_still_checks_dynamics(self):
        member = make_member("diana")
        bilibili = FakeVerifyingBilibili(STATUS_UNKNOWN, dynamics=[make_item("d1", member, datetime.now(timezone.utc))])
        service, state = self.make_service(bilibili)

        service.check_due_dynamics()

        self.assertEqual(bilibili.dynamic_fetch_count, 1)
        self.assertFalse(state.data["bilibili_cookie_expired"])
        self.assertEqual(state.data["cookie_check"]["state"], STATUS_UNKNOWN)

    def test_expired_state_is_throttled_for_thirty_minutes(self):
        bilibili = FakeVerifyingBilibili(STATUS_EXPIRED)
        service, state = self.make_service(bilibili)

        service.check_due_dynamics()
        service.check_due_dynamics()

        self.assertEqual(bilibili.verify_calls, 1)
        self.assertTrue(state.data["bilibili_cookie_expired"])

    def test_manual_check_forces_cookie_revalidation(self):
        bilibili = FakeVerifyingBilibili(STATUS_EXPIRED)
        service, _state = self.make_service(bilibili)
        service.check_due_dynamics()

        bilibili.state = STATUS_VALID
        service.check_due_dynamics(force=True)

        self.assertEqual(bilibili.verify_calls, 2)

    def test_mark_cookie_updated_clears_flag_and_forces_new_check(self):
        bilibili = FakeVerifyingBilibili(STATUS_EXPIRED)
        service, state = self.make_service(bilibili)
        service.check_due_dynamics()
        self.assertTrue(state.data["bilibili_cookie_expired"])

        bilibili.state = STATUS_VALID
        service.mark_cookie_updated()
        self.assertFalse(state.data["bilibili_cookie_expired"])
        self.assertNotIn("bilibili_cookie", state.data["last_errors"])

        service.check_due_dynamics()
        self.assertEqual(bilibili.verify_calls, 2)
        self.assertEqual(bilibili.dynamic_fetch_count, 1)

    def test_dynamic_api_minus_101_marks_cookie_expired(self):
        class LoggedOutBilibili(FakeBilibili):
            def fetch_recent_dynamics(self, member: MemberConfig, limit: int | None = None) -> list[DynamicItem]:
                raise BilibiliError("Bilibili dynamic API failed: code=-101, message=账号未登录")

        service, state = self.make_service(LoggedOutBilibili())
        service.check_due_dynamics()

        self.assertTrue(state.data["bilibili_cookie_expired"])

    def test_missing_cookie_keeps_missing_message(self):
        service, state = self.make_service(FakeVerifyingBilibili(STATUS_VALID), cookie="")
        service.check_due_dynamics()

        self.assertFalse(state.data["bilibili_cookie_expired"])
        self.assertIn("未配置", state.data["last_errors"]["bilibili_cookie"]["message"])


class ScheduleNoticeWindowTests(unittest.TestCase):
    """日程「新增/变动」只提醒还没结束的日程。

    asoul.love 每周重建日历页会把几周前的日程重新放回 initialEvents，
    没有这条过滤时这些历史条目会被当成新日程逐条弹窗。
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make_service(self, *members: MemberConfig) -> ReminderService:
        state = StateStore(Path(self.tmp.name) / "state.json")
        return ReminderService(
            make_config(*members),
            state,
            object(),
            object(),
            FakeNotifier(),
        )

    def test_skips_event_that_started_long_ago(self):
        member = make_member("diana")
        service = self.make_service(member)
        stale = make_event(member, datetime.now(timezone.utc) - timedelta(days=20))

        service.notify_schedule_changes({}, {stale.id: stale})

        self.assertEqual(service.notifier.messages, [])

    def test_notifies_upcoming_event(self):
        member = make_member("diana")
        service = self.make_service(member)
        upcoming = make_event(member, datetime.now(timezone.utc) + timedelta(hours=5))

        service.notify_schedule_changes({}, {upcoming.id: upcoming})

        self.assertEqual(len(service.notifier.messages), 1)
        self.assertIn("新增", service.notifier.messages[0][0])

    def test_notifies_event_that_just_started_within_grace(self):
        member = make_member("diana")
        service = self.make_service(member)
        just_started = make_event(member, datetime.now(timezone.utc) - timedelta(minutes=5))

        service.notify_schedule_changes({}, {just_started.id: just_started})

        self.assertEqual(len(service.notifier.messages), 1)

    def test_skips_removal_notice_for_past_event(self):
        member = make_member("diana")
        service = self.make_service(member)
        stale = make_event(member, datetime.now(timezone.utc) - timedelta(days=3))

        service.notify_schedule_changes({stale.id: stale}, {})

        self.assertEqual(service.notifier.messages, [])


class AsoulCheckJitterTests(unittest.TestCase):
    """日程检查节拍带 ±10% 抖动，避免所有客户端在同一分钟打 asoul.love。"""

    def make_service(self) -> ReminderService:
        member = make_member("diana")
        state = StateStore(Path(self.tmp.name) / "state.json")
        service = ReminderService(
            make_config(member),
            state,
            object(),
            object(),
            FakeNotifier(),
        )
        return service

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_jitter_offset_stays_within_ten_percent(self):
        service = self.make_service()
        current = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)

        with self.assertLogs("asoul_reminder.service", level="ERROR"):
            service.check_asoul(current=current)

        offset = float(service.state.data["asoul_check_jitter_seconds"])
        self.assertLessEqual(abs(offset), 30 * 60 * 0.1)
        self.assertGreaterEqual(service.asoul_check_delay(), timedelta(minutes=27))

    def test_negative_jitter_shortens_but_never_below_ninety_percent(self):
        service = self.make_service()
        service.state.data["asoul_check_jitter_seconds"] = -999  # 超出上限时被夹紧
        delay = service.asoul_check_delay()

        self.assertEqual(delay, timedelta(minutes=27))

    def test_positive_jitter_delays_next_check(self):
        service = self.make_service()
        current = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        service.asoul = FakeAsoul(events=[], error=RuntimeError("forbidden"))
        with self.assertLogs("asoul_reminder.service", level="ERROR"):
            service.check_asoul(current=current)

        service.state.data["asoul_check_jitter_seconds"] = 180.0  # +3 分钟
        self.assertFalse(service.is_asoul_check_due(current + timedelta(minutes=32)))
        self.assertTrue(service.is_asoul_check_due(current + timedelta(minutes=33)))

    def test_jitter_is_rerolled_on_each_check(self):
        service = self.make_service()
        service._jitter_source = RecordingJitter([-120.0, 60.0])
        current = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        service.asoul = FakeAsoul(events=[], error=RuntimeError("forbidden"))

        with self.assertLogs("asoul_reminder.service", level="ERROR"):
            service.check_asoul(current=current)
        first = service.state.data["asoul_check_jitter_seconds"]
        with self.assertLogs("asoul_reminder.service", level="ERROR"):
            service.check_asoul(current=current + timedelta(minutes=40))
        second = service.state.data["asoul_check_jitter_seconds"]

        self.assertEqual(first, -120.0)
        self.assertEqual(second, 60.0)


class RecordingJitter:
    """按顺序返回预设偏移的抖动源，用于断言「每次检查都重新抽签」。"""

    def __init__(self, values: list[float]) -> None:
        self.values = list(values)

    def uniform(self, low: float, high: float) -> float:
        value = self.values.pop(0)
        return max(low, min(high, value))


if __name__ == "__main__":
    unittest.main()
