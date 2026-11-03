from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Any


ERROR_ALREADY_EXISTS = 183
MESSAGE_ICON_INFORMATION = 0x40
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
COMMANDS = ("show_window", "check_now")


def _kernel32() -> Any:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


class SingleInstance:
    def __init__(self, name: str = "AsoulReminder") -> None:
        self.name = name
        self.handle: Any | None = None
        self.command_handles: dict[str, Any] = {}

    def _command_event_name(self, command: str) -> str:
        return f"Local\\{self.name}.{command}"

    def acquire(self) -> bool:
        if os.name != "nt":
            return True

        kernel32 = _kernel32()
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
        if not handle:
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False

        self.handle = handle
        for command in COMMANDS:
            event_handle = kernel32.CreateEventW(
                None,
                False,
                False,
                self._command_event_name(command),
            )
            if event_handle:
                self.command_handles[command] = event_handle
        return True

    def signal_existing(self, command: str) -> bool:
        if os.name != "nt" or command not in COMMANDS:
            return False
        kernel32 = _kernel32()
        handle = kernel32.OpenEventW(
            EVENT_MODIFY_STATE | SYNCHRONIZE,
            False,
            self._command_event_name(command),
        )
        if not handle:
            return False
        try:
            return bool(kernel32.SetEvent(handle))
        finally:
            kernel32.CloseHandle(handle)

    def poll_commands(self) -> list[str]:
        if os.name != "nt":
            return []
        kernel32 = _kernel32()
        pending: list[str] = []
        for command, handle in self.command_handles.items():
            if kernel32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0:
                pending.append(command)
        return pending

    def release(self) -> None:
        if os.name != "nt":
            return
        kernel32 = _kernel32()
        for handle in self.command_handles.values():
            kernel32.CloseHandle(handle)
        self.command_handles.clear()
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


def show_existing_instance_message() -> None:
    if os.name != "nt":
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW(
        None,
        "Asoul Reminder 已经在运行。\n未能唤醒现有窗口，请从系统托盘打开。",
        "Asoul Reminder",
        MESSAGE_ICON_INFORMATION,
    )