#!/usr/bin/env python3
"""
Verify the output Markdown from extract_tables.py.

Checks:
  1. File exists & non-empty
  2. Pin Assignment table: column count, row count, no artifact residue
  3. Multiplexing tables: one per expected port, correct AF columns
  4. Cross-validation: pins in assignment appear in their port's mux table
  5. Artifact scan: soft hyphens, embedded CR/LF inside cells
  6. Summary statistics

Usage:
  python verify_output.py MM32F0140_Pin_Assignment_and_Multiplexing.md
  python verify_output.py MM32F0140_Pin_Assignment_and_Multiplexing.md \
      --ports PA,PB,PC,PD --mux-cols 10
"""

import argparse
import re
import sys

# ── UTF-8 output on Windows ─────────────────────────────────────────────────
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


# ═══════════════════════════════════════════════════════════════════════════════
#  Markdown table parser
# ═══════════════════════════════════════════════════════════════════════════════

def parse_md_tables(text):
    """Parse all GitHub-flavored Markdown tables from text.

    Returns a list of dicts:
      { 'h2': str,           # the preceding ## heading (if any)
        'columns': [str],    # header column names
        'rows': [[str]],     # data rows (each row is a list of cell strings)
        'line_start': int }  # line number where the table header starts (1-indexed)
    """
    lines = text.split("\n")
    tables = []
    current_h2 = None
    i = 0
    while i < len(lines):
        # Track H1/H2 headings
        if lines[i].startswith("# "):
            current_h2 = None
            i += 1
            continue
        if lines[i].startswith("## "):
            current_h2 = lines[i][3:].strip()
            i += 1
            continue

        # Detect table header: line with pipes, colon characters optional
        stripped = lines[i].strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            i += 1
            continue

        # Check next line is a separator (| --- | --- |)
        if i + 1 >= len(lines):
            i += 1
            continue
        sep = lines[i + 1].strip()
        if not re.match(r"^\|[\s\-:]+\|", sep):
            i += 1
            continue

        header_cells = [c.strip() for c in stripped[1:-1].split("|")]
        col_count = len(header_cells)

        header_line = i + 1  # 1-indexed
        i += 2  # skip header + separator

        rows = []
        while i < len(lines):
            row_line = lines[i].strip()
            if not row_line.startswith("|") or not row_line.endswith("|"):
                break
            cells = [c.strip() for c in row_line[1:-1].split("|")]
            # Pad or trim to match header column count
            if len(cells) < col_count:
                cells.extend([""] * (col_count - len(cells)))
            elif len(cells) > col_count:
                cells = cells[:col_count]
            rows.append(cells)
            i += 1

        tables.append({
            "h2": current_h2,
            "columns": header_cells,
            "rows": rows,
            "line_start": header_line,
        })
        # i already advanced past table rows; continue scanning

    return tables


def normalize_to_legacy_format(tables, expected_ports=None):
    """Convert the new single-table Markdown format into the legacy
    multi-table layout (one Pin Assignment table + one mux table per port).

    The new format has one H1 heading and a single table with columns:
      Pin | Name | Type | IO Level | Main Function | Multiplex Functions |
      Additional Functions | AF0 | AF1 | ... | AFn

    Returns a list of tables that the legacy check functions understand.
    """
    # Already legacy format if any H2 heading says "multiplexing"
    if any(t.get("h2") and "multiplexing" in t["h2"].lower() for t in tables):
        return tables

    # Find the single combined table by looking for AF columns
    single = None
    for t in tables:
        if any(re.match(r"AF\d+", str(c).strip()) for c in t["columns"]):
            single = t
            break
    if single is None:
        return tables

    name_idx = None
    for i, c in enumerate(single["columns"]):
        if str(c).strip().lower() == "name":
            name_idx = i
            break
    if name_idx is None:
        # Cannot safely split; return as-is and let downstream checks fail gracefully
        return tables

    af_cols = [c for c in single["columns"] if re.match(r"AF\d+", str(c).strip())]
    af_indices = [single["columns"].index(c) for c in af_cols]

    # Determine port list from data + expected_ports
    data_ports = set()
    for row in single["rows"]:
        name = str(row[name_idx]).strip()
        m = re.match(r"(P[A-H])\d+", name)
        if m:
            data_ports.add(m.group(1))

    ports = []
    if expected_ports:
        for p in expected_ports:
            if p in data_ports:
                ports.append(p)
    ports.extend(sorted(p for p in data_ports if p not in ports))

    result = []

    # Virtual Pin Assignment table: drop AF columns, keep H2 so check_pin_table finds it
    pin_cols = [c for c in single["columns"] if not re.match(r"AF\d+", str(c).strip())]
    pin_col_indices = [single["columns"].index(c) for c in pin_cols]
    pin_rows = []
    for row in single["rows"]:
        pin_rows.append([row[i] for i in pin_col_indices])
    result.append({
        "h2": "Pin Assignment",
        "columns": pin_cols,
        "rows": pin_rows,
        "line_start": single["line_start"],
    })

    # Virtual mux table per port
    for port in ports:
        mux_rows = []
        for row in single["rows"]:
            name = str(row[name_idx]).strip()
            if re.match(rf"{port}\d+", name):
                mux_rows.append([name] + [row[i] for i in af_indices])
        result.append({
            "h2": f"{port} Port Multiplexing",
            "columns": ["Pin"] + af_cols,
            "rows": mux_rows,
            "line_start": single["line_start"],
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  Check functions — each returns (pass_count, warn_count, fail_count, messages)
# ═══════════════════════════════════════════════════════════════════════════════

PASS = 0
WARN = 1
FAIL = 2

RESULT = {0: "PASS", 1: "WARN", 2: "FAIL"}


def color_tag(level):
    if level == PASS:
        return "[PASS]"
    if level == WARN:
        return "[WARN]"
    return "[FAIL]"


def check_file(path):
    """Verify file exists and is non-empty."""
    msgs = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except FileNotFoundError:
        return (0, 0, 1, [f"File not found: {path}"])
    if not content.strip():
        return (0, 0, 1, ["File is empty"])
    line_count = len(content.split("\n"))
    msgs.append(f"File: {path} ({line_count} lines)")
    return (1, 0, 0, msgs)


def check_pin_table(tables, content):
    """Validate the Pin Assignment table."""
    pin_tables = [t for t in tables if t["h2"] and "pin assignment" in t["h2"].lower()]
    if not pin_tables:
        pin_tables = [t for t in tables if "LQFP" in str(t["columns"]) or "Name" in t["columns"]]
        # If still ambiguous, just use the first table after the H1
        if not pin_tables and tables:
            pin_tables = [tables[0]]

    if not pin_tables:
        return (0, 0, 1, ["Pin Assignment table not found"])

    t = pin_tables[0]
    msgs = []
    passes = 1
    warns = 0
    fails = 0

    col_count = len(t["columns"])
    row_count = len(t["rows"])
    msgs.append(f"Pin Assignment: {row_count} pins, {col_count} columns")
    msgs.append(f"  Columns: {t['columns']}")

    # Check for Name column
    name_cols = [c for c in t["columns"] if c.lower() == "name"]
    if not name_cols:
        fails += 1
        msgs.append("[FAIL] No 'Name' column found in pin assignment table")

    # Check minimum reasonable row count (most MCUs have >10 pins)
    if row_count < 5:
        fails += 1
        msgs.append(f"[FAIL] Only {row_count} pin rows — likely incomplete extraction")
    elif row_count < 15:
        warns += 1
        msgs.append(f"[WARN] Only {row_count} pin rows — verify against datasheet")

    # Check for empty rows or duplicate headers
    empty_rows = 0
    header_dup = 0
    for row in t["rows"]:
        if all(c == "" or c == " " for c in row):
            empty_rows += 1
        if row[0].strip() == t["columns"][0]:
            header_dup += 1
    if empty_rows:
        warns += 1
        msgs.append(f"[WARN] {empty_rows} empty row(s) in pin assignment table")
    if header_dup:
        warns += 1
        msgs.append(f"[WARN] {header_dup} row(s) matching header — possible page-break artifact")

    return (passes, warns, fails, msgs)


def check_mux_tables(tables, expected_ports, expected_cols, content):
    """Validate Multiplexing tables."""
    mux_tables = [t for t in tables if t["h2"] and "multiplexing" in t["h2"].lower()]
    if not mux_tables:
        mux_tables = [t for t in tables if "AF0" in t["columns"]]

    msgs = []
    passes = 0
    warns = 0
    fails = 0

    if not mux_tables:
        return (0, 0, 1, ["[FAIL] No multiplexing tables found"])

    found_ports = []
    for t in mux_tables:
        h2 = t.get("h2") or "(no heading)"
        msgs.append(f"  {h2}: {len(t['rows'])} pins, {len(t['columns'])} cols")
        found_ports.append(h2)

        # Check expected columns
        if expected_cols and len(t["columns"]) != expected_cols:
            warns += 1
            msgs.append(f"    [WARN] Expected {expected_cols} columns, got {len(t['columns'])}")
        elif not expected_cols:
            # Just note the column count
            msgs.append(f"    Columns: {t['columns']}")

        # First column should be "Pin"
        if t["columns"][0].lower() != "pin":
            warns += 1
            msgs.append(f"    [WARN] First column is '{t['columns'][0]}', expected 'Pin'")

        # Check for empty rows
        empty = sum(1 for r in t["rows"] if all(c == "" or c == " " for c in r))
        if empty:
            warns += 1
            msgs.append(f"    [WARN] {empty} empty row(s)")

    # Check expected port count
    if expected_ports:
        port_set = set(expected_ports)
        found_set = set()
        for h2 in found_ports:
            m = re.match(r"(P[A-H])", h2)
            if m:
                found_set.add(m.group(1))
        missing_ports = port_set - found_set
        extra_ports = found_set - port_set
        if missing_ports:
            fails += 1
            msgs.append(f"[FAIL] Missing multiplexing tables: {sorted(missing_ports)}")
        if extra_ports:
            warns += 1
            msgs.append(f"[WARN] Unexpected extra ports: {sorted(extra_ports)}")
        if not missing_ports and not extra_ports:
            passes += 1
            msgs.append(f"[PASS] All {len(port_set)} expected ports present")

    msgs.insert(0, f"Multiplexing: {len(mux_tables)} table(s)")
    return (passes, warns, fails, msgs)


def check_cross_validation(tables, content):
    """Verify pins in assignment table also appear in their port's mux table."""
    pin_tables = [t for t in tables if t["h2"] and "pin assignment" in t["h2"].lower()]
    if not pin_tables:
        pin_tables = [t for t in tables if "Name" in t["columns"]]
    if not pin_tables:
        return (0, 0, 1, ["[FAIL] Cannot cross-validate: missing pin assignment table"])

    mux_tables = [t for t in tables if t["h2"] and "multiplexing" in t["h2"].lower()]
    if not mux_tables:
        mux_tables = [t for t in tables if "AF0" in t["columns"]]

    if not mux_tables:
        return (0, 1, 0, ["[WARN] Cannot cross-validate: no mux tables found"])

    pt = pin_tables[0]
    name_idx = None
    for i, c in enumerate(pt["columns"]):
        if c.lower() == "name":
            name_idx = i
            break

    if name_idx is None:
        return (0, 1, 0, ["[WARN] Cannot cross-validate: 'Name' column not found"])

    # Collect pin names from assignment table
    # Format: usually "PA0" or "PA0/WKUP" — extract the base port+pin
    pin_re = re.compile(r"\b(P[A-H]\d+)\b")
    assign_pins = {}  # port -> set of pin numbers
    for row in pt["rows"]:
        raw = row[name_idx].strip()
        for m in pin_re.finditer(raw):
            pname = m.group(1)
            # port = first 2 chars (e.g. "PA"); full "PA0"-style name kept as key
            port = pname[:2]
            if port not in assign_pins:
                assign_pins[port] = set()
            assign_pins[port].add(pname)

    if not assign_pins:
        return (0, 1, 0, ["[WARN] No pin names found in assignment table"])

    # Collect pin names from mux tables
    mux_pins = {}  # port -> set of pin names
    for t in mux_tables:
        # Extract port from heading (e.g. "PA Port Multiplexing (AF0–AF8)")
        m = re.match(r"(P[A-H])", t.get("h2") or "")
        if not m:
            continue
        port = m.group(1)
        mux_pins[port] = set()
        for row in t["rows"]:
            pin = row[0].strip()
            m = pin_re.match(pin)
            if m:
                mux_pins[port].add(m.group(1))

    msgs = []
    passes = 0
    warns = 0
    fails = 0

    all_assign_ports = set(assign_pins.keys())
    all_mux_ports = set(mux_pins.keys())

    shared = all_assign_ports & all_mux_ports

    for port in sorted(shared):
        assign_set = assign_pins[port]
        mux_set = mux_pins[port]
        missing_in_mux = assign_set - mux_set
        extra_in_mux = mux_set - assign_set

        if missing_in_mux:
            warns += 1
            msgs.append(
                f"[WARN] {port}: {len(missing_in_mux)} pin(s) in assignment "
                f"but missing from mux: {sorted(missing_in_mux)[:5]}"
                + ("..." if len(missing_in_mux) > 5 else "")
            )
        if extra_in_mux:
            warns += 1
            msgs.append(
                f"[WARN] {port}: {len(extra_in_mux)} pin(s) in mux "
                f"but not in assignment: {sorted(extra_in_mux)[:5]}"
                + ("..." if len(extra_in_mux) > 5 else "")
            )

    if not shared:
        warns += 1
        msgs.append("[WARN] No port overlap between assignment and mux tables")
    elif not any(missing_in_mux for missing_in_mux in [
        assign_pins[p] - mux_pins.get(p, set()) for p in shared
    ]):
        passes += 1
        msgs.append(f"[PASS] Cross-validation: {len(shared)} port(s) consistent")

    return (passes, warns, fails, msgs)


def check_artifacts(content_bytes):
    """Scan for PDF extraction artifacts in the raw markdown bytes."""
    msgs = []
    warns = 0
    fails = 0
    passes = 0

    checks = {
        "soft hyphen (0xAD byte)": b"\xad",
        "zero-width space (U+200B)": b"\xe2\x80\x8b",
        "soft hyphen (U+00AD UTF-8)": b"\xc2\xad",
        "CRLF line endings": b"\r\n",
        "bullet char artifact (U+F0B7)": b"\xf4\xb0\xad\xb7",
    }

    found_any = False
    for label, seq in checks.items():
        count = content_bytes.count(seq)
        if count > 0:
            warns += 1
            msgs.append(f"[WARN] Found {count} instance(s) of {label}")
            found_any = True

    if not found_any:
        passes += 1
        msgs.append("[PASS] No PDF extraction artifacts detected")

    return (passes, warns, fails, msgs)


def check_stats(tables, content):
    """Print summary statistics."""
    msgs = []
    pin_tables = [t for t in tables if t["h2"] and "pin assignment" in t["h2"].lower()]
    if not pin_tables:
        pin_tables = [t for t in tables if "Name" in t["columns"]]
    mux_tables = [t for t in tables if t["h2"] and "multiplexing" in t["h2"].lower()]
    if not mux_tables:
        mux_tables = [t for t in tables if "AF0" in t["columns"]]

    msgs.append("Summary:")
    if pin_tables:
        pt = pin_tables[0]
        msgs.append(f"  Pin Assignment: {len(pt['rows'])} rows × {len(pt['columns'])} columns")

    if mux_tables:
        total_mux_pins = sum(len(t["rows"]) for t in mux_tables)
        msgs.append(f"  Multiplexing: {len(mux_tables)} port(s), {total_mux_pins} pin entries")

        # Count non-empty AF cells
        total_af = 0
        filled_af = 0
        for t in mux_tables:
            for row in t["rows"]:
                for cell in row[1:]:  # skip Pin column
                    total_af += 1
                    if cell.strip():
                        filled_af += 1
        coverage = (filled_af / total_af * 100) if total_af > 0 else 0
        msgs.append(f"  AF coverage: {filled_af}/{total_af} cells filled ({coverage:.1f}%)")

    return (1, 0, 0, msgs)


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Verify Pin Assignment & Multiplexing Markdown output"
    )
    parser.add_argument("markdown", help="Path to the Markdown file to verify")
    parser.add_argument("--ports", type=str, default=None,
                        help="Expected port names, comma-separated (e.g. 'PA,PB,PC,PD')")
    parser.add_argument("--mux-cols", type=int, default=None,
                        help="Expected columns in each mux table (e.g. 10 for Pin+AF0-AF8)")
    args = parser.parse_args()

    expected_ports = args.ports.split(",") if args.ports else None
    expected_cols = args.mux_cols

    # ── Read file ────────────────────────────────────────────────────────────
    try:
        with open(args.markdown, "rb") as f:
            raw_bytes = f.read()
    except FileNotFoundError:
        print(f"[FAIL] File not found: {args.markdown}")
        sys.exit(2)

    content = raw_bytes.decode("utf-8", errors="replace")
    tables = parse_md_tables(content)
    tables = normalize_to_legacy_format(tables, expected_ports)

    # ── Run all checks ───────────────────────────────────────────────────────
    all_results = []
    banner = f"Verifying: {args.markdown}"
    print(f"\n{'='*70}")
    print(f"  {banner}")
    print(f"{'='*70}")

    # 1. File check
    p, w, f, msgs = check_file(args.markdown)
    all_results.append(("File", p, w, f, msgs))

    # 2. Pin assignment table
    p, w, f, msgs = check_pin_table(tables, content)
    all_results.append(("Pin Assignment", p, w, f, msgs))

    # 3. Multiplexing tables
    p, w, f, msgs = check_mux_tables(tables, expected_ports, expected_cols, content)
    all_results.append(("Multiplexing", p, w, f, msgs))

    # 4. Cross-validation
    p, w, f, msgs = check_cross_validation(tables, content)
    all_results.append(("Cross-Validation", p, w, f, msgs))

    # 5. Artifact scan
    p, w, f, msgs = check_artifacts(raw_bytes)
    all_results.append(("Artifacts", p, w, f, msgs))

    # 6. Statistics
    p, w, f, msgs = check_stats(tables, content)
    all_results.append(("Statistics", p, w, f, msgs))

    # ── Print results ────────────────────────────────────────────────────────
    total_p = total_w = total_f = 0
    for section, p, w, f, msgs in all_results:
        total_p += p
        total_w += w
        total_f += f
        # Determine section-level result
        if f > 0:
            level = FAIL
        elif w > 0:
            level = WARN
        else:
            level = PASS
        print(f"\n── {section} {RESULT[level]}")
        for msg in msgs:
            print(f"   {msg}")

    # ── Final verdict ────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  TOTAL: {total_p} pass, {total_w} warning, {total_f} failure")
    if total_f > 0:
        print(f"  VERDICT: FAIL — {total_f} failure(s) need attention")
        print(f"{'='*70}\n")
        sys.exit(2)
    elif total_w > 0:
        print(f"  VERDICT: WARN — {total_w} warning(s), manual review recommended")
        print(f"{'='*70}\n")
        sys.exit(1)
    else:
        print("  VERDICT: PASS — all checks clean")
        print(f"{'='*70}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
