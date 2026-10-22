#!/usr/bin/env python3
"""
PDF Pin Assignment & Multiplexing Extractor — generalized CLI tool.

Default JSON output is the **aligned pinmap** format
(device / source / packages / legend / pinout[] / af{PORT:[]}).
Only the aligned JSON is written — no separate raw schema dump.

Usage:
  # Auto-detect everything -> aligned JSON only
  python extract_tables.py DS_MM32F0140_EN.pdf --chip-name MM32F0140

  # Search mode: only locate target pages, no extraction
  python extract_tables.py DS_MM32F0140_EN.pdf --search

  # Manual overrides
  python extract_tables.py DS_MM32F0140_EN.pdf --pin-pages 32-35 --mux-pages 36-39 --ports PA,PB,PC,PD

  # Per-package Markdown (sorted by pin number) + aligned MD reference
  python extract_tables.py DS_MM32F0140_EN.pdf --chip-name MM32F0140 --package LQFP48 --aligned-md

  # Custom JSON output path
  python extract_tables.py DS_MM32F0140_EN.pdf -o output.json --chip-name MM32F0140
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

# ── Import the aligned-output serializer (same directory) ────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from align_output import (  # noqa: E402
    DEFAULT_LEGEND,
    build_doc,
    parse_pkg_map,
    render_json_text,
    render_md,
    write_lf,
)

# Default pkg-map for MindMotion MM32 series (matches the canonical
# pinmap JSON: lqfp48, lqfp32, qfn32_5x5, qfn32_4x4, tssop20).
# Pass an explicit --pkg-map to override for other vendors.
DEFAULT_MM32_PKG_MAP = (
    "LQFP48=lqfp48,,"
    "LQFP32=lqfp32,,"
    "QFN325x5 mm2=qfn32_5x5|QFN32_5x5|QFN32 5x5 mm2,"
    "QFN324x4 mm2=qfn32_4x4|QFN32_4x4|QFN32 4x4 mm2"
)

# ── UTF-8 output on Windows ───────────────────────────────────────────────
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr.encoding != "utf-8":
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# camelot and pandas are imported lazily inside extraction functions
# so that --search mode works without them on the import path


# ═══════════════════════════════════════════════════════════════════════════
#  Keyword definitions for page search
# ═══════════════════════════════════════════════════════════════════════════

PIN_KEYWORDS = [
    "pin assignment", "pin definition", "pin description",
    "table 4-1", "table 3-1",
    "引脚定义", "管脚分配", "引脚分配", "表 4-1",
]

MUX_KEYWORDS = [
    "multiplexing", "alternate function", "af0", "af1",
    "table 4-2", "复用功能",
    "引脚复用", "复用功能", "交替功能", "port multiplexing",
]


# ═══════════════════════════════════════════════════════════════════════════
#  Text cleaning utilities
# ═══════════════════════════════════════════════════════════════════════════

def normalize(text):
    """Remove stray soft-hyphen / zero-width artifacts from PDF text extraction."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    for ch in ["\xad", "­", "​", "", "‐", "‑"]:
        text = text.replace(ch, "")
    return text


def clean_cell_compact(text):
    """Pin assignment: newlines -> spaces (independent functions stay separate)."""
    text = normalize(text)
    text = text.replace("\r\n", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_cell_slash(text):
    """Pin assignment multiplex/additional columns: join with '/'."""
    text = normalize(text)
    parts = [p for p in re.split(r"\s+", text) if p]
    return "/".join(parts)


def clean_cell_join(text):
    """Multiplexing table cells: remove all line breaks, join to one line."""
    text = normalize(text)
    text = text.replace("\r\n", "").replace("\n", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 1: Page search
# ═══════════════════════════════════════════════════════════════════════════

def search_pages(pdf_path):
    """Scan PDF for pin-assignment and multiplexing pages. Returns (pin_pages, mux_pages)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_path)
    total = len(pdf)

    pin_hits = set()
    mux_hits = set()
    # Pages containing pin names (P[A-H] followed by digits) — used to
    # extend keyword hits across table continuation pages.
    pin_name_pages = set()

    PIN_NAME_RE = re.compile(r"\bP[A-H]\d+\b")
    # Table-header keywords that distinguish table pages from diagram pages
    TABLE_HEADER_KW = ["main function", "multiplex function", "additional function",
                       "i/o level"]

    for i in range(total):
        text = pdf[i].get_textpage().get_text_range()
        text_lower = text.lower()

        if any(kw in text_lower for kw in PIN_KEYWORDS):
            pin_hits.add(i + 1)
        if any(kw in text_lower for kw in MUX_KEYWORDS):
            mux_hits.add(i + 1)
        # Only count pages with BOTH pin names AND table-column headers
        # (avoids pinout diagram pages that have pin names but no table structure)
        if PIN_NAME_RE.search(text) and any(kw in text_lower for kw in TABLE_HEADER_KW):
            pin_name_pages.add(i + 1)

    print(f"Total pages: {total}")
    print(f"Pin keyword hits:     {sorted(pin_hits)}")
    print(f"Pin-name pages:       {sorted(pin_name_pages)}")
    print(f"Multiplexing hits:    {sorted(mux_hits)}")

    # ── Pin assignment: start from keyword hit, extend through pin-name pages ──
    pin_pages = _resolve_table_range(pin_hits, pin_name_pages, total)

    # Extend by one trailing page if it contains table-tail content
    # (e.g. last rows like VDD/VSS pins + footnotes, no pin names like PA0)
    if pin_pages and pin_pages[-1] < total:
        next_text = pdf[pin_pages[-1]].get_textpage().get_text_range().lower()
        if any(kw in next_text for kw in ["i/o", "vdd", "vss", "i = input", "tc:"]):
            pin_pages.append(pin_pages[-1] + 1)
    mux_keyword_pages = mux_hits | {
        p for p in pin_name_pages
        if p not in (set(pin_pages) if pin_pages else set())
    }
    # But be smarter: mux tables have AF0..AFn headers AND pin names.
    af_pages = set()
    for i in range(total):
        text = pdf[i].get_textpage().get_text_range()
        if re.search(r"\bAF[0-9]+\b", text):
            af_pages.add(i + 1)
    af_pin_pages = {
        p for p in af_pages
        if PIN_NAME_RE.search(pdf[p - 1].get_textpage().get_text_range())
    }
    if af_pin_pages:
        mux_pages = _largest_contiguous(af_pin_pages)
    else:
        mux_pages = _largest_contiguous(mux_hits | (af_pages & pin_name_pages))

    if pin_pages:
        print(f"  -> Pin assignment pages: {pin_pages[0]}-{pin_pages[-1]}")
    if mux_pages:
        print(f"  -> Multiplexing pages:   {mux_pages[0]}-{mux_pages[-1]}")

    return pin_pages, mux_pages


def _resolve_table_range(keyword_hits, data_pages, total_pages, max_gap=1):
    """Given keyword-hit pages and pages containing table data (pin names),
    return the full contiguous span of the table.

    Strategy:
      1. Start from the latest keyword hit (datasheet tables appear in later chapters).
      2. Walk backwards/forwards through data_pages to find the full span.
    """
    if not keyword_hits:
        # Fallback: use the largest contiguous block of data pages
        return _largest_contiguous(data_pages)

    # Start from the last keyword hit (most likely the actual table, not TOC)
    candidates = sorted(keyword_hits, reverse=True)
    for start_pg in candidates:
        # Walk backwards to find table start
        lo = start_pg
        while lo > 1 and (lo - 1) in data_pages:
            lo -= 1
        # Walk forwards to find table end
        hi = start_pg
        while hi < total_pages and (hi + 1) in data_pages:
            hi += 1

        span = list(range(lo, hi + 1))
        # Accept if the span is at least 2 pages or contains the keyword page
        if len(span) >= 2:
            return span

    return _largest_contiguous(keyword_hits)


def _largest_contiguous(hits):
    """Find the largest contiguous block in a sorted set of page numbers.
    Tiebreaker: prefer later pages (tables are in later chapters, not TOC)."""
    if not hits:
        return []
    sorted_hits = sorted(hits)
    blocks = []
    current = [sorted_hits[0]]
    for p in sorted_hits[1:]:
        if p == current[-1] + 1:
            current.append(p)
        else:
            blocks.append(current)
            current = [p]
    blocks.append(current)

    # Sort by: (length desc, last_page desc) — prefer bigger blocks, then later ones
    blocks.sort(key=lambda b: (len(b), b[-1]), reverse=True)
    return blocks[0]


def detect_ports(pdf_path, mux_pages):
    """Parse mux page headers to discover port names (e.g. ['PA','PB','PC','PD']).

    Uses two strategies (in page order):
      1. Page header regex: "PX port multiplexing"
      2. Fallback: unique port prefixes from pin names (PA0, PB1, ...) on the page.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_path)
    ports = []
    seen = set()

    # Strategy 1: page header text with explicit "PX port multiplexing"
    for pg in mux_pages:
        text = pdf[pg - 1].get_textpage().get_text_range()
        for m in re.finditer(r"(P[A-H])\s+port\s+multiplexing", text):
            port = m.group(1)
            if port not in seen:
                ports.append(port)
                seen.add(port)

    # Strategy 2: fallback — scan pin names (e.g. PB0, PC13) on each page.
    for pg in mux_pages:
        text = pdf[pg - 1].get_textpage().get_text_range()
        # Collect unique port prefixes from pin-name patterns, preserving
        # page order so port sequence matches the table extraction order.
        page_ports = re.findall(r"\b(P[A-H])\d+\b", text)
        for port in page_ports:
            if port not in seen:
                ports.append(port)
                seen.add(port)

    return ports if ports else None


def detect_mux_cols(pdf_path, mux_pages):
    """Count AF columns from a mux table header. Returns (col_count, col_names, max_af)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_path)
    for pg in mux_pages:
        text = pdf[pg - 1].get_textpage().get_text_range()
        af_matches = re.findall(r"AF(\d+)", text)
        if af_matches:
            max_af = max(int(m) for m in af_matches)
            col_names = ["Pin"] + [f"AF{i}" for i in range(max_af + 1)]
            return len(col_names), col_names, max_af

    return 9, ["Pin"] + [f"AF{i}" for i in range(8)], 8


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 2 & 3: Extraction + Cleaning
# ═══════════════════════════════════════════════════════════════════════════

def _looks_like_pin_assignment(df):
    """Heuristic: a table is a pin-assignment table if its header mentions Name/Main function."""
    header_text = " ".join(str(v).lower() for v in df.iloc[0] if v)
    return ("name" in header_text or "main" in header_text or "multiplex" in header_text)


def _looks_like_mux_table(df):
    """Heuristic: a table is an AF mux table if its header contains AF0."""
    header_text = " ".join(str(v).lower() for v in df.iloc[0] if v)
    return "af0" in header_text


def extract_pin_assignment(pdf_path, pin_pages, flavor):
    """Extract and clean Pin Assignment table from given page range."""
    import camelot
    import pandas as pd

    page_range = f"{pin_pages[0]}-{pin_pages[-1]}"
    print(f"Extracting Pin Assignment table (pages {page_range})...", file=sys.stderr)

    tables = camelot.read_pdf(pdf_path, pages=page_range, flavor=flavor)
    print(f"  Found {len(tables)} fragments", file=sys.stderr)

    if not tables:
        print("  WARNING: No tables found. Try --flavor stream or widen page range.", file=sys.stderr)
        return pd.DataFrame()

    # Keep only fragments that look like pin-assignment tables.
    pin_tables = [t for t in tables if _looks_like_pin_assignment(t.df)]
    if not pin_tables:
        print("  WARNING: No pin-assignment tables matched heuristic. Using all fragments.", file=sys.stderr)
        pin_tables = tables

    # Derive header from the first matched table.
    header = [clean_cell_compact(str(v)) for v in pin_tables[0].df.iloc[0]]
    slash_cols = {"Multiplex function", "Additional function"}

    fragments = []
    for t in pin_tables:
        df = t.df.copy()
        if df.shape[1] != len(header):
            print(f"  Skipping fragment with {df.shape[1]} columns (expected {len(header)}).",
                  file=sys.stderr)
            continue
        df.columns = header
        df = df.iloc[1:].copy()
        for col in df.columns:
            df[col] = df[col].apply(clean_cell_compact)
        for col in slash_cols.intersection(df.columns):
            df[col] = df[col].apply(lambda x: clean_cell_slash(x) if str(x).strip() else "")
        fragments.append(df)

    if not fragments:
        print("  WARNING: No usable pin-assignment fragments after filtering.", file=sys.stderr)
        return pd.DataFrame()

    df = pd.concat(fragments, ignore_index=True)
    df = df[~(df == "").all(axis=1)]
    df = df.reset_index(drop=True)
    return df


def extract_multiplexing(pdf_path, mux_pages, port_names, mux_col_names, flavor):
    """Extract and clean Pin Multiplexing tables, one per port."""
    import camelot

    page_range = f"{mux_pages[0]}-{mux_pages[-1]}"
    print(f"Extracting Pin Multiplexing tables (pages {page_range})...", file=sys.stderr)

    tables = camelot.read_pdf(pdf_path, pages=page_range, flavor=flavor)
    print(f"  Found {len(tables)} tables", file=sys.stderr)

    # Keep only fragments that look like AF mux tables.
    mux_tables = [t for t in tables if _looks_like_mux_table(t.df)]
    if not mux_tables:
        print("  WARNING: No AF mux tables matched heuristic. Using all fragments.", file=sys.stderr)
        mux_tables = tables

    if len(mux_tables) < len(port_names):
        print(f"  WARNING: Expected {len(port_names)} tables but found {len(mux_tables)}. "
              f"Some ports may be missing.", file=sys.stderr)

    result = {}
    for name, t in zip(port_names, mux_tables):
        df = t.df.copy()
        if df.shape[1] == len(mux_col_names):
            df.columns = mux_col_names
        else:
            df.columns = [clean_cell_compact(str(c)) for c in df.columns]

        for col in df.columns:
            df[col] = df[col].apply(clean_cell_join)

        header_values = list(df.columns)
        mask = df.apply(
            lambda row: all(str(c).strip() == h for c, h in zip(row, header_values)), axis=1
        )
        df = df[~mask]
        df = df[~(df == "").all(axis=1)]
        df = df.reset_index(drop=True)
        result[name] = df

    return result


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 4: Output builders
# ═══════════════════════════════════════════════════════════════════════════

def df_to_markdown(df, title):
    """Convert a DataFrame to a GitHub-flavored Markdown table block."""
    lines = [f"## {title}", ""]
    if df.empty:
        lines.append("*No data extracted.*")
        lines.append("")
        return "\n".join(lines)

    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep = "|" + "|".join([" --- " for _ in df.columns]) + "|"
    lines.append(header)
    lines.append(sep)
    for _, row in df.iterrows():
        cells = [str(v) if str(v).strip() else " " for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def _col_starting(row, prefix):
    """Return first cell whose column name starts with prefix (case-insensitive)."""
    prefix_lower = prefix.lower()
    for col in row.index:
        if str(col).lower().strip().startswith(prefix_lower):
            return str(row.get(col, "")).strip()
    return ""


def clean_package_name(name):
    """Normalize package column names, e.g. 'TSSOP 20' -> 'TSSOP20', 'SOP8 - type 0' -> 'SOP8-type0'."""
    name = normalize(name)
    # Unify dash-like characters
    for ch in ["–", "—", "−", "‐", "‑", "—", "―"]:
        name = name.replace(ch, "-")
    # Drop any other non-alphanumeric chars except hyphen
    name = re.sub(r"[^A-Za-z0-9\s\-]", "", name)
    # Compact whitespace and remove spaces around hyphens / before digits
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\s*-\s*", "-", name)
    name = re.sub(r"\s+(\d)", r"\1", name)
    return name


def build_json(pin_df, mux, chip_name, pdf_basename, max_af):
    """Build a structured JSON dict from cleaned pin/mux DataFrames."""
    non_pkg_prefixes = (
        "name", "type", "i/o level", "main function",
        "multiplex function", "additional function",
    )
    raw_packages = [
        c for c in pin_df.columns
        if not any(c.lower().strip().startswith(p) for p in non_pkg_prefixes)
    ]
    packages = [clean_package_name(p) for p in raw_packages]
    raw_to_clean = dict(zip(raw_packages, packages))

    mux_lookup = {}
    for port, df in mux.items():
        for _, row in df.iterrows():
            pin = str(row.get("Pin", "")).strip()
            if not pin:
                continue
            mux_lookup[pin] = {
                col: (str(v).strip() if str(v).strip() else None)
                for col, v in row.items() if col != "Pin"
            }

    pins = []
    for _, row in pin_df.iterrows():
        raw_name = str(row.get("Name", "")).strip()
        m = re.match(r"(P[A-H]\d+)", raw_name)
        base_name = m.group(1) if m else raw_name

        pkg_nums = {}
        for raw_pkg in raw_packages:
            raw = str(row.get(raw_pkg, "")).strip()
            num = re.search(r"\d+", raw)
            pkg_nums[raw_to_clean[raw_pkg]] = int(num.group(0)) if num else None

        mux_str = _col_starting(row, "multiplex function")
        add_str = _col_starting(row, "additional function")
        main_function = _col_starting(row, "main function")
        if not main_function and m:
            main_function = base_name

        pins.append({
            "name": raw_name,
            "type": _col_starting(row, "type"),
            "io_level": _col_starting(row, "i/o level"),
            "main_function": main_function,
            "multiplex_functions": [f.strip() for f in mux_str.split("/") if f.strip()] if mux_str else [],
            "additional_functions": [f.strip() for f in add_str.split("/") if f.strip()] if add_str else [],
            "packages": pkg_nums,
            "alternate_functions": mux_lookup.get(base_name, {}),
        })

    return {
        "chip_name": chip_name,
        "source_pdf": pdf_basename,
        "packages": packages,
        "ports": [p for p in sorted(mux.keys())],
        "pins": pins,
        "metadata": {
            "extraction_date": datetime.now().isoformat(),
            "extractor_version": "1.2.0",
        },
    }


def write_package_markdown(data, package, out_path):
    """Write a compact package-specific Markdown table sorted by ascending pin number.

    Format follows the aligned-MD reference (render_md in align_output.py):
    compact pipes, blank empty cells, '/'-joined function lists, and no
    AF0..AFn columns (AF detail lives in the aligned MD / JSON).
    """
    pins = []
    for p in data["pins"]:
        raw = p["packages"].get(package)
        if raw is None:
            continue
        try:
            pin_num = int(raw)
        except (TypeError, ValueError):
            continue
        pins.append({**p, "package_pin": pin_num})
    pins.sort(key=lambda x: x["package_pin"])

    lines = [
        f"# {data['chip_name']} Pin Assignment and Multiplexing — {package}",
        "",
        "|Pin|Name|Type|I/O level|Main function|Multiplex function|Additional function|",
        "|-|-|-|-|-|-|-|",
    ]

    if not pins:
        lines.append("*No pins found for this package.*")
        Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return

    def cell(v):
        return " " if v is None or str(v).strip() == "" else str(v)

    for p in pins:
        row = [
            str(p["package_pin"]),
            p["name"].replace(" ", "/"),
            cell(p["type"]),
            cell(p["io_level"]),
            cell(p["main_function"]),
            cell("/".join(p["multiplex_functions"])),
            cell("/".join(p["additional_functions"])),
        ]
        lines.append("|" + "|".join(row) + "|")

    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Extract Pin Assignment & Multiplexing tables from MCU PDF datasheets"
    )
    parser.add_argument("pdf", help="Path to the PDF datasheet")
    parser.add_argument("--search", action="store_true",
                        help="Only search for target pages, do not extract")
    parser.add_argument("--pin-pages", type=str, default=None,
                        help="Pin assignment page range (e.g. '32-35')")
    parser.add_argument("--mux-pages", type=str, default=None,
                        help="Multiplexing page range (e.g. '36-39')")
    parser.add_argument("--ports", type=str, default=None,
                        help="Port names, comma-separated (e.g. 'PA,PB,PC,PD')")
    parser.add_argument("--mux-cols", type=int, default=None,
                        help="Number of columns in mux table (e.g. 9 for AF0-AF8)")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Output JSON file path (default: <chip-name>_Pin_Assignment_and_Multiplexing.json)")
    parser.add_argument("--chip-name", type=str, default=None,
                        help="Chip model name for the document title")
    parser.add_argument("--flavor", type=str, default="lattice",
                        choices=["lattice", "stream"],
                        help="Camelot extraction flavor (default: lattice)")
    parser.add_argument("--package", type=str, default=None,
                        help="Generate a package-specific Markdown file for this package "
                             "(e.g. 'TSSOP20'). Sorted by ascending pin number.")
    parser.add_argument("--aligned-md", action="store_true",
                        help="Also write the aligned Markdown reference "
                             "(<chip>_Pin_Assignment_and_Multiplexing.md)")
    parser.add_argument("--pkg-map", type=str, default=DEFAULT_MM32_PKG_MAP,
                        help="Package name mapping (default: MM32 conventions). "
                             "Format: 'RAWNAME=json_key|PkgName|Display,...'")
    parser.add_argument("--legend-type", type=str, default=DEFAULT_LEGEND["type"],
                        help="Legend text for the 'type' field")
    parser.add_argument("--legend-io", type=str, default=DEFAULT_LEGEND["io_level"],
                        help="Legend text for the 'io_level' field")
    args = parser.parse_args()

    # ── Search mode ──────────────────────────────────────────────────────
    pin_pages, mux_pages = search_pages(args.pdf)

    if args.search:
        return

    # ── Resolve page ranges ──────────────────────────────────────────────
    def _parse_range(s):
        if s is None:
            return None
        parts = s.split("-")
        start, end = int(parts[0]), int(parts[-1])
        return list(range(start, end + 1))

    pin_range = _parse_range(args.pin_pages) or pin_pages
    mux_range = _parse_range(args.mux_pages) or mux_pages

    if not pin_range:
        print("ERROR: Could not determine pin assignment pages. Use --pin-pages.", file=sys.stderr)
        sys.exit(1)
    if not mux_range:
        print("ERROR: Could not determine multiplexing pages. Use --mux-pages.", file=sys.stderr)
        sys.exit(1)

    # ── Resolve ports ────────────────────────────────────────────────────
    port_names = args.ports.split(",") if args.ports else detect_ports(args.pdf, mux_range)
    if not port_names:
        print("ERROR: Could not detect port names. Use --ports.", file=sys.stderr)
        sys.exit(1)
    print(f"  Ports: {port_names}", file=sys.stderr)

    # ── Resolve mux column count / names ─────────────────────────────────
    if args.mux_cols:
        mux_col_count = args.mux_cols
        # max_af is the highest AF index (e.g. 8 for AF0..AF8, not the count)
        max_af = mux_col_count - 2   # Pin column occupies 1 slot
        mux_col_names = ["Pin"] + [f"AF{i}" for i in range(max_af + 1)]
    else:
        mux_col_count, mux_col_names, max_af = detect_mux_cols(args.pdf, mux_range)
    print(f"  Mux columns: {mux_col_count} (Pin + AF0..AF{max_af})", file=sys.stderr)

    # ── Chip name ────────────────────────────────────────────────────────
    chip_name = args.chip_name or "MCU"
    pdf_basename = args.pdf.replace("\\", "/").split("/")[-1]

    # ── Output path ──────────────────────────────────────────────────────
    json_path = args.output or f"{chip_name}_Pin_Assignment_and_Multiplexing.json"

    # ── Extract ──────────────────────────────────────────────────────────
    pin_df = extract_pin_assignment(args.pdf, pin_range, args.flavor)
    mux = extract_multiplexing(args.pdf, mux_range, port_names, mux_col_names, args.flavor)

    # ── Build raw extraction JSON (kept in memory for downstream writers) ──
    data = build_json(pin_df, mux, chip_name, pdf_basename, max_af)

    # ── Build aligned pinmap doc (default JSON output) ────────────────────
    pkg_map = parse_pkg_map(args.pkg_map)
    ports = args.ports.split(",") if args.ports else port_names
    legend = {"type": args.legend_type, "io_level": args.legend_io}
    doc = build_doc(data, pkg_map, ports, legend)

    # ── Write aligned JSON (default) ─────────────────────────────────────
    write_lf(json_path, render_json_text(doc))
    print(f"Wrote {json_path}", file=sys.stderr)

    # ── Optional aligned Markdown reference ──────────────────────────────
    if args.aligned_md:
        md_path = str(Path(json_path).with_suffix(".md"))
        write_lf(md_path, render_md(doc))
        print(f"Wrote {md_path}", file=sys.stderr)

    # ── Optional package-specific Markdown (uses raw data) ───────────────
    if args.package:
        safe_suffix = re.sub(r"[^A-Za-z0-9_\-]", "_", args.package)
        md_path = str(Path(json_path).with_suffix(f".{safe_suffix}.md"))
        write_package_markdown(data, args.package, md_path)
        print(f"Wrote {md_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
