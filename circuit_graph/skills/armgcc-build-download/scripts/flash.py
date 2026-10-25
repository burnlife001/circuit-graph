#!/usr/bin/env python3
"""Flash MM32G0001 (G0001) firmware via pyOCD, then reset and resume.

Default chip is G0001 (MM32G0001A1TC). Works with any .hex/.bin/.elf.

Usage:
    python flash.py                       # auto-detect build/*.hex, chip = G0001
    python flash.py path/to/fw.hex
    python flash.py --target MM32G0001A1TC --pack D:/path/to/DFP/1.0.1 fw.hex
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CHIPS_FILE = SKILL_DIR / "chips.json"

# ---- defaults (G0001) ------------------------------------------------------
DEFAULT_CHIP = "G0001"
DEFAULT_TARGET = "MM32G0001A1TC"
DEFAULT_PACK = r"D:/Keil_v5/Packs/MindMotion/MM32G0001_DFP/1.0.1"
DEFAULT_FREQ = 1000000
DEFAULT_SEARCH_DIRS = ("build", "MDK-ARM/Objects")


def chip_defaults(chip_name):
    """Return (target, pack) for a chip id from chips.json (falls back to G0001)."""
    if not CHIPS_FILE.is_file():
        return DEFAULT_TARGET, DEFAULT_PACK
    chips = json.loads(CHIPS_FILE.read_text(encoding="utf-8"))
    entry = chips.get(chip_name) or chips.get(DEFAULT_CHIP) or {}
    return entry.get("target", DEFAULT_TARGET), entry.get("pack", DEFAULT_PACK)


def log(tag, msg):
    print(f"[flash] {tag}: {msg}", flush=True)


def find_pyocd_cmd():
    """Return (argv_prefix, label) able to run pyocd."""
    exe = shutil.which("pyocd")
    if exe:
        return [exe], exe

    # pyocd.exe lives in the <Scripts> dir of the interpreter that has it installed
    for base in [Path(sys.executable).parent / "Scripts"]:
        cand = base / "pyocd.exe"
        if cand.exists():
            return [str(cand)], str(cand)
    for cand in (Path.home() / "AppData/Roaming/uv/python").glob("*/Scripts/pyocd.exe"):
        return [str(cand)], str(cand)

    # last resort: this interpreter as a module
    try:
        subprocess.run([sys.executable, "-m", "pyocd", "--version"],
                       capture_output=True, check=True)
        return [sys.executable, "-m", "pyocd"], f"{sys.executable} -m pyocd"
    except Exception:
        return None, None


def find_hex(explicit):
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            sys.exit(f"[flash] ERROR: hex not found: {p}")
        return p

    root = Path.cwd()
    for d in DEFAULT_SEARCH_DIRS:
        dd = root / d
        if not dd.is_dir():
            continue
        candidates = [f for f in dd.iterdir()
                      if f.is_file() and f.suffix.lower() in (".hex", ".bin", ".elf")]
        if candidates:
            # .hex/.elf carry address info, prefer them over raw .bin; then newest
            rank = {".hex": 0, ".elf": 1, ".bin": 2}
            best = min(candidates, key=lambda f: (rank[f.suffix.lower()], -f.stat().st_mtime))
            log("hex", f"auto-detected {best} (from {d}/)")
            return best
    sys.exit("[flash] ERROR: no .hex/.bin/.elf found in " + ", ".join(DEFAULT_SEARCH_DIRS))


def run(cmd, label):
    log(label, " ".join(str(c) for c in cmd))
    proc = subprocess.run([str(c) for c in cmd])
    return proc.returncode


def main():
    ap = argparse.ArgumentParser(description="Flash MM32G0001 firmware via pyOCD")
    ap.add_argument("image", nargs="?", help="hex/bin/elf to flash (default: newest in build/)")
    ap.add_argument("--chip", default=DEFAULT_CHIP,
                    help=f"chip id from chips.json (default: {DEFAULT_CHIP})")
    ap.add_argument("--target", help="pyOCD target type (default: from chips.json)")
    ap.add_argument("--pack", help="CMSIS pack directory (default: from chips.json)")
    ap.add_argument("--frequency", type=int, default=DEFAULT_FREQ,
                    help=f"SWD clock in Hz (default: {DEFAULT_FREQ})")
    ap.add_argument("--probe", help="probe unique id (default: first found)")
    ap.add_argument("--no-reset", action="store_true",
                    help="skip the 'reset; continue' step after flashing")
    args = ap.parse_args()

    cmd, label = find_pyocd_cmd()
    if cmd is None:
        sys.exit("[flash] ERROR: pyocd not found. Install: pip install pyocd")
    log("pyocd", label)

    image = find_hex(args.image)
    dflt_target, dflt_pack = chip_defaults(args.chip)
    target = args.target or dflt_target
    pack = Path(args.pack or dflt_pack)
    if not pack.is_dir():
        log("warn", f"pack dir missing: {pack} (continuing anyway)")

    common = ["--pack", str(pack), "-t", target, "-f", str(args.frequency)]
    if args.probe:
        common += ["-u", args.probe]

    rc = run(cmd + ["flash"] + common + ["-e", "chip", str(image)], "flash")
    if rc != 0:
        log("flash", f"FAILED rc={rc}")
        sys.exit(rc)
    log("flash", "OK")

    if args.no_reset:
        log("reset", "skipped (--no-reset); chip stays halted")
        return

    rc = run(cmd + ["commander"] + common + ["-c", "reset; continue"], "reset")
    if rc != 0:
        log("reset", f"FAILED rc={rc} -- chip may be halted")
        sys.exit(rc)
    log("reset", "OK (chip running)")


if __name__ == "__main__":
    main()
