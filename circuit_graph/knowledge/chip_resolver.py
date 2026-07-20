"""Chip / IC resolution engine — JLCPCB lookup, symbol generation, pin parsing.

The primary module for turning a part number (e.g. ``"LM321"``) or LCSC id
(``"C9405"``) into structured pin data with semantic classification.

Resolution paths:

1. **JLCPCB search** — given a part number and optionally a package, search
   JLCPCB via the ``jlc`` CLI, generate a KiCad symbol with ``JLC2KiCadLib``,
   and parse the pin names from the symbol file.

2. **LCSC id** — given a ``Cxxxxx`` id, generate the symbol directly and parse
   pins (no JLCPCB search needed).

3. **Batch** — resolve multiple part numbers in a single ``JLC2KiCadLib``
   invocation.

Includes :func:`classify_pin_types` for semantic pin categorisation
(power_in, ground, comm, gpio, …).

The ``jlc`` CLI (``@jlcpcb/cli`` npm package) is required for path 1 but not
for paths 2/3.  Every public function returns ``None`` on failure so callers can
treat resolution as best-effort.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# KiCad symbol name cleaning
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# JLCPCB CLI helpers
# ---------------------------------------------------------------------------


def _jlc_cmd() -> str:
    """Return the ``jlc`` CLI command name, platform-aware."""
    if os.name == "nt":
        cmd = shutil.which("jlc.cmd")
        if cmd:
            return cmd
    return "jlc"


def jlc_search(
    query: str,
    package: str | None = None,
    limit: int = 1,
    *,
    in_stock: bool = False,
) -> tuple[str, str]:
    """Search JLCPCB for *query* via the ``jlc`` CLI.

    Returns ``(lcsc_id, package_name)`` of the first matching result.

    When *package* is given, only results whose ``package`` field contains
    *package* (case-insensitive substring) are considered; if none match,
    the first result overall is used and a warning is emitted to stderr.

    *in_stock* filters to in-stock parts only.  Defaults to ``False`` so
    batch resolvers see the full catalogue — many legitimate ICs are out of
    stock on JLCPCB but still ship from distributors.  Interactive callers
    that genuinely want only in-stock results should pass ``in_stock=True``.

    Raises ``RuntimeError`` on failure or empty result set.
    """
    cmd = [
        _jlc_cmd(),
        "search",
        query,
        "--limit",
        str(limit),
        "--json",
    ]
    if in_stock:
        cmd.insert(2, "--in-stock")
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
        stock_note = "in-stock only, " if in_stock else ""
        raise RuntimeError(
            f"No JLCPCB results found for '{query}' ({stock_note}{limit} max)"
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


# ---------------------------------------------------------------------------
# KiCad symbol generation + parsing
# ---------------------------------------------------------------------------


def generate_symbol(part_id: str) -> tuple[str, str]:
    """Generate a KiCad symbol for *part_id* using JLC2KiCadLib.

    Returns ``(symbol_file_path, temp_dir_path)``.
    The caller is responsible for cleaning up *temp_dir_path*.
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


def parse_kicad_sym(path: str | Path) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Parse a .kicad_sym file and return ``(symbol_name, pins)``.

    Each pin dict has keys: ``number``, ``name``, ``electrical_type``,
    ``graphic_type``, ``direction``, ``pos`` (x, y), ``length``.
    """
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


# ---------------------------------------------------------------------------
# Pin type classification
# ---------------------------------------------------------------------------

# Ordered (pin_type, [patterns]) list. First match wins against the
# UPPER-cased pin name.  Each pattern matches on exact equality OR on
# prefix-with-underscore/slash (e.g. "PA0" matches "PA0_ADC_IN").
_PIN_CLASSIFIERS: list[tuple[str, list[str]]] = [
    ("power_in", [
        "VCC", "VDD", "V+", "VBUS", "AVDD", "VIN", "PVDD",
        "VDDIO", "VDDA", "VCCIO", "VDD_CORE", "VDDQ",
        "VS", "VDRV", "VBIAS", "VCAP",
    ]),
    ("power_out", [
        "VOUT", "SW", "LX", "BST", "PHASE", "VSW",
        "LDO_OUT", "DRV", "HG", "LG", "BOOT", "VREG", "VREF",
    ]),
    ("ground", [
        "GND", "VSS", "AGND", "PGND", "DGND", "COM",
        "VSSA", "EP", "PAD", "GNDD", "GNDA",
    ]),
    ("input", [
        "+IN", "IN+", "IN-", "-IN", "INA", "INB",
        "FB", "COMP", "EN", "SHDN", "RUN",
        "TRIG", "THRES", "CV", "ADJ", "VSENSE",
        "CS", "ISP", "ISN", "ILIM", "SS",
    ]),
    ("output", [
        "OUT", "Q", "DOUT", "GATE", "SOURCE", "DRAIN",
    ]),
    ("gpio", [
        "PA0", "PA1", "PA2", "PA3", "PA4", "PA5", "PA6", "PA7",
        "PA8", "PA9", "PA10", "PA11", "PA12", "PA13", "PA14", "PA15",
        "PB0", "PB1", "PB2", "PB3", "PB4", "PB5", "PB6", "PB7",
        "PB8", "PB9", "PB10", "PB11", "PB12", "PB13", "PB14", "PB15",
        "PC0", "PC1", "PC2", "PC3", "PC4", "PC5", "PC6", "PC7",
        "PC8", "PC9", "PC10", "PC11", "PC12", "PC13", "PC14", "PC15",
        "GPIO", "GPIO0", "GPIO1",
    ]),
    ("comm", [
        "SDA", "SCL", "TX", "RX", "TXD", "RXD",
        "MOSI", "MISO", "SCK", "SS", "NSS",
        "CANH", "CANL", "CAN_RX", "CAN_TX",
        "USB_DP", "USB_DN", "DP", "DN",
        "ETH_TXP", "ETH_TXN", "ETH_RXP", "ETH_RXN",
        "SD_CLK", "SD_CMD", "SD_D0", "SD_D1", "SD_D2", "SD_D3",
    ]),
    ("clock", [
        "CLK", "CLKIN", "CLKOUT", "OSC", "OSC_IN", "OSC_OUT",
        "XTAL", "XTAL_IN", "XTAL_OUT", "XIN", "XOUT",
        "HSE", "LSE", "MCO",
    ]),
    ("nc", [
        "NC", "DNP", "RESERVED",
    ]),
]


def _classify_single_pin(name: str) -> str:
    """Classify one pin name string to a pin-type category.

    First-match wins against the ordered ``_PIN_CLASSIFIERS`` rules.
    Falls back to ``"unknown"`` when no classifier matches.
    """
    upper = name.upper().strip()
    for pin_type, patterns in _PIN_CLASSIFIERS:
        for pattern in patterns:
            if upper == pattern:
                return pin_type
            if upper.startswith(pattern + "_") or upper.startswith(pattern + "/"):
                return pin_type
    return "unknown"


def classify_pin_types(pins: dict[str, str]) -> dict[str, str]:
    """Classify a ``{pin_number: pin_name}`` dict into ``{pin_number: pin_type}``.

    Every pin receives a type (minimum ``"unknown"``), so the output dict
    always has the same keys as the input dict.
    """
    return {num: _classify_single_pin(name) for num, name in pins.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_lcsc_id(arg: str) -> bool:
    """Return True if *arg* looks like a JLCPCB/LCSC part id (Cxxxxx)."""
    return bool(arg) and arg[0].upper() == "C" and arg[1:].isdigit()


def resolve_lcsc(lcsc_id: str) -> Optional[dict[str, Any]]:
    """Resolve a ``Cxxxxx`` id to pin data via JLC2KiCadLib.

    Returns the pin2json result dict on success, ``None`` on any failure.
    """
    try:
        sym_file, cleanup = generate_symbol(lcsc_id)
    except (RuntimeError, OSError) as exc:
        print(f"[pin2json] resolve_lcsc({lcsc_id}) failed: {exc}", file=sys.stderr)
        return None

    try:
        symbol_name, pins = parse_kicad_sym(sym_file)
    except (OSError, re.error) as exc:
        print(f"[pin2json] parse_lcsc({lcsc_id}) parse failed: {exc}", file=sys.stderr)
        shutil.rmtree(os.path.dirname(cleanup), ignore_errors=True)
        return None

    shutil.rmtree(cleanup, ignore_errors=True)

    pin_dict = {p["number"]: p["name"] for p in pins}
    return {
        "symbol": clean_symbol_name(symbol_name) if symbol_name else lcsc_id,
        "package": "",
        "source": lcsc_id,
        "pin_count": len(pins),
        "pins": pin_dict,
        "pin_types": classify_pin_types(pin_dict),
    }


def resolve_part(part_number: str, package: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Resolve a part number to pin data via JLCPCB search + symbol generation.

    Returns the pin2json result dict on success, ``None`` on any failure.
    """
    try:
        lcsc_id, resolved_package = jlc_search(part_number, package=package)
    except RuntimeError as exc:
        print(f"[pin2json] resolve_part({part_number}) search failed: {exc}", file=sys.stderr)
        return None

    try:
        sym_file, cleanup = generate_symbol(lcsc_id)
    except RuntimeError as exc:
        print(f"[pin2json] resolve_part({part_number}) symbol failed: {exc}", file=sys.stderr)
        return None

    try:
        symbol_name, pins = parse_kicad_sym(sym_file)
    except (OSError, re.error) as exc:
        print(f"[pin2json] resolve_part({part_number}) parse failed: {exc}", file=sys.stderr)
        shutil.rmtree(cleanup, ignore_errors=True)
        return None

    shutil.rmtree(cleanup, ignore_errors=True)

    pin_dict = {p["number"]: p["name"] for p in pins}
    return {
        "symbol": clean_symbol_name(symbol_name) if symbol_name else part_number,
        "package": resolved_package,
        "source": lcsc_id,
        "pin_count": len(pins),
        "pins": pin_dict,
        "pin_types": classify_pin_types(pin_dict),
    }


# ---------------------------------------------------------------------------
# Batch resolution
# ---------------------------------------------------------------------------


def generate_symbols(part_ids: list[str]) -> tuple[str, str]:
    """Generate KiCad symbols for multiple *part_ids* in one JLC2KiCadLib call.

    JLC2KiCadLib writes all symbols into a single ``{lib}.kicad_sym`` file.
    Returns ``(combined_sym_path, cleanup_dir)``.

    The caller **must** call ``shutil.rmtree(cleanup_dir, ignore_errors=True)``
    after processing all symbols from the combined file.

    Raises :exc:`RuntimeError` on failure.
    """
    if not part_ids:
        raise ValueError("generate_symbols requires at least one part_id")

    tmp_root = tempfile.mkdtemp(prefix="pin2json_batch_")
    out_dir = os.path.join(tmp_root, "out")
    try:
        subprocess.run(
            [sys.executable, "-m", "JLC2KiCadLib.JLC2KiCadLib"]
            + part_ids
            + ["--no_footprint", "-dir", out_dir, "-symbol_lib", "tmp"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise RuntimeError(
            f"JLC2KiCadLib failed for {len(part_ids)} part(s): {exc.stderr or ''}"
        )

    sym_file = os.path.join(out_dir, "symbol", "tmp.kicad_sym")
    if not os.path.exists(sym_file):
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise RuntimeError(f"Symbol file not created at expected path: {sym_file}")

    return sym_file, tmp_root


def parse_all_symbols(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Parse ALL top-level symbol definitions from a combined ``.kicad_sym`` file.

    Returns ``{cleaned_symbol_name: [pin_dict, ...]}`` where each pin dict
    has the same keys as :func:`parse_kicad_sym`.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()

    result: dict[str, list[dict[str, Any]]] = {}
    pin_re = re.compile(
        r'\(pin\s+(\S+)\s+(\S+).*?'
        r'\(at\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\).*?'
        r'\(length\s+([-\d.]+)\).*?'
        r'\(name\s+"((?:[^"\\]|\\.)*)".*?'
        r'\(number\s+"((?:[^"\\]|\\.)*)".*?'
        r'\)',
        re.DOTALL,
    )

    idx = 0
    while True:
        m = re.search(r'\(symbol\s+"([^"]+)"', text[idx:])
        if not m:
            break
        sym_name = clean_symbol_name(m.group(1))
        block_start = idx + m.start()
        # Find matching close paren for this top-level symbol block.
        depth = 0
        block_end = block_start
        for i in range(block_start, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    block_end = i + 1
                    break

        sym_text = text[block_start:block_end]
        pins = []
        for pm in pin_re.finditer(sym_text):
            elec_type, graphic_type, x, y, angle, length, name, number = pm.groups()
            angle_int = int(float(angle)) % 360
            direction = {
                0: "right", 90: "up", 180: "left", 270: "down",
            }.get(angle_int, str(angle_int))
            pins.append({
                "number": number,
                "name": name,
                "electrical_type": elec_type,
                "graphic_type": graphic_type,
                "direction": direction,
                "pos": [float(x), float(y)],
                "length": float(length),
            })

        if pins:

            def _batch_sort(p):
                try:
                    return (0, int(p["number"]))
                except ValueError:
                    return (1, p["number"])
            pins.sort(key=_batch_sort)
            result[sym_name] = pins

        idx = block_end

    return result


def resolve_parts_batch(
    part_numbers: list[tuple[str, Optional[str]]],
) -> list[Optional[dict[str, Any]]]:
    """Resolve multiple part numbers to pin data in batch.

    Reduces JLC2KiCadLib subprocess invocations from *N* to 1 and shares a
    single temp directory across all resolutions.

    Returns a list in the same order as *part_numbers*, with ``None`` for
    parts that failed to resolve.

    The returned dicts include ``pin_types`` from :func:`classify_pin_types`.
    """
    # Step 1 — JLCPCB search per part (required for LCSC id discovery).
    lcsc_pairs: list[tuple[int, str, str]] = []  # (index, lcsc_id, package)
    for idx, (pn, pkg) in enumerate(part_numbers):
        if not pn:
            continue
        try:
            lcsc_id, resolved_pkg = jlc_search(pn, package=pkg)
            lcsc_pairs.append((idx, lcsc_id, resolved_pkg))
        except RuntimeError as exc:
            print(f"[pin2json] resolve_part({pn}) search failed: {exc}", file=sys.stderr)

    if not lcsc_pairs:
        return [None] * len(part_numbers)

    # Step 2 — Batch symbol generation (single subprocess), with per-part
    # fallback when the batch invocation fails (e.g. one LCSC id is a new
    # arrival that EasyEDA doesn't recognise yet).
    all_lcsc_ids = [lp[1] for lp in lcsc_pairs]
    all_symbols: dict[str, list[dict[str, str]]] = {}
    try:
        sym_file, cleanup = generate_symbols(all_lcsc_ids)
        all_symbols = parse_all_symbols(sym_file)
        shutil.rmtree(cleanup, ignore_errors=True)
    except RuntimeError as exc:
        print(f"[pin2json] batch symbol generation failed: {exc}", file=sys.stderr)
        # Retry one-by-one so a single bad part doesn't block the rest.
        print(
            f"[pin2json] retrying {len(all_lcsc_ids)} part(s) individually …",
            file=sys.stderr,
        )
        for lcsc_id in all_lcsc_ids:
            try:
                sym_file, cleanup = generate_symbols([lcsc_id])
                symbols = parse_all_symbols(sym_file)
                for name, pins in symbols.items():
                    if name not in all_symbols:
                        all_symbols[name] = pins
                shutil.rmtree(cleanup, ignore_errors=True)
            except Exception as exc2:
                print(
                    f"[pin2json] individual generation failed for "
                    f"{lcsc_id}: {exc2}",
                    file=sys.stderr,
                )

    # Step 4 — Match each LCSC id back to its symbol and build results.
    #
    # JLC2KiCadLib names symbols by **part number**, not LCSC id, and may
    # emit multiple variants per LCSC id (different package suffixes,
    # split units with ``_0_1``/``_0_2`` suffixes, etc.).  We anchor on
    # the input part number — case-insensitively — preferring an exact
    # match, then a symbol whose name starts with the part number, then
    # any symbol that contains the part number as a token (skipping
    # alternate-unit symbols when a primary exists).
    results: list[Optional[dict[str, Any]]] = [None] * len(part_numbers)
    for idx, lcsc_id, pkg in lcsc_pairs:
        target_part = (part_numbers[idx][0] or "").strip().upper()
        target_lcsc = lcsc_id.upper()
        primary = None
        fallback = None
        for sym_name, pins in all_symbols.items():
            upper = sym_name.upper()
            if target_part and upper == target_part:
                primary = (sym_name, pins)
                break
            if target_part and upper.startswith(target_part):
                if primary is None:
                    primary = (sym_name, pins)
            elif (
                (target_part and target_part in upper)
                or upper == target_lcsc
                or target_lcsc in upper
            ) and "_0_" not in sym_name:
                if fallback is None:
                    fallback = (sym_name, pins)
        chosen = primary or fallback
        if chosen is None:
            print(
                f"[pin2json] no symbol match for LCSC id {lcsc_id} "
                f"(part {part_numbers[idx][0]})",
                file=sys.stderr,
            )
            continue
        sym_name, pins = chosen
        pin_dict = {p["number"]: p["name"] for p in pins}
        results[idx] = {
            "symbol": sym_name,
            "package": pkg,
            "source": lcsc_id,
            "pin_count": len(pins),
            "pins": pin_dict,
            "pin_types": classify_pin_types(pin_dict),
        }

    shutil.rmtree(cleanup, ignore_errors=True)
    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_USAGE = """\
pin2json [--lcsc] <query|C-id|path.kicad_sym> [output.json]

Resolve a part number or LCSC id to pin JSON.

Options:
  --lcsc          Treat the argument as a Cxxxxx id (no JLCPCB search).
  -p, --package   Package name filter (only used for part-number search).

Examples:
  pin2json LM321 -p SOT-23-5     → search JLCPCB and resolve
  pin2json C9405                  → resolve C-id directly
  pin2json --lcsc C9405           → resolve C-id directly
  pin2json path/to/sym.kicad_sym  → parse existing symbol file
"""


def _parse_cli_args(argv: list[str]) -> Optional[dict[str, Any]]:
    """Parse CLI arguments and return a result dict or ``None`` on error."""
    if not argv:
        print(_USAGE, file=sys.stderr)
        return None

    is_lcsc = False
    package = None
    positional: list[str] = []

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--lcsc":
            is_lcsc = True
        elif arg in ("-p", "--package") and i + 1 < len(argv):
            i += 1
            package = argv[i]
        elif arg.startswith("-"):
            print(f"Unknown option: {arg}", file=sys.stderr)
            print(_USAGE, file=sys.stderr)
            return None
        else:
            positional.append(arg)
        i += 1

    if not positional:
        print("Error: missing query argument", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return None

    query = positional[0].strip()
    output_path = positional[1] if len(positional) > 1 else None

    # Route.
    if is_lcsc or is_lcsc_id(query):
        payload = resolve_lcsc(query)
    elif os.path.exists(query):
        # Existing file — parse directly.
        try:
            symbol_name, pins = parse_kicad_sym(query)
        except (OSError, re.error) as exc:
            print(f"Error parsing {query}: {exc}", file=sys.stderr)
            return None
        pin_dict = {p["number"]: p["name"] for p in pins}
        payload = {
            "symbol": clean_symbol_name(symbol_name) if symbol_name else query,
            "package": "",
            "source": query,
            "pin_count": len(pins),
            "pins": pin_dict,
        }
    else:
        payload = resolve_part(query, package=package)

    if payload is None:
        return None

    json_text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(json_text)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json_text + "\n", encoding="utf-8")

    return payload


def _parse_cli() -> None:
    """Thin entry point for ``pyproject.toml`` ``[project.scripts]``."""
    import sys

    result = _parse_cli_args(sys.argv[1:])
    if result is None:
        sys.exit(1)


# ---------------------------------------------------------------------------
# __main__ support
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _parse_cli()


__all__ = [
    "resolve_part",
    "resolve_lcsc",
    "resolve_parts_batch",
    "parse_kicad_sym",
    "parse_all_symbols",
    "generate_symbol",
    "generate_symbols",
    "clean_symbol_name",
    "is_lcsc_id",
    "jlc_search",
    "classify_pin_types",
]
