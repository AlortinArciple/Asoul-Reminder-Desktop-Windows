from __future__ import annotations

import argparse
import json
import logging
import sys

from .asoul import AsoulClient
from .bilibili import BilibiliClient
from .config import load_config
from .http import HttpClient
from .notifications import Notifier
from .paths import asoul_snapshot_path, dynamic_snapshot_path, log_path
from .service import ReminderService
from .single_instance import SingleInstance, show_existing_instance_message
from .startup import enable_startup
from .state import StateStore


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def configure_logging(*, fresh: bool = True) -> None:
    path = log_path()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.insert(0, logging.FileHandler(path, mode="w" if fresh else "a", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
    )


def build_service() -> ReminderService:
    config = load_config()
    http = HttpClient(timeout_seconds=config.request_timeout_seconds)
    state = StateStore()
    notifier = Notifier(
        enabled=config.notifications_enabled,
        live_sound_file=config.live_sound_file,
        live_sound_loop_seconds=config.live_sound_loop_seconds,
    )
    return ReminderService(
        config=config,
        state=state,
        asoul=AsoulClient(config, http),
        bilibili=BilibiliClient(config, http),
        notifier=notifier,
    )


def main(argv: list[str] | None = None) -> None:
    configure_console_encoding()
    parser = argparse.ArgumentParser(description="ASOUL tray reminder")
    parser.add_argument("--check-once", action="store_true", help="run one check then exit")
    parser.add_argument("--status", action="store_true", help="print current monitor status")
    parser.add_argument("--dump-asoul", action="store_true", help="print the latest ASOUL snapshot")
    parser.add_argument("--dump-dynamics", action="store_true", help="print the latest dynamic snapshot")
    parser.add_argument("--window", action="store_true", help="open the desktop dashboard window")
    parser.add_argument("--tray", action="store_true", help="start minimized to tray")
    args = parser.parse_args(argv)

    read_only = bool(args.status or args.dump_asoul or args.dump_dynamics)
    one_shot = bool(args.check_once or read_only)
    instance: SingleInstance | None = None
    if not read_only:
        instance = SingleInstance()
        if not instance.acquire():
            if args.check_once:
                if instance.signal_existing("check_now"):
                    print("已通知正在运行的 Asoul Reminder 立即检查。")
                else:
                    print("Asoul Reminder 已在运行，但旧版本不支持远程检查。")
            elif not args.tray:
                if not instance.signal_existing("show_window"):
                    show_existing_instance_message()
            return

    configure_logging(fresh=not one_shot)
    service = build_service()
    try:
        if args.dump_asoul:
            dump_asoul_snapshot()
            return
        if args.dump_dynamics:
            dump_dynamic_snapshot()
            return
        if args.status:
            print(service.status_text())
            return
        if service.config.auto_start and not args.check_once:
            try:
                enable_startup()
            except Exception:
                logging.getLogger(__name__).exception("Failed to enable startup")
        if args.check_once:
            service.check_all()
            return
        from .window import run_desktop_app

        run_desktop_app(service, show_window=not args.tray, instance=instance)
    finally:
        if instance is not None:
            instance.release()


def dump_asoul_snapshot() -> None:
    path = asoul_snapshot_path()
    if not path.exists():
        print(f"尚未生成 ASOUL 快照：{path}")
        return
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    for item in rows:
        print(
            f"{item.get('start_time')} | {item.get('host')} | {item.get('status')} | "
            f"{item.get('id')} | {item.get('title')} | {item.get('description')}"
        )


def dump_dynamic_snapshot() -> None:
    path = dynamic_snapshot_path()
    if not path.exists():
        print(f"尚未生成动态快照：{path}")
        return
    snapshot = json.loads(path.read_text(encoding="utf-8-sig"))
    for member_id, items in snapshot.items():
        print(f"## {member_id}")
        for item in items:
            print(
                f"{item.get('published_at')} | {item.get('id')} | "
                f"{item.get('text')} | {item.get('url')}"
            )


if __name__ == "__main__":
    main()
