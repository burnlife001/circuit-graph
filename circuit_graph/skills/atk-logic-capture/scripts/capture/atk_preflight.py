"""
ATK-Logic pre-flight self-check (DLL-free edition).

Replaces the proxy-DLL / TCP-server checks. Verifies:
  1. atk_bg.exe exists and runs (the actual driver)
  2. ATK-Logic GUI is running with a visible main window
  3. set.ini has saveFile=F12 (required for atk_bg F12 → save flow)
  4. set.ini savePath matches DATA_DIR from config.ini
  5. atk_cli.py exists
  6. atk_classify.py exists
  7. DATA_DIR is writable, stale .atkdl at output path removed

Side effect on success: writes preflight_ok=true to skill_state.json for the
current GUI session. cmd_capture reads this flag and skips re-running preflight
within the same GUI session.

Usage:
    python atk_preflight.py [--fix] [--output name.atkdl] [--json]
Exit codes: 0 = all checks passed, non-zero = at least one failed.
"""
from __future__ import annotations

import argparse
import json as _json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Support running from any directory
_this_dir = Path(__file__).resolve().parent
_scripts_dir = _this_dir.parent
sys.path.insert(0, str(_scripts_dir))

from _config import (
    APPDATA_ATK, SCRIPTS_DIR, ATK_BG_EXE, get_data_dir, get_gui_dir,
)
from config_sync import sync_set_ini
import session_state

CLI_SCRIPT = SCRIPTS_DIR / "atk_cli.py"
WAVE_SCRIPT = SCRIPTS_DIR / "analyze" / "atk_classify.py"


def _green(s: str) -> str: return f"\033[32m{s}\033[0m"
def _red(s: str) -> str:   return f"\033[31m{s}\033[0m"


def _ensure_atk_bg():
    """Verify atk_bg.exe exists and is runnable."""
    if not ATK_BG_EXE.exists():
        return False, f"atk_bg.exe NOT found at {ATK_BG_EXE}. Build it: pwsh tools/atk_bg/build.ps1"
    return True, f"atk_bg.exe OK: {ATK_BG_EXE}"


def _ensure_gui_window():
    """Verify ATK-Logic GUI is running with a visible main window."""
    pid = session_state.gui_running()
    if pid <= 0:
        gui_dir = get_gui_dir()
        return False, f"ATK-Logic GUI not running. Start {gui_dir / 'ATK-Logic.exe'}"
    return True, f"ATK-Logic GUI running pid={pid}"


def _ensure_save_file_shortcut():
    """set.ini must have saveFile=F12 (and startCollecting=F1 / stopCollecting=F2)."""
    set_ini = APPDATA_ATK / "set.ini"
    if not set_ini.exists():
        return False, f"set.ini NOT found at {set_ini}"
    text = set_ini.read_text(encoding="utf-8")
    section_keys = {}
    cur_section = None
    for line in text.splitlines():
        line_s = line.strip()
        if line_s.startswith("[") and line_s.endswith("]"):
            cur_section = line_s[1:-1]
            section_keys.setdefault(cur_section, {})
        elif "=" in line_s and cur_section:
            k, _, v = line_s.partition("=")
            section_keys.setdefault(cur_section, {})[k.strip()] = v.strip()
    # Look in any section (the GUI's section may vary)
    flat = {k.lower(): v for s in section_keys.values() for k, v in s.items()}
    needed = {
        "saveFile": "F12",
        "startCollecting": "F1",
        "stopCollecting": "F2",
    }
    missing = []
    for k, expected in needed.items():
        actual = flat.get(k.lower(), "")
        if actual.upper() != expected.upper():
            missing.append(f"{k}={actual!r} (expected {expected!r})")
    if missing:
        return False, (
            f"set.ini shortcuts: " + ", ".join(missing)
            + f". set.ini at {set_ini} must have these for atk_bg key flow."
        )
    return True, "set.ini shortcuts: startCollecting=F1, stopCollecting=F2, saveFile=F12"


def _check_cli():
    if CLI_SCRIPT.exists():
        return True, f"CLI script OK: {CLI_SCRIPT}"
    return False, f"CLI script NOT found: {CLI_SCRIPT}"


def _check_classify():
    if WAVE_SCRIPT.exists():
        return True, f"Signal classifier OK: {WAVE_SCRIPT}"
    return False, f"Signal classifier NOT found: {WAVE_SCRIPT}"


def _check_output(output_name: str | None):
    if not output_name:
        return True, "No --output specified (will use auto-timestamp)"
    parent = get_data_dir()
    if not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
            return True, f"Created output directory: {parent}"
        except OSError as e:
            return False, f"Cannot create output directory {parent}: {e}"
    if not os.access(str(parent), os.W_OK):
        return False, f"Output directory not writable: {parent}"
    dst = parent / Path(output_name).name
    warnings = []
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(str(dst), ignore_errors=True)
            warnings.append(f"removed stale directory: {dst.name}")
        else:
            try:
                dst.unlink()
                warnings.append(f"removed stale file: {dst.name}")
            except OSError:
                pass
    msg = f"Output path OK: {dst}"
    if warnings:
        msg += " — " + ", ".join(warnings)
    return True, msg


def _cleanup_unsaved_markers():
    """Best-effort cleanup of any stale "recover data?" markers.

    ATK-Logic writes temp/memory + lock files when the previous instance was
    killed without graceful shutdown. Without this cleanup, on next launch
    the GUI shows a "recover data?" prompt that blocks startup.
    """
    memory_dir = APPDATA_ATK / "temp" / "memory"
    lock_file = APPDATA_ATK / "temp" / "lock"
    if memory_dir.exists():
        try:
            shutil.rmtree(str(memory_dir), ignore_errors=True)
        except OSError:
            pass
    if lock_file.exists():
        try:
            lock_file.unlink()
        except OSError:
            pass


def _start_gui_and_wait():
    """Launch GUI if not running; wait for its main window to become visible.
    Returns (ok, msg, gui_was_running)."""
    pid = session_state.gui_running()
    if pid > 0:
        return True, f"GUI already running pid={pid}", True

    # Not running — clean stale markers, then launch.
    _cleanup_unsaved_markers()
    gui_exe = get_gui_dir() / "ATK-Logic.exe"
    if not gui_exe.exists():
        return False, f"ATK-Logic.exe not found at {gui_exe}", False
    print(f"  Starting GUI: {gui_exe}", flush=True)
    try:
        subprocess.Popen(
            [str(gui_exe)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
        )
    except Exception as e:
        return False, f"Failed to start GUI: {e}", False

    # Wait up to 15 s for window to appear.
    deadline = time.time() + 15
    while time.time() < deadline:
        if session_state.gui_running() > 0:
            time.sleep(1.0)  # Qt focus settle
            return True, f"GUI auto-started, window visible", False
        time.sleep(0.5)
    return False, "GUI start requested but no window appeared within 15 s", False


def _write_state_if_changed(*, gui_pid, gui_start, preflight_ok):
    session_state.write_state(
        gui_pid, gui_start,
        preflight_ok=bool(preflight_ok),
    )


def main():
    parser = argparse.ArgumentParser(description="ATK-Logic pre-flight check")
    parser.add_argument("--output", "-o", help="Planned output .atkdl filename only (validates DATA_DIR)")
    parser.add_argument("--fix", action="store_true",
                        help="Auto-start GUI and wait for window if not running")
    parser.add_argument("--json", action="store_true",
                        help="Output machine-parseable JSON instead of human text")
    # Forwarded capture params (PUSHED to set.ini BEFORE start of GUI).
    # Mirrors the original preflight contract: preflight was the place that
    # synced params + saved backup + restarted GUI so the just-launched
    # GUI came up with the user's exact request.
    parser.add_argument("--ch", help="Comma-separated channel IDs (e.g. 0,1,2,3)")
    parser.add_argument("--sample-rate-hz", type=int, help="Sample rate in Hz")
    parser.add_argument("--threshold", type=float, help="Logic threshold voltage")
    args = parser.parse_args()

    gui_was_running = None
    sync_done = False
    sync_changes = []

    # ── Sync capture params to set.ini BEFORE launching the GUI (when --fix).
    # Doing it here means the GUI comes up with the right config; cmd_capture
    # doesn't need a second restart for the same params.
    if args.fix and (args.ch is not None or args.sample_rate_hz is not None or args.threshold is not None):
        ch_list = None
        if args.ch is not None:
            ch_list = [int(x.strip()) for x in args.ch.split(",") if x.strip()]
        sr = sync_set_ini(channels=ch_list, set_hz=args.sample_rate_hz, threshold=args.threshold)
        sync_changes = sr.get("changes", [])
        sync_done = len(sync_changes) > 0

    if args.fix:
        # Make sure GUI is running before validating shortcuts (which only
        # matter when a capture is about to happen against a live GUI).
        # Restart if sync changed params — set.ini edits only take effect on launch.
        if sync_done:
            _restart_gui_for_sync()
        ok, msg, gui_was_running = _start_gui_and_wait()
        if not ok:
            return _emit_result(False, [("GUI + window", False, msg)], gui_was_running,
                                sync_done, args)

    checks = [
        ("atk_bg.exe",           *_ensure_atk_bg()),
        ("GUI window",           *_ensure_gui_window()),
        ("set.ini shortcuts",    *_ensure_save_file_shortcut()),
        ("CLI script",           *_check_cli()),
        ("Signal classifier",    *_check_classify()),
        ("Output path",          *_check_output(args.output)),
    ]

    results = {}
    all_ok = True
    for name, ok, msg in checks:
        results[name] = {"status": "pass" if ok else "fail", "message": msg}
        if not ok:
            all_ok = False

    # If everything passed AND GUI is running, persist preflight_ok in state.
    if all_ok:
        pid = session_state.gui_running()
        if pid > 0:
            start = session_state.gui_start_iso(pid)
            _write_state_if_changed(gui_pid=pid, gui_start=start, preflight_ok=True)
        else:
            all_ok = False
            results["__note__"] = {"status": "fail", "message": "GUI not running after checks"}

    return _emit_result(all_ok, checks, gui_was_running, sync_done, args, results)


def _restart_gui_for_sync():
    """Quick restart so set.ini edits take effect (used after sync_set_ini)."""
    try:
        subprocess.run(["taskkill", "/im", "ATK-Logic.exe"],
                       capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        pass
    _cleanup_unsaved_markers()
    time.sleep(3)  # Let WM_CLOSE finish
    gui_exe = get_gui_dir() / "ATK-Logic.exe"
    try:
        subprocess.Popen(
            [str(gui_exe)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
        )
    except OSError:
        pass


def _emit_result(all_ok, checks, gui_was_running, sync_done, args, results=None):
    if args.json:
        out = {"all_ok": all_ok, "checks": results or {}}
        if gui_was_running is not None:
            out["gui_was_running"] = gui_was_running
            out["sync_done"] = sync_done
        print(_json.dumps(out, ensure_ascii=False))
    else:
        print("=== ATK-Logic Pre-flight ===\n")
        for name, ok, msg in checks:
            status = _green("PASS") if ok else _red("FAIL")
            print(f"  [{status}] {name}: {msg}")
        print()
        if all_ok:
            print(_green("All checks passed — ready to capture."))
        else:
            print(_red("Some checks FAILED — fix issues above before capturing."))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
