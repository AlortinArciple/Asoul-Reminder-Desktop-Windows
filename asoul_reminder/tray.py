from __future__ import annotations

import logging
import os

from .config import read_config_data, update_config_values, write_config_data
from .notifications import Notifier, ensure_icon
from .paths import default_config_path
from .service import ReminderService
from .startup import (
    disable_startup,
    enable_startup,
    registered_startup_command,
    set_registered_startup_command,
    startup_enabled,
)

LOGGER = logging.getLogger(__name__)


class TrayApp:
    def __init__(self, service: ReminderService) -> None:
        self.service = service
        self.icon = None

    def run(self) -> None:
        import pystray
        from PIL import Image

        icon_file = ensure_icon()
        image = Image.open(icon_file)
        self.icon = pystray.Icon(
            "Asoul Reminder",
            image,
            "Asoul Reminder",
            menu=self.build_menu(pystray),
        )
        self.service.start()
        self.icon.run()

    def build_menu(self, pystray):
        return pystray.Menu(
            pystray.MenuItem("打开 ASOUL 时间表", self.open_asoul),
            pystray.MenuItem("打开嘉然直播间", self.open_diana_live),
            pystray.MenuItem("打开乃琳直播间", self.open_eileen_live),
            pystray.MenuItem("打开贝拉直播间", self.open_bella_live),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("立即检查一次", self.check_now),
            pystray.MenuItem("显示状态", self.show_status),
            pystray.MenuItem(
                "暂停提醒",
                self.toggle_pause,
                checked=lambda item: bool(self.service.state.snapshot().get("paused")),
            ),
            pystray.MenuItem(
                "开机自启动",
                self.toggle_startup,
                checked=lambda item: startup_enabled(),
            ),
            pystray.MenuItem("打开配置文件", self.open_config),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self.quit),
        )

    def open_asoul(self, icon=None, item=None) -> None:
        Notifier.open_url(self.service.config.asoul_home_url)

    def open_diana_live(self, icon=None, item=None) -> None:
        self.open_member_live("diana")

    def open_eileen_live(self, icon=None, item=None) -> None:
        self.open_member_live("eileen")

    def open_bella_live(self, icon=None, item=None) -> None:
        self.open_member_live("bella")

    def open_member_live(self, member_id: str) -> None:
        member = self.service.config.member_by_id.get(member_id)
        if member and member.live_url:
            Notifier.open_url(member.live_url)

    def check_now(self, icon=None, item=None) -> None:
        self.service.check_all_async()

    def show_status(self, icon=None, item=None) -> None:
        self.service.notifier.notify(
            "Asoul Reminder 状态",
            self.service.status_text(),
            self.service.config.asoul_home_url,
        )

    def toggle_pause(self, icon=None, item=None) -> None:
        paused = not bool(self.service.state.snapshot().get("paused"))
        self.service.set_paused(paused)
        self.refresh_menu()

    def toggle_startup(self, icon=None, item=None) -> None:
        original_startup = registered_startup_command()
        original_config = None
        try:
            _, original_config = read_config_data()
            enabled = not startup_enabled()
            if enabled:
                enable_startup()
            else:
                disable_startup()
            update_config_values({"auto_start": enabled})
        except Exception:
            if original_config is not None:
                try:
                    write_config_data(original_config)
                    set_registered_startup_command(original_startup)
                except Exception:
                    LOGGER.exception("Failed to roll back startup settings")
            LOGGER.exception("Failed to toggle startup")
        self.refresh_menu()

    def open_config(self, icon=None, item=None) -> None:
        config_path = default_config_path()
        if os.name == "nt":
            os.startfile(config_path)  # type: ignore[attr-defined]
        else:
            Notifier.open_url(config_path.as_uri())

    def refresh_menu(self) -> None:
        if self.icon:
            self.icon.update_menu()

    def quit(self, icon=None, item=None) -> None:
        self.service.stop()
        if self.icon:
            self.icon.stop()
