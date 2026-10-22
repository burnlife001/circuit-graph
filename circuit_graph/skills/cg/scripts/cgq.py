#!/usr/bin/env python3
"""cgq - compact queries over a cg-graph JSON export.

Why this exists
---------------
`cg view <id> <file> --pretty` prints the entire ego-subgraph as JSON
(several KB per call, mostly unrelated components).  Answering "what is C9?"
then takes many exploratory round trips.

cgq.py does one export per netlist and caches it, then prints only the rows
you asked for, with values, so a whole circuit question is answered in a
single command.

Usage
-----
  cgq.py --graph G.json --comp C9 R26 --net SW2
  cgq.py --netlist FILE.NET --conn C9
  cgq.py --netlist FILE.NET --find SW5001
  cgq.py --netlist FILE.NET --all --type mosfet
  cgq.py --netlist FILE.NET --stats

Stdlib only, UTF-8, no side effects beyond a cache file under d:/tmp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_CG = r"E:\__electric\__workspace\stacking\circuit-graph\.venv\Scripts\cg.exe"
CACHE_DIRS = (Path("d:/tmp/cg"), Path(tempfile.gettempdir()) / "cg")
MAX_MEMBERS = 24


# --------------------------------------------------------------------------
# CLI resolution + graph export (cached)
# --------------------------------------------------------------------------
def find_cg() -> str | None:
    """Locate the cg executable: PATH first, then the known venv copy."""
    exe = shutil.which("cg")
    if exe:
        return exe
    if os.path.exists(DEFAULT_CG):
        return DEFAULT_CG
    return None


def cache_path(netlist: Path) -> Path:
    st = netlist.stat()
    key = hashlib.md5(
        f"{netlist.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")
    ).hexdigest()[:12]
    for base in CACHE_DIRS:
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return base / f"{netlist.stem}.{key}.graph.json"
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / f"{netlist.stem}.{key}.graph.json"


def export_graph(netlist: Path, out: Path | None) -> Path:
    cg = find_cg()
    if cg is None:
        sys.exit(
            "cg executable not found.\n"
            f"  install it, or run: {DEFAULT_CG}\n"
            "  (also add that Scripts dir to PATH)"
        )
    if out is None:
        out = cache_path(netlist)
        if out.exists():
            return out
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [cg, "export", "graph", str(netlist), "-o", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 or not out.exists():
        sys.exit(f"cg export graph failed:\n{proc.stdout}\n{proc.stderr}")
    return out


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------
def val_of(comp: dict) -> str:
    v = comp.get("value")
    return str(v) if v else "-"


def part_of(comp: dict) -> str:
    return str(comp.get("part_number") or "-")


def pins_of(comp: dict) -> str:
    out = []
    for p in comp.get("pins", []) or []:
        tag = f"{p.get('pin')}:{p.get('net')}"
        if p.get("name"):
            tag += f"({p['name']})"
        out.append(tag)
    return "  ".join(out)


def brief(comp: dict) -> str:
    v = val_of(comp)
    p = part_of(comp)
    if v == "-" and p == "-":
        return comp.get("type", "?")
    if p == "-":
        return f"{v}"
    return f"{v} [{p}]"


def load_graph(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def get_component(graph: dict, designator: str) -> dict | None:
    comps = graph.get("components", {})
    if designator in comps:
        return comps[designator]
    # case-insensitive fallback (U1 vs u1)
    low = designator.lower()
    for k, v in comps.items():
        if k.lower() == low:
            return v
    return None


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------
def show_components(graph: dict, designators: list[str], as_json: bool) -> None:
    comps = graph.get("components", {})
    rows = []
    for d in designators:
        c = get_component(graph, d)
        if c is None:
            print(f"{d:<6} !! not found in netlist")
            continue
        rows.append(
            {
                "id": c.get("id", d),
                "type": c.get("type"),
                "value": c.get("value"),
                "part_number": c.get("part_number"),
                "package": c.get("package"),
                "pins": [
                    {"pin": p.get("pin"), "net": p.get("net"), "name": p.get("name")}
                    for p in c.get("pins", []) or []
                ],
            }
        )
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for r in rows:
        pins = "  ".join(
            f"{p['pin']}:{p['net']}" + (f"({p['name']})" if p.get("name") else "")
            for p in r["pins"]
        )
        print(f"{r['id']:<6} {str(r['type']):<11} {str(r['value'] or '-'):<16} "
              f"{str(r['part_number'] or '-'):<16} {r['package'] or '':<8} | {pins}")


def show_nets(graph: dict, names: list[str], as_json: bool) -> None:
    nets = graph.get("nets", {})
    comps = graph.get("components", {})
    out = []
    for n in names:
        nd = nets.get(n)
        if nd is None:
            print(f"NET {n} !! not found")
            continue
        members = nd.get("members", [])
        out.append({"net": n, "kind": nd.get("kind"), "degree": nd.get("degree"),
                    "members": members})
        if as_json:
            continue
        print(f"NET {n}  degree={nd.get('degree')}  kind={nd.get('kind')}")
        shown = members[:MAX_MEMBERS]
        line = []
        for m in shown:
            c = comps.get(m, {})
            line.append(f"{m}={brief(c)}")
        print("   " + "  ".join(line))
        if len(members) > len(shown):
            print(f"   ... (+{len(members) - len(shown)} more)")
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))


def show_conn(graph: dict, designator: str, as_json: bool) -> None:
    comps = graph.get("components", {})
    nets = graph.get("nets", {})
    c = get_component(graph, designator)
    if c is None:
        sys.exit(f"{designator} not found in netlist")
    if as_json:
        payload = {"component": c, "nets": {}}
        for p in c.get("pins", []) or []:
            payload["nets"].setdefault(p.get("net"), []).extend(
                m for m in nets.get(p.get("net"), {}).get("members", []) if m != designator
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"{designator}  {c.get('type')}  value={val_of(c)}  part={part_of(c)}")
    for p in c.get("pins", []) or []:
        net = p.get("net")
        members = [m for m in nets.get(net, {}).get("members", []) if m != designator]
        if not members:
            print(f"   pin{p.get('pin')} {net}: (only {designator} on this net)")
            continue
        shown = members[:MAX_MEMBERS]
        parts = "  ".join(f"{m}={brief(comps.get(m, {}))}" for m in shown)
        more = f"  ... (+{len(members) - len(shown)} more)" if len(members) > len(shown) else ""
        print(f"   pin{p.get('pin')} {net}: {parts}{more}")


def show_find(graph: dict, needle: str, ctype: str | None) -> None:
    comps = graph.get("components", {})
    low = needle.lower()
    hits = []
    for cid, c in sorted(comps.items()):
        if ctype and c.get("type") != ctype:
            continue
        hay = " ".join(
            str(c.get(k) or "") for k in ("id", "value", "part_number", "package")
        ).lower()
        if low in hay:
            hits.append(c)
    if not hits:
        print(f"no component matches {needle!r}")
        return
    print(f"{len(hits)} match(es) for {needle!r}:")
    for c in hits:
        print(f"   {c.get('id'):<6} {str(c.get('type')):<11} {val_of(c):<16} "
              f"{part_of(c):<16} | {pins_of(c)}")


def show_all(graph: dict, ctype: str | None) -> None:
    comps = graph.get("components", {})
    for cid, c in sorted(comps.items()):
        if ctype and c.get("type") != ctype:
            continue
        print(f"{cid:<6} {str(c.get('type')):<11} {val_of(c):<16} "
              f"{part_of(c):<16} | {pins_of(c)}")


def show_stats(graph: dict) -> None:
    comps = graph.get("components", {})
    nets = graph.get("nets", {})
    kinds: dict[str, int] = {}
    for c in comps.values():
        kinds[c.get("type", "?")] = kinds.get(c.get("type", "?"), 0) + 1
    print(f"components={len(comps)}  nets={len(nets)}")
    for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"   {k:<14} {v}")


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", help="path to a cg-graph JSON export")
    ap.add_argument("--netlist", help="Protel .NET / KiCad .net to export (cached)")
    ap.add_argument("--comp", nargs="+", help="component designators")
    ap.add_argument("--net", nargs="+", help="net names")
    ap.add_argument("--conn", help="connectivity expansion for one component")
    ap.add_argument("--find", help="substring match on id/value/part_number")
    ap.add_argument("--all", action="store_true", help="list all components")
    ap.add_argument("--type", dest="ctype", help="filter by component type")
    ap.add_argument("--stats", action="store_true", help="component/net counts")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args(argv)

    if args.graph:
        graph = load_graph(Path(args.graph))
    elif args.netlist:
        graph = load_graph(export_graph(Path(args.netlist), None))
    else:
        ap.error("need --graph or --netlist")

    if args.stats:
        show_stats(graph)
    if args.comp:
        show_components(graph, args.comp, args.json)
    if args.net:
        show_nets(graph, args.net, args.json)
    if args.conn:
        show_conn(graph, args.conn, args.json)
    if args.find:
        show_find(graph, args.find, args.ctype)
    if args.all:
        show_all(graph, args.ctype)
    if not any([args.stats, args.comp, args.net, args.conn, args.find, args.all]):
        show_stats(graph)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
