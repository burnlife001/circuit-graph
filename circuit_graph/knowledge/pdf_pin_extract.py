"""PDF datasheet pin extraction — Camelot-powered fallback resolver.

Stage 5 of the ``_auto_resolve_ics`` chain: when JLCPCB batch lookup fails,
try parsing the pin table directly from a PDF datasheet via Camelot.

Design
------
- Camelot runs in an **external venv** (never imported into circuit-graph).
  Communication is via ``subprocess.run`` over a plain-text JSON pipeline.
- Only ``lattice`` mode is used — ``stream`` (borderless tables) has too high
  an error rate for IC datasheets.
- Multi-page pin tables are **merged** by column signature before parsing,
  with fallback to per-table iteration when merge is ambiguous.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from circuit_graph.config import camelot_venv, datasheet_dir

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword lists for page detection
# ---------------------------------------------------------------------------

PIN_KEYWORDS: tuple[str, ...] = (
    # English
    "pin description",
    "terminal description",
    "terminal function",
    "pin configuration",
    "pin function",
    "table of pin",
    "pin assignment",
    "pinout",
    "ball map",
    # Chinese
    "引脚说明",
    "引脚功能",
    "管脚描述",
    "端子说明",
    "引脚定义",
)

# Columns that map to ``pin_number`` (case-insensitive match).
_NUMBER_COLS: tuple[str, ...] = (
    "pin no.", "number", "pin#", "pin", "terminal", "no.", "#",
)
# Columns that map to ``pin_name`` (case-insensitive match).
_NAME_COLS: tuple[str, ...] = (
    "pin name", "name", "symbol", "terminal name",
    "description", "function", "pin assignment",
)

# Package suffixes to strip during fuzzy filename matching.
_PACKAGE_SUFFIX_RE = re.compile(
    r"-(QFN\d+|QFP\d+|SOT\d[\d-]*\w*|SOP\d|TSSOP\d|MSOP\d|DFN\d|"
    r"BGA\d+|LQFP\d+|SOIC\d|DIP\d|TO-\d+|TO\d+|SMD\d|"
    r"[A-Z]+-\d+)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Inline Camelot extraction script (runs inside the external venv)
# ---------------------------------------------------------------------------

_CAMELOT_SCRIPT = r"""
import json, sys

# Force UTF-8 on Windows (PDF soft-hyphens break GBK encoding).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import camelot
except ImportError:
    print(json.dumps({"error": "camelot not installed"}))
    sys.exit(0)

pdf_path = sys.argv[1]
pages_str = sys.argv[2]

try:
    tables = camelot.read_pdf(pdf_path, pages=pages_str, flavor="lattice")
    out = []
    for t in tables:
        # Convert table to list-of-dicts using the first row as header.
        df = t.df
        if df.empty:
            continue
        header = [str(c).strip() for c in df.iloc[0].tolist()]
        rows = []
        for i in range(1, len(df)):
            row = [str(c).strip() for c in df.iloc[i].tolist()]
            rows.append(row)
        out.append({"header": header, "rows": rows, "page": t.page, "accuracy": t.accuracy})
    print(json.dumps(out, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({"error": str(exc)}))
    sys.exit(0)
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_pin_pages(pdf_path: Path) -> list[int]:
    """Find pages containing actual pin-assignment TABLES (not pinout diagrams).

    Strategy (from the eetool skill extractor):
      1. Find pages with pin-assignment keyword hits.
      2. Find pages with BOTH pin-name patterns (``P[A-H]\\d+``) AND
         table-header keywords (``main function``, ``multiplex function``,
         etc.) — these are real table data pages, not visual pinout diagrams.
      3. Resolve a contiguous table range starting from the last keyword hit
         and extending through adjacent data pages.
      4. Extend by one trailing page for footnotes / table tail.

    Returns a sorted list of 1-indexed page numbers forming the
    contiguous pin-table range, or an empty list on failure.
    """
    if not pdf_path.is_file():
        _logger.warning("PDF not found: %s", pdf_path)
        return []

    # Table-header keywords that distinguish table pages from pinout diagrams.
    _table_header_kw = json.dumps([
        "main function", "multiplex function", "additional function",
        "i/o level", "pin function", "pin assignment",
    ])
    # Narrow pin-assignment keywords (excludes TOC matches like "pin description").
    _pin_kw = json.dumps([
        "pin assignment", "pin definition", "pin description",
        "引脚定义", "管脚分配", "引脚分配",
    ])

    try:
        venv = camelot_venv()
        if venv is None:
            _logger.warning("Camelot venv not available, skipping page search")
            return []
        py_exe = venv / "Scripts" / "python"
        import json as _json
        import tempfile as _tempfile

        _script = (
            "import json, sys, re\n"
            "from pypdfium2 import PdfDocument\n"
            "pdf_path = sys.argv[1]\n"
            "pin_kw = json.loads(sys.argv[2])\n"
            "table_kw = json.loads(sys.argv[3])\n"
            "doc = PdfDocument(pdf_path)\n"
            "total = len(doc)\n"
            "keyword_hits = []\n"
            "data_pages = []\n"
            "pin_name_re = re.compile(r'\\bP[A-H]\\d+\\b')\n"
            "for i in range(total):\n"
            "  page = doc[i]\n"
            "  tp = page.get_textpage()\n"
            "  text = tp.get_text_range()\n"
            "  if not text:\n"
            "    continue\n"
            "  text_lower = text.lower()\n"
            "  for k in pin_kw:\n"
            "    if k in text_lower:\n"
            "      keyword_hits.append(i + 1)\n"
            "      break\n"
            "  # Check for pin-name patterns + table headers (real data pages).\n"
            "  if pin_name_re.search(text) and any(kw in text_lower for kw in table_kw):\n"
            "    data_pages.append(i + 1)\n"
            "data_set = set(data_pages)\n"
            "result = []\n"
            "if keyword_hits:\n"
            "  # Resolve contiguous range: start from last keyword hit, extend\n"
            "  # through adjacent data pages, add one trailing page for footnotes.\n"
            "  candidates = sorted(keyword_hits, reverse=True)\n"
            "  for start_pg in candidates:\n"
            "    lo = start_pg\n"
            "    while lo > 1 and (lo - 1) in data_set:\n"
            "      lo -= 1\n"
            "    hi = start_pg\n"
            "    while hi < total and (hi + 1) in data_set:\n"
            "      hi += 1\n"
            "    span = list(range(lo, hi + 1))\n"
            "    if len(span) >= 1:\n"
            "      if hi < total:\n"
            "        span.append(hi + 1)\n"
            "      result = span\n"
            "      break\n"
            "if not result:\n"
            "  result = sorted(set(keyword_hits))\n"
            "print(json.dumps(result))\n"
        )
        with _tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8",
        ) as f:
            f.write(_script)
            script_path = f.name
        try:
            result = subprocess.run(
                [str(py_exe), script_path, str(pdf_path), _pin_kw, _table_header_kw],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
        finally:
            Path(script_path).unlink(missing_ok=True)
        if result.returncode != 0:
            _logger.warning("pdf page search stderr: %s", result.stderr[:200])
            return []
        pages = _json.loads(result.stdout.strip())
        if not isinstance(pages, list):
            return []
        return pages
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as exc:
        _logger.warning("PDF page search failed for %s: %s", pdf_path, exc)
        return []


def _merge_tables(tables: list[dict]) -> dict | None:
    """Merge multi-page pin tables into one consolidated table dict.

    Groups tables by column signature, merges rows within the largest
    group, and normalises column counts.  Handles empty headers by
    inheriting column mapping from the previous page.

    Returns ``{"header": [...], "rows": [[...], ...]}`` or ``None`` when
    tables cannot be meaningfully merged.
    """
    from collections import Counter

    def _norm(h: list[str]) -> tuple[str, ...]:
        return tuple(c.lower().strip() for c in h)

    def _empty_h(h: list[str]) -> bool:
        return all(c.strip() == "" for c in h)

    # Filter out tables with no rows.
    tables_valid = [t for t in tables if t.get("rows")]
    if not tables_valid:
        return None

    # --- Step 1: Group by column signature ---
    groups: list[list[dict]] = []
    prev_header: tuple[str, ...] | None = None

    for table in tables_valid:
        header = table.get("header", [])

        if _empty_h(header):
            # Inherit column mapping from the previous page.
            if groups and prev_header is not None:
                ncols = len(header)
                matched = False
                for g in reversed(groups):
                    gh = g[0].get("header", [])
                    if len(gh) == ncols:
                        g.append(table)
                        matched = True
                        break
                if not matched:
                    # Fallback: add to the last group.
                    groups[-1].append(table)
            # else: no context to inherit from → skip.
            continue
        else:
            sig = (len(header), _norm(header))
            prev_header = sig[1]
            matched = False
            for g in groups:
                gh = g[0].get("header", [])
                if (len(gh), _norm(gh)) == sig:
                    g.append(table)
                    matched = True
                    break
            if not matched:
                groups.append([table])

    if not groups:
        return None

    # --- Step 2: Ambiguity check ---
    # If every table forms its own group (each has a unique column structure),
    # and there are at least 2 groups, this is not a multi-page pin table.
    if len(groups) >= 2 and len(groups) == len(tables_valid):
        _logger.warning(
            "Each page has a unique table structure (%d groups from %d tables), cannot merge",
            len(groups), len(tables_valid),
        )
        return None

    # Pick the group with the most rows (total across all tables in that group).
    best_group = max(groups, key=lambda g: sum(len(t.get("rows", [])) for t in g))
    ref_header = best_group[0].get("header", [])

    # --- Step 3: Determine target column count (most common) ---
    col_counter = Counter()
    for t in best_group:
        col_counter[len(t.get("header", []))] += 1
    target_ncols = col_counter.most_common(1)[0][0]

    # Normalise reference header to target column count.
    if len(ref_header) > target_ncols:
        ref_header = ref_header[:target_ncols]
    elif len(ref_header) < target_ncols:
        ref_header = ref_header + [""] * (target_ncols - len(ref_header))

    ref_sig = _norm(ref_header)

    # --- Step 4: Merge all rows (skip duplicate headers) ---
    all_rows: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    for table in best_group:
        for row in table.get("rows", []):
            # Normalise column count.
            if len(row) > target_ncols:
                row_clean = row[:target_ncols]
            elif len(row) < target_ncols:
                row_clean = row + [""] * (target_ncols - len(row))
            else:
                row_clean = list(row)

            # Skip rows that look like a duplicate header or are completely empty.
            if _norm(row_clean) == ref_sig:
                continue
            if all(c == "" for c in row_clean):
                continue

            # Deduplicate at the row level.
            key = tuple(row_clean)
            if key not in seen:
                seen.add(key)
                all_rows.append(row_clean)

    if not all_rows:
        return None

    return {"header": ref_header, "rows": all_rows}


def extract_pin_table(pdf_path: Path, pages: list[int]) -> dict[str, str] | None:
    """Extract a ``{pin_number: pin_name}`` map from *pages* of *pdf_path*.

    Uses Camelot ``lattice`` mode via an external subprocess.  Returns
    ``None`` on any failure (no table found, parsing error, timeout).

    Column auto-mapping
    -------------------
    * If recognised column names are found, ``pin_number`` and ``pin_name``
      are mapped accordingly (case-insensitive).
    * When no known column names match, a heuristic fallback is used:
      the first column that looks numeric is treated as ``pin_number`` and
      the adjacent text column as ``pin_name``.
    """
    venv = camelot_venv()
    if venv is None:
        _logger.warning("Camelot venv not available, skipping table extraction")
        return None
    if not pdf_path.is_file():
        _logger.warning("PDF not found: %s", pdf_path)
        return None
    if not pages:
        return None

    py_exe = venv / "Scripts" / "python"
    # Pass contiguous page ranges as "START-END" so Camelot can detect table
    # continuations across pages.  Non-contiguous pages use comma-separated.
    if pages == list(range(pages[0], pages[-1] + 1)):
        page_str = f"{pages[0]}-{pages[-1]}"
    else:
        page_str = ",".join(str(p) for p in pages)

    try:
        result = subprocess.run(
            [str(py_exe), "-c", _CAMELOT_SCRIPT, str(pdf_path), page_str],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except subprocess.TimeoutExpired:
        _logger.warning("Camelot timed out for %s pages=%s", pdf_path, pages)
        return None

    if result.returncode != 0:
        _logger.warning("Camelot subprocess failed for %s: %s", pdf_path, result.stderr[:200])
        return None

    try:
        tables = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        _logger.warning("Camelot output not valid JSON for %s", pdf_path)
        return None

    if not isinstance(tables, list) or not tables:
        return None

    # If Camelot returned an error entry (top-level dict with "error" key),
    # treat as failure.
    if isinstance(tables, dict) and tables.get("error"):
        _logger.warning("Camelot error: %s", tables["error"])
        return None

    # --- Multi-page merge (P5) ---
    # Group tables by column signature, merge rows, then parse once.
    merged = _merge_tables(tables)
    if merged is not None:
        pin_map = _parse_table(merged["header"], merged["rows"])
        if pin_map:
            _logger.debug(
                "Multi-page merge produced %d pins from %d tables",
                len(pin_map), len(tables),
            )
            return pin_map

    # Fallback: iterate tables individually (single-page datasheets and
    # cases where merge was ambiguous).
    for table in tables:
        if not isinstance(table, dict):
            continue
        header = table.get("header") or []
        rows = table.get("rows") or []
        if not rows:
            continue

        pin_map = _parse_table(header, rows)
        if pin_map:
            return pin_map

    return None


# ---------------------------------------------------------------------------
# Table parsing
# ---------------------------------------------------------------------------


def _parse_table(header: list[str], rows: list[list[str]]) -> dict[str, str] | None:
    """Try to extract a ``{pin_number: pin_name}`` map from one table.

    First attempts **column-name matching**; falls back to a **heuristic**
    if no known column names are found.
    """
    # Attempt 1: column-name mapping.
    pin_map = _parse_by_column_names(header, rows)
    if pin_map:
        return pin_map

    # Attempt 2: heuristic fallback (first numeric col + adjacent text col).
    pin_map = _parse_heuristic(header, rows)
    if pin_map:
        _logger.debug("Heuristic fallback succeeded for header=%s", header)
        return pin_map

    return None


def _parse_by_column_names(
    header: list[str], rows: list[list[str]],
) -> dict[str, str] | None:
    """Match column names to ``pin_number`` / ``pin_name`` via keyword lists.

    When only one column is identified by name (e.g. ``Name`` header found
    but pin-number column is a package column like ``LQFP48``), the missing
    index is resolved heuristically:
    - Missing ``num_idx`` → first column that is 60%+ numeric.
    - Missing ``name_idx`` → first text-heavy column after ``num_idx``.
    """
    if not header:
        return None

    header_lower = [h.lower().strip() for h in header]
    num_idx: Optional[int] = None
    name_idx: Optional[int] = None

    for i, h in enumerate(header_lower):
        if num_idx is None:
            for kw in _NUMBER_COLS:
                if h == kw or h.startswith(kw) or kw.startswith(h):
                    num_idx = i
                    break
        if name_idx is None:
            for kw in _NAME_COLS:
                if h == kw or h.startswith(kw):
                    name_idx = i
                    break

    # ── Partial-match resolution ──────────────────────────────────────────
    if name_idx is not None and num_idx is None:
        # Found the name column; auto-detect the numeric (pin-number) column.
        num_idx = _find_numeric_col(rows)
    elif num_idx is not None and name_idx is None:
        # Found the number column; find the first text-heavy column after it.
        name_idx = _find_text_col(rows, num_idx)

    if num_idx is None or name_idx is None:
        return None

    pin_map: dict[str, str] = {}
    for row in rows:
        row_clean = [_clean_cell(c) for c in row]
        if num_idx >= len(row_clean) or name_idx >= len(row_clean):
            continue
        num = row_clean[num_idx]
        name = row_clean[name_idx]
        if not num:
            continue  # skip empty pin-number cells
        pin_map[num] = name or "NC"
    return pin_map if pin_map else None


def _find_numeric_col(rows: list[list[str]]) -> int | None:
    """Return the index of the first column that is 60%+ purely numeric."""
    ncols = max(len(r) for r in rows) if rows else 0
    for col_idx in range(ncols):
        values = []
        for row in rows:
            if col_idx < len(row):
                values.append(row[col_idx].strip())
        if not values:
            continue
        numeric_count = sum(1 for v in values if re.match(r"^\d+$", v))
        if numeric_count >= len(values) * 0.6:
            return col_idx
    return None


def _find_text_col(rows: list[list[str]], after_idx: int) -> int | None:
    """Return the index of the first column after *after_idx* with meaningful
    text content (values that are not purely numeric and not all empty)."""
    ncols = max(len(r) for r in rows) if rows else 0
    for col_idx in range(after_idx + 1, ncols):
        non_empty = 0
        numeric = 0
        for row in rows:
            if col_idx < len(row):
                v = row[col_idx].strip()
                if v:
                    non_empty += 1
                    if re.match(r"^\d+$", v):
                        numeric += 1
        # Must have > 50% non-empty AND less than 50% numeric among non-empty.
        if non_empty > len(rows) * 0.4:
            if numeric < non_empty * 0.5:
                return col_idx
    return None


def _parse_heuristic(
    header: list[str], rows: list[list[str]],
) -> dict[str, str] | None:
    """Heuristic: treat the first numeric-ish column as pin_number and the
    next text-only column as pin_name.

    Covers 90%+ of simple IC datasheet layouts without explicit column headers.
    """
    if not rows:
        return None

    # Determine which column index to use for pin_number (first that looks
    # like a number 90%+ of the time).
    num_col: Optional[int] = None
    name_col: Optional[int] = None
    ncols = max(len(r) for r in rows) if rows else 0

    for col_idx in range(ncols):
        values = []
        for row in rows:
            if col_idx < len(row):
                values.append(row[col_idx].strip())
        if not values:
            continue
        numeric_count = sum(1 for v in values if re.match(r"^\d+$", v))
        if numeric_count >= len(values) * 0.6 and num_col is None:
            num_col = col_idx
            # The name column is the next column.
            if col_idx + 1 < ncols:
                name_col = col_idx + 1
            break

    if num_col is None or name_col is None:
        return None

    pin_map: dict[str, str] = {}
    for row in rows:
        if num_col >= len(row) or name_col >= len(row):
            continue
        num = _clean_cell(row[num_col])
        name = _clean_cell(row[name_col])
        if not num:
            continue
        pin_map[num] = name or "NC"
    return pin_map if pin_map else None


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Normalise whitespace and strip common PDF artifacts."""
    if not text:
        return ""
    # Collapse all whitespace (including non-breaking spaces) into single spaces.
    text = re.sub(r"[\xa0​\s]+", " ", text)
    # Strip leading/trailing junk.
    text = text.strip().strip(".,;:-")
    return text.strip()


def _clean_cell(raw: str) -> str:
    """Clean a single table cell string."""
    if not raw:
        return ""
    # Strip "(Note N)" style annotations.
    raw = re.sub(r"\([Nn]ote\s*\d+\)", "", raw)
    # Strip soft-hyphens and zero-width characters.
    for ch in ("\xad", "​", "‌", "‍", "﻿"):
        raw = raw.replace(ch, "")
    # Collapse whitespace.
    raw = re.sub(r"[\xa0​\s]+", " ", raw)
    raw = raw.strip()
    return raw


def _merge_multiline(rows: list[list[str]], col: int) -> list[list[str]]:
    """Forward-fill empty cells and join multi-line descriptions in *col*."""
    out: list[list[str]] = []
    pending: Optional[str] = None
    for row in rows:
        if col < len(row) and row[col].strip():
            if pending:
                _flush_pending(out, pending)
            pending = row[col].strip()
        elif pending:
            # Continuation of a multi-line description.
            extra = row[col].strip() if col < len(row) else ""
            if extra:
                pending += " " + extra
            else:
                _flush_pending(out, pending)
                pending = None
    if pending:
        _flush_pending(out, pending)
    # Fill in shorter rows.
    while len(out) < len(rows):
        out.append(out[-1] if out else [""] * (col + 1))
    return out


def _flush_pending(out: list[list[str]], value: str) -> None:
    """Append a single-row list with *value* at index 0."""
    out.append([value] if value else [""])


# ---------------------------------------------------------------------------
# Datasheet file matching
# ---------------------------------------------------------------------------


def _find_datasheet(part_number: str, ds_dir: Path) -> Path | None:
    """Locate a datasheet PDF for *part_number* under *ds_dir*.

    Strategy (descending priority):

    1. **Exact match** — ``fn.stem == part_number`` (case-insensitive).
    2. **Prefix match** — ``fn.stem.startswith(part_number)``.
    3. **Fuzzy match** — strip packaging suffix from ``fn.stem`` and retry
       prefix match.  Avoids ``JW5065H.pdf`` matching ``JW5065``.

    Returns the first match or ``None``.
    """
    if not ds_dir.is_dir():
        _logger.warning("datasheet_dir does not exist: %s", ds_dir)
        return None

    target = part_number.strip().upper()
    if not target:
        return None

    candidates: list[Path] = []

    # Collect all PDFs under ds_dir (recursive).
    pdfs = sorted(p for p in ds_dir.rglob("*.pdf") if p.is_file())

    # Phase 1 — exact match.
    for pdf in pdfs:
        stem = pdf.stem.upper()
        if stem == target:
            _logger.debug("Exact datasheet match: %s", pdf)
            return pdf

    # Phase 2 — prefix match.
    for pdf in pdfs:
        stem = pdf.stem.upper()
        if stem.startswith(target) and len(stem) > len(target):
            # Exclusion: the next character must not be alphanumeric
            # (prevents 'JW5065' matching 'JW5065H').
            next_ch = stem[len(target)]
            if next_ch.isalnum():
                continue
            candidates.append(pdf)

    # Phase 3 — substring match (datasheets often named like
    # ``DS_MM32F0140_EN.pdf`` where the part number appears in the middle).
    if not candidates:
        for pdf in pdfs:
            stem = pdf.stem.upper()
            if target in stem:
                # Verify the match is on word boundaries (prevents
                # 'JW5065' matching 'JW5065X').
                idx = stem.index(target)
                after = idx + len(target)
                if after < len(stem) and stem[after].isalnum():
                    continue
                candidates.append(pdf)

    # Phase 4 — fuzzy match (strip packaging suffix).
    if not candidates:
        for pdf in pdfs:
            stem = pdf.stem
            stripped = _PACKAGE_SUFFIX_RE.sub("", stem).strip().upper()
            if stripped == target:
                _logger.debug("Fuzzy datasheet match: %s", pdf)
                return pdf
            if stripped.startswith(target):
                next_ch = stripped[len(target)]
                if next_ch.isalnum():
                    continue
                candidates.append(pdf)

    if len(candidates) > 1:
        _logger.warning(
            "Multiple datasheet candidates for %s, picking first: %s",
            part_number, [c.name for c in candidates],
        )
    if candidates:
        return candidates[0]

    _logger.debug("No datasheet found for %s under %s", part_number, ds_dir)
    return None


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def extract_from_datasheet(part_number: str) -> dict[str, str] | None:
    """One-shot: locate the datasheet for *part_number* and extract a pin map.

    Returns ``None`` when the datasheet is missing, Camelot is unavailable,
    or no pin table could be parsed.
    """
    ds_dir = datasheet_dir()
    if ds_dir is None:
        return None
    pdf = _find_datasheet(part_number, ds_dir)
    if pdf is None:
        return None
    pages = search_pin_pages(pdf)
    if not pages:
        _logger.debug("No pin-description pages found in %s", pdf)
        # Still try page 1 as a fallback.
        pages = [1]
    return extract_pin_table(pdf, pages)


__all__ = [
    "search_pin_pages",
    "extract_pin_table",
    "_find_datasheet",
    "extract_from_datasheet",
    "normalize",
]
