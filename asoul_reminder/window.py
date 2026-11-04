from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

try:  # PySide6 is an optional runtime dependency for the desktop window.
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover - depends on local environment.
    QtCore = None
    QtGui = None
    QtWidgets = None

from .asoul import event_from_state
from .bilibili_login import (
    QR_STATE_EXPIRED,
    QR_STATE_MESSAGES,
    QR_STATE_OK,
    QR_STATE_UNKNOWN,
    QR_STATE_WAITING,
    STATUS_VALID,
    BilibiliLogin,
    CookieStatus,
    LoginUnavailableError,
    QrLoginSession,
    QrPollResult,
    qr_matrix,
)
from .http import HttpClient
from .config import (
    ASOUL_MIN_CHECK_INTERVAL_MINUTES,
    load_config,
    read_config_data,
    update_config_values,
    update_member_config,
    write_config_data,
)
from .emoji import emoji_cache_path
from .formatting import WEEKDAY_TEXT, truncate_text, weekday_text
from .timeutils import as_beijing_time, beijing_now
from .models import LiveEvent, MemberConfig
from .notifications import Notifier, play_live_sound, stop_live_sound
from .paths import (
    app_icon_path,
    asoul_snapshot_path,
    default_config_path,
    dynamic_snapshot_path,
    log_path,
    resolve_app_path,
    tray_icon_path,
)
from .service import ReminderService, parse_time
from .startup import (
    disable_startup,
    enable_startup,
    registered_startup_command,
    set_registered_startup_command,
    startup_enabled,
)
from .updater import (
    SETUP_ASSET_NAME,
    UPDATE_CHECK_INTERVAL_HOURS,
    DownloadCancelled,
    ReleaseInfo,
    UpdateClient,
    UpdateError,
    UpdateProgress,
    download_release,
    is_newer,
)
from .version import app_version


BG = "#ECEBE7"
SURFACE = "#F0EFEB"
SUBTLE = "#EEEDE8"
BORDER = "#FAF8F1"
TEXT = "#3F403D"
MUTED = "#83837D"
SHADOW = "#CFCBC3"
ACCENT = "#FFC83D"
GREEN = "#8AB79C"
WARN = ACCENT
RED = "#D77D72"
SOFT_WRAP = "\u200b"

OPTIONAL_MEMBER_IDS = {"fiona", "gladys", "sinno"}
MEMBER_DISPLAY_IDS = {
    "diana": "Diana",
    "eileen": "Eileen",
    "bella": "Bella",
    "fiona": "Fiona",
    "gladys": "Gladys",
    "sinno": "Gladys",
}

STATUS_LABELS = {
    "published": "已发布",
    "live": "正在直播",
    "rescheduled": "已改期",
    "cancelled": "已取消",
}


def local_clock_text(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')} {WEEKDAY_TEXT[now.weekday()]}"


def run_window(service: ReminderService) -> None:
    run_desktop_app(service, show_window=True)


def run_tray_app(service: ReminderService) -> None:
    run_desktop_app(service, show_window=False)


def run_desktop_app(
    service: ReminderService,
    *,
    show_window: bool,
    instance: Any | None = None,
) -> None:
    if QtWidgets is None or QtCore is None or QtGui is None:
        raise SystemExit("桌面窗口需要 PySide6。请先运行：.\\.venv\\Scripts\\python.exe -m pip install PySide6")

    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("Asoul Reminder")
    app.setStyle("Fusion")
    icon = app_icon_path()
    if icon.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon)))

    tray_available = QtWidgets.QSystemTrayIcon.isSystemTrayAvailable()
    app.setQuitOnLastWindowClosed(not tray_available)
    window = DashboardWindow(service, close_to_tray=tray_available)
    tray = DashboardTray(service, window) if tray_available else None

    ipc_timer: Any | None = None
    if instance is not None:
        def poll_instance_commands() -> None:
            for command in instance.poll_commands():
                if command == "show_window":
                    window.show_dashboard()
                elif command == "check_now":
                    window.check_now()

        ipc_timer = QtCore.QTimer(window)
        ipc_timer.timeout.connect(poll_instance_commands)
        ipc_timer.start(500)

    service.start()
    if tray:
        tray.show()
    if show_window or not tray_available:
        window.show_dashboard()
    try:
        app.exec()
    finally:
        if tray:
            tray.hide()
        service.stop()
        if owns_app:
            app.deleteLater()


class SoftCard(QtWidgets.QFrame if QtWidgets else object):
    def __init__(
        self,
        parent: Any = None,
        *,
        surface: str = SURFACE,
        radius: int = 24,
        shadow: bool = True,
        padding: int = 18,
    ) -> None:
        super().__init__(parent)
        self.surface = surface
        self.radius = radius
        self.setObjectName("softCard")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"""
            QFrame#softCard {{
                background: {surface};
                border: 1px solid {BORDER};
                border-radius: {radius}px;
            }}
            """
        )
        if shadow:
            effect = QtWidgets.QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(22)
            effect.setOffset(8, 8)
            effect.setColor(QtGui.QColor(SHADOW))
            self.setGraphicsEffect(effect)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(padding, padding, padding, padding)
        layout.setSpacing(12)


class ScheduleItemCard(QtWidgets.QFrame if QtWidgets else object):
    def __init__(
        self,
        parent: Any = None,
        *,
        surface: str = SUBTLE,
        stripe: str = ACCENT,
        radius: int = 16,
        stripe_width: int = 8,
        padding: int = 12,
    ) -> None:
        super().__init__(parent)
        self.surface = surface
        self.stripe = stripe
        self.radius = radius
        self.stripe_width = stripe_width
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        effect = QtWidgets.QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(22)
        effect.setOffset(8, 8)
        effect.setColor(QtGui.QColor(SHADOW))
        self.setGraphicsEffect(effect)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(padding + stripe_width, padding, padding, padding)
        layout.setSpacing(12)

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt method name.
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)
        painter.fillPath(path, QtGui.QColor(self.surface))
        painter.setClipPath(path)
        stripe_rect = QtCore.QRectF(rect.left(), rect.top(), self.stripe_width, rect.height())
        painter.fillRect(stripe_rect, QtGui.QColor(self.stripe))
        painter.setClipping(False)
        painter.setPen(QtGui.QPen(QtGui.QColor(BORDER), 1))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()


class DynamicBodyWidget(QtWidgets.QWidget if QtWidgets else object):
    emoji_size = 56
    line_gap = 4

    def __init__(self, data: dict[str, Any], parent: Any = None) -> None:
        super().__init__(parent)
        self.data = data
        self.text_color = QtGui.QColor("#4F524B")
        font = self.font()
        font.setPixelSize(17)
        self.setFont(font)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        self._movies = self._build_emoji_movies()

    def _build_emoji_movies(self) -> dict[int, QtGui.QMovie]:
        movies: dict[int, QtGui.QMovie] = {}
        for index, segment in enumerate(self._segments()):
            if segment["type"] != "emoji" or not segment["url"]:
                continue
            path = emoji_cache_path(segment["url"])
            if not path.exists():
                continue
            movie = QtGui.QMovie(str(path), parent=self)
            if not movie.isValid():
                continue
            movie.setCacheMode(QtGui.QMovie.CacheMode.CacheAll)
            movie.jumpToFrame(0)
            movie.frameChanged.connect(self.update)
            movie.start()
            movies[index] = movie
        return movies

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt method name.
        return True

    def sizeHint(self) -> Any:  # noqa: N802 - Qt method name.
        return QtCore.QSize(360, self.heightForWidth(360))

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt method name.
        return self._layout_for_width(max(120, width))[1]

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt method name.
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        painter.setFont(self.font())
        painter.setPen(self.text_color)
        lines, _, line_gap = self._layout_for_width(max(120, self.width()))
        metrics = QtGui.QFontMetrics(self.font())
        y = 0
        for line, line_height in lines:
            x = 0
            baseline = y + (line_height + metrics.ascent() - metrics.descent()) // 2
            for item in line:
                if item["type"] == "emoji":
                    pixmap = item["movie"].currentPixmap()
                    top = y + (line_height - self.emoji_size) // 2
                    painter.drawPixmap(x, top, self.emoji_size, self.emoji_size, pixmap)
                    x += self.emoji_size
                else:
                    text = item["text"]
                    painter.drawText(x, baseline, text)
                    x += metrics.horizontalAdvance(text)
            y += line_height + line_gap
        painter.end()

    def _layout_for_width(self, width: int) -> tuple[list[tuple[list[dict[str, Any]], int]], int, int]:
        metrics = QtGui.QFontMetrics(self.font())
        text_line_height = metrics.lineSpacing()
        current_line_height = text_line_height
        lines: list[tuple[list[dict[str, Any]], int]] = []
        line: list[dict[str, Any]] = []
        x = 0
        has_rendered_emoji = False

        def finish_line() -> None:
            nonlocal current_line_height, line, x
            lines.append((line, current_line_height))
            line = []
            x = 0
            current_line_height = text_line_height

        def append_text(char: str) -> None:
            nonlocal x
            if char == SOFT_WRAP:
                return
            char_width = metrics.horizontalAdvance(char)
            if char != "\n" and x > 0 and x + char_width > width:
                finish_line()
            if char == "\n":
                finish_line()
                return
            if line and line[-1]["type"] == "text":
                line[-1]["text"] += char
            else:
                line.append({"type": "text", "text": char})
            x += char_width

        def append_emoji(movie: QtGui.QMovie) -> bool:
            nonlocal current_line_height, has_rendered_emoji, x
            pixmap = movie.currentPixmap()
            if pixmap.isNull():
                return False
            if x > 0 and x + self.emoji_size > width:
                finish_line()
            line.append({"type": "emoji", "movie": movie})
            x += self.emoji_size
            current_line_height = max(current_line_height, self.emoji_size)
            has_rendered_emoji = True
            return True

        for segment_index, segment in enumerate(self._segments()):
            if segment["type"] == "emoji":
                movie = self._movies.get(segment_index)
                if movie is not None and append_emoji(movie):
                    continue
            for char in self._display_wrap_text(segment["text"]):
                append_text(char)
        if line or not lines:
            finish_line()
        line_gap = self.line_gap if has_rendered_emoji else 0
        total_height = sum(height for _, height in lines) + line_gap * max(0, len(lines) - 1)
        return lines, total_height, line_gap

    def _segments(self) -> list[dict[str, str]]:
        rich_text = self.data.get("rich_text") or []
        segments: list[dict[str, str]] = []
        for segment in rich_text:
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text") or "")
            if not text:
                continue
            segments.append(
                {
                    "type": str(segment.get("type") or "text"),
                    "text": text,
                    "url": str(segment.get("url") or ""),
                }
            )
        if segments:
            return segments
        return [{"type": "text", "text": str(self.data.get("text") or "新动态"), "url": ""}]

    @staticmethod
    def _display_wrap_text(text: str) -> str:
        def wrap_token(match: re.Match[str]) -> str:
            token = match.group(0)
            return SOFT_WRAP.join(token[index : index + 18] for index in range(0, len(token), 18))

        return re.sub(r"[^\s\u4e00-\u9fff]{28,}", wrap_token, text)


class DashboardWindow(QtWidgets.QMainWindow if QtWidgets else object):
    def __init__(self, service: ReminderService, *, close_to_tray: bool = False) -> None:
        super().__init__()
        self.service = service
        self.close_to_tray = close_to_tray
        self.allow_close = False
        self.has_been_shown = False
        self.render_keys: dict[str, Any] = {}
        self.state_snapshot: dict[str, Any] = {}
        self.status_text = ""
        self.config_widgets: dict[str, Any] = {}
        self.member_widgets: dict[tuple[str, str], Any] = {}
        self.settings_dialog: SettingsDialog | None = None
        self.activity_status_label: Any | None = None
        self.schedule_scope = "today"
        self.schedule_title_label: Any | None = None
        self.schedule_today_button: Any | None = None
        self.schedule_week_button: Any | None = None
        self.schedule_refresh_button: Any | None = None
        self.fast_refresh_until: datetime | None = None
        self.login_dialog: QrLoginDialog | None = None
        self.cookie_prompt_marker: str | None = None
        self.update_dialog: UpdateDialog | None = None
        self.update_checked_at: datetime | None = None
        self.update_busy = False
        self._update_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._update_manual = False

        self.setWindowTitle("Asoul Reminder")
        self.resize(1600, 920)
        self.setMinimumSize(1180, 720)
        icon = app_icon_path()
        if icon.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon)))

        root = QtWidgets.QWidget()
        root.setObjectName("root")
        root.setStyleSheet(self._style_sheet())
        self.setCentralWidget(root)
        main = QtWidgets.QVBoxLayout(root)
        main.setContentsMargins(18, 18, 18, 18)
        main.setSpacing(18)

        self._build_top_bar(main)
        self._build_content(main)

        self.clock_timer = QtCore.QTimer(self)
        self.clock_timer.timeout.connect(self._tick_clock)
        self.clock_timer.start(1000)

        self.data_refresh_timer = QtCore.QTimer(self)
        self.data_refresh_timer.timeout.connect(self._refresh_if_visible)
        self.data_refresh_timer.start(3000)

        self.fast_refresh_timer = QtCore.QTimer(self)
        self.fast_refresh_timer.timeout.connect(self._poll_fast_refresh)
        self.fast_refresh_timer.setInterval(1000)

        self.update_timer = QtCore.QTimer(self)
        self.update_timer.timeout.connect(self._drain_update_queue)
        self.update_timer.start(200)

        self.refresh(force=True)

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt method name.
        if self.close_to_tray and not self.allow_close:
            event.ignore()
            self.hide()
            return
        if not self.close_to_tray:
            self.service.stop()
        super().closeEvent(event)

    def show_dashboard(self) -> None:
        self.refresh(force=True)
        if not self.has_been_shown:
            self._center_on_current_screen()
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.has_been_shown = True
        self.raise_()
        self.activateWindow()
        QtCore.QTimer.singleShot(1500, self._maybe_prompt_cookie_login)
        QtCore.QTimer.singleShot(4000, self.check_updates_on_startup)

    def check_updates_on_startup(self) -> None:
        """启动后静默检查一次更新；同一次运行内只检查一次。"""
        if self.update_checked_at is not None or self.update_busy:
            return
        self.check_for_updates(manual=False)

    def check_for_updates(self, manual: bool = True) -> None:
        """查询 GitHub 最新 Release。manual=False 时无新版不打扰用户。"""
        if self.update_busy:
            return
        now = datetime.now(timezone.utc)
        if not manual and self.update_checked_at is not None:
            if now - self.update_checked_at < timedelta(hours=UPDATE_CHECK_INTERVAL_HOURS):
                return
        self.update_checked_at = now
        self.update_busy = True
        self._update_manual = manual
        if manual:
            self.status_text = "正在检查更新..."
            self._set_activity_status()
        self.refresh_installer_status()

        http = getattr(self.service.bilibili, "http", None) or HttpClient()
        client = UpdateClient(http)

        def worker() -> None:
            try:
                info = client.latest(SETUP_ASSET_NAME)
            except UpdateError as exc:
                self._update_queue.put(("error", str(exc)))
                return
            except Exception as exc:  # 兜底，绝不让后台线程崩掉窗口
                self._update_queue.put(("error", f"检查更新失败：{exc}"))
                return
            self._update_queue.put(("info", info))

        threading.Thread(target=worker, name="update-check", daemon=True).start()

    def _drain_update_queue(self) -> None:
        while True:
            try:
                kind, payload = self._update_queue.get_nowait()
            except queue.Empty:
                return
            self._handle_update_message(kind, payload)

    def _handle_update_message(self, kind: str, payload: Any) -> None:
        self.update_busy = False
        self.refresh_installer_status()
        current = app_version()
        if kind == "error":
            if payload and self._update_manual:
                QtWidgets.QMessageBox.warning(self, "检查更新", str(payload))
            self.status_text = "检查更新失败，稍后可重试"
            self._set_activity_status()
            return
        info = payload
        if not isinstance(info, ReleaseInfo):
            return
        if not is_newer(info.tag, current):
            self.status_text = f"已是最新版本 v{current}"
            self._set_activity_status()
            if self._update_manual:
                QtWidgets.QMessageBox.information(
                    self,
                    "检查更新",
                    f"当前已是最新版本 v{current}。",
                )
            return
        self.status_text = f"发现新版本 {info.tag}"
        self._set_activity_status()
        self.open_update_dialog(info)

    def open_update_dialog(self, info: ReleaseInfo) -> None:
        if self.update_dialog is not None and self.update_dialog.isVisible():
            self.update_dialog.raise_()
            self.update_dialog.activateWindow()
            return
        self.update_dialog = UpdateDialog(self, info)
        self.update_dialog.show()

    def install_update(self, path: str) -> None:
        """关闭程序并运行下载好的安装包。"""
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        QtCore.QTimer.singleShot(600, self._quit_for_update)

    def _quit_for_update(self) -> None:
        self.request_exit()
        service = self.service
        try:
            service.stop()
        except Exception:
            pass
        QtWidgets.QApplication.quit()

    def refresh_installer_status(self) -> None:
        if self.settings_dialog is not None and self.settings_dialog.isVisible():
            self.settings_dialog._update_update_status_label()

    def request_exit(self) -> None:
        self.allow_close = True
        self.close()

    def _center_on_current_screen(self) -> None:
        screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
        if screen is None:
            screen = self.screen() or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        size = self.frameSize()
        width = size.width() if size.width() > 0 else self.width()
        height = size.height() if size.height() > 0 else self.height()
        x = available.x() + max(0, (available.width() - width) // 2)
        y = available.y() + max(0, (available.height() - height) // 2)
        self.move(x, y)

    def _style_sheet(self) -> str:
        return f"""
        QWidget#root {{
            background: {BG};
            color: {TEXT};
            font-family: "Microsoft YaHei UI", "Segoe UI";
            font-size: 14px;
        }}
        QLabel {{
            color: {TEXT};
            background: transparent;
        }}
        QLabel[muted="true"] {{
            color: {MUTED};
        }}
        QPushButton {{
            background: {SURFACE};
            color: {TEXT};
            border: 1px solid {BORDER};
            border-radius: 14px;
            padding: 9px 18px;
        }}
        QPushButton:hover {{
            background: #E7E5DF;
        }}
        QPushButton#primaryButton {{
            background: {ACCENT};
            border: 1px solid #F5BB21;
            font-weight: 700;
        }}
        QPushButton#toggleButton {{
            padding: 7px 12px;
            border-radius: 12px;
        }}
        QPushButton#toggleButton:checked {{
            background: #FFF5CE;
            border: 1px solid #F2C13A;
            font-weight: 700;
        }}
        QLineEdit {{
            background: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 12px;
            padding: 8px 10px;
            color: {TEXT};
        }}
        QCheckBox {{
            color: {TEXT};
            spacing: 8px;
        }}
        QScrollArea {{
            border: 0;
            background: transparent;
        }}
        QScrollBar:vertical {{
            width: 12px;
            background: transparent;
            margin: 4px 0;
        }}
        QScrollBar::handle:vertical {{
            background: #D4D0C8;
            border-radius: 6px;
            min-height: 36px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        """

    def _build_top_bar(self, main: Any) -> None:
        top = SoftCard(padding=18, radius=22)
        main.addWidget(top)
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(24)
        top.layout().addLayout(layout)

        layout.addWidget(self._top_logo(42))

        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(2)
        title = QtWidgets.QLabel("Asoul Reminder")
        title.setStyleSheet("font-size:30px; font-weight:500;")
        subtitle = self._muted_label("直播日程与 B 站动态提醒")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)

        self.clock_label = self._metric("系统时间", "")
        self.next_live_label = self._metric("下次直播", "暂无")
        self.monitor_label = self._metric("运行状态", "")
        layout.addWidget(self.clock_label)
        layout.addWidget(self.next_live_label)
        layout.addWidget(self.monitor_label)
        layout.addStretch(1)

        log_button = QtWidgets.QPushButton("日志")
        log_button.setFixedWidth(64)
        log_button.clicked.connect(self.open_log_file)
        settings_button = QtWidgets.QPushButton("⚙")
        settings_button.setFixedWidth(52)
        settings_button.clicked.connect(self.open_settings)
        refresh_button = QtWidgets.QPushButton("刷新")
        refresh_button.setToolTip("刷新界面并重新读取 ASOUL 日程")
        refresh_button.clicked.connect(self.refresh_dashboard)
        check_button = QtWidgets.QPushButton("立即检查")
        check_button.setObjectName("primaryButton")
        check_button.clicked.connect(self.check_now)
        layout.addWidget(log_button)
        layout.addWidget(settings_button)
        layout.addWidget(refresh_button)
        layout.addWidget(check_button)

    def _build_content(self, main: Any) -> None:
        content = QtWidgets.QHBoxLayout()
        content.setSpacing(20)
        main.addLayout(content, 1)

        self.dynamic_card, self.dynamic_list = self._section("最新动态", "按成员混排，优先使用动态发布时间排序")
        self.schedule_card, self.schedule_list = self._schedule_section()
        self.activity_card, self.activity_list = self._section("运行状态", "检查时间、错误和高频动态窗口")
        self.live_card, self.live_list = self._section("直播间", "B 站直播间状态")

        center = QtWidgets.QVBoxLayout()
        center.setSpacing(20)
        center.addWidget(self.schedule_card, 3)
        center.addWidget(self.activity_card, 2)
        content.addWidget(self.live_card, 3)
        content.addLayout(center, 3)
        content.addWidget(self.dynamic_card, 4)

    def _section(self, title: str, subtitle: str) -> tuple[Any, Any]:
        card = SoftCard(radius=24, padding=18)
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet("font-size:24px; font-weight:700;")
        header.addWidget(title_label)
        header.addStretch(1)
        header.addWidget(self._muted_label(subtitle))
        card.layout().addLayout(header)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)
        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_content)
        card.layout().addWidget(scroll, 1)
        return card, scroll_layout

    def _schedule_section(self) -> tuple[Any, Any]:
        card = SoftCard(radius=24, padding=18)
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.schedule_title_label = QtWidgets.QLabel("今日日程")
        self.schedule_title_label.setStyleSheet("font-size:24px; font-weight:700;")
        header.addWidget(self.schedule_title_label)
        header.addStretch(1)
        self.schedule_refresh_button = QtWidgets.QPushButton()
        self.schedule_refresh_button.setFixedSize(38, 38)
        self.schedule_refresh_button.setAccessibleName("刷新日程")
        self.schedule_refresh_button.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.schedule_refresh_button.setToolTip("立即重新读取 ASOUL 日程")
        self.schedule_refresh_button.clicked.connect(self.refresh_schedule)
        header.addWidget(self.schedule_refresh_button)
        self.schedule_today_button = QtWidgets.QPushButton("今日")
        self.schedule_today_button.setObjectName("toggleButton")
        self.schedule_today_button.setCheckable(True)
        self.schedule_today_button.clicked.connect(lambda _=False: self.set_schedule_scope("today"))
        self.schedule_week_button = QtWidgets.QPushButton("本周")
        self.schedule_week_button.setObjectName("toggleButton")
        self.schedule_week_button.setCheckable(True)
        self.schedule_week_button.clicked.connect(lambda _=False: self.set_schedule_scope("week"))
        header.addWidget(self.schedule_today_button)
        header.addWidget(self.schedule_week_button)
        card.layout().addLayout(header)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)
        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_content)
        card.layout().addWidget(scroll, 1)
        self._update_schedule_toggle()
        return card, scroll_layout

    def _metric(self, title: str, value: str) -> Any:
        box = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._muted_label(title))
        label = QtWidgets.QLabel(value)
        label.setStyleSheet("font-size:18px; font-weight:700;")
        layout.addWidget(label)
        return box

    def _top_logo(self, size: int) -> Any:
        label = QtWidgets.QLabel("AS")
        label.setObjectName("logo")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(size, size)
        icon_file = tray_icon_path()
        if icon_file.exists():
            pixmap = QtGui.QIcon(str(icon_file)).pixmap(size, size)
            if not pixmap.isNull():
                label.setText("")
                label.setPixmap(pixmap)
                label.setStyleSheet("background: transparent;")
                return label
        label.setStyleSheet(
            f"background:{ACCENT}; color:{TEXT}; border-radius:12px; font-size:18px; font-weight:700;"
        )
        return label

    def _metric_value(self, widget: Any, value: str) -> None:
        label = widget.layout().itemAt(1).widget()
        label.setText(value)

    def _muted_label(self, text: str) -> Any:
        label = QtWidgets.QLabel(text)
        label.setProperty("muted", True)
        return label

    def refresh(self, force: bool = False) -> None:
        self.state_snapshot = self.service.state.snapshot()
        self._reload_member_config()
        self.status_text = f"已刷新：{datetime.now().strftime('%H:%M:%S')}"
        dynamic_items = self._dynamic_feed_items()
        dynamic_key = self._dynamic_render_key(dynamic_items)
        if force or dynamic_key != self.render_keys.get("dynamic"):
            self._render_dynamic_feed(dynamic_items)
            self.render_keys["dynamic"] = dynamic_key

        schedule_events = self._schedule_events()
        schedule_key = (self.schedule_scope, self._schedule_render_key(schedule_events))
        if force or schedule_key != self.render_keys.get("schedule"):
            self._render_schedule(schedule_events)
            self.render_keys["schedule"] = schedule_key

        activity_rows = self._activity_rows()
        activity_key = tuple(activity_rows)
        if force or activity_key != self.render_keys.get("activity"):
            self._render_activity(activity_rows)
            self.render_keys["activity"] = activity_key
        self._set_activity_status()

        room_status = self.state_snapshot.get("live_room_status") or {}
        live_members = [member for member in self.service.config.members if member.enabled and member.monitor_live]
        live_key = self._live_render_key(live_members, room_status)
        if force or live_key != self.render_keys.get("live"):
            self._render_live_rooms(room_status, live_members)
            self.render_keys["live"] = live_key

        self._render_top_status()
        self._maybe_prompt_cookie_login()

    def _maybe_prompt_cookie_login(self) -> None:
        """Cookie 失效时自动弹一次扫码窗口；同一份失效记录只弹一次。"""
        state = self.state_snapshot or {}
        if not state.get("bilibili_cookie_expired") or not self.isVisible():
            return
        marker = str(
            ((state.get("last_errors") or {}).get("bilibili_cookie") or {}).get("time") or "expired"
        )
        if marker == self.cookie_prompt_marker:
            return
        self.cookie_prompt_marker = marker
        self.open_qr_login(auto=True)

    def open_qr_login(self, auto: bool = False) -> None:
        if self.login_dialog is not None and self.login_dialog.isVisible():
            self.login_dialog.raise_()
            self.login_dialog.activateWindow()
            return
        self.login_dialog = QrLoginDialog(self)
        if auto:
            self.login_dialog.setWindowTitle("B 站 Cookie 已失效，请重新扫码登录")
        self.login_dialog.show()

    def refresh_cookie_status(self) -> None:
        if self.settings_dialog is not None and self.settings_dialog.isVisible():
            self.settings_dialog._update_cookie_status_label()

    def refresh_dashboard(self, checked: bool = False) -> None:
        self.refresh(force=True)
        self.refresh_schedule()

    def check_now(self) -> None:
        self.status_text = "正在检查 ASOUL 日程、直播间状态和动态..."
        self._set_activity_status()
        self._start_fast_refresh()
        self.service.check_all_async()

    def refresh_schedule(self) -> None:
        self._reload_member_config()
        mode = "直连" if self.service.config.asoul_bypass_proxy else "系统代理"
        self.status_text = f"正在刷新 ASOUL 日程（{mode}）..."
        self._set_activity_status()
        self._start_fast_refresh()
        if not self.service.refresh_schedule_async():
            self.status_text = "日程刷新已经在进行中。"
            self._set_activity_status()

    def _refresh_if_visible(self) -> None:
        if self.isVisible():
            self.refresh()

    def _start_fast_refresh(self) -> None:
        self.fast_refresh_until = datetime.now(timezone.utc) + timedelta(seconds=90)
        if not self.fast_refresh_timer.isActive():
            self.fast_refresh_timer.start()

    def _poll_fast_refresh(self) -> None:
        if not self.fast_refresh_until or datetime.now(timezone.utc) > self.fast_refresh_until:
            self.fast_refresh_timer.stop()
            self.fast_refresh_until = None
            return
        if self.isVisible():
            self.refresh()

    def open_settings(self) -> None:
        if self.settings_dialog and self.settings_dialog.isVisible():
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.show()

    def open_log_file(self) -> None:
        path = log_path()
        if not path.exists():
            QtWidgets.QMessageBox.information(self, "日志", f"日志文件还不存在：\n{path}")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def set_schedule_scope(self, scope: str) -> None:
        if scope not in {"today", "week"}:
            return
        if self.schedule_scope == scope:
            self._update_schedule_toggle()
            return
        self.schedule_scope = scope
        self._update_schedule_toggle()
        self.render_keys.pop("schedule", None)
        self.refresh()

    def _update_schedule_toggle(self) -> None:
        if self.schedule_title_label is not None:
            self.schedule_title_label.setText("本周日程" if self.schedule_scope == "week" else "今日日程")
        if self.schedule_today_button is not None:
            self.schedule_today_button.setChecked(self.schedule_scope == "today")
        if self.schedule_week_button is not None:
            self.schedule_week_button.setChecked(self.schedule_scope == "week")

    def _reload_member_config(self) -> None:
        config = load_config()
        self.service.apply_config(config)

    def _dynamic_render_key(self, items: list[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
        return tuple(
            (
                item["member"].id,
                item["data"].get("id"),
                item["data"].get("text"),
                tuple(
                    (
                        segment.get("type"),
                        segment.get("text"),
                        segment.get("url"),
                    )
                    for segment in item["data"].get("rich_text", [])
                    if isinstance(segment, dict)
                ),
                item["data"].get("published_at"),
                item["time_label"],
            )
            for item in items[:24]
        )

    def _schedule_render_key(self, events: list[LiveEvent]) -> tuple[tuple[Any, ...], ...]:
        return tuple(
            (
                event.id,
                event.title,
                event.description,
                event.start_time.isoformat(),
                event.host,
                event.status,
                event.color,
            )
            for event in events[:32]
        )

    def _live_render_key(
        self,
        live_members: list[MemberConfig],
        room_status: dict[str, Any],
    ) -> tuple[tuple[Any, ...], ...]:
        key = []
        for member in live_members:
            room = room_status.get(member.id)
            if isinstance(room, dict):
                room_key = (
                    room.get("live_status"),
                    room.get("title"),
                    room.get("checked_at"),
                    room.get("live_time"),
                )
            else:
                room_key = (None, None, None, None)
            key.append((member.id, member.name, member.live_url, member.color, *room_key))
        return tuple(key)

    def _render_dynamic_feed(self, items: list[dict[str, Any]]) -> None:
        self._clear_layout(self.dynamic_list)
        if not items:
            self.dynamic_list.addWidget(self._muted_label("暂无动态快照，或启用成员未开启动态监控。"))
        for item in items[:24]:
            self.dynamic_list.addWidget(self._dynamic_item_card(item))
        self.dynamic_list.addStretch(1)

    def _dynamic_item_card(self, item: dict[str, Any]) -> Any:
        member = item["member"]
        card = SoftCard(surface=SUBTLE, radius=18, padding=14)
        layout = QtWidgets.QGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        card.layout().addLayout(layout)

        avatar = self._avatar(member, 34)
        layout.addWidget(avatar, 0, 0, 2, 1, QtCore.Qt.AlignmentFlag.AlignTop)
        name = QtWidgets.QLabel(member.name)
        name.setStyleSheet("font-size:16px; font-weight:700;")
        layout.addWidget(name, 0, 1)
        layout.addWidget(self._muted_label(item["time_label"]), 0, 2, QtCore.Qt.AlignmentFlag.AlignRight)

        body = DynamicBodyWidget(item["data"])
        layout.addWidget(body, 1, 1, 1, 2)

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        url = str(item["data"].get("url") or member.dynamic_url or "")
        if url:
            button = QtWidgets.QPushButton("打开")
            button.clicked.connect(lambda _=False, target=url: Notifier.open_url(target))
            footer.addWidget(button)
        layout.addLayout(footer, 2, 1, 1, 2)
        layout.setColumnStretch(1, 1)
        return card

    @staticmethod
    def _display_wrap_text(text: str) -> str:
        def wrap_token(match: re.Match[str]) -> str:
            token = match.group(0)
            return SOFT_WRAP.join(token[index : index + 18] for index in range(0, len(token), 18))

        return re.sub(r"[^\s\u4e00-\u9fff]{28,}", wrap_token, text)

    def _dynamic_feed_items(self) -> list[dict[str, Any]]:
        snapshot = self._load_dynamic_snapshot()
        last_dynamic = self.state_snapshot.get("last_dynamic_check") or {}
        rows: list[dict[str, Any]] = []
        fallback_now = datetime.fromtimestamp(0, tz=timezone.utc)
        for member in self.service.config.members:
            if not member.enabled or not member.monitor_dynamic or not member.uid:
                continue
            fallback_time = parse_time(last_dynamic.get(member.id)) or fallback_now
            for order, item in enumerate(snapshot.get(member.id) or []):
                published_at = parse_time(item.get("published_at"))
                sort_time = published_at or fallback_time
                rows.append(
                    {
                        "member": member,
                        "data": item,
                        "sort_time": sort_time,
                        "order": order,
                        "time_label": self._dynamic_time_label(published_at),
                    }
                )
        rows.sort(key=lambda row: (row["sort_time"], -row["order"]), reverse=True)
        return rows

    def _dynamic_time_label(self, published_at: datetime | None) -> str:
        if published_at:
            return f"发布 {as_beijing_time(published_at).strftime('%m-%d %H:%M')} {weekday_text(published_at)}"
        return "发布时间未知"

    def _today_events(self) -> list[LiveEvent]:
        today = beijing_now().date()
        return [
            event
            for event in self._events()
            if as_beijing_time(event.start_time).date() == today and event.status != "rescheduled"
        ]

    def _week_events(self) -> list[LiveEvent]:
        today = beijing_now().date()
        calendar_week_start = today - timedelta(days=today.weekday())
        week_start = today
        week_end = calendar_week_start + timedelta(days=7)
        return [
            event
            for event in self._events()
            if week_start <= as_beijing_time(event.start_time).date() < week_end
            and event.status != "rescheduled"
        ]

    def _schedule_events(self) -> list[LiveEvent]:
        if self.schedule_scope == "week":
            return self._week_events()
        return self._today_events()

    def _render_schedule(self, events: list[LiveEvent]) -> None:
        self._clear_layout(self.schedule_list)
        if not events:
            empty_text = "本周暂无已读取到的直播日程。" if self.schedule_scope == "week" else "今天暂无已读取到的直播日程。"
            self.schedule_list.addWidget(self._muted_label(empty_text))
        for event in events[:32]:
            self.schedule_list.addWidget(self._schedule_item_card(event))
        self.schedule_list.addStretch(1)

    def _schedule_item_card(self, event: LiveEvent) -> Any:
        color = event.color or self._member_color(event.host) or ACCENT
        card = ScheduleItemCard(surface=SUBTLE, stripe=color, radius=16, stripe_width=8, padding=12)
        layout = QtWidgets.QGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        card.layout().addLayout(layout)
        if self.schedule_scope == "week":
            time_label = QtWidgets.QLabel(
                f"{as_beijing_time(event.start_time).strftime('%m-%d %H:%M')}<br>"
                f"<span style=\"color:{MUTED}; font-size:12px;\">{weekday_text(event.start_time)}</span>"
            )
            time_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        else:
            time_label = QtWidgets.QLabel(as_beijing_time(event.start_time).strftime("%H:%M"))
        time_label.setStyleSheet("font-size:16px;")
        time_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
        )
        layout.addWidget(time_label, 0, 0, 2, 1)
        title = QtWidgets.QLabel(f"{self._member_name(event.host)} · {event.title}")
        title.setStyleSheet("font-weight:600;")
        layout.addWidget(title, 0, 1)
        layout.addWidget(self._muted_label(STATUS_LABELS.get(event.status, event.status)), 0, 2)
        desc = truncate_text(event.description, 52) if event.description else ""
        layout.addWidget(self._muted_label(desc), 1, 1, 1, 2)
        layout.setColumnStretch(1, 1)
        return card

    def _render_activity(self, rows: list[tuple[str, str, str]]) -> None:
        self._clear_layout(self.activity_list)
        for time_text, item_text, state_text in rows:
            card = SoftCard(surface=SUBTLE, radius=16, padding=10)
            layout = QtWidgets.QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            card.layout().addLayout(layout)
            layout.addWidget(self._muted_label(time_text))
            item = QtWidgets.QLabel(item_text)
            item.setStyleSheet("font-weight:600;")
            layout.addWidget(item)
            layout.addWidget(self._muted_label(state_text), 1)
            self.activity_list.addWidget(card)
        self.activity_status_label = self._muted_label(self.status_text or "准备就绪")
        self.activity_list.addWidget(self.activity_status_label)
        self.activity_list.addStretch(1)

    def _set_activity_status(self) -> None:
        if self.activity_status_label is not None:
            self.activity_status_label.setText(self.status_text or "准备就绪")

    def _activity_rows(self) -> list[tuple[str, str, str]]:
        rows = []
        state = self.state_snapshot
        rows.append((self._time_text(parse_time(state.get("last_asoul_check"))), "ASOUL 日程", "最近一次检查"))
        for member in self.service.config.enabled_dynamic_members:
            checked = parse_time((state.get("last_dynamic_check") or {}).get(member.id))
            latest_id = (state.get("latest_dynamic_id") or {}).get(member.id) or "-"
            rows.append((self._time_text(checked), f"{member.name}动态", f"最新 ID：{latest_id}"))
        for member_id, window in (state.get("high_frequency_dynamic") or {}).items():
            expires = parse_time(window.get("expires_at"))
            rows.append((self._time_text(expires), self._member_name(member_id), "高频动态检查中"))
        for key, error in (state.get("last_errors") or {}).items():
            rows.append((self._time_text(parse_time(error.get("time"))), key, str(error.get("message") or "")))
        return rows[:12]

    def _render_live_rooms(self, room_status: dict[str, Any], members: list[MemberConfig]) -> None:
        self._clear_layout(self.live_list)
        if not members:
            self.live_list.addWidget(self._muted_label("暂无启用的直播间监控成员。"))
        for member in members:
            self.live_list.addWidget(self._live_room_card(member, room_status.get(member.id)))
        self.live_list.addStretch(1)

    def _live_room_card(self, member: MemberConfig, room: Any) -> Any:
        card = SoftCard(surface=SUBTLE, radius=18, padding=14)
        layout = QtWidgets.QGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        card.layout().addLayout(layout)
        layout.addWidget(self._avatar(member, 38), 0, 0, 3, 1, QtCore.Qt.AlignmentFlag.AlignTop)
        name = QtWidgets.QLabel(member.name)
        name.setStyleSheet("font-size:17px; font-weight:700;")
        layout.addWidget(name, 0, 1)
        badge_text, badge_color = self._live_room_status_parts(room)
        badge = QtWidgets.QLabel(badge_text)
        badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        text_color = TEXT if badge_color == ACCENT else "#FFFFFF"
        badge.setStyleSheet(
            f"background:{badge_color}; color:{text_color}; border-radius:10px; padding:3px 12px; font-weight:700;"
        )
        layout.addWidget(badge, 0, 2, QtCore.Qt.AlignmentFlag.AlignRight)
        title = self._muted_label(self._live_title(room))
        title.setWordWrap(True)
        layout.addWidget(title, 1, 1, 1, 2)
        layout.addWidget(self._muted_label(self._live_room_text(room)), 2, 1)
        if member.live_url:
            button = QtWidgets.QPushButton("打开直播间")
            button.clicked.connect(lambda _=False, target=member.live_url: Notifier.open_url(target))
            layout.addWidget(button, 2, 2)
        else:
            layout.addWidget(self._muted_label("直播间未配置"), 2, 2)
        layout.setColumnStretch(1, 1)
        return card

    def _render_top_status(self) -> None:
        self._metric_value(self.clock_label, local_clock_text())
        next_event = self._next_event()
        if next_event:
            self._metric_value(
                self.next_live_label,
                f"{self._member_name(next_event.host)} {as_beijing_time(next_event.start_time).strftime('%m-%d %H:%M')} {weekday_text(next_event.start_time)}",
            )
        else:
            self._metric_value(self.next_live_label, "暂无后续直播")

        monitor = self.state_snapshot.get("monitor_process") or {}
        heartbeat = parse_time(monitor.get("heartbeat_at"))
        stopped = parse_time(monitor.get("stopped_at"))
        if heartbeat and (not stopped or heartbeat > stopped):
            age = datetime.now(timezone.utc) - heartbeat
            status = "监控中" if age <= timedelta(seconds=120) else "可能未运行"
        else:
            status = "未检测到后台监控"
        self._metric_value(self.monitor_label, status)

    def _tick_clock(self) -> None:
        self._metric_value(self.clock_label, local_clock_text())

    def _events(self) -> list[LiveEvent]:
        events = self.service.current_events()
        if not events:
            events = self._load_asoul_snapshot()
        return sorted(events, key=lambda item: item.start_time)

    def _next_event(self) -> LiveEvent | None:
        now = datetime.now(timezone.utc)
        for event in self._events():
            if event.status in {"cancelled", "rescheduled"}:
                continue
            if event.start_time >= now:
                return event
        return None

    def _load_asoul_snapshot(self) -> list[LiveEvent]:
        path = asoul_snapshot_path()
        if not path.exists():
            return []
        try:
            rows = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return []
        events = []
        for item in rows:
            if not isinstance(item, dict) or not item.get("start_time"):
                continue
            try:
                events.append(event_from_state(item))
            except Exception:
                continue
        return events

    def _load_dynamic_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        path = dynamic_snapshot_path()
        if not path.exists():
            return {}
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        if isinstance(snapshot, dict):
            return {str(key): value for key, value in snapshot.items() if isinstance(value, list)}
        return {}

    def _live_room_status_parts(self, status: Any) -> tuple[str, str]:
        if not isinstance(status, dict):
            return "未检查", MUTED
        live_status = status.get("live_status")
        if live_status == 1:
            return "直播中", GREEN
        if live_status in (0, 2):
            return "未开播", MUTED
        return "未知", RED

    def _live_title(self, status: Any) -> str:
        if not isinstance(status, dict):
            return "尚未读取直播间状态"
        title = str(status.get("title") or "").strip()
        return truncate_text(title, 58) if title else "直播间暂无标题"

    def _live_room_text(self, status: Any) -> str:
        if not isinstance(status, dict):
            return "直播间状态未检查"
        live_status = status.get("live_status")
        checked = self._time_text(parse_time(status.get("checked_at")))
        if live_status == 1:
            return f"直播中 · {checked}"
        if live_status == 2:
            return f"未开播 · 轮播状态 · {checked}"
        if live_status == 0:
            return f"未开播 · {checked}"
        if live_status is None:
            return "直播间状态未检查"
        return f"live_status={live_status} · {checked}"

    def _member_name(self, member_id: str) -> str:
        member = self.service.config.member_by_id.get(member_id)
        return member.name if member else member_id

    def _member_color(self, member_id: str) -> str:
        member = self.service.config.member_by_id.get(member_id)
        return member.color if member else ACCENT

    def _member_display_id(self, member_id: str) -> str:
        return MEMBER_DISPLAY_IDS.get(member_id, member_id[:1].upper() + member_id[1:])

    def _avatar(self, member: MemberConfig, size: int) -> Any:
        label = QtWidgets.QLabel(member.name[:1])
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(size, size)
        avatar_file = self._member_avatar_file(member)
        if avatar_file:
            pixmap = QtGui.QPixmap(str(avatar_file))
            if not pixmap.isNull():
                label.setPixmap(
                    self._rounded_pixmap(pixmap, size, self._avatar_device_pixel_ratio(label))
                )
                label.setStyleSheet("background: transparent;")
                return label
        label.setStyleSheet(
            f"background:{member.color or ACCENT}; color:#FFFFFF; border-radius:{size // 2}px; font-weight:700;"
        )
        return label

    def _member_avatar_file(self, member: MemberConfig) -> Any | None:
        candidates = []
        if member.avatar_path.strip():
            candidates.append(resolve_app_path(member.avatar_path.strip()))
        for suffix in ("png", "jpg", "jpeg", "webp", "bmp", "ico"):
            candidates.append(resolve_app_path(f"assets/avatars/{member.id}.{suffix}"))
        for path in candidates:
            if path.exists() and path.is_file():
                return path
        return None

    @staticmethod
    def _avatar_device_pixel_ratio(widget: Any) -> float:
        ratios = [float(widget.devicePixelRatioF() or 1.0)]
        screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            ratios.append(float(screen.devicePixelRatio() or 1.0))
        return max(ratios)

    @staticmethod
    def _rounded_pixmap(source: Any, size: int, device_pixel_ratio: float = 1.0) -> Any:
        dpr = max(1.0, float(device_pixel_ratio or 1.0))
        physical_size = max(1, round(size * dpr))

        source_width = source.width()
        source_height = source.height()
        crop_size = min(source_width, source_height)
        crop_x = max(0, (source_width - crop_size) // 2)
        crop_y = max(0, (source_height - crop_size) // 2)
        cropped = source.copy(crop_x, crop_y, crop_size, crop_size)
        scaled = cropped.scaled(
            physical_size,
            physical_size,
            QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )

        result = QtGui.QPixmap(physical_size, physical_size)
        result.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(result)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        path = QtGui.QPainterPath()
        path.addEllipse(0, 0, physical_size, physical_size)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, scaled)
        painter.end()
        result.setDevicePixelRatio(dpr)
        return result

    @staticmethod
    def _time_text(value: datetime | None) -> str:
        if not value:
            return "-"
        return f"{as_beijing_time(value).strftime('%m-%d %H:%M')} {weekday_text(value)}"

    @staticmethod
    def _clear_layout(layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                DashboardWindow._clear_layout(child_layout)


class DashboardTray(QtCore.QObject if QtCore else object):
    def __init__(self, service: ReminderService, window: DashboardWindow) -> None:
        super().__init__()
        self.service = service
        self.window = window
        self.tray = QtWidgets.QSystemTrayIcon(self)
        icon_file = tray_icon_path()
        if not icon_file.exists():
            icon_file = app_icon_path()
        if icon_file.exists():
            self.tray.setIcon(QtGui.QIcon(str(icon_file)))
        self.tray.setToolTip("Asoul Reminder")
        self.menu = QtWidgets.QMenu()
        self.pause_action: Any | None = None
        self.startup_action: Any | None = None
        self._build_menu()
        self.menu.aboutToShow.connect(self.refresh_menu_state)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        show_action = self.menu.addAction("打开桌面窗口")
        show_action.triggered.connect(self.show_window)

        self.menu.addSeparator()
        asoul_action = self.menu.addAction("打开 ASOUL 时间表")
        asoul_action.triggered.connect(lambda _=False: Notifier.open_url(self.service.config.asoul_home_url))

        live_menu = self.menu.addMenu("打开直播间")
        for member in self.service.config.members:
            if not member.live_url:
                continue
            action = live_menu.addAction(member.name)
            action.triggered.connect(lambda _=False, target=member.live_url: Notifier.open_url(target))

        self.menu.addSeparator()
        schedule_action = self.menu.addAction("刷新日程")
        schedule_action.triggered.connect(self.refresh_schedule)
        check_action = self.menu.addAction("立即检查一次")
        check_action.triggered.connect(self.check_now)
        status_action = self.menu.addAction("显示状态")
        status_action.triggered.connect(self.show_status)

        self.pause_action = self.menu.addAction("暂停提醒")
        self.pause_action.setCheckable(True)
        self.pause_action.triggered.connect(self.toggle_pause)

        self.startup_action = self.menu.addAction("开机自启动")
        self.startup_action.setCheckable(True)
        self.startup_action.triggered.connect(self.toggle_startup)

        config_action = self.menu.addAction("打开配置文件")
        config_action.triggered.connect(self.open_config)

        self.menu.addSeparator()
        quit_action = self.menu.addAction("退出")
        quit_action.triggered.connect(self.quit)
        self.refresh_menu_state()

    def show(self) -> None:
        self.tray.show()

    def hide(self) -> None:
        self.tray.hide()

    def _on_activated(self, reason: Any) -> None:
        if reason in (
            QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
            QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def show_window(self, checked: bool = False) -> None:
        self.window.show_dashboard()

    def check_now(self, checked: bool = False) -> None:
        self.window.status_text = "正在检查 ASOUL 日程、直播间状态和动态..."
        self.window._set_activity_status()
        self.window._start_fast_refresh()
        self.service.check_all_async()

    def refresh_schedule(self, checked: bool = False) -> None:
        self.window.refresh_schedule()

    def show_status(self, checked: bool = False) -> None:
        self.service.notifier.notify(
            "Asoul Reminder 状态",
            self.service.status_text(),
            self.service.config.asoul_home_url,
        )

    def toggle_pause(self, checked: bool = False) -> None:
        self.service.set_paused(bool(checked))
        self.refresh_menu_state()

    def toggle_startup(self, checked: bool = False) -> None:
        original_config: dict[str, Any] | None = None
        original_startup = registered_startup_command()
        try:
            _, original_config = read_config_data()
            if checked:
                enable_startup()
            else:
                disable_startup()
            update_config_values({"auto_start": bool(checked)})
        except Exception as exc:
            if original_config is not None:
                try:
                    write_config_data(original_config)
                    set_registered_startup_command(original_startup)
                except Exception:
                    pass
            QtWidgets.QMessageBox.warning(self.window, "开机自启动设置失败", str(exc))
        self.window._reload_member_config()
        self.refresh_menu_state()

    def open_config(self, checked: bool = False) -> None:
        config_path = default_config_path()
        if os.name == "nt":
            os.startfile(config_path)  # type: ignore[attr-defined]
        else:
            Notifier.open_url(config_path.as_uri())

    def refresh_menu_state(self) -> None:
        if self.pause_action:
            self.pause_action.setChecked(bool(self.service.state.snapshot().get("paused")))
        if self.startup_action:
            self.startup_action.setChecked(startup_enabled())

    def quit(self, checked: bool = False) -> None:
        self.window.request_exit()
        QtWidgets.QApplication.quit()


class QrLoginDialog(QtWidgets.QDialog if QtWidgets else object):
    """扫码登录对话框：网络请求在后台线程，界面只消费队列消息。"""

    POLL_INTERVAL_SECONDS = 2

    def __init__(self, dashboard: DashboardWindow) -> None:
        super().__init__(dashboard)
        self.dashboard = dashboard
        http = getattr(dashboard.service.bilibili, "http", None) or HttpClient()
        self.login = BilibiliLogin(http)
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.session: QrLoginSession | None = None
        self.finished_login = False

        self.setWindowTitle("扫码登录 B 站")
        self.setModal(False)
        self.setMinimumWidth(430)
        self._build()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._drain_messages)
        self.timer.start(150)
        self._start_worker()

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("扫码登录 B 站")
        title.setStyleSheet("font-size:20px; font-weight:700;")
        layout.addWidget(title)

        hint = self.dashboard._muted_label(
            "用哔哩哔哩手机客户端扫码。登录成功后会先询问，确认后才写入 config.json；Cookie 只留在本机。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.qr_label = QtWidgets.QLabel("正在获取二维码…")
        self.qr_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(280, 280)
        self.qr_label.setStyleSheet(
            f"background:#FFFFFF; border:1px solid {BORDER}; border-radius:12px; color:#8A8676;"
        )
        layout.addWidget(self.qr_label, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)

        self.status_label = QtWidgets.QLabel("正在连接 B 站登录服务…")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        self.url_label = self.dashboard._muted_label("")
        self.url_label.setWordWrap(True)
        self.url_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url_label.hide()
        layout.addWidget(self.url_label)

        buttons = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("刷新二维码")
        self.refresh_button.clicked.connect(self._restart)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        close_button = QtWidgets.QPushButton("关闭")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _start_worker(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.stop_event.set()
        self.stop_event = threading.Event()
        self.messages = queue.Queue()
        self.refresh_button.setEnabled(False)
        self.qr_label.setPixmap(QtGui.QPixmap())
        self.qr_label.setText("正在获取二维码…")
        self.status_label.setText("正在连接 B 站登录服务…")
        self.url_label.hide()
        self.worker = threading.Thread(
            target=self._run_worker,
            args=(self.messages, self.stop_event),
            name="BilibiliQrLogin",
            daemon=True,
        )
        self.worker.start()

    def _run_worker(self, messages: queue.Queue, stop_event: threading.Event) -> None:
        try:
            session = self.login.create_qr()
        except Exception as exc:
            messages.put(("error", f"获取二维码失败：{exc}"))
            return
        messages.put(("session", session))
        while not stop_event.wait(self.POLL_INTERVAL_SECONDS):
            try:
                result = self.login.poll(session)
            except Exception as exc:
                messages.put(("error", f"轮询登录状态失败：{exc}"))
                return
            messages.put(("poll", result))
            if result.state == QR_STATE_OK:
                try:
                    cookie = self.login.build_cookie(
                        result, self.dashboard.service.config.bilibili_cookie
                    )
                    status = self.login.verify_cookie(cookie)
                except Exception as exc:
                    messages.put(("error", f"登录已完成，但整理 Cookie 失败：{exc}"))
                    return
                messages.put(("success", (cookie, status)))
                return
            if result.state in {QR_STATE_EXPIRED, QR_STATE_UNKNOWN}:
                return

    def _drain_messages(self) -> None:
        while self.isVisible():
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                return
            self._handle_message(kind, payload)

    def _handle_message(self, kind: str, payload: Any) -> None:
        if kind == "session":
            self.session = payload
            self._render_qr(payload.url)
            self.status_label.setText(QR_STATE_MESSAGES[QR_STATE_WAITING])
            return
        if kind == "poll":
            result: QrPollResult = payload
            self.status_label.setText(result.message or QR_STATE_MESSAGES.get(result.state, ""))
            if result.state == QR_STATE_EXPIRED:
                self.qr_label.setPixmap(QtGui.QPixmap())
                self.qr_label.setText("二维码已过期")
                self.refresh_button.setEnabled(True)
            return
        if kind == "success":
            self._handle_success(*payload)
            return
        self.status_label.setText(str(payload))
        self.refresh_button.setEnabled(True)

    def _render_qr(self, url: str) -> None:
        try:
            matrix = qr_matrix(url)
        except LoginUnavailableError as exc:
            self.qr_label.setText("无法生成二维码")
            self.url_label.setText(f"{exc}\n也可以复制链接在手机上打开：{url}")
            self.url_label.show()
            return
        scale = max(4, 240 // max(1, len(matrix)))
        size = len(matrix) * scale
        image = QtGui.QImage(size, size, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtGui.QColor("#FFFFFF"))
        painter = QtGui.QPainter(image)
        painter.fillRect(0, 0, size, size, QtGui.QColor("#FFFFFF"))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor("#161616"))
        for row_index, row in enumerate(matrix):
            for column_index, filled in enumerate(row):
                if filled:
                    painter.drawRect(column_index * scale, row_index * scale, scale, scale)
        painter.end()
        self.qr_label.setPixmap(QtGui.QPixmap.fromImage(image))
        self.url_label.setText(url)
        self.url_label.hide()

    def _handle_success(self, cookie: str, status: CookieStatus) -> None:
        self.finished_login = True
        self.stop_event.set()
        self.timer.stop()
        if status.state == STATUS_VALID:
            detail = f"{status.uname or 'B 站账号'}（UID {status.mid or '-'}）"
        else:
            detail = "账号信息未能校验，但已取得登录凭证"
        self.status_label.setText(f"已登录：{detail}")
        answer = QtWidgets.QMessageBox.question(
            self,
            "确认写入",
            (
                f"登录成功：{detail}\n\n"
                "是否把新的 Cookie 写入 config.json？\n"
                "Cookie 等同登录凭证，只会保存在本机，不会上传。"
            ),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            self.status_label.setText("已取消写入，Cookie 未保存。")
            return
        try:
            update_config_values({"bilibili_cookie": cookie})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "保存失败", f"写入 config.json 失败：{exc}")
            self.status_label.setText("写入 config.json 失败，Cookie 未保存。")
            return
        self.dashboard.service.mark_cookie_updated()
        self.dashboard._reload_member_config()
        self.dashboard.refresh_cookie_status()
        self.dashboard.render_keys.clear()
        self.dashboard.refresh(force=True)
        QtWidgets.QMessageBox.information(
            self, "已保存", "新的 B 站 Cookie 已保存，下一次检查会立即使用。"
        )
        self.accept()

    def _restart(self) -> None:
        self.finished_login = False
        self._start_worker()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt method name.
        self.stop_event.set()
        self.timer.stop()
        super().closeEvent(event)


class UpdateDialog(QtWidgets.QDialog if QtWidgets else object):
    """新版本对话框：后台下载安装包，完成后退出并启动安装程序。"""

    def __init__(self, dashboard: DashboardWindow, info: ReleaseInfo) -> None:
        super().__init__(dashboard)
        self.dashboard = dashboard
        self.info = info
        self.http = getattr(dashboard.service.bilibili, "http", None) or HttpClient()
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.downloaded_path = ""
        self.finished_update = False

        self.setWindowTitle("发现新版本")
        self.setModal(False)
        self.resize(560, 460)
        self._build()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._drain)
        self.timer.start(150)
        self._start_download()

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QtWidgets.QLabel(f"发现新版本 {self.info.tag}")
        title.setStyleSheet("font-size:20px; font-weight:700;")
        layout.addWidget(title)

        detail = QtWidgets.QLabel(
            f"当前版本 v{app_version()} → 最新版本 {self.info.tag}\n"
            f"安装包 {self.info.asset_name}（{self.info.size_text}）"
        )
        detail.setWordWrap(True)
        layout.addWidget(detail)

        notes = QtWidgets.QPlainTextEdit(self.info.notes.strip() or "（该版本没有提供更新说明）")
        notes.setReadOnly(True)
        notes.setMinimumHeight(180)
        layout.addWidget(notes, 1)

        self.status_label = QtWidgets.QLabel("正在下载安装包...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        self.later_button = QtWidgets.QPushButton("稍后")
        self.later_button.setFixedWidth(96)
        self.later_button.clicked.connect(self.reject)
        buttons.addWidget(self.later_button)
        buttons.addStretch(1)
        self.install_button = QtWidgets.QPushButton("立即安装")
        self.install_button.setObjectName("primaryButton")
        self.install_button.setFixedWidth(120)
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self._install)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

    def _start_download(self) -> None:
        self.worker = threading.Thread(target=self._run, name="update-download", daemon=True)
        self.worker.start()

    def _run(self) -> None:
        from .paths import app_data_dir

        target_dir = app_data_dir() / "updates"
        try:
            path = download_release(
                self.http,
                self.info,
                target_dir,
                progress=lambda item: self.messages.put(("progress", item)),
                cancel=self.cancel_event.is_set,
            )
        except DownloadCancelled:
            self.messages.put(("cancelled", None))
            return
        except UpdateError as exc:
            self.messages.put(("error", str(exc)))
            return
        except Exception as exc:  # 兜底
            self.messages.put(("error", f"下载失败：{exc}"))
            return
        self.messages.put(("done", str(path)))

    def _drain(self) -> None:
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                return
            if kind == "progress":
                item = payload
                if isinstance(item, UpdateProgress):
                    if item.total > 0:
                        self.progress.setValue(item.percent)
                        self.status_label.setText(
                            f"正在下载安装包... {item.downloaded / (1024 * 1024):.1f} / "
                            f"{item.total / (1024 * 1024):.1f} MB"
                        )
                    else:
                        self.status_label.setText(
                            f"正在下载安装包... {item.downloaded / (1024 * 1024):.1f} MB"
                        )
            elif kind == "done":
                self.timer.stop()
                self.downloaded_path = str(payload)
                self.progress.setValue(100)
                self.status_label.setText(f"下载完成：{self.downloaded_path}")
                self.install_button.setEnabled(True)
                self.later_button.setText("取消")
                self._install()
            elif kind == "error":
                self.timer.stop()
                self.status_label.setText(str(payload))
                self.install_button.setEnabled(False)
                self.later_button.setText("关闭")
                QtWidgets.QMessageBox.warning(self, "下载失败", str(payload))
            elif kind == "cancelled":
                self.timer.stop()
                self.status_label.setText("下载已取消")
                self.later_button.setText("关闭")

    def _install(self) -> None:
        if not self.downloaded_path:
            return
        self.finished_update = True
        self.timer.stop()
        self.dashboard.install_update(self.downloaded_path)
        self.accept()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt method name.
        if not self.finished_update:
            self.cancel_event.set()
        self.timer.stop()
        super().closeEvent(event)


class SettingsDialog(QtWidgets.QDialog if QtWidgets else object):
    def __init__(self, dashboard: DashboardWindow) -> None:
        super().__init__(dashboard)
        self.dashboard = dashboard
        self.config_widgets: dict[str, Any] = {}
        self.member_widgets: dict[tuple[str, str], Any] = {}
        self.setWindowTitle("设置")
        self.resize(720, 760)
        self.setMinimumSize(620, 620)
        self.setStyleSheet(dashboard._style_sheet())
        self._build()

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        form = QtWidgets.QVBoxLayout(content)
        form.setContentsMargins(0, 0, 10, 0)
        form.setSpacing(14)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        config = self.dashboard.service.config
        title = QtWidgets.QLabel("设置")
        title.setStyleSheet("font-size:24px; font-weight:700;")
        form.addWidget(title)
        form.addWidget(self.dashboard._muted_label("保存后会写回 config.json，Cookie 只显示配置状态。"))
        form.addWidget(self._global_settings_card(config))

        member_title = QtWidgets.QLabel("成员配置")
        member_title.setStyleSheet("font-size:22px; font-weight:700;")
        form.addWidget(member_title)
        for member in config.members:
            form.addWidget(self._member_settings_card(member))
        form.addStretch(1)

    def _global_settings_card(self, config: Any) -> Any:
        card = SoftCard(surface=SUBTLE, radius=20, padding=16)
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        card.layout().addLayout(form)

        for key, label, value in (
            ("notifications_enabled", "通知提醒", config.notifications_enabled),
            ("auto_start", "开机自启", config.auto_start),
            ("write_snapshots", "写出快照", config.write_snapshots),
            ("asoul_bypass_proxy", "日程绕过系统代理", config.asoul_bypass_proxy),
        ):
            checkbox = QtWidgets.QCheckBox(label)
            checkbox.setChecked(value)
            self.config_widgets[key] = checkbox
            form.addRow("", checkbox)

        for key, label, value in (
            ("asoul_calendar_url", "ASOUL 日程地址", config.asoul_calendar_url),
            ("asoul_home_url", "ASOUL 主页地址", config.asoul_home_url),
            (
                "check_interval_minutes",
                f"日程检查间隔/分钟（最低{ASOUL_MIN_CHECK_INTERVAL_MINUTES}）",
                config.check_interval_minutes,
            ),
            ("dynamic_check_interval_minutes", "动态检查间隔/分钟", config.dynamic_check_interval_minutes),
            ("dynamic_high_frequency_interval_minutes", "高频动态间隔/分钟", config.dynamic_high_frequency_interval_minutes),
            ("dynamic_high_frequency_max_minutes", "高频窗口时长/分钟", config.dynamic_high_frequency_max_minutes),
            (
                "live_status_open_check_interval_seconds",
                "开播检测间隔/秒",
                config.live_status_open_check_interval_seconds,
            ),
            (
                "live_status_close_check_interval_minutes",
                "开播后关播检查/分钟",
                config.live_status_close_check_interval_minutes,
            ),
            ("dynamic_fetch_limit", "每人动态读取条数", config.dynamic_fetch_limit),
            (
                "suppress_live_start_dynamic_window_minutes",
                "开播同刻动态过滤/分钟",
                config.suppress_live_start_dynamic_window_minutes,
            ),
            ("request_timeout_seconds", "请求超时/秒", config.request_timeout_seconds),
            ("live_sound_loop_seconds", "直播提示音循环/秒（0=只响一次）", config.live_sound_loop_seconds),
            ("live_remind_before_minutes", "直播提前提醒/分钟", ", ".join(str(v) for v in config.live_remind_before_minutes)),
        ):
            edit = QtWidgets.QLineEdit(str(value))
            self.config_widgets[key] = edit
            form.addRow(label, edit)

        sound_edit = QtWidgets.QLineEdit(config.live_sound_file)
        self.config_widgets["live_sound_file"] = sound_edit
        form.addRow("直播提示音文件", sound_edit)

        sound_buttons = QtWidgets.QHBoxLayout()
        sound_buttons.setContentsMargins(0, 0, 0, 0)
        choose_sound = QtWidgets.QPushButton("选择")
        choose_sound.setFixedWidth(88)
        choose_sound.clicked.connect(self.choose_live_sound_file)
        sound_buttons.addWidget(choose_sound)
        preview_sound = QtWidgets.QPushButton("试听")
        preview_sound.setFixedWidth(88)
        preview_sound.clicked.connect(self.preview_live_sound)
        sound_buttons.addWidget(preview_sound)
        stop_sound = QtWidgets.QPushButton("停止")
        stop_sound.setFixedWidth(88)
        stop_sound.clicked.connect(self.stop_preview_sound)
        sound_buttons.addWidget(stop_sound)
        sound_buttons.addStretch(1)
        form.addRow("", sound_buttons)
        sound_hint = self.dashboard._muted_label(
            "留空使用内置铃声 C:\\Windows\\Media\\Ring01.wav，仅支持 .wav；正常桌面走系统通知音效，检测到全屏应用时自动改用程序铃声。"
        )
        sound_hint.setWordWrap(True)
        form.addRow("", sound_hint)

        cookie_row = QtWidgets.QHBoxLayout()
        cookie_row.setContentsMargins(0, 0, 0, 0)
        cookie_row.setSpacing(10)
        self.cookie_status_label = self.dashboard._muted_label("")
        cookie_row.addWidget(self.cookie_status_label)
        login_button = QtWidgets.QPushButton("扫码登录")
        login_button.setFixedWidth(104)
        login_button.clicked.connect(self.dashboard.open_qr_login)
        cookie_row.addWidget(login_button)
        cookie_row.addStretch(1)
        form.addRow("B 站 Cookie", cookie_row)
        self._update_cookie_status_label()

        update_row = QtWidgets.QHBoxLayout()
        update_row.setContentsMargins(0, 0, 0, 0)
        update_row.setSpacing(10)
        self.update_status_label = self.dashboard._muted_label("")
        update_row.addWidget(self.update_status_label)
        self.check_update_button = QtWidgets.QPushButton("检查更新")
        self.check_update_button.setFixedWidth(104)
        self.check_update_button.clicked.connect(self._check_updates)
        update_row.addWidget(self.check_update_button)
        update_row.addStretch(1)
        form.addRow("程序版本", update_row)
        self._update_update_status_label()
        button = QtWidgets.QPushButton("保存全局配置")
        button.setObjectName("primaryButton")
        button.clicked.connect(self.save_global_settings)
        form.addRow("", button)
        return card

    def _update_cookie_status_label(self) -> None:
        label = getattr(self, "cookie_status_label", None)
        if label is None:
            return
        snapshot = self.dashboard.service.state.snapshot()
        if snapshot.get("bilibili_cookie_expired"):
            text = "已失效，需要重新扫码"
        elif self.dashboard.service.config.bilibili_cookie.strip():
            text = "已配置"
        else:
            text = "未配置"
        label.setText(text)

    def _check_updates(self) -> None:
        self.dashboard.check_for_updates(manual=True)
        self._update_update_status_label()

    def _update_update_status_label(self) -> None:
        label = getattr(self, "update_status_label", None)
        if label is None:
            return
        if self.dashboard.update_busy:
            label.setText("正在检查...")
        else:
            label.setText(f"当前 v{app_version()}")

    def _member_settings_card(self, member: MemberConfig) -> Any:
        card = SoftCard(surface=SUBTLE, radius=20, padding=16)
        layout = QtWidgets.QGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        card.layout().addLayout(layout)
        layout.addWidget(self.dashboard._avatar(member, 34), 0, 0, 2, 1)
        optional = " · 可选" if member.id in OPTIONAL_MEMBER_IDS else ""
        title = QtWidgets.QLabel(f"{member.name}{optional}")
        title.setStyleSheet("font-size:17px; font-weight:700;")
        layout.addWidget(title, 0, 1)
        layout.addWidget(
            self.dashboard._muted_label(self.dashboard._member_display_id(member.id)),
            0,
            2,
            QtCore.Qt.AlignmentFlag.AlignRight,
        )

        checks = QtWidgets.QHBoxLayout()
        for field, text, value in (
            ("enabled", "启用", member.enabled),
            ("monitor_dynamic", "动态监控", member.monitor_dynamic),
            ("monitor_live", "直播监控", member.monitor_live),
            ("live_sound_enabled", "直播通知音效", member.live_sound_enabled),
        ):
            checkbox = QtWidgets.QCheckBox(text)
            checkbox.setChecked(value)
            self.member_widgets[(member.id, field)] = checkbox
            checks.addWidget(checkbox)
        checks.addStretch(1)
        layout.addLayout(checks, 1, 1, 1, 2)

        for row, (field, label, value) in enumerate(
            (
                ("name", "显示名称", member.name),
                ("uid", "UID", member.uid),
                ("dynamic_url", "动态主页", member.dynamic_url),
                ("live_url", "直播间", member.live_url),
                ("avatar_path", "头像", member.avatar_path),
                ("live_sound_file", "音效文件", member.live_sound_file),
                ("priority", "优先级", str(member.priority)),
            ),
            start=2,
        ):
            edit = QtWidgets.QLineEdit(value)
            self.member_widgets[(member.id, field)] = edit
            layout.addWidget(QtWidgets.QLabel(label), row, 0)
            if field == "avatar_path":
                avatar_layout = QtWidgets.QHBoxLayout()
                avatar_layout.setContentsMargins(0, 0, 0, 0)
                avatar_layout.addWidget(edit, 1)
                browse = QtWidgets.QPushButton("选择")
                browse.setFixedWidth(88)
                browse.clicked.connect(lambda _=False, member_id=member.id: self.choose_avatar(member_id))
                avatar_layout.addWidget(browse)
                layout.addLayout(avatar_layout, row, 1, 1, 2)
            elif field == "live_sound_file":
                sound_layout = QtWidgets.QHBoxLayout()
                sound_layout.setContentsMargins(0, 0, 0, 0)
                sound_layout.addWidget(edit, 1)
                browse_sound = QtWidgets.QPushButton("选择")
                browse_sound.setFixedWidth(88)
                browse_sound.clicked.connect(
                    lambda _=False, member_id=member.id: self.choose_member_sound_file(member_id)
                )
                sound_layout.addWidget(browse_sound)
                layout.addLayout(sound_layout, row, 1, 1, 2)
            else:
                layout.addWidget(edit, row, 1, 1, 2)

        button = QtWidgets.QPushButton("保存成员配置")
        button.clicked.connect(lambda _=False, member_id=member.id: self.save_member_settings(member_id))
        layout.addWidget(button, 9, 1, 1, 2)
        layout.setColumnStretch(1, 1)
        return card

    def choose_member_sound_file(self, member_id: str) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择成员音效",
            "",
            "音频文件 (*.wav);;所有文件 (*.*)",
        )
        if path:
            self.member_widgets[(member_id, "live_sound_file")].setText(path)

    def choose_avatar(self, member_id: str) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择头像",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp *.ico);;所有文件 (*.*)",
        )
        if path:
            self.member_widgets[(member_id, "avatar_path")].setText(path)

    def choose_live_sound_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择直播提示音",
            "",
            "音频文件 (*.wav);;所有文件 (*.*)",
        )
        if path:
            self.config_widgets["live_sound_file"].setText(path)

    def preview_live_sound(self) -> None:
        value = self.config_widgets["live_sound_file"].text().strip()
        if value and not resolve_app_path(value).exists():
            QtWidgets.QMessageBox.warning(
                self, "试听失败", f"文件不存在：{resolve_app_path(value)}"
            )
            return
        loop_seconds = self._live_sound_loop_value()
        if not play_live_sound(value, loop_seconds):
            QtWidgets.QMessageBox.warning(self, "试听失败", "提示音播放失败，请查看运行日志。")

    def stop_preview_sound(self) -> None:
        stop_live_sound()

    def _live_sound_loop_value(self) -> int:
        try:
            return max(0, min(60, int(self.config_widgets["live_sound_loop_seconds"].text().strip())))
        except (KeyError, TypeError, ValueError):
            return 0

    def save_global_settings(self) -> None:
        bool_keys = ("notifications_enabled", "auto_start", "write_snapshots", "asoul_bypass_proxy")
        string_keys = ("asoul_calendar_url", "asoul_home_url")
        int_keys = (
            "check_interval_minutes",
            "dynamic_check_interval_minutes",
            "dynamic_high_frequency_interval_minutes",
            "dynamic_high_frequency_max_minutes",
            "live_status_open_check_interval_seconds",
            "live_status_close_check_interval_minutes",
            "dynamic_fetch_limit",
            "suppress_live_start_dynamic_window_minutes",
            "request_timeout_seconds",
            "live_sound_loop_seconds",
        )
        try:
            updates: dict[str, Any] = {}
            for key in bool_keys:
                updates[key] = self.config_widgets[key].isChecked()
            for key in string_keys:
                value = self.config_widgets[key].text().strip()
                if not value:
                    raise ValueError(f"{key} 不能为空")
                updates[key] = value
            for key in int_keys:
                value = int(self.config_widgets[key].text().strip())
                allow_zero = {
                    "suppress_live_start_dynamic_window_minutes",
                    "live_sound_loop_seconds",
                }
                if value < 0 or key not in allow_zero and value == 0:
                    raise ValueError(f"{key} 必须是正整数，支持关闭的项目可填 0")
                if key == "check_interval_minutes":
                    value = max(ASOUL_MIN_CHECK_INTERVAL_MINUTES, value)
                if key == "live_sound_loop_seconds":
                    value = max(0, min(60, value))
                updates[key] = value
            reminders_raw = self.config_widgets["live_remind_before_minutes"].text().replace("，", ",")
            reminders = [int(item.strip()) for item in reminders_raw.split(",") if item.strip()]
            if not reminders or any(value <= 0 for value in reminders):
                raise ValueError("直播提前提醒必须是逗号分隔的正整数")
            updates["live_remind_before_minutes"] = reminders
            updates["live_sound_file"] = self.config_widgets["live_sound_file"].text().strip()
        except (KeyError, TypeError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self, "保存失败", str(exc))
            return

        original_config: dict[str, Any] | None = None
        original_startup: str | None = None
        captured_original = False
        try:
            _, original_config = read_config_data()
            original_startup = registered_startup_command()
            captured_original = True
            update_config_values(updates)
            if updates["auto_start"]:
                enable_startup()
            else:
                disable_startup()
        except Exception as exc:
            rollback_errors: list[str] = []
            if captured_original and original_config is not None:
                try:
                    write_config_data(original_config)
                except Exception as rollback_exc:
                    rollback_errors.append(f"配置恢复失败：{rollback_exc}")
                try:
                    set_registered_startup_command(original_startup)
                except Exception as rollback_exc:
                    rollback_errors.append(f"启动项恢复失败：{rollback_exc}")
            detail = str(exc)
            if rollback_errors:
                detail += "\n" + "\n".join(rollback_errors)
            QtWidgets.QMessageBox.warning(self, "保存失败", detail)
            return
        self.dashboard._reload_member_config()
        self.dashboard.render_keys.clear()
        self.dashboard.refresh(force=True)
        QtWidgets.QMessageBox.information(self, "已保存", "全局配置已保存。")

    def save_member_settings(self, member_id: str) -> None:
        try:
            name = self.member_widgets[(member_id, "name")].text().strip()
            if not name:
                raise ValueError("显示名称不能为空")
            values = {
                "name": name,
                "enabled": self.member_widgets[(member_id, "enabled")].isChecked(),
                "monitor_dynamic": self.member_widgets[(member_id, "monitor_dynamic")].isChecked(),
                "monitor_live": self.member_widgets[(member_id, "monitor_live")].isChecked(),
                "uid": self.member_widgets[(member_id, "uid")].text().strip(),
                "dynamic_url": self.member_widgets[(member_id, "dynamic_url")].text().strip(),
                "live_url": self.member_widgets[(member_id, "live_url")].text().strip(),
                "avatar_path": self.member_widgets[(member_id, "avatar_path")].text().strip(),
                "live_sound_enabled": self.member_widgets[(member_id, "live_sound_enabled")].isChecked(),
                "live_sound_file": self.member_widgets[(member_id, "live_sound_file")].text().strip(),
                "priority": int(self.member_widgets[(member_id, "priority")].text().strip() or "0"),
            }
            update_member_config(member_id, values)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.dashboard._reload_member_config()
        self.dashboard.render_keys.clear()
        self.dashboard.refresh(force=True)
        QtWidgets.QMessageBox.information(self, "已保存", "成员配置已保存。")
