#!/usr/bin/env python3
"""Extract pin information from a KiCad symbol file or a JLCPCB part number.

If the argument is a component name (not a C-id, not a file path), JLCPCB is
searched automatically and the first matching C-id is used.

Outputs JSON to stdout.

Usage:
    pin2json.py [-p/--package PACKAGE] <query|C-id|path.kicad_sym> [output.json]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


def is_part_id(arg: str) -> bool:
    """Return True if arg looks like a JLCPCB/LCSC part id (Cxxxxx)."""
    return bool(arg) and arg[0].upper() == "C" and arg[1:].isdigit()


# Common packaging / order-code suffixes to strip from KiCad symbol names.
# "/" prefixed suffixes come from KiCad S-expression escapes ({slash}).
_PACKAGE_SUFFIXES = (
    "/NOPB",
    "/T&R",
    "/REEL",
    "/TAPE",
    "/TR",
    "/MP",
    "/EE",
    "/T",
)


def clean_symbol_name(raw: str) -> str:
    """Decode KiCad S-expression escapes and strip common packaging suffixes.

    Example: ``LM321MFX{slash}NOPB`` -> ``LM321MFX``.
    """
    if not raw:
        return raw
    name = raw.replace("{slash}", "/").replace("{backslash}", "\\")
    upper = name.upper()
    changed = True
    while changed:
        changed = False
        for sfx in _PACKAGE_SUFFIXES:
            if upper.endswith(sfx) and len(name) > len(sfx):
                name = name[: -len(sfx)]
                upper = name.upper()
                changed = True
                break
    return name


def _jlc_cmd() -> str:
    """Return the ``jlc`` CLI command name, platform-aware."""
    # On Windows the .ps1 wrapper is not directly callable by subprocess;
    # use the .cmd sibling instead.
    if os.name == "nt":
        cmd = shutil.which("jlc.cmd")
        if cmd:
            return cmd
    return "jlc"


def jlc_search(query: str, package: str | None = None, limit: int = 1) -> tuple[str, str]:
    """Search JLCPCB for *query* via the ``jlc`` CLI.

    Returns ``(lcsc_id, package_name)`` of the first matching result.

    When *package* is given, only results whose ``package`` field contains
    *package* (case-insensitive substring) are considered; if none match,
    the first result overall is used and a warning is printed to stderr.

    Raises ``RuntimeError`` on failure or empty result set.
    """
    cmd = [
        _jlc_cmd(),
        "search",
        query,
        "--in-stock",
        "--limit",
        str(limit),
        "--json",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", check=True
        )
    except FileNotFoundError:
        raise RuntimeError(
            "jlc CLI not found. Install it with: npm install -g @jlcpcb/cli"
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"jlc search failed (exit {exc.returncode}): {exc.stderr or exc.stdout or ''}"
        )

    data = json.loads(proc.stdout)
    if not data.get("success"):
        raise RuntimeError(f"jlc search returned error: {data}")

    results = data.get("results", [])
    if not results:
        raise RuntimeError(
            f"No JLCPCB results found for '{query}' (--in-stock, {limit} max)"
        )

    # Filter by package if requested.
    if package:
        pkg_lower = package.lower()
        filtered = [r for r in results if pkg_lower in (r.get("package") or "").lower()]
        if filtered:
            results = filtered
        else:
            print(
                f"warning: no '{query}' result in package '{package}', "
                f"using first result ({results[0]['lcsc_id']}, {results[0].get('package','?')})",
                file=sys.stderr,
            )

    return results[0]["lcsc_id"], results[0].get("package") or ""


def generate_symbol(part_id: str) -> str:
    """Generate a KiCad symbol for part_id using JLC2KiCadLib.

    Returns the path to the generated .kicad_sym file.
    """
    tmp_root = tempfile.mkdtemp(prefix="pin2json_")
    out_dir = os.path.join(tmp_root, "out")
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "JLC2KiCadLib.JLC2KiCadLib",
                part_id,
                "--no_footprint",
                "-dir",
                out_dir,
                "-symbol_lib",
                "tmp",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise RuntimeError(
            f"JLC2KiCadLib failed to generate symbol for {part_id}: {exc.stderr or ''}"
        )

    sym_file = os.path.join(out_dir, "symbol", "tmp.kicad_sym")
    if not os.path.exists(sym_file):
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise RuntimeError(f"Symbol file not created at expected path: {sym_file}")

    return sym_file, tmp_root


def parse_kicad_sym(path: str):
    """Parse a .kicad_sym file and return (symbol_name, pins)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()

    sym_match = re.search(r'\(symbol\s+"([^"]+)"', text)
    symbol_name = sym_match.group(1) if sym_match else None

    pins = []
    pin_re = re.compile(
        r'\(pin\s+(\S+)\s+(\S+).*?'
        r'\(at\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\).*?'
        r'\(length\s+([-\d.]+)\).*?'
        r'\(name\s+"((?:[^"\\]|\\.)*)".*?'
        r'\(number\s+"((?:[^"\\]|\\.)*)".*?'
        r'\)',
        re.DOTALL,
    )

    for m in pin_re.finditer(text):
        elec_type, graphic_type, x, y, angle, length, name, number = m.groups()
        angle_int = int(float(angle)) % 360
        direction = {
            0: "right",
            90: "up",
            180: "left",
            270: "down",
        }.get(angle_int, str(angle_int))

        pins.append(
            {
                "number": number,
                "name": name,
                "electrical_type": elec_type,
                "graphic_type": graphic_type,
                "direction": direction,
                "pos": [float(x), float(y)],
                "length": float(length),
            }
        )

    def sort_key(p):
        try:
            return (0, int(p["number"]))
        except ValueError:
            return (1, p["number"])

    pins.sort(key=sort_key)
    return symbol_name, pins


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Extract pin info from KiCad symbols or JLCPCB part numbers.",
    )
    parser.add_argument(
        "query",
        help="A JLCPCB C-id (Cxxxxx), a .kicad_sym file path, or a component name to search",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Optional output JSON file path",
    )
    parser.add_argument(
        "-p",
        "--package",
        required=True,
        help="Package name filter (e.g. SOT-23-5) — only used when searching JLCPCB",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    query = args.query.strip()
    output_path = args.output
    package_filter = args.package
    cleanup_dir = None
    source = query

    try:
        # --- Resolve the LCSC part id (Cxxxxx) --------------------------------
        if is_part_id(query):
            part_id = query
            resolved_package = ""
        elif os.path.exists(query):
            # Existing file — use directly as a .kicad_sym.
            part_id = None
            sym_file = query
            resolved_package = ""
        else:
            # Treat as a JLCPCB search query.
            part_id, resolved_package = jlc_search(query, package=package_filter)
            source = part_id  # report the C-id as source
            print(
                f"info: searched JLCPCB for '{query}' -> {part_id} ({resolved_package})",
                file=sys.stderr,
            )

        # --- Generate symbol if we have a part_id ----------------------------
        if part_id is not None:
            sym_file, cleanup_dir = generate_symbol(part_id)

        # --- Parse pins -------------------------------------------------------
        symbol_name, pins = parse_kicad_sym(sym_file)

        # Build minimal pin dict: {pin_number: pin_name}.
        pin_dict = {p["number"]: p["name"] for p in pins}

        result = {
            "symbol": clean_symbol_name(symbol_name),
            "package": resolved_package,
            "source": source,
            "pin_count": len(pins),
            "pins": pin_dict,
        }
        json_text = json.dumps(result, indent=2, ensure_ascii=False)

        print(json_text)

        if output_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(json_text + "\n")

    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "source": source,
                    "package": package_filter or None,
                    "symbol": None,
                    "pins": [],
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
