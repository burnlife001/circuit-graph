#!/usr/bin/env python3
"""Generic arm-gcc (bare-metal Cortex-M) build — portable across projects.

Design: this script knows nothing about any particular project.
Everything project-specific comes from `armgcc.build.json` (in the project
root) or from auto-discovery of the usual MM32/ARM layout.
If there is no config file, the auto-discovery defaults are good enough for
a typical MM32G0001 project; add a config only when you need to deviate.

Usage:
    python build.py                     # build the project in the current dir
    python build.py --project ../other  # build another project
    python build.py --chip G0001        # pick the chip (default G0001)
    python build.py --flash             # build, then flash with flash.py
"""

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CHIPS_FILE = SKILL_DIR / "chips.json"
CONFIG_NAME = "armgcc.build.json"

# Fallback toolchain search roots (Windows first, then MSYS-style)
TOOLCHAIN_GLOBS = [
    "E:/__electric/tools/arm-gcc/xpack-arm-none-eabi-gcc-*",
    "E:/__electric/tools/arm-gcc/*",
    "/e/__electric/tools/arm-gcc/xpack-arm-none-eabi-gcc-*",
    "/e/__electric/tools/arm-gcc/*",
]

# Auto-discovery source globs (relative to project root)
SRC_GLOBS = ["src/**/*.c", "Device/*/Source/*.c", "Device/*/HAL_Lib/src/*.c"]

# Never compile these (framework internals / Keil-only / non-reproducible)
SRC_EXCLUDES = [
    "*/ports/arm-cm/*",   # Keil-only port layer, conflicts with project src/ports
    "*/qstamp.c",         # embeds __DATE__/__TIME__ -> breaks reproducible builds
]

DEFAULT_CFLAGS = [
    "-Os", "-g3", "-gdwarf-3",
    "-Wall", "-Wextra", "-Wno-unused-parameter",
    "-ffunction-sections", "-fdata-sections", "-fno-common",
    "--specs=nosys.specs", "--specs=nano.specs",
]

DEFAULT_LDFLAGS = [
    "-Wl,--gc-sections", "-Wl,--print-memory-usage", "-nostartfiles",
    "--specs=nosys.specs", "--specs=nano.specs",
]

DEFAULT_ASFLAGS = ["-g3", "-gdwarf-3"]


def log(tag, msg):
    print(f"[build] {tag}: {msg}", flush=True)


def die(msg):
    print(f"[build] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


# ---------------------------------------------------------------- chip table

def load_chip(chip_name, override=None):
    chips = {}
    if CHIPS_FILE.is_file():
        chips = json.loads(CHIPS_FILE.read_text(encoding="utf-8"))
    if chip_name not in chips:
        die(f"unknown chip '{chip_name}' (known: {', '.join(chips) or 'none'}). "
            f"Add it to {CHIPS_FILE} or pass --config with explicit fields.")
    chip = dict(chips[chip_name])
    chip["name"] = chip_name
    if override:
        chip.update(override)
    return chip


# ------------------------------------------------------------- config layer

def find_config(project, explicit):
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            die(f"config not found: {p}")
        return p
    p = project / CONFIG_NAME
    return p if p.is_file() else None


def load_config(project, explicit):
    cfg_file = find_config(project, explicit)
    cfg = {}
    if cfg_file:
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            die(f"bad JSON in {cfg_file}: {e}")
        log("config", str(cfg_file))
    else:
        log("config", f"none found in {project} -> auto-discovery")
    cfg["_file"] = str(cfg_file) if cfg_file else None
    return cfg


# ---------------------------------------------------------------- toolchain

def tool_path(toolchain, name):
    """Return the executable path, adding .exe on Windows."""
    for cand in (toolchain / "bin" / name, toolchain / "bin" / f"{name}.exe"):
        if cand.is_file():
            return cand
    return toolchain / "bin" / name


def find_toolchain(cfg):
    tc = cfg.get("toolchain")
    candidates = []
    if tc:
        candidates.append(Path(tc))
    if os.environ.get("GCC_PATH"):
        candidates.append(Path(os.environ["GCC_PATH"]))
    for pat in TOOLCHAIN_GLOBS:
        candidates += [Path(p) for p in sorted(__import__("glob").glob(pat))]
    for c in candidates:
        if tool_path(c, "arm-none-eabi-gcc").is_file():
            return c
    which = shutil.which("arm-none-eabi-gcc")
    if which:
        return Path(which).resolve().parent.parent
    die("arm-none-eabi-gcc not found. Set --toolchain, GCC_PATH env, or install xpack arm-gcc.")


def find_project_file(name, cfg, chip, project, toolchain):
    """Locate startup .S / linker script: config -> chip -> toolchain dir -> project."""
    tools_dir = Path(cfg.get("toolchain_files")
                     or toolchain.parent / "gccPrjFiles")
    roots = [tools_dir, project]
    for key in ("startup", "linker_script"):
        want = cfg.get(key) or chip.get(key)
        if not want:
            continue
        p = Path(want)
        if p.is_file():
            cfg[key] = str(p)
            continue
        for root in roots:
            cand = root / want
            if cand.is_file():
                cfg[key] = str(cand)
                break
        else:
            die(f"{key} '{want}' not found (searched {', '.join(str(r) for r in roots)})")
    return cfg


# ------------------------------------------------------------------- source

def excluded(rel, patterns):
    posix = rel.as_posix()
    return any(fnmatch.fnmatch(posix, pat) for pat in patterns)


def discover_sources(project, cfg):
    globs = cfg.get("source_globs") or SRC_GLOBS
    excludes = cfg.get("exclude") or SRC_EXCLUDES
    found = set()
    for pat in globs:
        for f in project.glob(pat):
            if f.is_file():
                found.add(f)
    kept = [f for f in found if not excluded(f.relative_to(project), excludes)]
    dropped = sorted(f.relative_to(project).as_posix() for f in found - set(kept))
    for d in dropped:
        log("skip", d)
    return sorted(kept, key=lambda f: f.relative_to(project).as_posix())


def discover_includes(project, cfg):
    incs = []
    src = project / "src"
    if src.is_dir():
        incs.append(src)
        for d in sorted(src.iterdir()):
            if d.is_dir() and any(d.glob("*.h")):
                incs.append(d)
        for d in sorted(src.glob("*/*")):
            if d.is_dir() and d.name == "include":
                incs.append(d)
    for pat in ("Device/*/Include", "Device/*/HAL_Lib/inc", "Device/CMSIS/Core/Include"):
        incs += sorted(project.glob(pat))
    seen, out = set(), []
    for i in incs:
        s = str(i)
        if s not in seen and Path(s).is_dir():
            seen.add(s)
            out.append(s)
    return out


# --------------------------------------------------------------------- run

def run(cmd, cwd=None, capture=False):
    log("run", " ".join(str(c) for c in cmd))
    proc = subprocess.run([str(c) for c in cmd], cwd=cwd,
                          capture_output=capture, text=capture)
    if proc.returncode != 0:
        if capture and proc.stderr:
            print(proc.stderr, file=sys.stderr)
        die(f"command failed (rc={proc.returncode}): {cmd[0]}")
    return proc


def main():
    ap = argparse.ArgumentParser(description="Generic arm-gcc build")
    ap.add_argument("--project", default=".", help="project root (default: cwd)")
    ap.add_argument("--config", help=f"config file (default: <project>/{CONFIG_NAME})")
    ap.add_argument("--chip", default="G0001", help="chip id from chips.json (default: G0001)")
    ap.add_argument("--target", help="output name (default: config target or project dir name)")
    ap.add_argument("--build-dir", help="output dir (default: config or 'build')")
    ap.add_argument("--toolchain", help="arm-gcc install dir (default: auto-detect)")
    ap.add_argument("--clean", action="store_true", help="remove build dir first")
    ap.add_argument("--flash", action="store_true", help="flash after build (uses flash.py)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    project = Path(args.project).resolve()
    if not project.is_dir():
        die(f"project dir not found: {project}")

    cfg = load_config(project, args.config)
    chip = load_chip(args.chip, {k: v for k, v in cfg.items()
                                 if k in ("target", "pack", "mcpu", "startup", "linker_script")})

    if args.toolchain:
        cfg["toolchain"] = args.toolchain
    toolchain = find_toolchain(cfg)
    cfg = find_project_file("startup", cfg, chip, project, toolchain)

    cc = tool_path(toolchain, "arm-none-eabi-gcc")
    objcopy = tool_path(toolchain, "arm-none-eabi-objcopy")
    size = tool_path(toolchain, "arm-none-eabi-size")
    for exe in (cc, objcopy, size):
        if not exe.is_file():
            die(f"missing tool: {exe}")

    target = args.target or cfg.get("target") or project.name
    build_dir = Path(args.build_dir or cfg.get("build_dir") or "build")
    if not build_dir.is_absolute():
        build_dir = project / build_dir

    mcpu = cfg.get("mcpu") or chip.get("mcpu", "cortex-m0plus")
    mcpu_flags = f"-mcpu={mcpu} -mthumb -mfloat-abi=soft".split()

    sources = [project / s for s in cfg["sources"]] if cfg.get("sources") else discover_sources(project, cfg)
    if not sources:
        die("no C sources found (check config 'sources' or project layout)")

    includes = cfg.get("includes")
    if includes:
        incs = [str((project / i).resolve() if not Path(i).is_absolute() else i) for i in includes]
    else:
        incs = [str(i) for i in discover_includes(project, cfg)]
    if not incs:
        die("no include dirs found")

    cflags = mcpu_flags + list(cfg.get("cflags") or DEFAULT_CFLAGS)
    ldflags = mcpu_flags + list(cfg.get("ldflags") or DEFAULT_LDFLAGS)
    asflags = mcpu_flags + list(cfg.get("asflags") or DEFAULT_ASFLAGS)

    # HAL-based projects need hal_conf.h force-included (vendor convention)
    if not cfg.get("cflags"):
        hal_conf = next(iter(sorted(project.glob("Device/*/HAL_Lib/inc/hal_conf.h"))), None)
        if hal_conf:
            cflags.append(f"-include{hal_conf.as_posix()}")
            log("hal", f"force-include {hal_conf.relative_to(project).as_posix()}")

    if args.clean and build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    for old in build_dir.glob("*.o"):
        old.unlink()

    log("toolchain", str(toolchain))
    log("chip", f"{chip['name']} (mcpu={mcpu})")
    log("target", target)
    log("sources", f"{len(sources)} files, {len(incs)} include dirs")
    if args.verbose:
        for s in sources:
            log("src", s.relative_to(project).as_posix())
        for i in incs:
            log("inc", i)
        log("cflags", " ".join(cflags))
        log("ldflags", " ".join(ldflags))

    # --- compile (object name = source basename; detect collisions) ---
    objs = []
    used = {}
    for src in sources:
        rel = src.relative_to(project).as_posix()
        obj_name = src.stem + ".o"
        if obj_name in used:
            die(f"object name collision: {used[obj_name]} and {rel} -> {obj_name}. "
                f"Use an explicit 'sources' list or rename one file.")
        used[obj_name] = rel
        obj = build_dir / obj_name
        run([cc] + cflags + [f"-I{i}" for i in incs] + ["-c", rel, "-o", str(obj)], cwd=project)
        objs.append(obj)

    # --- assemble startup ---
    startup = Path(cfg.get("startup"))
    if not startup.is_file():
        die(f"startup file not found: {startup}")
    startup_obj = build_dir / (startup.stem + ".o")
    run([cc, "-x", "assembler-with-cpp"] + asflags + ["-c", str(startup), "-o", str(startup_obj)],
        cwd=project)
    objs.append(startup_obj)

    # --- link (sorted by object basename, matching the established .sh behaviour) ---
    objs.sort(key=lambda p: p.name)
    ld = cfg.get("linker_script")
    elf = build_dir / f"{target}.elf"
    link = run([cc] + ldflags + ["-T", str(ld), f"-Wl,-Map={build_dir / (target + '.map')}"]
               + [str(o) for o in objs] + ["-o", str(elf)], cwd=project, capture=True)
    if link.stdout:
        print(link.stdout.rstrip())

    # --- objcopy ---
    hexf = build_dir / f"{target}.hex"
    binf = build_dir / f"{target}.bin"
    run([objcopy, "-O", "ihex", str(elf), str(hexf)])
    run([objcopy, "-O", "binary", str(elf), str(binf)])

    # --- size report ---
    print()
    print("=== Size Report ===")
    run([size, str(elf)])

    flash_kb = chip.get("flash_kb")
    ram_kb = chip.get("ram_kb")
    if flash_kb and ram_kb and link.stdout:
        import re
        regions = {}
        for line in link.stdout.splitlines():
            m = re.match(r"\s*(FLASH|RAM):\s+(\d+)\s*B\s+(\d+)\s*KB\s+([\d.]+)%", line)
            if m:
                regions[m.group(1)] = (int(m.group(2)), int(m.group(3)) * 1024, m.group(4))
        if "FLASH" in regions and "RAM" in regions:
            fu, ft, fp = regions["FLASH"]
            ru, rt, rp = regions["RAM"]
            print(f"[build] usage: FLASH {fu}/{ft} B ({fp}%), RAM {ru}/{rt} B ({rp}%)")

    print()
    log("done", f"{hexf} (elf: {elf})")

    if args.flash:
        flash_py = Path(__file__).resolve().parent / "flash.py"
        log("flash", "invoking flash.py")
        rc = subprocess.run([sys.executable, str(flash_py), str(hexf),
                             "-t", chip.get("target", "MM32G0001A1TC")]).returncode
        if rc != 0:
            die(f"flash failed (rc={rc})")


if __name__ == "__main__":
    main()
