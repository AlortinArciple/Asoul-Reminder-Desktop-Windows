import json
import tempfile
import unittest
from pathlib import Path

from asoul_reminder.asoul import solid_deserialize
from asoul_reminder.bilibili import (
    extract_dynamic_rich_text,
    parse_publish_time,
    parse_publish_time_value,
    summarize_dynamic,
)
from asoul_reminder.config import load_config, update_member_config
from asoul_reminder.timeutils import as_beijing_time


class ParserTests(unittest.TestCase):
    def test_solid_deserialize_object_and_array(self):
        payload = [
            0,
            {
                "title": [0, "嘉然突击"],
                "performers": [1, [[0, "diana"], [0, "bella"]]],
                "nested": [0, {"status": [0, "published"]}],
            },
        ]

        self.assertEqual(
            solid_deserialize(payload),
            {
                "title": "嘉然突击",
                "performers": ["diana", "bella"],
                "nested": {"status": "published"},
            },
        )

    def test_summarize_dynamic_prefers_text(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "desc": {"text": "  新 动态\n来了  "},
                    "major": {"archive": {"title": "视频标题"}},
                }
            }
        }

        self.assertEqual(summarize_dynamic(item), "新 动态\n来了")

    def test_summarize_dynamic_preserves_rich_text_node_order(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "desc": {
                        "rich_text_nodes": [
                            {"orig_text": "乃琳"},
                            {"orig_text": "Queen"},
                            {"orig_text": "\n第二行"},
                        ]
                    },
                }
            }
        }

        self.assertEqual(summarize_dynamic(item), "乃琳Queen\n第二行")

    def test_extract_dynamic_rich_text_keeps_bilibili_emoji_url(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "desc": {
                        "rich_text_nodes": [
                            {"type": "RICH_TEXT_NODE_TYPE_TEXT", "orig_text": "可爱"},
                            {
                                "type": "RICH_TEXT_NODE_TYPE_EMOJI",
                                "orig_text": "[嘉然_可爱捏]",
                                "emoji": {
                                    "icon_url": "//i0.hdslb.com/bfs/emote/test.png",
                                },
                            },
                        ]
                    },
                }
            }
        }

        segments = extract_dynamic_rich_text(item)

        self.assertEqual(summarize_dynamic(item), "可爱[嘉然_可爱捏]")
        self.assertEqual(segments[0].type, "text")
        self.assertEqual(segments[0].text, "可爱")
        self.assertEqual(segments[1].type, "emoji")
        self.assertEqual(segments[1].text, "[嘉然_可爱捏]")
        self.assertEqual(segments[1].url, "https://i0.hdslb.com/bfs/emote/test.png")

    def test_extract_dynamic_rich_text_prefers_gif_url_for_animated_emoji(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "desc": {
                        "rich_text_nodes": [
                            {
                                "type": "RICH_TEXT_NODE_TYPE_EMOJI",
                                "orig_text": "[打call]",
                                "emoji": {
                                    "icon_url": "//i0.hdslb.com/bfs/emote/preview.png",
                                    "gif_url": "//i0.hdslb.com/bfs/emote/animated.gif",
                                },
                            },
                        ]
                    },
                }
            }
        }

        segments = extract_dynamic_rich_text(item)

        self.assertEqual(segments[0].type, "emoji")
        self.assertEqual(segments[0].url, "https://i0.hdslb.com/bfs/emote/animated.gif")

    def test_extract_dynamic_rich_text_reads_opus_summary_nodes(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "major": {
                        "opus": {
                            "summary": {
                                "rich_text_nodes": [
                                    {"type": "RICH_TEXT_NODE_TYPE_TEXT", "orig_text": "开心"},
                                    {
                                        "type": "RICH_TEXT_NODE_TYPE_EMOJI",
                                        "orig_text": "[贝拉_吃东西]",
                                        "icon_url": "http://i0.hdslb.com/bfs/emote/bella.png",
                                    },
                                ]
                            }
                        }
                    },
                }
            }
        }

        segments = extract_dynamic_rich_text(item)

        self.assertEqual(summarize_dynamic(item), "开心[贝拉_吃东西]")
        self.assertEqual(segments[0].text, "开心")
        self.assertEqual(segments[1].type, "emoji")
        self.assertEqual(segments[1].url, "https://i0.hdslb.com/bfs/emote/bella.png")

    def test_summarize_dynamic_falls_back_to_major(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "major": {"archive": {"title": "视频标题"}},
                }
            }
        }

        self.assertEqual(summarize_dynamic(item), "视频标题")

    def test_parse_publish_time_uses_numeric_timestamp(self):
        item = {"modules": {"module_author": {"pub_ts": 1710000000}}}

        self.assertEqual(parse_publish_time(item).timestamp(), 1710000000)

    def test_parse_publish_time_accepts_local_datetime_text(self):
        parsed = parse_publish_time_value("2026-06-10 01:23")

        self.assertEqual(as_beijing_time(parsed).strftime("%Y-%m-%d %H:%M"), "2026-06-10 01:23")

    def test_config_normalizes_sinno_to_gladys(self):
        raw = {
            "asoul_calendar_url": "https://asoul.love/calendar/latest",
            "asoul_home_url": "https://asoul.love/",
            "check_interval_minutes": 20,
            "dynamic_check_interval_minutes": 10,
            "dynamic_high_frequency_interval_minutes": 3,
            "dynamic_high_frequency_max_minutes": 60,
            "live_remind_before_minutes": [10, 5],
            "auto_start": False,
            "notifications_enabled": True,
            "members": [
                {
                    "id": "sinno",
                    "name": "思诺",
                    "enabled": True,
                    "monitor_dynamic": False,
                    "monitor_live": True,
                    "color": "#7252C0",
                    "uid": "",
                    "dynamic_url": "",
                    "live_url": "https://live.bilibili.com/30858592",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")

            config = load_config(path)

        self.assertEqual(config.members[0].id, "gladys")
        self.assertEqual(config.member_by_id["gladys"].name, "思诺")
        self.assertEqual(config.member_by_id["sinno"].name, "思诺")
        self.assertTrue(config.asoul_bypass_proxy)

    def test_member_display_name_can_be_updated(self):
        raw = {
            "asoul_calendar_url": "https://asoul.love/calendar/latest",
            "asoul_home_url": "https://asoul.love/",
            "check_interval_minutes": 20,
            "dynamic_check_interval_minutes": 10,
            "dynamic_high_frequency_interval_minutes": 3,
            "dynamic_high_frequency_max_minutes": 60,
            "live_remind_before_minutes": [10, 5],
            "auto_start": False,
            "notifications_enabled": True,
            "members": [
                {
                    "id": "diana",
                    "name": "嘉然",
                    "enabled": True,
                    "monitor_dynamic": True,
                    "monitor_live": True,
                    "color": "#E799B0",
                    "uid": "672328094",
                    "dynamic_url": "https://space.bilibili.com/672328094/dynamic",
                    "live_url": "https://live.bilibili.com/22637261",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")

            update_member_config("diana", {"name": "嘉心糖测试"}, path)
            config = load_config(path)

        self.assertEqual(config.members[0].id, "diana")
        self.assertEqual(config.members[0].name, "嘉心糖测试")


if __name__ == "__main__":
    unittest.main()
