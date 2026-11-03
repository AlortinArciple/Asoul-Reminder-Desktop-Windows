import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from asoul_reminder.config import load_config, update_config_values
from asoul_reminder.notifications import Notifier, play_live_sound
from asoul_reminder.single_instance import SingleInstance
from asoul_reminder.state import StateStore


class ReliabilityTests(unittest.TestCase):
    def test_state_snapshot_only_exposes_fully_saved_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.json")
            state.data["paused"] = True

            self.assertFalse(state.snapshot()["paused"])

            state.save()
            snapshot = state.snapshot()
            snapshot["paused"] = False

            self.assertTrue(state.snapshot()["paused"])

    def test_config_multi_update_is_validated_before_atomic_write(self):
        source = Path(__file__).resolve().parents[1] / "config.example.json"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

            config = update_config_values(
                {"dynamic_fetch_limit": 7, "notifications_enabled": False},
                path,
            )

            self.assertEqual(config.dynamic_fetch_limit, 7)
            self.assertFalse(config.notifications_enabled)
            before_invalid_update = path.read_text(encoding="utf-8")
            with self.assertRaises(ValueError):
                update_config_values({"members": "invalid"}, path)
            self.assertEqual(path.read_text(encoding="utf-8"), before_invalid_update)
            self.assertEqual(load_config(path).dynamic_fetch_limit, 7)

    def test_notifier_logs_request_and_success(self):
        shown: list[bool] = []

        class FakeNotification:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def add_actions(self, **kwargs):
                pass

            def show(self):
                shown.append(True)

        notifier = Notifier.__new__(Notifier)
        notifier.enabled = True
        notifier.icon = ""
        fake_module = SimpleNamespace(Notification=FakeNotification)
        with patch.dict(sys.modules, {"winotify": fake_module}):
            with self.assertLogs("asoul_reminder.notifications", level="INFO") as logs:
                result = notifier.notify("测试通知", "第一行\n第二行", "https://example.com")

        self.assertTrue(result)
        self.assertTrue(shown)
        output = "\n".join(logs.output)
        self.assertIn("Windows 通知请求", output)
        self.assertIn("Windows 通知已提交", output)
        self.assertIn("测试通知", output)

    def test_notifier_sound_channel_depends_on_fullscreen_state(self):
        audio_calls: list[tuple[str, bool]] = []
        sound_calls: list[tuple[str, int]] = []

        class SoundNotification:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def set_audio(self, sound, loop):
                audio_calls.append((str(sound), bool(loop)))

            def add_actions(self, **kwargs):
                pass

            def show(self):
                pass

        fake_winsound = SimpleNamespace(
            SND_ASYNC=0x0001,
            SND_FILENAME=0x20000,
            SND_ALIAS=0x10000,
            PlaySound=lambda sound, flags: sound_calls.append((str(sound), int(flags))),
        )
        fake_audio = SimpleNamespace(
            Default="ms-winsoundevent:Notification.Default",
            Silent="silent",
        )
        fake_winotify = SimpleNamespace(Notification=SoundNotification, audio=fake_audio)

        notifier = Notifier.__new__(Notifier)
        notifier.enabled = True
        notifier.icon = ""
        notifier.live_sound_file = ""
        notifier.live_sound_loop_seconds = 0
        with tempfile.TemporaryDirectory() as tmp:
            missing_ring = Path(tmp) / "nope.wav"
            with patch.dict(sys.modules, {"winotify": fake_winotify, "winsound": fake_winsound}):
                with patch("asoul_reminder.notifications.DEFAULT_RING_FILE", str(missing_ring)):
                    with patch(
                        "asoul_reminder.notifications.fullscreen_notification_suppressed",
                        return_value=False,
                    ):
                        self.assertTrue(notifier.notify("桌面音效", "内容", None, sound=True))
                    with patch(
                        "asoul_reminder.notifications.fullscreen_notification_suppressed",
                        return_value=True,
                    ):
                        self.assertTrue(notifier.notify("全屏音效", "内容", None, sound=True))
                        self.assertTrue(notifier.notify("静音", "内容", None))

        self.assertEqual(audio_calls, [(fake_audio.Default, False)])
        self.assertEqual(len(sound_calls), 1)
        self.assertEqual(
            sound_calls[0],
            ("Notification.Default", fake_winsound.SND_ALIAS | fake_winsound.SND_ASYNC),
        )

    def test_notifier_prefers_member_sound_file_over_global(self):
        class PlainNotification:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def add_actions(self, **kwargs):
                pass

            def show(self):
                pass

        notifier = Notifier.__new__(Notifier)
        notifier.enabled = True
        notifier.icon = ""
        notifier.live_sound_file = "global.wav"
        notifier.live_sound_loop_seconds = 7
        fake_module = SimpleNamespace(Notification=PlainNotification)
        calls: list[tuple[str, int]] = []

        def fake_play(file_path, loop_seconds):
            calls.append((file_path, loop_seconds))
            return True

        with patch.dict(sys.modules, {"winotify": fake_module}):
            with patch(
                "asoul_reminder.notifications.fullscreen_notification_suppressed",
                return_value=True,
            ):
                with patch("asoul_reminder.notifications.play_live_sound", fake_play):
                    notifier.notify("成员音效", "内容", None, sound=True, sound_file="member.wav")
                    notifier.notify("全局音效", "内容", None, sound=True)

        self.assertEqual(calls, [("member.wav", 7), ("global.wav", 7)])

    def test_notifier_assigns_unique_toast_tags(self):
        tags: list[str] = []

        class TaggedNotification:
            def __init__(self, **kwargs):
                object.__setattr__(self, "kwargs", kwargs)

            def __setattr__(self, name, value):
                if name == "tag":
                    tags.append(str(value))
                object.__setattr__(self, name, value)

            def add_actions(self, **kwargs):
                pass

            def show(self):
                pass

        notifier = Notifier.__new__(Notifier)
        notifier.enabled = True
        notifier.icon = ""
        notifier.live_sound_file = ""
        notifier.live_sound_loop_seconds = 0
        fake_module = SimpleNamespace(Notification=TaggedNotification)
        with patch.dict(sys.modules, {"winotify": fake_module}):
            notifier.notify("直播已开始", "成员A", None, tag="notified_live_started|event-1")
            notifier.notify("直播已开始", "成员B", None, tag="notified_live_started|event-2")
            notifier.notify("直播已开始", "成员A", None, tag="notified_live_started|event-1")

        self.assertEqual(len(tags), 3)
        self.assertNotEqual(tags[0], tags[1])
        self.assertEqual(tags[0], tags[2])
        self.assertTrue(all(len(tag) <= 16 for tag in tags))

    def test_live_sound_prefers_configured_wav_file(self):
        play_calls: list[tuple[str, int]] = []

        fake_winsound = SimpleNamespace(
            SND_ASYNC=0x0001,
            SND_FILENAME=0x20000,
            SND_ALIAS=0x10000,
            PlaySound=lambda sound, flags: play_calls.append((str(sound), int(flags))),
        )
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "custom.wav"
            wav.write_bytes(b"RIFF")
            missing = Path(tmp) / "missing.wav"
            with patch.dict(sys.modules, {"winsound": fake_winsound}):
                with patch("asoul_reminder.notifications.DEFAULT_RING_FILE", str(missing)):
                    self.assertTrue(play_live_sound(str(wav)))
                    self.assertTrue(play_live_sound(str(missing)))

        self.assertEqual(
            play_calls[0],
            (str(wav), fake_winsound.SND_FILENAME | fake_winsound.SND_ASYNC),
        )
        self.assertEqual(
            play_calls[1][1],
            fake_winsound.SND_ALIAS | fake_winsound.SND_ASYNC,
        )

    def test_live_sound_loops_then_stops_via_timer(self):
        play_calls: list[tuple[str, int]] = []
        timers: list = []

        class FakeTimer:
            def __init__(self, interval, fn):
                self.interval = interval
                self.fn = fn
                self.daemon = False
                self.started = False

            def start(self):
                self.started = True
                timers.append(self)

            def cancel(self):
                pass

        fake_winsound = SimpleNamespace(
            SND_ASYNC=0x0001,
            SND_FILENAME=0x20000,
            SND_LOOP=0x0008,
            SND_ALIAS=0x10000,
            PlaySound=lambda sound, flags: play_calls.append((str(sound), int(flags))),
        )
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "custom.wav"
            wav.write_bytes(b"RIFF")
            with patch.dict(sys.modules, {"winsound": fake_winsound}):
                with patch(
                    "asoul_reminder.notifications.threading",
                    SimpleNamespace(Timer=FakeTimer),
                ):
                    self.assertTrue(play_live_sound(str(wav), 5))
                    self.assertTrue(play_live_sound(str(wav), 0))

        self.assertEqual(
            play_calls[0],
            (
                str(wav),
                fake_winsound.SND_FILENAME | fake_winsound.SND_ASYNC | fake_winsound.SND_LOOP,
            ),
        )
        self.assertEqual(len(timers), 1)
        self.assertEqual(timers[0].interval, 5)
        self.assertEqual(
            play_calls[1],
            (str(wav), fake_winsound.SND_FILENAME | fake_winsound.SND_ASYNC),
        )

    def test_config_parses_live_sound_file(self):
        source = Path(__file__).resolve().parents[1] / "config.example.json"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.live_sound_file, "")
            self.assertEqual(config.live_sound_loop_seconds, 5)
            config = update_config_values(
                {"live_sound_file": "sounds/live.wav", "live_sound_loop_seconds": 12},
                path,
            )
            self.assertEqual(config.live_sound_file, "sounds/live.wav")
            self.assertEqual(config.live_sound_loop_seconds, 12)

    @unittest.skipUnless(os.name == "nt", "Windows named events only")
    def test_single_instance_can_signal_existing_process(self):
        name = f"ASOULReminderTest-{uuid.uuid4().hex}"
        primary = SingleInstance(name)
        secondary = SingleInstance(name)
        try:
            self.assertTrue(primary.acquire())
            self.assertFalse(secondary.acquire())
            self.assertTrue(secondary.signal_existing("check_now"))
            self.assertEqual(primary.poll_commands(), ["check_now"])
            self.assertEqual(primary.poll_commands(), [])
        finally:
            secondary.release()
            primary.release()


if __name__ == "__main__":
    unittest.main()
