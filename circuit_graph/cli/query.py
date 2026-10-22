"""cg query — compact single-question queries against a netlist.

Unlike ``cg view`` (a full ego-subgraph JSON), ``cg query`` answers one
specific question per invocation and defaults to a compact text table:

    cg query board.NET --conn C9          # what is C9 connected to?
    cg query board.NET --comp R1 R2       # component summaries
    cg query board.NET --net GND          # who sits on this net?
    cg query board.NET --find 4.7nf       # fuzzy value / part-number search
    cg query board.NET --all --type Resistor
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from circuit_graph.cli.app import _parse_and_enrich, main
from circuit_graph.ir import CircuitIR

# Members listed per net in --conn text output before folding to "(+N more)";
# keeps `cg query --conn C9` under ~20 lines on boards with big rails.
_CONN_MEMBER_CAP = 8


def _value_text(comp) -> str:
    from circuit_graph.generators.cg_graph import _format_component_value

    return _format_component_value(comp) or comp.raw_value or "-"


def _comp_summary(comp) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": comp.id,
        "type": comp.device_kind.value.lower(),
        "value": _value_text(comp),
    }
    if comp.raw_value and comp.raw_value != _value_text(comp):
        entry["part_number"] = comp.raw_value
    entry["pins"] = [
        {"pin": p.number, "name": p.name} for p in comp.pins
    ]
    return entry


@main.command(name="query")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--conn", "conn", help="Component designator: pins, nets and co-members.")
@click.option("--comp", "comps", multiple=True, help="Component designator(s) to summarise.")
@click.option("--net", "net", help="Net name: list member components.")
@click.option("--find", "find", help="Fuzzy search across designator / value / part number.")
@click.option("--all", "list_all", is_flag=True, help="List every component.")
@click.option("--type", "dev_type", help="Filter by device kind (resistor, capacitor, ic, ...).")
@click.option(
    "--format",
    "out_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="IR cache directory (default: <repo>/.cg-cache).",
)
@click.option("--no-cache", is_flag=True, help="Bypass the IR parse/enrich cache.")
def query_cmd(
    file: Path,
    conn: str | None,
    comps: tuple[str, ...],
    net: str | None,
    find: str | None,
    list_all: bool,
    dev_type: str | None,
    out_format: str,
    cache_dir: Path | None,
    no_cache: bool,
) -> None:
    """Answer a single question about the netlist FILE.

    Examples:

        cg query board.NET --conn C9
        cg query board.NET --find 4.7nf --format json
    """
    if not any([conn, comps, net, find, list_all]):
        raise click.UsageError(
            "one of --conn / --comp / --net / --find / --all is required"
        )

    ir = _parse_and_enrich(file, cache_dir=cache_dir, use_cache=not no_cache)

    pin_to_net = {c.pin_id: c.net_id for c in ir.connections}
    net_members: dict[str, set[str]] = {}
    for c in ir.connections:
        comp_id = c.pin_id.rsplit(".", 1)[0]
        net_members.setdefault(c.net_id, set()).add(comp_id)
    comp_map = {c.id: c for c in ir.components}
    net_map = {n.id: n for n in ir.nets}
    net_by_name = {n.name: n for n in ir.nets}

    sections: list[dict[str, object]] = []

    if conn:
        comp = comp_map.get(_canonical(ir, conn))
        if comp is None:
            _emit(
                {"error": f"component not found: {conn}"}, out_format, is_error=True
            )
            sys.exit(1)
        pin_rows = []
        for pin in comp.pins:
            net_id = pin_to_net.get(pin.id)
            net_name = net_map[net_id].name if net_id in net_map else None
            members = []
            if net_id is not None:
                for cid in sorted(net_members.get(net_id, ())):
                    member = comp_map.get(cid)
                    if member is None:
                        continue
                    members.append(
                        {
                            "id": cid,
                            "value": _value_text(member),
                            "pins_on_net": sorted(
                                p.number
                                for p in member.pins
                                if pin_to_net.get(p.id) == net_id
                            ),
                        }
                    )
            truncated = 0
            if len(members) > _CONN_MEMBER_CAP:
                truncated = len(members) - _CONN_MEMBER_CAP
                members = members[:_CONN_MEMBER_CAP]
            pin_rows.append(
                {
                    "pin": pin.number,
                    "name": pin.name,
                    "net": net_name,
                    "members": members,
                    "truncated": truncated,
                }
            )
        sections.append(
            {
                "query": "conn",
                "component": _comp_summary(comp),
                "pins": pin_rows,
            }
        )

    for designator in comps:
        comp = comp_map.get(_canonical(ir, designator))
        if comp is None:
            _emit({"error": f"component not found: {designator}"}, out_format, True)
            sys.exit(1)
        summary = _comp_summary(comp)
        summary["nets"] = {
            p.number: (net_map[pin_to_net[p.id]].name if pin_to_net.get(p.id) in net_map else None)
            for p in comp.pins
        }
        sections.append({"query": "comp", **summary})

    if net:
        target = net_by_name.get(net)
        if target is None:
            _emit({"error": f"net not found: {net}"}, out_format, True)
            sys.exit(1)
        members = [
            {"id": cid, "value": _value_text(comp_map[cid])}
            for cid in sorted(net_members.get(target.id, ()))
            if cid in comp_map
        ]
        sections.append(
            {
                "query": "net",
                "net": target.name,
                "degree": len(members),
                "members": members,
            }
        )

    if find:
        needle = find.strip().lower()
        rows = []
        for comp in ir.components:
            haystacks = [
                comp.id,
                comp.raw_value or "",
                _value_text(comp),
                comp.part_number or "",
            ]
            if any(needle in h.lower() for h in haystacks):
                rows.append(_comp_summary(comp))
        sections.append({"query": "find", "keyword": find, "matches": rows})

    if list_all:
        rows = [_comp_summary(c) for c in sorted(ir.components, key=lambda c: c.id)]
        if dev_type:
            rows = [r for r in rows if r["type"] == dev_type.lower()]
        sections.append({"query": "all", "components": rows})

    _emit({"sections": sections}, out_format)


def _canonical(ir: CircuitIR, designator: str) -> str:
    ids = {c.id for c in ir.components}
    if designator in ids:
        return designator
    lowered = designator.lower()
    return next((i for i in sorted(ids) if i.lower() == lowered), designator)


def _emit(payload: dict[str, object], out_format: str, is_error: bool = False) -> None:
    if out_format == "json":
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        text = _render_text(payload)
    stream = sys.stderr if is_error else sys.stdout
    stream.buffer.write(text.encode("utf-8"))
    stream.buffer.write(b"\n")


def _render_text(payload: dict[str, object]) -> str:
    lines: list[str] = []
    if "error" in payload:
        return f"error: {payload['error']}"
    for section in payload.get("sections", []):  # type: ignore[union-attr]
        kind = section.get("query")
        if kind == "conn":
            comp = section["component"]
            lines.append(f"{comp['id']}  {comp['type']}  {comp['value']}")
            for pin in section["pins"]:
                name = f" ({pin['name']})" if pin.get("name") else ""
                lines.append(f"  pin {pin['pin']}{name} -> {pin['net']}")
                for m in pin["members"]:
                    lines.append(
                        f"    {m['id']} [{m['value']}] pins {','.join(m['pins_on_net'])}"
                    )
                if pin.get("truncated"):
                    lines.append(f"    (+{pin['truncated']} more)")
        elif kind == "comp":
            lines.append(
                f"{section['id']}  {section['type']}  {section['value']}"
            )
            for pin, net in (section.get("nets") or {}).items():
                lines.append(f"  pin {pin} -> {net}")
        elif kind == "net":
            lines.append(f"net {section['net']}  degree={section['degree']}")
            for m in section["members"]:
                lines.append(f"  {m['id']} [{m['value']}]")
        elif kind == "find":
            lines.append(f"find {section['keyword']!r}: {len(section['matches'])} match(es)")
            for m in section["matches"]:
                lines.append(f"  {m['id']}  {m['type']}  {m['value']}")
        elif kind == "all":
            for m in section["components"]:
                lines.append(f"{m['id']}  {m['type']}  {m['value']}")
    return "\n".join(lines)
