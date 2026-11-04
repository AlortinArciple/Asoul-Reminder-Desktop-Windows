import json
import tempfile
import unittest
from pathlib import Path

from asoul_reminder.config import (
    DEFAULT_MEMBER_IDENTITIES,
    fill_default_identities,
    load_config,
    read_config_data,
)

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "config.example.json"


def member_by_id(raw: dict, member_id: str) -> dict:
    return next(item for item in raw["members"] if item.get("id") == member_id)


class FillDefaultIdentitiesTests(unittest.TestCase):
    def test_fills_empty_uid_and_dynamic_url(self):
        raw = {
            "members": [
                {"id": "fiona", "uid": "", "dynamic_url": ""},
                {"id": "gladys", "uid": "", "dynamic_url": ""},
            ]
        }

        changed = fill_default_identities(raw)

        self.assertTrue(changed)
        for member_id in ("fiona", "gladys"):
            member = member_by_id(raw, member_id)
            uid, dynamic_url = DEFAULT_MEMBER_IDENTITIES[member_id]
            self.assertEqual(member["uid"], uid)
            self.assertEqual(member["dynamic_url"], dynamic_url)

    def test_keeps_user_filled_values(self):
        raw = {
            "members": [
                {
                    "id": "fiona",
                    "uid": "999999",
                    "dynamic_url": "https://space.bilibili.com/999999/dynamic",
                }
            ]
        }

        self.assertFalse(fill_default_identities(raw))
        member = member_by_id(raw, "fiona")
        self.assertEqual(member["uid"], "999999")
        self.assertEqual(member["dynamic_url"], "https://space.bilibili.com/999999/dynamic")

    def test_fills_only_the_missing_field(self):
        raw = {"members": [{"id": "gladys", "uid": "123", "dynamic_url": ""}]}

        self.assertTrue(fill_default_identities(raw))

        member = member_by_id(raw, "gladys")
        self.assertEqual(member["uid"], "123")
        self.assertEqual(member["dynamic_url"], DEFAULT_MEMBER_IDENTITIES["gladys"][1])

    def test_ignores_unknown_members_and_bad_shapes(self):
        raw = {
            "members": [
                {"id": "diana", "uid": "", "dynamic_url": ""},
                {"id": "fiona", "uid": "", "dynamic_url": ""},
                "not-a-dict",
            ]
        }

        self.assertTrue(fill_default_identities(raw))

        self.assertEqual(member_by_id(raw, "diana")["uid"], "")
        self.assertEqual(member_by_id(raw, "fiona")["uid"], DEFAULT_MEMBER_IDENTITIES["fiona"][0])
        self.assertFalse(fill_default_identities({"members": "invalid"}))

    def test_load_config_writes_back_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            raw = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
            member_by_id(raw, "fiona")["uid"] = ""
            member_by_id(raw, "fiona")["dynamic_url"] = ""
            member_by_id(raw, "gladys")["uid"] = ""
            member_by_id(raw, "gladys")["dynamic_url"] = ""
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

            config = load_config(path)

            on_disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                member_by_id(on_disk, "fiona")["uid"],
                DEFAULT_MEMBER_IDENTITIES["fiona"][0],
            )
            self.assertEqual(
                member_by_id(on_disk, "gladys")["dynamic_url"],
                DEFAULT_MEMBER_IDENTITIES["gladys"][1],
            )
            self.assertEqual(
                {member.id for member in config.members},
                {"diana", "eileen", "bella", "fiona", "gladys"},
            )

    def test_load_config_does_not_flip_switches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            raw = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
            member_by_id(raw, "fiona")["uid"] = ""
            member_by_id(raw, "fiona")["dynamic_url"] = ""
            fiona = member_by_id(raw, "fiona")
            fiona["enabled"] = True
            fiona["monitor_dynamic"] = False
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

            config = load_config(path)

            loaded = next(member for member in config.members if member.id == "fiona")
            self.assertTrue(loaded.enabled)
            self.assertFalse(loaded.monitor_dynamic)

    def test_example_config_already_complete(self):
        _, raw = read_config_data(EXAMPLE_CONFIG)

        self.assertFalse(fill_default_identities(raw))
        for member_id in ("fiona", "gladys"):
            self.assertTrue(member_by_id(raw, member_id)["uid"])


if __name__ == "__main__":
    unittest.main()
