"""cg kb — knowledge base sub-commands (extracted from app.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from circuit_graph.cli.app import DEFAULT_KB_PATH, main
from circuit_graph.knowledge.db import ComponentDatabase
from circuit_graph.parsers.protel import ParserInput
from circuit_graph.parsers.registry import parse_netlist



# ---------------------------------------------------------------------------
# cg kb — knowledge base sub-commands
# ---------------------------------------------------------------------------


@main.group()
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    default=DEFAULT_KB_PATH,
    show_default=True,
    help="SQLite database file (default: <pkg>/knowledge/circuit_graph.db).",
)
@click.pass_context
def kb(ctx: click.Context, db_path: Path) -> None:
    """Query and update the component knowledge base (SQLite)."""
    ctx.ensure_object(dict)
    ctx.obj["kb_path"] = db_path


def _open_kb(ctx: click.Context) -> ComponentDatabase:
    db_path: Path = ctx.obj["kb_path"]
    return ComponentDatabase.open(db_path)


@kb.command(name="init")
@click.pass_context
def kb_init(ctx: click.Context) -> None:
    """Create / upgrade the KB schema and migrate vendor_db.json."""
    db = _open_kb(ctx)
    click.echo(f"Database: {db.db_path}")
    click.echo(json.dumps(db.stats(), indent=2))
    vendor = _PROJECT_ROOT / "circuit_graph" / "knowledge" / "vendor_db.json"
    if vendor.exists():
        n = db.migrate_from_vendor_db(vendor)
        click.echo(f"Migrated {n} prefixes from vendor_db.json")


@kb.command(name="lookup")
@click.argument("part_number")
@click.pass_context
def kb_lookup(ctx: click.Context, part_number: str) -> None:
    """Show a single component + pinout + properties."""
    db = _open_kb(ctx)
    rec = db.get_component(part_number)
    if rec is None:
        click.echo(json.dumps({"error": f"not found: {part_number}"}))
        sys.exit(1)
    payload: dict[str, object] = {
        "part_number": rec.part_number,
        "manufacturer": rec.manufacturer,
        "category": rec.category,
        "subcategory": rec.subcategory,
        "package": rec.package,
        "pin_count": rec.pin_count,
        "description": rec.description,
        "is_credible": rec.is_credible,
        "pin_source": rec.pin_source,
    }
    pinout = db.get_pinout(rec.part_number)
    if pinout:
        payload["pinout"] = pinout
    props = db.get_properties(rec.part_number)
    if props:
        payload["properties"] = props
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@kb.command(name="search")
@click.argument("keyword")
@click.pass_context
def kb_search(ctx: click.Context, keyword: str) -> None:
    """Full-text search across part_number / description / category."""
    db = _open_kb(ctx)
    rows = db.query(any=keyword)
    payload = [
        {
            "part_number": r.part_number,
            "manufacturer": r.manufacturer,
            "category": r.category,
            "package": r.package,
        }
        for r in rows
    ]
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    click.echo(f"# {len(payload)} match(es)", err=True)


@kb.command(name="pinout")
@click.argument("part_number")
@click.pass_context
def kb_pinout(ctx: click.Context, part_number: str) -> None:
    """Show only the pin map for ``part_number``."""
    db = _open_kb(ctx)
    pinout = db.get_pinout(part_number)
    if pinout is None:
        click.echo(json.dumps({"error": f"no pinout for {part_number}"}))
        sys.exit(1)
    click.echo(json.dumps(pinout, indent=2, ensure_ascii=False))


# NOTE: Earlier revisions also defined `@kb.group(name="import") -> kb_import_group`
# plus bare-bones `protel` / `kicad` subcommands (no --symbol-dir). Click silently
# overrides that group with the later `kb_import` definition further down, so the
# earlier block was unreachable dead code. It has been removed in 2026-07; if you
# re-introduce it, register on the canonical `kb_import` group below.


@kb.command(name="extract-pins")
@click.option(
    "--pdf",
    "pdf_path",
    type=click.Path(exists=True, path_type=Path),
    help="Extract pins from a specific PDF file.",
)
@click.option(
    "--part",
    "part_number",
    help="Auto-locate datasheet by part number (searches DATASHEET_DIR).",
)
@click.option(
    "--pages",
    help="Manual page range override (e.g. '32-35').",
)
@click.option(
    "--search",
    is_flag=True,
    help="Only detect pin-table pages, skip extraction.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Save result to JSON file.",
)
@click.pass_context
def kb_extract_pins(
    ctx: click.Context,
    pdf_path: Path | None,
    part_number: str | None,
    pages: str | None,
    search: bool,
    output: Path | None,
) -> None:
    """Extract pin names from a PDF datasheet via Camelot.

    Supports two modes:

    \b
    1. --pdf DS_MM32F0140_EN.pdf   Extract from a specific PDF file.
    2. --part MM32F0140            Auto-locate the datasheet under DATASHEET_DIR.

    \b
    Use --pages to override the auto-detected page range.
    Use --search to only detect pages without extracting.
    """
    from circuit_graph.config import load as _load_config
    from circuit_graph.knowledge.pdf_pin_extract import (
        search_pin_pages,
        extract_pin_table,
        extract_from_datasheet,
    )

    _load_config()

    # Resolve PDF path.
    if pdf_path is not None:
        target_pdf = pdf_path
    elif part_number is not None:
        pin_map = extract_from_datasheet(part_number)
        if pin_map is None:
            click.echo(json.dumps({"error": f"no pin table found for {part_number}"}))
            sys.exit(1)
        _emit_pin_result(pin_map, output)
        return
    else:
        click.echo(
            json.dumps({"error": "either --pdf or --part is required"}),
            err=True,
        )
        sys.exit(1)

    # Search mode.
    if search:
        detected = search_pin_pages(target_pdf)
        click.echo(json.dumps({"pdf": str(target_pdf), "pin_pages": detected}, indent=2))
        return

    # Extract mode.
    if pages is not None:
        page_list = _parse_page_range(pages)
        if not page_list:
            click.echo(json.dumps({"error": f"invalid --pages range: {pages!r}"}))
            sys.exit(1)
    else:
        page_list = search_pin_pages(target_pdf)
        if not page_list:
            click.echo(json.dumps({"error": "no pin-table pages detected; use --pages to specify manually"}))
            sys.exit(1)

    pin_map = extract_pin_table(target_pdf, page_list)
    if pin_map is None:
        click.echo(json.dumps({"error": "failed to extract pin table"}))
        sys.exit(1)

    _emit_pin_result(pin_map, output)


def _emit_pin_result(pin_map: dict[str, str], output: Path | None) -> None:
    """Print pin map to stdout or write to file."""
    result = {
        "pin_count": len(pin_map),
        "pins": {num: name for num, name in sorted(
            pin_map.items(),
            key=lambda x: (int(x[0]) if x[0].isdigit() else 9999, x[0]),
        )},
    }
    if output is not None:
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        click.echo(f"Wrote {len(pin_map)} pins to {output}")
    else:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))


def _parse_page_range(s: str) -> list[int] | None:
    """Parse a page range string like '32-35' or '32,33,34' into a list of ints."""
    try:
        if "-" in s:
            parts = s.split("-", 1)
            start, end = int(parts[0].strip()), int(parts[1].strip())
            if start < 1 or end < start:
                return None
            return list(range(start, end + 1))
        else:
            return [int(p.strip()) for p in s.split(",") if p.strip()]
    except (ValueError, IndexError):
        return None


@kb.group(name="import")
def kb_import() -> None:
    """Bulk import a KiCad netlist, Protel netlist, or chip_convert JSON file."""


@kb_import.command(name="kicad")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--symbol-dir",
    "symbol_dirs",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Directories to search for .kicad_sym files (before JLCPCB).",
)
@click.pass_context
def kb_import_kicad(ctx: click.Context, path: Path, symbol_dirs: tuple[Path, ...]) -> None:
    """Parse a KiCad S-expression netlist and upsert components + pins."""
    db = _open_kb(ctx)
    n = db.import_kicad_netlist(path, symbol_dirs=list(symbol_dirs) or None)
    click.echo(f"Imported {n} component(s) from {path}")


@kb_import.command(name="protel")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--symbol-dir",
    "symbol_dirs",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Directories to search for .kicad_sym files (before JLCPCB).",
)
@click.pass_context
def kb_import_protel(ctx: click.Context, path: Path, symbol_dirs: tuple[Path, ...]) -> None:
    """Parse Protel .NET netlist(s) and upsert components + pins."""
    db = _open_kb(ctx)
    n = db.import_protel_netlist(path, symbol_dirs=list(symbol_dirs) or None)
    click.echo(f"Imported {n} component(s) from {path}")


@kb.command(name="resolve")
@click.argument("netlist", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--symbol-dir",
    "symbol_dirs",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Extra directories to search for .kicad_sym files.",
)
@click.option("--dry-run", is_flag=True, help="Only report, do not invoke pin2json.")
@click.pass_context
def kb_resolve(
    ctx: click.Context,
    netlist: Path,
    symbol_dirs: tuple[Path, ...],
    dry_run: bool,
) -> None:
    """Scan ``netlist`` and resolve unnamed pins via KB + pin2json."""
    db = _open_kb(ctx)
    try:
        ir = parse_netlist(
            ParserInput(source_id=str(netlist), file_path=str(netlist))
        )
    except Exception as exc:  # noqa: BLE001
        click.echo(json.dumps({"error": f"parse failed: {exc}"}))
        sys.exit(1)

    non_passive = [
        c
        for c in ir.components
        if c.part_number and any(p.name is None for p in c.pins)
    ]
    if dry_run:
        report = {
            "netlist": str(netlist),
            "dry_run": True,
            "components_with_unnamed_pins": [
                {"id": c.id, "part_number": c.part_number, "pins": len(c.pins)}
                for c in non_passive
            ],
        }
        click.echo(json.dumps(report, indent=2, ensure_ascii=False))
        return

    resolved = db.resolve_pins(non_passive, list(symbol_dirs))
    click.echo(
        json.dumps(
            {
                "netlist": str(netlist),
                "resolved_pins": resolved,
                "warnings": db.pin_resolver_warnings,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@kb.command(name="stats")
@click.pass_context
def kb_stats(ctx: click.Context) -> None:
    """Print aggregate counts in the knowledge base."""
    db = _open_kb(ctx)
    click.echo(json.dumps(db.stats(), indent=2))


