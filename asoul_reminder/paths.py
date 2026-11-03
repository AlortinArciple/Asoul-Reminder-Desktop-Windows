from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AsoulReminder"
SOURCE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else SOURCE_DIR
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        path = Path(base) / APP_NAME
    else:
        path = ROOT_DIR / ".data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_config_path() -> Path:
    return ROOT_DIR / "config.json"


def example_config_path() -> Path:
    bundled = RESOURCE_DIR / "config.example.json"
    if bundled.exists():
        return bundled
    return ROOT_DIR / "config.example.json"


def state_path() -> Path:
    return app_data_dir() / "state.json"


def log_path() -> Path:
    return app_data_dir() / "app.log"


def asoul_snapshot_path() -> Path:
    return app_data_dir() / "asoul_events.json"


def dynamic_snapshot_path() -> Path:
    return app_data_dir() / "dynamic_items.json"


def version_info_path() -> Path:
    bundled = RESOURCE_DIR / "version.json"
    if bundled.exists():
        return bundled
    generated = SOURCE_DIR / "build" / "version.json"
    if generated.exists():
        return generated
    return ROOT_DIR / "version.json"


def emoji_cache_dir() -> Path:
    path = app_data_dir() / "emoji_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _asset_path(name: str) -> Path:
    bundled = RESOURCE_DIR / "assets" / name
    if bundled.exists():
        return bundled
    return ROOT_DIR / "assets" / name


def resolve_app_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    root_path = ROOT_DIR / path
    if root_path.exists():
        return root_path
    return RESOURCE_DIR / path


def tray_icon_path() -> Path:
    return _asset_path("asoul_tray_icon.ico")


def app_icon_path() -> Path:
    return tray_icon_path()


def icon_path() -> Path:
    return tray_icon_path()
