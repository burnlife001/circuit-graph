#!/usr/bin/env python3
"""Align pdf-pin-extract raw JSON into the compact 'pinmap' format.

Input : the JSON produced by extract_tables.py (skill raw schema:
        chip_name / source_pdf / packages / ports / pins[] / metadata)
Output: <chip>_Pin_Assignment_and_Multiplexing.json  (compact, one line
        per pin, column-aligned — diff-friendly, LF endings)
        <chip>_Pin_Assignment_and_Multiplexing.md    (human reference)

Canonical JSON schema (device-level):
    {device, source, packages, legend, pinout[], af{PORT: []}}
    pinout[]: {name, <pkg_key>..., type, io, main, af[], add[]}
    af rows : {pin, AF0..AFn}

Transforms applied to the raw extraction:
  - pin name "PD0 OSC_IN"        -> "PD0/OSC_IN"   (space -> '/')
  - package keys                 -> slugified json keys (override --pkg-map)
  - 'af' list granularity: rebuilt from alternate_functions cells in AF
    order (functions sharing one AF cell stay grouped with '/'), which
    reproduces the datasheet's multiplex-column grouping exactly
  - empty strings -> null; empty main_function -> base pin name

NOTE: the compact serialization format is shared with project-local
canonical builders (e.g. build_pinmap.py). Keep render_json_text /
_compact_rows in sync with those when changing the layout.

Usage:
    python align_output.py RAW.json [options]

Options:
    --pkg-map SPEC   'RAWNAME=json_key|PkgName|Display,...' — maps a raw
                     package name to its pinout field key (json_key), the
                     top-level "packages" entry (PkgName, default:
                     json_key uppercased) and the Markdown column header
                     (Display, default: raw name). Both optional:
                     'QFN325x5 mm2=qfn32_5x5|QFN32_5x5|QFN32 5x5 mm2,
                      LQFP48=lqfp48'
                     (unmapped packages: key slugified, name = raw name)
    --ports LIST     port names, comma-separated (default: raw 'ports')
    --legend-type S  legend text for the 'type' field
    --legend-io S    legend text for the 'io_level' field
    -o, --output PFX output file prefix
                     (default: <chip>_Pin_Assignment_and_Multiplexing)
    --no-md          write JSON only
    --diff REF.json  data-level compare against a reference JSON, exit 1
                     on mismatch
"""

import argparse
import json
import re
import sys
from pathlib import Path

PIN_RE = re.compile(r"^(P[A-Z]+)(\d+)")
PIN_FIELDS_TAIL = ("type", "io", "main", "af", "add")

DEFAULT_LEGEND = {
    "type": "I=input, O=output, S=power, HiZ=high-impedance",
    "io_level": "TC=standard IO, input signal level must not exceed VDD",
}


# ---------------------------------------------------------------------------
# Compact JSON serialization (keep in sync with project canonical builders)
# ---------------------------------------------------------------------------

def _tok(v):
    """Compact JSON token for a scalar or array (no inner spaces)."""
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _compact_rows(records, fields, indent, skip_align=()):
    """One line per record, column-aligned by max token width per field.

    Fields in skip_align are emitted unpadded (used for high-variance
    array fields whose widest entry would otherwise blow up every line).
    """
    toks = [[_tok(r[f]) for f in fields] for r in records]
    widths = [max(len(t[i]) for t in toks) for i in range(len(fields))]
    lines = []
    for t in toks:
        parts = []
        for i, (f, tok) in enumerate(zip(fields, t)):
            cell = f'"{f}":{tok}'
            if i < len(fields) - 1 and f not in skip_align:
                cell = cell.ljust(widths[i] + len(f) + 3)
            parts.append(cell)
        lines.append(" " * indent + "{" + ",".join(parts) + "}")
    return lines


def render_json_text(doc):
    """Serialize the canonical doc as compact one-line-per-pin JSON."""
    pkg_keys = doc["_pkg_keys"]
    pin_fields = ("name",) + tuple(pkg_keys) + PIN_FIELDS_TAIL
    af_fields = ("pin",) + tuple(doc["_af_cols"])

    lines = ["{"]
    lines.append(f'  "device": {_tok(doc["device"])},')
    lines.append(f'  "source": {_tok(doc["source"])},')
    lines.append('  "packages": ['
                 + ", ".join(_tok(p) for p in doc["packages"]) + "],")
    lines.append('  "legend": {')
    lines.append(f'    "type": {_tok(doc["legend"]["type"])},')
    lines.append(f'    "io_level": {_tok(doc["legend"]["io_level"])}')
    lines.append("  },")
    lines.append('  "pinout": [')
    lines.append(",\n".join(_compact_rows(doc["pinout"], pin_fields, 4,
                                          skip_align=("af",))))
    lines.append("  ],")
    lines.append('  "af": {')
    ports = list(doc["af"])
    for i, port in enumerate(ports):
        lines.append(f'    "{port}": [')
        lines.append(",\n".join(_compact_rows(doc["af"][port], af_fields, 6)))
        lines.append("    ]" + ("," if i < len(ports) - 1 else ""))
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def public_doc(doc):
    """Strip internal underscore keys before comparison/output."""
    return {k: v for k, v in doc.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_md(doc):
    blank = " "

    def cell(v):
        return blank if v is None or str(v).strip() == "" else str(v)

    pkg_keys = doc["_pkg_keys"]
    displays = doc["_pkg_display"]
    af_cols = doc["_af_cols"]

    out = [f'# {doc["device"]} Pin Assignment and Multiplexing', "",
           "## Pin Assignment Table", "",
           "|" + "|".join(displays)
           + "|Name|Type (1)|I/O level (2)|Main function"
             "|Multiplex function|Additional function|",
           "|" + "-|" * (len(displays) + 6)]
    for p in doc["pinout"]:
        out.append("|"
                   + "|".join(cell(p[k]) for k in pkg_keys)
                   + f"|{p['name']}|{cell(p['type'])}|{cell(p['io'])}"
                     f"|{cell(p['main'])}"
                     f"|{cell('/'.join(p['af']))}|{cell('/'.join(p['add']))}|")
    out.append("")
    for port, rows in doc["af"].items():
        out += [f"## {port} Port Multiplexing ({af_cols[0]}–{af_cols[-1]})",
                "",
                "|Pin|" + "|".join(af_cols) + "|",
                "|" + "-|" * (len(af_cols) + 1)]
        for row in rows:
            out.append(f"|{row['pin']}|"
                       + "|".join(cell(row[c]) for c in af_cols) + "|")
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Raw -> canonical doc
# ---------------------------------------------------------------------------

def slugify(name):
    s = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").lower()
    return s


def parse_pkg_map(spec):
    """'RAW=json_key|PkgName|Display,...' -> {raw: (key, pkg_name, display)}"""
    m = {}
    if not spec:
        return m
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        raw, _, rest = item.partition("=")
        parts = (rest.split("|") + ["", ""])[:3]
        key = parts[0].strip() or slugify(raw)
        m[raw.strip()] = (key,
                          parts[1].strip() or key.upper(),
                          parts[2].strip() or raw.strip())
    return m


def base_name(name):
    return name.split("/")[0]


def none_if_empty(v):
    return v if v else None


def build_doc(raw, pkg_map, ports, legend):
    raw_pkgs = raw.get("packages") or list(raw["pins"][0]["packages"])
    pkg_keys, pkg_names, pkg_display = [], [], []
    for rp in raw_pkgs:
        key, name, disp = pkg_map.get(rp, (slugify(rp), rp, rp))
        pkg_keys.append(key)
        pkg_names.append(name)
        pkg_display.append(disp)

    # AF columns: union across pins, sorted numerically
    af_cols = sorted({c for p in raw["pins"] for c in p["alternate_functions"]},
                     key=lambda c: int(c[2:]))

    pinout = []
    for p in raw["pins"]:
        name = p["name"].replace(" ", "/")
        afc = p["alternate_functions"]
        af_list = [afc[c] for c in af_cols if afc.get(c)]
        raw_mux = "/".join(p["multiplex_functions"])
        if af_list and "/".join(af_list) != raw_mux:
            print(f"WARN {name}: AF-derived mux != assignment mux\n"
                  f"  af : {'/'.join(af_list)}\n  raw: {raw_mux}",
                  file=sys.stderr)
        entry = {"name": name}
        for rp, key in zip(raw_pkgs, pkg_keys):
            entry[key] = p["packages"].get(rp)
        entry.update({
            "type": none_if_empty(p["type"]),
            "io": none_if_empty(p["io_level"]),
            "main": none_if_empty(p["main_function"]),
            "af": af_list if af_list else p["multiplex_functions"],
            "add": p["additional_functions"],
        })
        pinout.append(entry)

    af = {port: [] for port in ports}
    for p in raw["pins"]:
        m = PIN_RE.match(p["name"])
        if not m or m.group(1) not in af:
            continue
        port, num = m.group(1), int(m.group(2))
        afc = p["alternate_functions"]
        af[port].append((num, {"pin": f"{port}{num}",
                               **{c: afc.get(c) for c in af_cols}}))
    af = {port: [r for _, r in sorted(rows, key=lambda t: t[0])]
          for port, rows in af.items()}

    return {
        "device": raw["chip_name"],
        "source": raw["source_pdf"],
        "packages": pkg_names,
        "legend": legend,
        "pinout": pinout,
        "af": af,
        "_pkg_keys": pkg_keys,
        "_pkg_display": pkg_display,
        "_af_cols": af_cols,
    }


def write_lf(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("raw", help="raw JSON from extract_tables.py")
    ap.add_argument("--pkg-map", default="")
    ap.add_argument("--ports", default="")
    ap.add_argument("--legend-type", default=DEFAULT_LEGEND["type"])
    ap.add_argument("--legend-io", default=DEFAULT_LEGEND["io_level"])
    ap.add_argument("-o", "--output", default="")
    ap.add_argument("--no-md", action="store_true")
    ap.add_argument("--diff", default="")
    args = ap.parse_args()

    raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
    ports = args.ports.split(",") if args.ports else raw.get("ports") or []
    legend = {"type": args.legend_type, "io_level": args.legend_io}
    doc = build_doc(raw, parse_pkg_map(args.pkg_map), ports, legend)

    prefix = args.output or f'{raw["chip_name"]}_Pin_Assignment_and_Multiplexing'
    js_path = Path(prefix + ".json")
    write_lf(js_path, render_json_text(doc))
    print(f"wrote {js_path.name} ({js_path.stat().st_size} bytes, LF)")
    if not args.no_md:
        md_path = Path(prefix + ".md")
        write_lf(md_path, render_md(doc))
        print(f"wrote {md_path.name} ({md_path.stat().st_size} bytes, LF)")

    if args.diff:
        ref = json.loads(Path(args.diff).read_text(encoding="utf-8"))
        if ref == public_doc(doc):
            print("diff: CLEAN — matches reference data")
        else:
            print("diff: MISMATCH vs reference", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
