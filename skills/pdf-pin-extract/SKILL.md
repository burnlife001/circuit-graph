---
name: pdf-pin-extract
description: Extract Pin Assignment and Multiplexing tables from MCU/chip PDF datasheets into structured JSON. Optionally generate package-specific Markdown. Use whenever the user asks to extract pin definitions, pin assignment, pin multiplexing, GPIO alternate functions, or AF tables from a chip PDF. Triggers on: 提取引脚, 引脚复用, pin assignment, pin multiplexing, AF table, GPIO alternate function, extract pins from PDF, 引脚定义, 管脚分配.
---

# PDF Pin Assignment & Multiplexing Extractor

Extract **Pin Assignment** and **Pin Multiplexing (Alternate Function)** tables from any MCU/chip PDF datasheet into a structured, machine-readable JSON file. Optionally generate a package-specific Markdown table sorted by ascending pin number.

## Python Environment

**Known working venv:** `E:\__electric\tools\camelot\.venv`

All commands use this Python interpreter directly — **do NOT create a new venv** or copy files.

> **Shell note:** All commands use Bash syntax. Quoted paths work directly — no `&` needed (that's a PowerShell-ism).

```bash
# Verify environment is healthy (first check)
"E:/__electric/tools/camelot/.venv/Scripts/python.exe" -c "import camelot; print('camelot', camelot.__version__)"

# Run the script directly from the skill directory (no copy needed)
"E:/__electric/tools/camelot/.venv/Scripts/python.exe" "C:/Users/yg/.claude/skills/pdf-pin-extract/scripts/extract_tables.py" <args...>
```

**Dependencies** (already installed in the venv above): `camelot-py`, `numpy`, `pandas`, `opencv-python-headless`, `pypdfium2`, `pillow`, `playa-pdf`.

### Recovering from broken editable install

The venv was originally set up with `pip install -e`, which creates a `.pth` pointer to a source directory. If that source directory is deleted/moved, `import camelot` fails with `ModuleNotFoundError` even though `pip list` shows `camelot-py` as installed.

**Fix — force reinstall as regular (non-editable) package:**
```bash
"E:/__electric/tools/camelot/.venv/Scripts/python.exe" -m pip install --force-reinstall camelot-py
```

## Workflow

| Phase | Step | Script |
|---|---|---|
| 1 | Locate target pages | `extract_tables.py --search` |
| 2 | Extract tables → aligned JSON | `extract_tables.py --chip-name ...` |
| 3 | Verify output | `verify_output.py --ports ... --mux-cols ...` |

The **only JSON output is the aligned pinmap** format (one line per pin,
column-aligned, LF endings). `extract_tables.py` calls
`align_output.build_doc` internally, so one command produces the
canonical file. No `_raw.json` is ever written.

### Phase 1 — Locate Target Pages

Use `pypdfium2` to search the PDF for relevant keywords. Search for **both** English and Chinese terms depending on the datasheet language:

| Table Type | Keywords (EN) | Keywords (CN) |
|---|---|---|
| Pin Assignment | `pin assignment`, `pin definition`, `pin description`, `Table 4-1`, `Table 3-1` | `引脚定义`, `管脚分配`, `引脚分配`, `表 4-1` |
| Pin Multiplexing | `multiplexing`, `alternate function`, `AF0`, `AF1`, `Table 4-2`, `复用功能` | `引脚复用`, `复用功能`, `交替功能` |

**Search script pattern:**

```python
import pypdfium2 as pdfium

pdf = pdfium.PdfDocument(PDF_PATH)
for i in range(len(pdf)):
    page = pdf[i]
    text = page.get_textpage().get_text_range().lower()
    if any(kw in text for kw in ["pin assignment", "multiplex", "af0", "引脚定义"]):
        print(f"Page {i+1}: keyword hit")
```

### Phase 2 — Extract Tables with Camelot

Use **`lattice`** flavor (line-based tables) as the default — MCU datasheets almost always use bordered tables. Fall back to `stream` only if lattice returns zero tables.

```python
import camelot

# Pin Assignment (adjust page range from Phase 1)
tables = camelot.read_pdf(PDF_PATH, pages="START-END", flavor="lattice")
print(f"Found {len(tables)} table fragments")
```

### Phase 3 — Data Cleaning

This is the most critical phase. PDF text extraction introduces common artifacts:

| Artifact | Cause | Fix |
|---|---|---|
| `\xad` / `­` / `​` | Soft hyphens / zero-width chars | `str.replace()` |
| `\r\n` inside cells | PDF line wrapping | Replace with space or `/` depending on context |
| Repeated header rows | Multi-page table breaks | Detect and drop rows matching header |
| Trailing empty rows | Table boundary detection | Drop rows where all cells are empty |
| Merged/split cells | PDF encoding quirks | Manual inspection; adjust column count if needed |

**Cleaning function templates:**

```python
def normalize(text):
    """Remove PDF extraction artifacts."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    for ch in ["\xad", "­", "​", "­"]:
        text = text.replace(ch, "")
    return text

def clean_cell_multifunc(text, sep="/"):
    """
    Multi-function cells: newlines separate independent functions.
    Use '/' for pin assignment function columns, '' (join) for multiplexing AF cells.
    """
    text = normalize(text)
    text = text.replace("\r\n", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()
```

## Full Extraction Script

The self-contained script lives at `scripts/extract_tables.py` in this skill directory. **Run it directly — no copying needed.** The script saves output relative to CWD, so `cd` to the project directory first.

### Quick start

```bash
# Step 1: cd to project directory (output lands here)
cd "C:/Users/yg/Desktop/11"

# Step 2: search first to verify page detection
"E:/__electric/tools/camelot/.venv/Scripts/python.exe" "C:/Users/yg/.claude/skills/pdf-pin-extract/scripts/extract_tables.py" DS_MM32F0140_EN.pdf --search

# Step 3: extract aligned JSON (default — one line per pin, LF endings)
"E:/__electric/tools/camelot/.venv/Scripts/python.exe" "C:/Users/yg/.claude/skills/pdf-pin-extract/scripts/extract_tables.py" DS_MM32F0140_EN.pdf --chip-name "MM32F0140"

# Step 4 (optional): also generate a per-package Markdown table sorted by pin number
"E:/__electric/tools/camelot/.venv/Scripts/python.exe" "C:/Users/yg/.claude/skills/pdf-pin-extract/scripts/extract_tables.py" DS_MM32F0140_EN.pdf --chip-name "MM32F0140" --package LQFP48

# Step 5 (optional): also write the aligned Markdown reference for the verifier
"E:/__electric/tools/camelot/.venv/Scripts/python.exe" "C:/Users/yg/.claude/skills/pdf-pin-extract/scripts/extract_tables.py" DS_MM32F0140_EN.pdf --chip-name "MM32F0140" --aligned-md
```

### CLI reference

```
python extract_tables.py <pdf> [options]

Options:
  --search              Only search for target pages, skip extraction
  --pin-pages RANGE     Pin assignment page range (e.g. '32-35')
  --mux-pages RANGE     Multiplexing page range (e.g. '36-39')
  --ports LIST          Port names, comma-separated (e.g. 'PA,PB,PC,PD')
  --mux-cols N          Total columns in mux table (e.g. 9 for Pin+AF0-AF8)
  --chip-name NAME      Chip model for the document title
  -o, --output PATH     Output JSON file path
  --flavor FLAVOR       Camelot flavor: 'lattice' (default) or 'stream'
  --package PKG         Generate a per-package Markdown file sorted by pin
                        number (e.g. 'TSSOP20', 'LQFP48')
  --aligned-md          Also write the aligned Markdown reference
                        (<chip>_Pin_Assignment_and_Multiplexing.md)
  --pkg-map SPEC        Package name mapping (default: MM32 conventions).
                        Format: 'RAWNAME=json_key|PkgName|Display,...'
  --legend-type TEXT    Legend text for the 'type' field (default: pin
                        type glossary)
  --legend-io TEXT      Legend text for the 'io_level' field (default:
                        IO-level dictionary)
```

All extraction options are optional — the script auto-detects pages, ports, and column count via keyword search. Use manual overrides only when auto-detection falls short.

### Verification

After extraction, run `verify_output.py` to validate the Markdown output:

```bash
"E:/__electric/tools/camelot/.venv/Scripts/python.exe" "C:/Users/yg/.claude/skills/pdf-pin-extract/scripts/verify_output.py" <output.md> --ports PA,PB,PC,PD --mux-cols 10
```

**Checks performed:**

| Check | What it catches |
|---|---|
| File integrity | Missing / empty file |
| Pin Assignment table | Column count, row count, empty rows, duplicate header artifacts |
| Multiplexing tables | One table per expected port, correct AF column count, empty rows |
| Cross-validation | Pins in assignment table must appear in their port's mux table |
| Artifact scan | Soft hyphens (`\xad`, `U+00AD`), zero-width spaces, embedded CRLF |
| Summary statistics | Total pins, pins per port, AF cell fill coverage |

Exit codes: `0` = all clean, `1` = warnings (manual review recommended), `2` = failures (must fix).

### When auto-detection needs help

| Scenario | Manual override |
|---|---|
| Keyword search picks wrong pages (e.g. TOC) | `--pin-pages 32-35` |
| Port names not in standard `P[A-H]` format | `--ports PA,PB,PC,PD,PE` |
| AF count detected wrong | `--mux-cols 17` (for AF0–AF15 on STM32) |
| Lattice finds 0 tables | `--flavor stream` |

## Output Format

### Aligned JSON Output (default)

By default the extractor writes the **aligned pinmap** JSON — one line
per pin, column-aligned, LF endings, machine- and human-readable. The
filename is derived from `--chip-name`:

```bash
# Default: writes aligned JSON
python extract_tables.py DS_MM32F0140_EN.pdf --chip-name MM32F0140
# Writes: MM32F0140_Pin_Assignment_and_Multiplexing.json
```

#### Aligned JSON Schema

| Field | Type | Description |
|---|---|---|
| `device` | string | Chip model name (e.g. `MM32F0140`) |
| `source` | string | Source PDF filename |
| `packages` | [string] | Package display names (e.g. `["LQFP48","LQFP32","QFN32_5x5","QFN32_4x4","TSSOP20"]`) |
| `legend.type` | string | Human-readable legend for the `type` field |
| `legend.io_level` | string | Human-readable legend for the `io_level` field |
| `pinout` | [object] | One entry per pin (flat, column-aligned) |
| `pinout[].name` | string | Pin name, space aliased to `/` (e.g. `PA0/WKUP`) |
| `pinout[].<pkg_key>` | number \| null | Pin number in package `pkg_key` (e.g. `lqfp48:10`), or `null` if not bonded |
| `pinout[].type` | string \| null | I/O type (e.g. `I/O`, `S`) |
| `pinout[].io` | string \| null | I/O level (e.g. `TC`) |
| `pinout[].main` | string | Primary function name |
| `pinout[].af` | [string] | Multiplex functions, grouped by AF cell with `/` |
| `pinout[].add` | [string] | Additional functions (e.g. ADC inputs) |
| `af` | object | Per-port AF tables: `{"PA":[…],"PB":[…],…}` |
| `af.<PORT>[].pin` | string | Pin name (e.g. `PA0`) |
| `af.<PORT>[].AF0..AFn` | string \| null | Signal on that AF, or `null` if none |

**Design notes:**

- One physical pin = one row in `pinout`. Aliases (e.g. `PD0/OSC_IN`)
  live inside `name` with `/` as the separator.
- Package key names are slugified JSON keys (`lqfp48`, `qfn32_5x5`).
  Override with `--pkg-map` for other vendors.
- `af` lists are rebuilt from the AF cells — functions sharing one cell
  (e.g. `SPI1_NSS/I2S1_WS`) stay grouped, matching the datasheet.
- Empty cells → `null` (no `""` empty strings).

### Package-Specific Markdown Output

When `--package PKG` is provided, the extractor also writes a Markdown file for that package, with pins sorted from Pin 1 upward:

```bash
python extract_tables.py DS_MM32G0001_EN.pdf --chip-name MM32G0001 --package TSSOP20
# Writes: MM32G0001_Pin_Assignment_and_Multiplexing.json         (aligned)
#         MM32G0001_Pin_Assignment_and_Multiplexing.TSSOP20.md
```

The Markdown is emitted in the same compact style as the aligned-MD
reference (compact pipes, blank empty cells, `/`-joined function lists):

| Column | Content |
|---|---|
| Pin | Package pin number (sorted ascending) |
| Name | Pin name, aliases joined with `/` (e.g. `PD0/OSC_IN`) |
| Type | I/O type |
| I/O level | I/O level |
| Main function | Primary function |
| Multiplex function | All multiplex functions, `/`-separated |
| Additional function | Additional functions (e.g. ADC inputs) |

AF0…AFn columns are intentionally omitted from the per-package file —
AF detail already lives in the aligned MD (per-port tables) and the JSON.

### Aligned Markdown Reference (optional, `--aligned-md`)

Pass `--aligned-md` to also write the canonical reference Markdown
`<chip>_Pin_Assignment_and_Multiplexing.md` — Pin Assignment table
followed by per-port AF tables. This is the file the verifier expects
when the per-package MD isn't available, and is useful as a single
human-readable view of the whole pinmap.

```bash
python extract_tables.py DS_MM32F0140_EN.pdf --chip-name MM32F0140 --aligned-md --package LQFP48
# Writes: MM32F0140_Pin_Assignment_and_Multiplexing.json       (aligned)
#         MM32F0140_Pin_Assignment_and_Multiplexing.md          (aligned MD)
#         MM32F0140_Pin_Assignment_and_Multiplexing.LQFP48.md   (per-package MD)
```

## Vendor-Specific Notes

### MindMotion (MM32 series)
- Keywords: `pin assignment`, `multiplex`, `Table 4-`
- Pin Assignment typically spans 4 pages
- Multiplexing tables organized by port: PA → PB → PC → PD
- Port count varies by chip (MM32F0140 has 4 ports; some have 2 or 5)

### STMicroelectronics (STM32 series)
- Keywords: `pin definitions`, `alternate function`, `Table 1`, `Table 2`
- Pin Assignment and AF tables often in different chapters
- Larger chips (STM32F4, STM32H7) may have 8+ ports (PA–PH)
- AF table format: rows=pins, columns=AF0–AF15 (wider table)

### GigaDevice (GD32 series)
- Similar structure to STM32
- Keywords: `pin assignment`, `alternate function`, `复用`
- Chinese/English bilingual datasheets common — search both

### Artery (AT32 series)
- Keywords: `pin definitions`, `alternate function mapping`
- Table numbering varies; rely more on keyword search than fixed table numbers

## Diagnosis & Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'camelot'` but `pip list` shows `camelot-py` | Broken editable install — source dir deleted/moved | `pip install --force-reinstall camelot-py` (see Recovery section above) |
| 0 tables found | Wrong `flavor` or page range | Try `flavor="stream"` or widen page range |
| Tables found but columns misalign | Merged cells in PDF | Check raw table with `table.df.to_string()`, manually adjust column count |
| Chinese characters garbled | PDF uses non-unicode CMap | Try `camelot.read_pdf(..., encoding="latin-1")` or manually decode |
| Extra/duplicate columns | Camelot detected phantom vertical lines | Drop unexpected columns: `df = df.iloc[:, :EXPECTED_COLS]` |
| Rows split across fragments | Table crosses page boundary | `pd.concat(fragments)` and drop repeated header rows |
