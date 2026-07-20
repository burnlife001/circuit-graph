"""Windows dialog auto-dismisser for Ngspice.

Ngspice on Windows can display GUI error dialogs even in batch mode (-b).
This module provides a context manager that runs a background thread to detect
and automatically dismiss these dialogs during simulation.

Uses EnumWindows to find ALL matching dialogs (not just the first one),
so multiple concurrent popups are all dismissed.

Only active on Windows; a no-op stub is used on other platforms.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import threading
import time

# Ngspice dialog window titles that should be auto-dismissed
_DIALOG_TITLES = (
    "Ngspice Info",
    "Ngspice",
    "Fatal error",
    "SPICE Error",
    "Error",
)

# Dialog button labels to auto-click (case-insensitive)
_BUTTON_LABELS = ("ok", "确定", "&ok", "&yes", "yes")

WM_CLOSE = 0x0010
BM_CLICK = 0x00F5


def _dismiss_all_dialogs(user32, dismissed: set[int]) -> None:
    """Enumerate all top-level windows and dismiss any ngspice dialogs.

    Uses EnumWindows to find ALL matching windows (not just the first one),
    so multiple concurrent dialogs are handled in a single pass.
    """
    title_set = frozenset(_DIALOG_TITLES)
    found_hwnds: list[int] = []

    # Pre-fetch IsWindowVisible as a local
    is_visible = user32.IsWindowVisible
    send_msg = user32.SendMessageW

    def _enum_callback(hwnd: int, _lparam: int) -> bool:
        if hwnd in dismissed:
            return True  # already handled, continue enumeration
        if not is_visible(hwnd):
            return True  # not visible, skip
        # Get window title
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        if title and title in title_set:
            found_hwnds.append(hwnd)
        return True  # Continue enumeration

    ENUM_PROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )
    user32.EnumWindows(ENUM_PROC(_enum_callback), 0)

    for hwnd in found_hwnds:
        if hwnd in dismissed:
            continue
        # Strategy 1: WM_CLOSE
        send_msg(hwnd, WM_CLOSE, 0, 0)
        time.sleep(0.05)
        if not is_visible(hwnd):
            dismissed.add(hwnd)
            continue
        # Strategy 2: click OK/确定 button
        _click_button(user32, hwnd)
        time.sleep(0.03)
        dismissed.add(hwnd)


def _click_button(user32, hwnd: int) -> None:
    """Find and click a dismiss button (OK/确定) in a dialog."""
    found_buttons: list[int] = []

    def _enum_callback(child_hwnd: int, _lparam: int) -> bool:
        buf = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(child_hwnd, buf, 64)
        text = buf.value.lower() if buf.value else ""
        if text in _BUTTON_LABELS:
            found_buttons.append(child_hwnd)
            return False  # Stop — found the button
        return True  # Continue enumeration

    ENUM_CHILD_PROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )
    user32.EnumChildWindows(hwnd, ENUM_CHILD_PROC(_enum_callback), 0)

    for btn_hwnd in found_buttons:
        user32.SendMessageW(btn_hwnd, BM_CLICK, 0, 0)


def _dismiss_loop(stop_event: threading.Event, interval: float = 0.3) -> None:
    """Background thread: periodically find and dismiss ngspice dialogs.

    Uses EnumWindows to catch ALL matching dialogs in each pass,
    handling multiple concurrent popups correctly.
    """
    user32 = ctypes.windll.user32

    # Define Win32 API function signatures
    user32.EnumWindows.restype = ctypes.wintypes.BOOL
    user32.FindWindowW.restype = ctypes.wintypes.HWND
    user32.FindWindowW.argtypes = (
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.LPCWSTR,
    )
    user32.IsWindowVisible.restype = ctypes.wintypes.BOOL
    user32.IsWindowVisible.argtypes = (ctypes.wintypes.HWND,)
    user32.SendMessageW.restype = ctypes.c_longlong
    user32.SendMessageW.argtypes = (
        ctypes.wintypes.HWND,
        ctypes.wintypes.UINT,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    )
    user32.EnumChildWindows.restype = ctypes.wintypes.BOOL
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPWSTR,
        ctypes.c_int,
    )

    dismissed: set[int] = set()

    while not stop_event.is_set():
        _dismiss_all_dialogs(user32, dismissed)
        time.sleep(interval)


class NgspiceDialogSuppressor:
    """Context manager that auto-dismisses ngspice GUI dialogs on Windows.

    Usage::

        with NgspiceDialogSuppressor():
            subprocess.run(["ngspice", "-b", "deck.cir"], ...)
    """

    def __init__(self, check_interval: float = 0.3) -> None:
        self._interval = check_interval
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "NgspiceDialogSuppressor":
        if sys.platform != "win32":
            return self
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=_dismiss_loop,
            args=(self._stop_event, self._interval),
            daemon=True,
            name="ngspice-dialog-dismisser",
        )
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
