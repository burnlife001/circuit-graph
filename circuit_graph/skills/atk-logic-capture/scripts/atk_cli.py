"""
ATK-Logic CLI entry point.

Commands:
    info          Show capture file metadata
    export        Export edge events for channel(s)
    list-decoders List available protocol decoders
    decode        Run protocol decoder on capture data
    capture       Start capture from USB device and save as .atkdl

Built-in decoders (native Python, no DLL needed): uart, i2c, spi.
Other decoders require libsigrokdecode-4.dll + Python 3.14.

Usage:
    python atk_cli.py info test.atkdl
    python atk_cli.py export test.atkdl --ch 0 --start 0 --end 1ms
    python atk_cli.py list-decoders --filter uart
    python atk_cli.py decode test.atkdl --decoder uart --rx 0 --option baudrate=115200
    python atk_cli.py capture start --ch 0 --duration 5s --output capture.atkdl
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import session_state
from _config import (
    APPDATA_ATK,
    ATK_BG_EXE,
    SCRIPTS_DIR,
    get_data_dir,
    get_gui_dir,
)
from capture_reader import CaptureReader, parse_time
from config_sync import get_set_ini_params, sync_set_ini

# Native protocol decoders live in analyze/ (one file per protocol)
sys.path.insert(0, str(Path(__file__).parent / "analyze"))
from atk_i2c import I2CDecoder
from atk_spi import SpiDecoder
from atk_uart import UartDecoder

# ---------------------------------------------------------------------------
# Native decoder registry (uart / i2c / spi from analyze/)
# ---------------------------------------------------------------------------

_NATIVE_DECODERS = {
    "uart": (UartDecoder, UartDecoder.decoder_info()),
    "i2c": (I2CDecoder, I2CDecoder.decoder_info()),
    "spi": (SpiDecoder, SpiDecoder.decoder_info()),
}

_ALIASES = {
    "rs232": "uart",
    "serial": "uart",
    "twi": "i2c",
}


def get_native_decoder_ids() -> list[str]:
    """Return list of built-in decoder IDs."""
    return sorted(_NATIVE_DECODERS.keys())


def list_native_decoders(filter_ids: list[str] | None = None) -> list[dict]:
    """List all native decoders, optionally filtered."""
    result = []
    for did in _NATIVE_DECODERS:
        if filter_ids and did not in filter_ids:
            continue
        result.append(_NATIVE_DECODERS[did][1])
    return result


def native_decode(
    decoder_id: str,
    channel_map: dict[str, int],
    reader,  # CaptureReader
    options: dict[str, str] | None = None,
    start_ns: int = 0,
    end_ns: int | None = None,
    max_events: int = 100000,
) -> list[dict]:
    """Run a native protocol decoder against a CaptureReader.

    Returns a list of frame dicts ready for JSON serialization.
    """
    decoder_id = _ALIASES.get(decoder_id, decoder_id)
    entry = _NATIVE_DECODERS.get(decoder_id)
    if entry is None:
        raise ValueError(f"Unknown native decoder: {decoder_id}")

    decoder_cls, _ = entry
    info = reader.get_info()
    sr_hz = info.sample_rate_hz
    opts = options or {}

    def _read(ch_key: str):
        ch = channel_map.get(ch_key)
        if ch is None:
            return []
        return reader.read_edges(ch, start_ns, end_ns, max_events)

    if decoder_id == "uart":
        rx = _read("rx")
        tx = _read("tx")
        edges = rx if rx else tx
        if not edges:
            raise ValueError("UART requires --rx or --tx channel mapping")
        dec = decoder_cls(edges, sr_hz, opts)
        drop_errors = opts.pop("drop_errors", "true").lower() in ("1", "true", "yes")
        frames = dec.decode(drop_errors=drop_errors)

    elif decoder_id == "i2c":
        edges_scl = _read("scl")
        edges_sda = _read("sda")
        if not edges_scl or not edges_sda:
            raise ValueError("I²C requires --scl and --sda channel mappings")
        dec = decoder_cls(edges_scl, edges_sda, sr_hz, opts)
        frames = dec.decode()

    elif decoder_id == "spi":
        edges_clk = _read("clk")
        if not edges_clk:
            raise ValueError("SPI requires --clk channel mapping")
        edges_mosi = _read("mosi")
        edges_miso = _read("miso")
        edges_cs = _read("cs")
        dec = decoder_cls(edges_clk, edges_mosi, edges_miso, edges_cs, sr_hz, opts)
        frames = dec.decode()

    else:
        raise ValueError(f"Unknown native decoder: {decoder_id}")

    return [
        {"t_ns": f.t_ns, "type": f.type, "data": f.data, "errors": f.errors}
        for f in frames
    ]


def _out(data, fmt):
    if fmt == "json":
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        for k, v in data.items():
            print(f"{k}: {v}")


def _err(msg):
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# preflight status helpers — backed by skill_state.json on disk (was: DLL TCP)
# ---------------------------------------------------------------------------

def api_get_preflight_status() -> bool:
    """True if preflight passed in the current GUI session.

    Wraps session_state.read_state() so older callers keep working.
    """
    pid = session_state.gui_running()
    if pid <= 0:
        return False
    state = session_state.read_state(pid, session_state.gui_start_iso(pid))
    return bool(state.get("preflight_ok", False))


def api_set_preflight_status(ok: bool):
    """Persist preflight_ok in skill_state.json (replaces DLL TCP)."""
    pid = session_state.gui_running()
    if pid <= 0:
        return
    session_state.write_state(
        pid,
        session_state.gui_start_iso(pid),
        preflight_ok=bool(ok),
    )


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------

def cmd_info(args):
    reader = CaptureReader(args.file)
    info = reader.get_info()
    _out(
        {
            "channels": info.channels,
            "sample_rate_hz": info.sample_rate_hz,
            "total_samples": info.total_samples,
            "duration_ns": info.duration_ns,
            "file_format": info.file_format,
        },
        args.format,
    )


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def cmd_export(args):
    reader = CaptureReader(args.file)
    start_ns = parse_time(args.start)
    end_ns = parse_time(args.end) if args.end else None
    channel_ids = [int(c.strip()) for c in args.ch.split(",")]
    result = {}
    for cid in channel_ids:
        edges = reader.read_edges(cid, start_ns, end_ns, args.max_events)
        result[str(cid)] = [
            {"t_ns": e.t_ns, "level": e.level, "dur_ns": e.dur_ns} for e in edges
        ]
    _out({"channels": result}, args.format)


# ---------------------------------------------------------------------------
# list-decoders
# ---------------------------------------------------------------------------

def cmd_list_decoders(args):
    filter_ids = [f.strip() for f in args.filter.split(",")] if args.filter else None
    data = list_native_decoders(filter_ids)
    _out(data, args.format)


# ---------------------------------------------------------------------------
# decode
# ---------------------------------------------------------------------------

def cmd_decode(args):
    reader = CaptureReader(args.file)
    info = reader.get_info()
    multiply_ns = 1_000_000_000 // info.sample_rate_hz if info.sample_rate_hz else 50

    start_ns = parse_time(args.start)
    end_ns = parse_time(args.end) if args.end else None

    # Build channel map from --rx, --tx, --scl, etc.
    channel_map: dict[str, int] = {}
    for key in ("rx", "tx", "scl", "sda", "clk", "mosi", "miso", "cs"):
        val = getattr(args, key, None)
        if val is not None:
            channel_map[key] = val

    decoder_options: dict[str, str] = {}
    if args.option:
        for opt in args.option:
            if "=" in opt:
                k, v = opt.split("=", 1)
                decoder_options[k] = v

    # ── Native decoder path (built-in, no DLL needed) ──
    native_ids = get_native_decoder_ids()
    if args.decoder in native_ids:
        try:
            frames = native_decode(
                decoder_id=args.decoder,
                channel_map=channel_map,
                reader=reader,
                options=decoder_options,
                start_ns=start_ns,
                end_ns=end_ns,
                max_events=args.max_events,
            )
        except Exception as e:
            _out({"error": str(e)}, args.format)
            sys.exit(1)

        _out(
            {
                "decoder": args.decoder,
                "engine": "native",
                "frames": frames,
                "stats": {"total_frames": len(frames)},
            },
            args.format,
        )
        return

    # ── Unknown decoder ──
    _out(
        {
            "error": (
                f"Unknown decoder '{args.decoder}'. "
                f"Built-in decoders: {', '.join(native_ids)}."
            )
        },
        args.format,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

def _cleanup_unsaved_markers():
    """Remove ATK-Logic temp/memory markers left by a previous force-kill.

    These markers cause ATK-Logic to show a "recover unsaved data?" dialog
    on close (WM_CLOSE) and on startup.  The dialog blocks graceful shutdown,
    which forces us to /f kill again — a vicious cycle.

    _restart_gui() calls this BEFORE sending WM_CLOSE so the process can exit
    cleanly.  Since _restart_gui() is only invoked for config sync (not to
    recover user data), deleting these markers is safe.
    """
    memory_dir = APPDATA_ATK / "temp" / "memory"
    lock_file = APPDATA_ATK / "temp" / "lock"
    if memory_dir.exists():
        try:
            shutil.rmtree(str(memory_dir), ignore_errors=True)
        except Exception:
            pass
    if lock_file.exists():
        try:
            lock_file.unlink()
        except Exception:
            pass


def _restart_gui():
    """Restart ATK-Logic.exe and wait for its main window to appear.

    Replaces the old version that polled TCP. The TCP server lived in
    Qt5Network.dll proxy and is gone; we now poll for the GUI window title
    appearing (MainWindowTitle != ''), which is the same signal we use to
    detect a "ready" GUI for atk_bg hotkey delivery.

    Cleans up unsaved-data markers first so WM_CLOSE does not trigger the
    blocking "recover data?" dialog.
    """
    import subprocess as sp

    # ── Step 0: clean up markers from any previous force-kill ──
    _cleanup_unsaved_markers()

    # ── Step 1: graceful close first, force-kill only as last resort ──
    print("  [sync] gracefully closing ATK-Logic.exe...")
    try:
        sp.run(["taskkill", "/im", "ATK-Logic.exe"],
               capture_output=True, timeout=10)
    except sp.TimeoutExpired:
        pass

    # Poll for process exit (up to 3 s).
    poll_deadline = time.time() + 3
    still_alive = False
    while time.time() < poll_deadline:
        try:
            check = sp.run(
                ["tasklist", "/fi", "IMAGENAME eq ATK-Logic.exe", "/nh"],
                capture_output=True, text=True, timeout=5
            )
            if "ATK-Logic.exe" not in check.stdout:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        still_alive = True

    if still_alive:
        print("  [sync] still running after 3s, force killing...")
        try:
            sp.run(["taskkill", "/f", "/im", "ATK-Logic.exe"],
                   capture_output=True, timeout=10)
            time.sleep(0.5)
        except Exception:
            pass
        _cleanup_unsaved_markers()

    # ── Step 2: restart GUI ──
    gui_exe = get_gui_dir() / "ATK-Logic.exe"
    print(f"  [sync] restarting GUI: {gui_exe}")
    sp.Popen(
        [str(gui_exe)],
        stdout=sp.DEVNULL, stderr=sp.DEVNULL,
        creationflags=sp.DETACHED_PROCESS if sys.platform == "win32" else 0
    )

    # ── Step 3: wait for GUI window title to appear ──
    # Qt needs a moment to instantiate its main window after the process
    # starts.  Polling for MainWindowTitle != '' is the readiness signal
    # before any atk_bg hotkey delivery.
    print("  [sync] waiting for GUI window...")
    deadline = time.time() + 15
    while time.time() < deadline:
        if session_state.gui_running() > 0:
            print("  [sync] GUI window visible")
            time.sleep(1.0)  # settle for Qt focus
            return
        time.sleep(0.5)
    print("  [warn] GUI window not visible within 15s, proceeding anyway")


def read_path_ini():
    """Read trgDir from config.ini DATA_DIR. Returns Path object."""
    return get_data_dir()


def sync_save_path(trgDir):
    """Ensure set.ini savePath matches trgDir. Kills/restarts GUI if mismatch.
    Returns True if GUI was restarted."""
    set_ini = APPDATA_ATK / "set.ini"
    if not set_ini.exists():
        print(f"  [warn] set.ini not found at {set_ini}, skipping savePath sync")
        return False

    text = set_ini.read_text(encoding="utf-8")
    trgDir_s = str(trgDir).replace("\\", "/")

    # Parse savePath= value
    atkDir = None
    for line in text.splitlines():
        line_s = line.strip()
        if line_s.startswith("savePath") and "=" in line_s:
            val = line_s.split("=", 1)[1].strip()
            if val:
                atkDir = val.replace("\\", "/")
            break

    if atkDir is None:
        print(f"  [warn] savePath not found in set.ini, will set to {trgDir_s}")
    elif atkDir == trgDir_s:
        print(f"  [sync] savePath already correct: {trgDir_s}")
        return False

    # Mismatch or missing — modify set.ini then restart GUI
    print(f"  [sync] savePath mismatch: set.ini={atkDir} vs trgDir={trgDir_s}")

    if atkDir is not None:
        # Try forward-slash variant first, then backslash
        new_text = text.replace(f"savePath={atkDir}", f"savePath={trgDir_s}")
        atkDir_bs = atkDir.replace("/", "\\")
        if atkDir_bs != atkDir:
            n2 = text.replace(f"savePath={atkDir_bs}", f"savePath={trgDir_s}")
            if n2 != text:
                new_text = n2
    else:
        new_text = text.rstrip("\n") + f"\nsavePath={trgDir_s}\n"

    set_ini.write_text(new_text, encoding="utf-8")
    print(f"  [sync] set.ini updated: savePath={trgDir_s}")

    _restart_gui()
    return True


def cmd_capture(args):
    """Run capture using atk_bg.exe + skill_state.json (no DLL injection).

    Flow:
      preflight (state file) → sync (optional) → F1 → sleep duration →
      F2 → sleep 3s → F12 → mode (a) fill dialog + click save OR mode (b)
      mtime scan for the new .atkdl.
    """
    import subprocess as sp

    # ── Step 1: locate trgDir + GUI process ──
    trgDir = read_path_ini()
    print(f"  [pre] trgDir from config.ini: {trgDir}")

    gui_pid = session_state.gui_running()
    if gui_pid <= 0:
        _err("ATK-Logic GUI is not running. Start D:/Programs/ATK-Logic/ATK-Logic.exe first.")
        sys.exit(1)
    gui_start = session_state.gui_start_iso(gui_pid)
    state = session_state.read_state(gui_pid, gui_start)
    preflight_ok = state["preflight_ok"]
    pf_sync_done = False

    # ── Step 2: preflight if needed ──
    if not preflight_ok:
        print("  [pre] Preflight status: NOT OK — running preflight...")
        preflight_script = str(SCRIPTS_DIR / "capture" / "atk_preflight.py")
        pf_cmd = [sys.executable, preflight_script, "--fix", "--json"]
        if args.ch is not None:
            pf_cmd.extend(["--ch", args.ch])
        if args.sample_rate_hz is not None:
            pf_cmd.extend(["--sample-rate-hz", str(args.sample_rate_hz)])
        if args.threshold is not None:
            pf_cmd.extend(["--threshold", str(args.threshold)])
        pf_result = sp.run(pf_cmd, capture_output=True, text=True, timeout=25)
        if pf_result.returncode != 0:
            _err(f"Preflight failed (exit {pf_result.returncode})")
            if pf_result.stdout:
                _err(pf_result.stdout.strip())
            sys.exit(1)
        try:
            pf_json = json.loads(pf_result.stdout.strip().split("\n")[-1])
            if not pf_json.get("all_ok"):
                _err("Preflight failed — check output above")
                sys.exit(1)
        except (json.JSONDecodeError, IndexError):
            _err("Preflight output parse error")
            sys.exit(1)
        # Recompute gui_pid + state — preflight may have restarted GUI
        gui_pid = session_state.gui_running()
        if gui_pid > 0:
            gui_start = session_state.gui_start_iso(gui_pid)
            session_state.write_state(gui_pid, gui_start, preflight_ok=True)
            state = session_state.read_state(gui_pid, gui_start)
            # Re-read whether preflight synced params (no GUI restart unless --fix did it)
            pf_sync_done = pf_json.get("sync_done", False)
            preflight_ok = True
            print("  [pre] Preflight passed — state updated")
        else:
            _err("GUI exited during preflight")
            sys.exit(1)
    else:
        print("  [pre] Preflight status: OK (already verified this GUI session)")

    # ── Step 3: optional sync (--ch/--hz/--threshold/--sync) ──
    explicit_ch = args.ch is not None
    explicit_hz = args.sample_rate_hz is not None
    explicit_threshold = args.threshold is not None
    do_sync = (args.sync or explicit_ch or explicit_hz or explicit_threshold) and not pf_sync_done

    gui_restarted = False

    if do_sync:
        ch_list_for_sync = [int(x.strip()) for x in args.ch.split(",") if x.strip()] if explicit_ch else None
        sync_result = sync_set_ini(
            channels=ch_list_for_sync,
            set_hz=args.sample_rate_hz,
            threshold=args.threshold,
        )
        needs_restart = len(sync_result["changes"]) > 0
        save_restarted = sync_save_path(trgDir)
        if needs_restart and not save_restarted:
            _restart_gui()
        gui_restarted = needs_restart or save_restarted

        # Refresh session after potential restart
        gui_pid = session_state.gui_running()
        if gui_pid > 0:
            gui_start = session_state.gui_start_iso(gui_pid)
            state = session_state.read_state(gui_pid, gui_start)

        actual = get_set_ini_params()
        ch_list = ch_list_for_sync if explicit_ch else actual.get("channels", [0, 1, 2, 3])
        display_hz = args.sample_rate_hz if explicit_hz else actual.get("setHz", 20_000_000)
        display_threshold = args.threshold if explicit_threshold else actual.get("thresholdLevel", 1.5)
    else:
        actual = get_set_ini_params()
        ch_list = actual.get("channels", [0, 1, 2, 3])
        display_hz = actual.get("setHz", 20_000_000)
        display_threshold = actual.get("thresholdLevel", 1.5)

        if pf_sync_done:
            print(f"  [pre] set.ini: ch={ch_list}, {display_hz/1e6:.0f}MHz, {display_threshold}V (synced by preflight)")
        else:
            set_ini_path = APPDATA_ATK / "set.ini"
            set_ini_mtime = set_ini_path.stat().st_mtime if set_ini_path.exists() else 0
            # Compare set.ini mtime vs GUI start time (convert ISO -> ts).
            gui_start_ts = 0.0
            if gui_start:
                try:
                    from datetime import datetime
                    gui_start_ts = datetime.fromisoformat(gui_start).timestamp()
                except Exception:
                    pass
            if gui_start_ts > 0 and set_ini_mtime < gui_start_ts:
                freshness = "⚠ stale (in-GUI changes may not be reflected)"
                hint = True
            elif gui_start_ts > 0:
                freshness = "(fresh — written after GUI start)"
                hint = False
            else:
                freshness = "(GUI not detected)"
                hint = False
            print(f"  [pre] set.ini: ch={ch_list}, {display_hz/1e6:.0f}MHz, {display_threshold}V {freshness}")
            if hint:
                print("  [pre] Pass --ch/--sample-rate-hz/--threshold to enforce known values if in doubt")

    # ── Step 4: post-restart preflight re-check ──
    # A new GUI process invalidates the preflight_ok stored against the old
    # pid — the new GUI may be missing DLLs, save dir, etc.  Re-run.
    if gui_restarted:
        # state was just refreshed above against potentially new pid
        if not state["preflight_ok"]:
            print("  [pre] Post-restart preflight: NOT OK — re-running...")
            preflight_script = str(SCRIPTS_DIR / "capture" / "atk_preflight.py")
            pf_cmd = [sys.executable, preflight_script, "--fix", "--json"]
            pf_result = sp.run(pf_cmd, capture_output=True, text=True, timeout=25)
            if pf_result.returncode != 0:
                _err(f"Post-restart preflight failed (exit {pf_result.returncode})")
                if pf_result.stdout:
                    _err(pf_result.stdout.strip())
                sys.exit(1)
            try:
                pf_json = json.loads(pf_result.stdout.strip().split("\n")[-1])
                if pf_json.get("all_ok"):
                    gui_pid = session_state.gui_running()
                    if gui_pid > 0:
                        gui_start = session_state.gui_start_iso(gui_pid)
                        session_state.write_state(gui_pid, gui_start, preflight_ok=True)
                    print("  [pre] Post-restart preflight passed")
                else:
                    _err("Post-restart preflight failed — check output above")
                    sys.exit(1)
            except (json.JSONDecodeError, IndexError):
                _err("Post-restart preflight output parse error")
                sys.exit(1)
        else:
            print("  [pre] Post-restart preflight: OK (state matched new pid)")

    # ── Step 5: dispatch start | stop ──
    if args.capture_action == "stop":
        # Direct key delivery — no save flow needed.
        print("  [stop] sending F2 (stop capture) to background GUI...")
        r = sp.run([str(ATK_BG_EXE), "key", "F2"], capture_output=True, timeout=10)
        if r.returncode != 0:
            _err(f"atk_bg key F2 failed: {r.stderr.decode('utf-8', 'replace').strip()}")
            sys.exit(1)
        print("Capture stopped.")
        return

    if args.capture_action != "start":
        _err(f"Unknown capture action: {args.capture_action}")
        sys.exit(1)

    # ── Step 6: capture start ──
    duration_str = args.duration
    duration_ns = parse_time(duration_str)
    duration_s = duration_ns / 1_000_000_000

    if args.output is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        output = str(trgDir / f"capture-{ts}.atkdl")
    else:
        output = str(trgDir / Path(args.output).name)

    Path(output).parent.mkdir(parents=True, exist_ok=True)

    # Pre-capture newest .atkdl mtime (for direct-save detection).
    pre_mtime = 0.0
    for f in trgDir.glob("*.atkdl"):
        mt = f.stat().st_mtime
        if mt > pre_mtime:
            pre_mtime = mt

    print("=== ATK-Logic Capture ===")
    print(f"  Sample rate: {display_hz / 1e6:.0f} MHz")
    print(f"  Duration:    {duration_s:.1f}s")
    print(f"  Channels:    {ch_list}")
    print(f"  Threshold:   {display_threshold} V")
    print(f"  Output:      {Path(output).name}")
    print(f"  RLE:         {'on' if args.rle else 'off'}")

    is_ctrl_s_pressed = state["is_ctrl_s_pressed"]
    print(f"  [pre] is_ctrl_s_pressed = {is_ctrl_s_pressed}  (first save opens picker, subsequent are direct)")

    # ── Drive GUI: F1 → wait → F2 → wait → F12 ──
    if not ATK_BG_EXE.exists():
        _err(f"atk_bg.exe not found at {ATK_BG_EXE}. Build it: pwsh {ATK_BG_EXE.parent}/build.ps1")
        sys.exit(1)

    print("  [capture] sending F1 (start)...")
    r = sp.run([str(ATK_BG_EXE), "key", "F1"], capture_output=True, timeout=10)
    if r.returncode != 0:
        _err(f"atk_bg key F1 failed: {r.stderr.decode('utf-8', 'replace').strip()}")
        sys.exit(1)

    print(f"  [capture] waiting {duration_s:.1f}s...", flush=True)
    time.sleep(duration_s)

    print("  [capture] sending F2 (stop)...")
    r = sp.run([str(ATK_BG_EXE), "key", "F2"], capture_output=True, timeout=10)
    if r.returncode != 0:
        _err(f"atk_bg key F2 failed: {r.stderr.decode('utf-8', 'replace').strip()}")
        sys.exit(1)

    # GUI needs a moment to finish processing data + render post-capture state
    print("  [capture] waiting 1s for GUI to settle before save trigger...")
    time.sleep(1.0)

    print("  [save] sending F12 (save)...")
    r = sp.run([str(ATK_BG_EXE), "key", "F12"], capture_output=True, timeout=10)
    if r.returncode != 0:
        _err(f"atk_bg key F12 failed: {r.stderr.decode('utf-8', 'replace').strip()}")
        sys.exit(1)

    # Branch on is_ctrl_s_pressed:
    #   False (first save in this session) → atk_bg fills Save-As picker
    #   True  (subsequent saves) → GUI does direct save, we mtime-scan
    if not is_ctrl_s_pressed:
        print("  [save] first save — filling Save-As picker via atk_bg save...")
        r = sp.run([str(ATK_BG_EXE), "save", str(Path(output)), "--timeout", "2"],
                   capture_output=True, timeout=15)
        # atk_bg save may report saved=false (file landed at GUI's choice path)
        # — don't fail just on that. We verify by file existence + size.
        if r.returncode not in (0, 7):
            _err(f"atk_bg save failed: {r.stderr.decode('utf-8', 'replace').strip()}")
            sys.exit(1)

        # Verify expected file. If it didn't land at output, do mtime scan.
        dst = Path(output)
        if dst.exists() and dst.stat().st_size > 1000:
            size_kb = dst.stat().st_size / 1024
            # Mark first-save done so subsequent saves skip the picker.
            if gui_pid > 0:
                session_state.write_state(gui_pid, gui_start, is_ctrl_s_pressed=True)
            print("Capture complete!")
            print(f"  File:    {output}")
            print(f"  Size:    {size_kb:.0f} KB")
            return

        # Fallback: scan for any new file (GUI may have ignored our filename).
        newest = None
        newest_time = pre_mtime
        for f in trgDir.glob("*.atkdl"):
            mt = f.stat().st_mtime
            if mt > newest_time:
                newest_time = mt
                newest = f
        if newest:
            if gui_pid > 0:
                session_state.write_state(gui_pid, gui_start, is_ctrl_s_pressed=True)
            print("Capture complete!")
            print(f"  File:    {newest}")
            print(f"  Size:    {newest.stat().st_size / 1024:.0f} KB")
            return

        print(f"Error: no .atkdl file found at {output} or newer than mtime {pre_mtime} in {trgDir}")
        sys.exit(1)

    else:
        # Subsequent save: GUI direct-saves to last-used path. Scan mtime.
        # Wait briefly (≤1.5s) for any new file to appear.
        deadline = time.time() + 1.5
        newest = None
        newest_time = pre_mtime
        while time.time() < deadline:
            for f in trgDir.glob("*.atkdl"):
                mt = f.stat().st_mtime
                if mt > newest_time:
                    newest_time = mt
                    newest = f
            if newest:
                break
            time.sleep(0.05)
        if newest:
            print("Capture complete!")
            print(f"  File:    {newest}")
            print(f"  Size:    {newest.stat().st_size / 1024:.0f} KB")
        else:
            print(f"Capture complete (no new .atkdl found in {trgDir})")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def cmd_config(args):
    """Manage ATK-Logic set.ini capture parameters.

    show — read and display set.ini params
    set  — write params to set.ini (backs up first if changes needed)
    """
    action = args.config_action

    if action == "show":
        params = get_set_ini_params()
        if not params:
            print("(no capture params found in set.ini)")
        else:
            for k, v in params.items():
                print(f"  {k}: {v}")
        return

    if action == "set":
        # Parse comma-separated channels
        ch_list = None
        if args.ch is not None:
            ch_list = [int(x.strip()) for x in args.ch.split(",") if x.strip()]
        sync_set_ini(
            channels=ch_list,
            set_hz=args.hz,
            threshold=args.threshold,
            dry_run=args.dry_run,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    # Force UTF-8 stdout on Windows (avoid GBK codec errors with e.g. I²C)
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )

    parser = argparse.ArgumentParser(
        description="ATK-Logic CLI — logic analyzer data reader and decoder"
    )
    parser.add_argument(
        "--format", choices=("json", "text"), default="json", help="Output format"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # info
    p = sub.add_parser("info", help="Show capture file metadata")
    p.add_argument("file", type=Path, help="Path to .atkdl or .bin file")
    p.set_defaults(func=cmd_info)

    # export
    p = sub.add_parser("export", help="Export edge events for channel(s)")
    p.add_argument("file", type=Path)
    p.add_argument("--ch", required=True, help="Comma-separated channel IDs, e.g. 0,2,3")
    p.add_argument("--start", default="0", help="Start time (ns/us/ms/s)")
    p.add_argument("--end", default=None, help="End time (ns/us/ms/s)")
    p.add_argument("--max-events", type=int, default=10000)
    p.set_defaults(func=cmd_export)

    # list-decoders
    p = sub.add_parser("list-decoders", help="List available protocol decoders")
    p.add_argument("--filter", default=None, help="Comma-separated decoder IDs")
    p.set_defaults(func=cmd_list_decoders)

    # decode
    p = sub.add_parser("decode", help="Decode waveform with a protocol decoder")
    p.add_argument("file", type=Path)
    p.add_argument("--decoder", required=True, help="Decoder ID, e.g. uart")
    p.add_argument("--start", default="0", help="Start time")
    p.add_argument("--end", default=None, help="End time")
    p.add_argument("--max-events", type=int, default=100000, help="Max edge events")
    p.add_argument("--rx", type=int, help="Map RX to channel N")
    p.add_argument("--tx", type=int, help="Map TX to channel N")
    p.add_argument("--scl", type=int, help="Map SCL to channel N")
    p.add_argument("--sda", type=int, help="Map SDA to channel N")
    p.add_argument("--clk", type=int, help="Map CLK to channel N")
    p.add_argument("--mosi", type=int, help="Map MOSI to channel N")
    p.add_argument("--miso", type=int, help="Map MISO to channel N")
    p.add_argument("--cs", type=int, help="Map CS to channel N")
    p.add_argument("--option", action="append", help="Decoder option as key=value")
    p.set_defaults(func=cmd_decode)

    # capture
    p = sub.add_parser("capture", help="Start/stop USB capture from logic analyzer device")
    p.add_argument("capture_action", choices=("start", "stop"), help="Start or stop capture")
    p.add_argument("--ch", type=str, default=None,
                   help="Comma-separated channel IDs. Default: use GUI current. Pass to force-sync channels.")
    p.add_argument("--duration", default="3s", help="Capture duration (ns/us/ms/s, default 3s)")
    p.add_argument("--sample-rate-hz", dest="sample_rate_hz", type=int, default=None,
                   help="Sample rate in Hz. Default: use GUI current. Pass to force-sync rate.")
    p.add_argument("--threshold", type=float, default=None,
                   help="Logic threshold voltage. Default: use GUI current. Pass to force-sync threshold.")
    p.add_argument("--sync", "-S", action="store_true", default=False,
                   help="Sync capture params + savePath to GUI before capture (may restart GUI)")
    p.add_argument("--rle", action="store_true", default=False,
                   help="Enable FPGA RLE compression (default off, matching GUI)")
    p.add_argument("--no-rle", action="store_false", dest="rle",
                   help="Disable FPGA RLE compression")
    p.add_argument("--output", "-o", default=None, help="Output .atkdl filename only (no path; always saved to DATA_DIR. default: capture_<timestamp>.atkdl)")
    p.set_defaults(func=cmd_capture)

    # config
    p = sub.add_parser("config", help="Read/write ATK-Logic set.ini capture parameters")
    p.add_argument("config_action", choices=("show", "set"),
                   help="show: display set.ini params | set: write params to set.ini")
    p.add_argument("--ch", default=None, help="Comma-separated channel IDs (e.g. 0,1,2,3)")
    p.add_argument("--hz", type=int, default=None, help="Sample rate in Hz (e.g. 20000000)")
    p.add_argument("--threshold", type=float, default=None, help="Threshold voltage (e.g. 1.5)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would change without writing")
    p.set_defaults(func=cmd_config)

    # where — print SCRIPTS_DIR (for shell wrappers / path discovery)
    p = sub.add_parser("where", help="Print absolute path of the scripts directory and exit")
    p.set_defaults(func=_cmd_where)

    args = parser.parse_args()
    args.func(args)


def _cmd_where(args):
    """Print SCRIPTS_DIR absolute path."""
    print(SCRIPTS_DIR)


if __name__ == "__main__":
    main()
