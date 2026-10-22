"""
atk-logic-capture session state — JSON file at APPDATA_ATK/skill_state.json.

Persists preflight_ok and is_ctrl_s_pressed across CLI invocations within a
single ATK-Logic GUI session. Reset implicitly when GUI restarts because the
stored gui_pid + gui_start (ISO8601) no longer match the running process.

Replaces the previous TCP server inside Qt5Network.dll proxy. State is now
plain JSON on disk — readable by both atk_cli.py and atk_preflight.py, no
network needed.

File location:
    C:/Users/yg/AppData/Roaming/ALIENTEK/ATK-LogicView/skill_state.json

Used by:
    atk_cli.py          — read/write preflight_ok + is_ctrl_s_pressed
    atk_preflight.py    — read/write preflight_ok
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from _config import APPDATA_ATK

STATE_PATH = APPDATA_ATK / "skill_state.json"


# ── GUI process detection ────────────────────────────────────────────────


def gui_running() -> int:
    """Return GUI pid if ATK-Logic.exe is running with MainWindowTitle set, else 0.

    Mirrors the project's "GUI must have a real window" check (the proxy-DLL
    DllMain ran before Qt created the window — TCP could connect without a
    window being ready to receive keys). We require MainWindowTitle != '' so
    key F1 lands on a window, not on a pre-paint placeholder.
    """
    if sys.platform != "win32":
        return 0
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Name 'ATK-Logic' -ErrorAction SilentlyContinue |"
             " Where-Object { $_.MainWindowTitle -ne '' }).Id"],
            capture_output=True, text=True, timeout=5,
        )
        out = r.stdout.strip()
        if out and out != "":
            try:
                return int(out.split()[0])
            except (ValueError, IndexError):
                return 0
    except Exception:
        pass
    return 0


def gui_start_iso(pid: int) -> str:
    """Return StartTime of ATK-Logic.exe (pid) as ISO8601 UTC. Empty string on failure."""
    if sys.platform != "win32" or pid <= 0:
        return ""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Id " + str(pid) + " -ErrorAction SilentlyContinue |"
             " Select-Object -First 1).StartTime.ToUniversalTime().ToString('o')"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def has_gui_window() -> bool:
    """True if ATK-Logic.exe is running AND its main window has a title."""
    return gui_running() > 0


# ── state JSON ──────────────────────────────────────────────────────────


_DEFAULTS = {
    "gui_pid": 0,
    "gui_start": "",
    "is_ctrl_s_pressed": False,
    "preflight_ok": False,
}


def _atomic_write(path: Path, obj: dict) -> None:
    """Write obj to path atomically (temp + rename)."""
    tmp = path.with_suffix(".json.tmp")
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def read_state(current_pid: int, current_start: str) -> dict:
    """Read state for the running GUI session.

    Matches stored gui_pid + gui_start against the supplied values. Mismatch
    (or missing file) → all flags default to False. Caller is expected to
    supply a freshly detected pid + start; missing values → flags False.

    Returns a dict with keys: gui_pid, gui_start, is_ctrl_s_pressed, preflight_ok.
    """
    if current_pid <= 0 or not current_start:
        return dict(_DEFAULTS)

    if not STATE_PATH.exists():
        return dict(_DEFAULTS)

    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)

    if not isinstance(raw, dict):
        return dict(_DEFAULTS)
    if raw.get("gui_pid") != current_pid or raw.get("gui_start") != current_start:
        return dict(_DEFAULTS)

    return {
        "gui_pid": current_pid,
        "gui_start": current_start,
        "is_ctrl_s_pressed": bool(raw.get("is_ctrl_s_pressed", False)),
        "preflight_ok": bool(raw.get("preflight_ok", False)),
    }


def write_state(current_pid: int, current_start: str, **changes) -> dict:
    """Update state file. Tag with current pid+start so future reads can
    match the GUI session. Returns the new state dict.
    """
    if current_pid <= 0 or not current_start:
        # No GUI — clear the file to avoid stale-session confusion next time.
        clear_state()
        return dict(_DEFAULTS)

    cur = read_state(current_pid, current_start)
    cur["gui_pid"] = current_pid
    cur["gui_start"] = current_start
    for k, v in changes.items():
        if k in _DEFAULTS:
            cur[k] = bool(v) if isinstance(_DEFAULTS[k], bool) else v
    _atomic_write(STATE_PATH, cur)
    return cur


def clear_state() -> None:
    """Remove state file."""
    try:
        if STATE_PATH.exists():
            STATE_PATH.unlink()
    except OSError:
        pass
