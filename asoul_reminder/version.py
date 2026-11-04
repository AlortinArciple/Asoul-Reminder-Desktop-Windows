from __future__ import annotations

import json
from typing import Any

from .paths import version_info_path


APP_VERSION = "1.2.2"


def app_version() -> str:
    """当前程序版本号，用于和 GitHub Release 标签比较。"""
    return APP_VERSION


def load_version_info() -> dict[str, Any]:
    path = version_info_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def version_text() -> str:
    info = load_version_info()
    if not info:
        return "开发版（未打包）"
    commit = str(info.get("commit") or "unknown")
    dirty = "，含未提交修改" if info.get("dirty") else ""
    built_at = str(info.get("built_at") or "")
    suffix = f"，构建于 {built_at}" if built_at else ""
    return f"{commit}{dirty}{suffix}"


def full_version_text() -> str:
    """`v1.2.0（8fa3e0a，构建于 …）` 形式的完整版本说明。"""
    return f"v{app_version()}（{version_text()}）"
