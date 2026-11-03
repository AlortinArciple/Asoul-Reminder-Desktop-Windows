"""B 站扫码登录与 Cookie 失效检测。

只处理协议与纯数据转换，不依赖 Qt；桌面窗口负责画二维码和确认写入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, urlsplit

from .http import HttpClient

LOGGER = logging.getLogger(__name__)

QRCODE_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QRCODE_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
FINGER_SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
LOGIN_REFERER = "https://www.bilibili.com/"

# poll 接口把状态放在 data.code，外层 code 只表示接口本身是否成功。
QR_STATE_WAITING = "waiting"
QR_STATE_SCANNED = "scanned"
QR_STATE_EXPIRED = "expired"
QR_STATE_OK = "ok"
QR_STATE_UNKNOWN = "unknown"
QR_STATE_NAMES = {
    0: QR_STATE_OK,
    86101: QR_STATE_WAITING,
    86090: QR_STATE_SCANNED,
    86038: QR_STATE_EXPIRED,
}
QR_STATE_MESSAGES = {
    QR_STATE_WAITING: "请使用哔哩哔哩手机客户端扫码",
    QR_STATE_SCANNED: "已扫码，请在手机上确认登录",
    QR_STATE_EXPIRED: "二维码已过期，请刷新后重新扫码",
}

# 只有这些键属于登录凭证；其余键（buvid3、b_nut 等）沿用旧 Cookie 以免指纹突变。
LOGIN_COOKIE_KEYS = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")

STATUS_VALID = "valid"
STATUS_EXPIRED = "expired"
STATUS_UNKNOWN = "unknown"

COOKIE_MISSING_MESSAGE = "未配置 Bilibili Cookie，已跳过动态检查。填入 config.json 后会从最新动态开始记录。"
COOKIE_EXPIRED_MESSAGE = "B 站 Cookie 已失效，已跳过动态检查，请重新扫码登录。"


class LoginUnavailableError(RuntimeError):
    """登录依赖缺失或接口返回无法解析。"""


@dataclass(frozen=True)
class QrLoginSession:
    key: str
    url: str


@dataclass(frozen=True)
class QrPollResult:
    state: str
    message: str = ""
    redirect_url: str = ""
    cookies: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CookieStatus:
    state: str
    message: str = ""
    uname: str = ""
    mid: str = ""


def normalize_cookie(raw: str) -> str:
    """去掉 "Cookie:" 前缀和换行，统一成 a=1; b=2 的形式。"""
    text = (raw or "").strip()
    if text[:7].lower() == "cookie:":
        text = text[7:].strip()
    segments = [segment.strip() for segment in text.replace("\n", ";").split(";")]
    return "; ".join(segment for segment in segments if segment)


def cookie_dict(cookie: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for segment in normalize_cookie(cookie).split(";"):
        key, separator, value = segment.partition("=")
        key = key.strip()
        if not separator or not key or key in pairs:
            continue
        pairs[key] = value.strip()
    return pairs


def has_login_cookie(cookie: str) -> bool:
    return bool(cookie_dict(cookie).get("SESSDATA", ""))


def merge_cookie(existing: str, updates: dict[str, str]) -> str:
    """以旧 Cookie 顺序为基准覆盖/追加，避免整串重排导致指纹变化。"""
    merged = cookie_dict(existing)
    for key, value in (updates or {}).items():
        key = str(key or "").strip()
        value = str(value or "").strip()
        if key and value:
            merged[key] = value
    return "; ".join(f"{key}={value}" for key, value in merged.items())


def cookie_pairs_from_url(url: str) -> dict[str, str]:
    query = urlsplit(url or "").query
    return {key: value for key, value in parse_qsl(query, keep_blank_values=False) if value}


def login_cookies_from_url(url: str) -> dict[str, str]:
    pairs = cookie_pairs_from_url(url)
    return {key: pairs[key] for key in LOGIN_COOKIE_KEYS if pairs.get(key)}


def qr_matrix(data: str) -> list[list[bool]]:
    """返回二维码点阵，交给 Qt 直接绘制，避免额外依赖 Pillow。"""
    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover - 取决于本地环境。
        raise LoginUnavailableError("缺少 qrcode 依赖，无法生成二维码：pip install qrcode") from exc
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2, box_size=8)
    code.add_data(data)
    code.make(fit=True)
    return code.get_matrix()


class BilibiliLogin:
    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def create_qr(self) -> QrLoginSession:
        payload = self.http.get_json(QRCODE_GENERATE_URL, headers={"Referer": LOGIN_REFERER})
        data = payload.get("data") or {}
        key = str(data.get("qrcode_key") or "").strip()
        url = str(data.get("url") or "").strip()
        if payload.get("code") != 0 or not key or not url:
            raise LoginUnavailableError(
                f"二维码接口返回异常：code={payload.get('code')} message={payload.get('message')}"
            )
        return QrLoginSession(key=key, url=url)

    def poll(self, session: QrLoginSession) -> QrPollResult:
        url = (
            f"{QRCODE_POLL_URL}?qrcode_key={quote(session.key)}"
            "&source=main-fe-header"
        )
        payload, response = self.http.get_json_with_response(url, headers={"Referer": LOGIN_REFERER})
        if payload.get("code") not in (0, None):
            raise LoginUnavailableError(
                f"轮询登录状态失败：code={payload.get('code')} message={payload.get('message')}"
            )
        data = payload.get("data") or {}
        raw_code = data.get("code")
        state = QR_STATE_NAMES.get(raw_code if isinstance(raw_code, int) else None, QR_STATE_UNKNOWN)
        redirect_url = str(data.get("url") or "")
        cookies: dict[str, str] = {}
        for key, value in (response.cookies or {}).items():
            if key in LOGIN_COOKIE_KEYS and value:
                cookies[key] = value
        if state == QR_STATE_OK and not cookies and not login_cookies_from_url(redirect_url):
            raise LoginUnavailableError("登录成功但没有拿到 Cookie，请重试")
        return QrPollResult(
            state=state,
            message=QR_STATE_MESSAGES.get(state, ""),
            redirect_url=redirect_url,
            cookies=cookies,
        )

    def fetch_buvid(self) -> dict[str, str]:
        payload = self.http.get_json(FINGER_SPI_URL, headers={"Referer": LOGIN_REFERER})
        data = payload.get("data") or {}
        result: dict[str, str] = {}
        if data.get("b_3"):
            result["buvid3"] = str(data["b_3"])
        if data.get("b_4"):
            result["buvid4"] = str(data["b_4"])
        return result

    def build_cookie(self, poll: QrPollResult, existing: str = "") -> str:
        """合并新凭证与旧 Cookie，缺少 buvid 时补一次指数字段。"""
        updates = login_cookies_from_url(poll.redirect_url)
        updates.update(poll.cookies)
        cookie = merge_cookie(existing, updates)
        if not cookie_dict(cookie).get("buvid3"):
            try:
                cookie = merge_cookie(cookie, self.fetch_buvid())
            except Exception as exc:
                LOGGER.warning("获取 buvid 失败，登录 Cookie 可能不稳定：%s", exc)
        return cookie

    def verify_cookie(self, cookie: str) -> CookieStatus:
        """nav 校验：区分有效、失效和网络异常，网络异常不能当成失效。"""
        if not has_login_cookie(cookie):
            return CookieStatus(STATUS_EXPIRED, COOKIE_MISSING_MESSAGE)
        try:
            payload = self.http.get_json(
                NAV_URL,
                headers={"Cookie": normalize_cookie(cookie), "Referer": LOGIN_REFERER},
            )
        except Exception as exc:
            LOGGER.warning("B 站登录状态校验请求失败：%s", exc)
            return CookieStatus(STATUS_UNKNOWN, f"登录校验请求失败：{exc}")
        code = payload.get("code")
        data = payload.get("data") or {}
        if code == 0 and data.get("isLogin"):
            return CookieStatus(
                STATUS_VALID,
                uname=str(data.get("uname") or ""),
                mid=str(data.get("mid") or ""),
            )
        if code == -101:
            return CookieStatus(STATUS_EXPIRED, COOKIE_EXPIRED_MESSAGE)
        return CookieStatus(STATUS_UNKNOWN, f"登录校验返回 code={code}")
