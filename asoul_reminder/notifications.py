from __future__ import annotations

import hashlib
import logging
import os
import threading
import webbrowser
from pathlib import Path

from .paths import app_data_dir, resolve_app_path, tray_icon_path

LOGGER = logging.getLogger(__name__)

SYSTEM_NOTIFICATION_ALIAS = "Notification.Default"
DEFAULT_RING_FILE = r"C:\Windows\Media\Ring01.wav"
QUNS_ACCEPTS_NOTIFICATIONS = 5

_sound_stop_timer: threading.Timer | None = None


def toast_tag(key: str) -> str:
    """Windows Toast Tag 上限 16 字符；用 md5 截断保证唯一、且只含十六进制字符。"""
    return hashlib.md5(str(key).encode("utf-8")).hexdigest()[:16]


def fullscreen_notification_suppressed() -> bool:
    """检测 Shell 是否处于会抑制 toast 通知的状态（全屏游戏、全屏 UWP、演示模式等）。

    使用系统自己的判定接口 SHQueryUserNotificationState；查询失败时按
    "非全屏"处理，回退到常规 toast 音效通道。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        state = ctypes.c_int(0)
        result = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
        if result != 0:
            LOGGER.warning("SHQueryUserNotificationState 返回 %s，按非全屏处理", result)
            return False
        suppressed = state.value != QUNS_ACCEPTS_NOTIFICATIONS
        if suppressed:
            LOGGER.info("检测到通知被抑制的状态（全屏应用等）：QUNS=%d", state.value)
        return suppressed
    except Exception:
        LOGGER.exception("查询通知状态失败，按非全屏处理")
        return False


def stop_live_sound() -> None:
    """停止当前提示音并取消未触发的循环停止计时器。"""
    global _sound_stop_timer
    if _sound_stop_timer is not None:
        _sound_stop_timer.cancel()
        _sound_stop_timer = None
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_ASYNC)
    except Exception:
        LOGGER.exception("停止直播提示音失败")


def play_live_sound(sound_file: str = "", loop_seconds: int = 0) -> bool:
    """播放直播提示音：配置的 .wav 优先，其次内置系统铃声，最后系统通知默认音。

    winsound 是系统级播放，不受勿扰模式/全屏规则影响；SND_ASYNC 立即返回，
    不阻塞服务线程。loop_seconds > 0 时循环播放指定秒数后自动停止。
    播放失败只记日志，不影响通知本身的送达判定。
    """
    global _sound_stop_timer
    try:
        import winsound
    except ImportError:
        LOGGER.warning("当前平台不支持 winsound，跳过直播提示音")
        return False
    if _sound_stop_timer is not None:
        _sound_stop_timer.cancel()
        _sound_stop_timer = None

    flags = winsound.SND_ASYNC
    source: str | None = None
    configured = sound_file.strip()
    if configured:
        path = resolve_app_path(configured)
        if path.exists():
            source = str(path)
            flags |= winsound.SND_FILENAME
        else:
            LOGGER.warning("直播提示音文件不存在，回退内置铃声：%s", path)
    if source is None and Path(DEFAULT_RING_FILE).exists():
        source = DEFAULT_RING_FILE
        flags |= winsound.SND_FILENAME
    if source is None:
        source = SYSTEM_NOTIFICATION_ALIAS
        flags |= winsound.SND_ALIAS

    loop = max(0, min(60, int(loop_seconds or 0)))
    if loop:
        flags |= winsound.SND_LOOP
    try:
        winsound.PlaySound(source, flags)
    except Exception:
        LOGGER.exception("直播提示音播放失败")
        return False
    if loop:
        _sound_stop_timer = threading.Timer(loop, stop_live_sound)
        _sound_stop_timer.daemon = True
        _sound_stop_timer.start()
        LOGGER.info("直播提示音循环播放 %d 秒：%s", loop, source)
    else:
        LOGGER.info("直播提示音已播放：%s", source)
    return True


def ensure_icon() -> Path:
    configured = tray_icon_path()
    if configured.exists():
        return configured
    fallback = app_data_dir() / "asoul.ico"
    if fallback.exists():
        return fallback
    try:
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGBA", (256, 256), "#E799B0")
        draw = ImageDraw.Draw(image)
        draw.ellipse((28, 28, 228, 228), fill="#576690")
        draw.ellipse((58, 58, 198, 198), fill="#DB7D74")
        font = ImageFont.truetype("arial.ttf", 72)
        text = "AS"
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (256 - (bbox[2] - bbox[0])) / 2
        y = (256 - (bbox[3] - bbox[1])) / 2 - 4
        draw.text((x, y), text, fill="white", font=font)
        image.save(fallback, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    except Exception:
        LOGGER.exception("Failed to generate fallback icon")
    return fallback


class Notifier:
    def __init__(
        self,
        enabled: bool = True,
        live_sound_file: str = "",
        live_sound_loop_seconds: int = 0,
    ) -> None:
        self.enabled = enabled
        self.live_sound_file = live_sound_file
        self.live_sound_loop_seconds = live_sound_loop_seconds
        self.icon = str(ensure_icon())

    def notify(
        self,
        title: str,
        message: str,
        url: str | None = None,
        *,
        sound: bool = False,
        sound_file: str = "",
        tag: str = "",
    ) -> bool:
        log_message = " ".join(str(message).splitlines())
        LOGGER.info(
            "Windows 通知请求：title=%r message=%r target=%r sound=%s",
            title,
            log_message,
            url or "",
            "on" if sound else "off",
        )
        if not self.enabled:
            LOGGER.info("Windows 通知跳过：通知功能已关闭 title=%r", title)
            return False
        try:
            from winotify import Notification

            toast = Notification(
                app_id="Asoul Reminder",
                title=title,
                msg=message,
                icon=self.icon if os.path.exists(self.icon) else "",
                duration="short",
                launch=url or "",
            )
            # winotify 默认 tag=标题，导致同标题通知互相替换；这里覆盖为调用方给的唯一 key。
            # winotify 1.1.0 没有公开的 tag 参数，但其 show() 用 self.__dict__ 渲染模板，覆盖即生效。
            toast.tag = toast_tag(tag or f"{title}|{message}")
            # 音效通道：正常桌面状态走 toast 自带音效；检测到全屏应用等会
            # 抑制 toast 声音的状态时，横幅保持静音，改由 winsound 独立播放。
            use_winsound = False
            if sound:
                if fullscreen_notification_suppressed():
                    use_winsound = True
                else:
                    from winotify import audio

                    toast.set_audio(audio.Default, False)
            if url:
                toast.add_actions(label="打开", launch=url)
            toast.show()
            LOGGER.info("Windows 通知已提交：title=%r", title)
            if use_winsound:
                # 成员独立音效文件优先，留空回退全局设置/内置铃声。
                play_live_sound(
                    sound_file.strip() or self.live_sound_file,
                    self.live_sound_loop_seconds,
                )
            return True
        except Exception:
            LOGGER.exception("Windows 通知失败：title=%r", title)
            if url:
                LOGGER.info("Windows 通知目标：%s", url)
            return False

    @staticmethod
    def open_url(url: str) -> None:
        webbrowser.open(url)
