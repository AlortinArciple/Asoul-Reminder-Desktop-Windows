from __future__ import annotations

import os
import sys
from pathlib import Path

APP_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_VALUE_NAME = "AsoulReminder"


def startup_command() -> str:
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    runner = Path(__file__).resolve().parents[1] / "run.py"
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    if pythonw.exists():
        return f'"{pythonw}" "{runner}" --tray'
    return f'"{sys.executable}" "{runner}" --tray'


def is_windows() -> bool:
    return os.name == "nt"


def registered_startup_command() -> str | None:
    if not is_windows():
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None


def set_registered_startup_command(command: str | None) -> None:
    if not is_windows():
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if command is None:
            try:
                winreg.DeleteValue(key, APP_VALUE_NAME)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, APP_VALUE_NAME, 0, winreg.REG_SZ, command)


def enable_startup() -> None:
    set_registered_startup_command(startup_command())


def disable_startup() -> None:
    set_registered_startup_command(None)


def startup_enabled() -> bool:
    registered = registered_startup_command()
    if registered is None:
        return False
    return registered.strip().casefold() == startup_command().strip().casefold()