"""cg view — ego-subgraph view command and its context builders."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from circuit_graph.cli.app import _parse_and_enrich, main
from circuit_graph.ir import CircuitIR


@main.command()
@click.argument("designator")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--hops", type=int, default=2, show_default=True)
@click.option("--pretty", is_flag=True, help="Indent the JSON for humans (~2x larger).")
@click.option(
    "--with-power",
    is_flag=True,
    help="Include `power_context` describing the focal component's power-supply path.",
)
@click.option(
    "--with-comm",
    is_flag=True,
    help="Include `comm_context` describing the focal component's communication buses.",
)
@click.option(
    "--with-blocks",
    is_flag=True,
    help="Include `blocks` listing functional blocks that touch the ego subgraph.",
)
@click.option(
    "--with-all",
    is_flag=True,
    help="Shorthand for --with-power --with-comm --with-blocks.",
)
@click.option(
    "--topology-timeout",
    type=float,
    default=None,
    help="Per-template topology match timeout in seconds (default: recognizer built-in).",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="IR cache directory (default: <repo>/.cg-cache).",
)
@click.option("--no-cache", is_flag=True, help="Bypass the IR parse/enrich cache.")
def view(
    designator: str,
    file: Path,
    hops: int,
    pretty: bool,
    with_power: bool,
    with_comm: bool,
    with_blocks: bool,
    with_all: bool,
    topology_timeout: float | None,
    cache_dir: Path | None,
    no_cache: bool,
) -> None:
    """Output a scoped ego-subgraph for a single component.

    Pass --with-power, --with-comm and/or --with-blocks to enrich the output
    JSON with the focal component's power-supply path, communication-bus
    participation and recognised functional blocks.
    """
    if with_all:
        with_power = True
        with_comm = True
        with_blocks = True

    warnings: list[str] = []
    ir = _parse_and_enrich(
        file,
        warnings_out=warnings,
        topology_timeout=topology_timeout,
        cache_dir=cache_dir,
        use_cache=not no_cache,
    )
    if warnings:
        click.echo(f"warnings: {len(warnings)} — {'; '.join(warnings)}", err=True)
    designator = _canonical_designator(ir, designator)
    from circuit_graph.knowledge.db import ComponentDatabase

    db = ComponentDatabase.default()
    from circuit_graph.generators.cg_graph import view_component

    result = view_component(ir, designator, hops=hops, db=db)
    if result is None:
        payload = {"error": _not_found_message(ir, designator), "warnings": warnings}
        sys.stdout.buffer.write(
            json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        )
        sys.stdout.buffer.write(b"\n")
        sys.exit(1)

    result["warnings"] = warnings

    if with_power:
        ctx = _build_power_context(ir, designator)
        if ctx is not None:
            result["power_context"] = ctx
    if with_comm:
        ctx = _build_comm_context(ir, designator)
        if ctx is not None:
            result["comm_context"] = ctx
    if with_blocks:
        result["blocks"] = _build_blocks_context(ir, result)

    output = json.dumps(result, indent=2 if pretty else None, ensure_ascii=False)
    sys.stdout.buffer.write(output.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")


def _canonical_designator(ir: CircuitIR, designator: str) -> str:
    """Resolve ``designator`` to the exact component id, case-insensitively.

    Protel designators are uppercase by convention but users type ``u2b``;
    an exact match always wins, otherwise the unique case-insensitive match
    is used.  Returns the input unchanged when nothing matches.
    """
    ids = {c.id for c in ir.components}
    if designator in ids:
        return designator
    lowered = designator.lower()
    return next((i for i in sorted(ids) if i.lower() == lowered), designator)


def _not_found_message(ir: CircuitIR, designator: str) -> str:
    """Build the "component not found" error message for ``cg view``.

    When ``designator`` names a multi-unit device that MultiUnitSplitter has
    split into per-unit virtual components (e.g. LM358 U2 -> U2A/U2B/U2C),
    the bare designator no longer exists in the IR.  The message then lists
    the split units — annotating the power unit — and suggests a concrete
    signal unit to view instead.  Matching is case-insensitive so ``u2``
    still surfaces the hint for ``U2``.
    """
    lowered = designator.lower()
    units = sorted(
        (
            c
            for c in ir.components
            if str(c.metadata.get("split_from", "")).lower() == lowered
        ),
        key=lambda c: c.id,
    )
    if not units:
        return f"Component {designator} not found"
    parent = str(units[0].metadata["split_from"])
    base = f"Component {parent} not found"
    labels = [
        f"{u.id}(电源)" if u.metadata.get("unit_type") == "power" else u.id
        for u in units
    ]
    suggested = next(
        (u.id for u in units if u.metadata.get("unit_type") != "power"),
        units[0].id,
    )
    return f"{base}. 已拆分为多单元: {', '.join(labels)} — 请用 cg view {suggested} 查看"


def _build_power_context(ir: CircuitIR, designator: str) -> dict[str, object] | None:
    """Return a power-supply context dict for ``designator``, or ``None``.

    The context is None when the component is not on any recognised power
    path (e.g. it sits on a signal net only).  When present, the returned
    dict exposes:

    * ``rail`` — the power-rail net name the focal component sits on
    * ``source_net`` — the upstream source net name (USB_5V, VBAT, ...)
    * ``source_kind`` — the inferred source kind (``usb``, ``battery``,
      ``barrel_jack``, ``header_pin``)
    * ``upstream_regulators`` — regulators between the rail and the source,
      listed in source-to-load order

    ComponentDatabase loading is deferred so the cost is only paid when
    ``--with-power`` is requested.
    """
    from circuit_graph.ir import NetType
    from circuit_graph.knowledge.db import ComponentDatabase
    from circuit_graph.power_tree_recognizer import build_power_tree

    comp_map = {c.id: c for c in ir.components}
    if designator not in comp_map:
        return None

    net_ids_for_comp: set[str] = set()
    for conn in ir.connections:
        if conn.pin_id.startswith(f"{designator}."):
            net_ids_for_comp.add(conn.net_id)
    if not net_ids_for_comp:
        return None

    from circuit_graph.graph import create_graph

    graph = create_graph().load(ir).graph
    db = ComponentDatabase.default()
    tree = build_power_tree(ir, graph, db)

    net_lookup = {n.id: n for n in ir.nets}
    net_id_to_name = {n.id: n.name for n in ir.nets}

    # Restrict to nets the focal component actually touches AND that the
    # NetClassifier has tagged as POWER.  This avoids reporting context for
    # signal-only nets (e.g. CC2, RXD) and for ground nets (handled below).
    power_net_ids_for_comp = {
        net_id
        for net_id in net_ids_for_comp
        if (net_lookup.get(net_id) is not None and net_lookup[net_id].type is NetType.POWER)
    }
    if not power_net_ids_for_comp:
        return None

    # 1) Try the deep path first: regulator-rooted chains.
    best: dict[str, object] | None = None
    for net_id in sorted(power_net_ids_for_comp):
        net = net_lookup.get(net_id)
        if net is None:
            continue
        info = _resolve_power_path(tree, net_id, designator, net_id_to_name)
        if info is None:
            continue
        info["rail"] = net.name
        if best is None or (
            not best.get("upstream_regulators") and info.get("upstream_regulators")
        ):
            best = info
    if best is not None:
        best["confidence"] = "high"
        return best

    # 2) Fallback: the focal component sits directly on a source net.
    source_net_lookup: dict[str, str] = {}
    for rid in getattr(tree, "roots", []):
        meta = tree.nodes[rid].metadata or {}  # type: ignore[attr-defined]
        nid = meta.get("net_id")
        kind = meta.get("source_kind")
        if isinstance(nid, str) and isinstance(kind, str):
            source_net_lookup[nid] = kind
    for net_id in sorted(power_net_ids_for_comp):
        if net_id in source_net_lookup:
            net = net_lookup.get(net_id)
            return {
                "rail": net.name if net else net_id,
                "source_net_id": net_id,
                "source_net": net.name if net else net_id,
                "source_kind": source_net_lookup[net_id],
                "upstream_regulators": [],
                "confidence": "medium",
            }

    # 3) Last-resort fallback: the component sits on a POWER-typed net but
    # no source pattern matched (e.g. a downstream rail such as 3V3 / 5V
    # whose upstream tracing was not annotated).  Surface a minimal context
    # so the caller still knows the rail identity, but flag source_kind as
    # ``unknown`` to be honest about the missing source.
    for net_id in sorted(power_net_ids_for_comp):
        net = net_lookup.get(net_id)
        if net is None:
            continue
        return {
            "rail": net.name,
            "source_net_id": None,
            "source_net": None,
            "source_kind": "unknown",
            "upstream_regulators": [],
            "confidence": "low",
        }

    return None


def _resolve_power_path(
    tree: object,
    net_id: str,
    designator: str,
    net_id_to_name: dict[str, str],
) -> dict[str, object] | None:
    """Walk ``tree`` and find the power path leading to ``designator``.

    Returns a dict with ``source_net``, ``source_kind``, ``upstream_regulators``
    or ``None`` when the component is not on any tracked power path.
    """
    for root_id in getattr(tree, "roots", []):
        path: list[str] = []
        if not _scan_path_for_component(tree, root_id, net_id, designator, path):
            continue
        source_node = tree.nodes[path[0]]  # type: ignore[attr-defined]
        source_meta = source_node.metadata or {}
        source_net_id = source_meta.get("net_id")  # type: ignore[assignment]
        source_kind = source_meta.get("source_kind")  # type: ignore[assignment]

        upstream: list[dict[str, object]] = []
        for node_id in path[1:]:
            node = tree.nodes[node_id]  # type: ignore[attr-defined]
            if node.kind == "regulator":
                reg_meta = (node.metadata or {}).get("regulator") or {}
                upstream.append(
                    {
                        "component_id": reg_meta.get("component_id", node.label),
                        "kind": reg_meta.get("kind"),
                        "part_number": reg_meta.get("part_number"),
                        "confidence": reg_meta.get("confidence"),
                        "output_net_id": reg_meta.get("output_net_id"),
                    }
                )

        return {
            "source_net_id": source_net_id,
            "source_net": net_id_to_name.get(source_net_id or "", source_net_id),  # type: ignore[arg-type]
            "source_kind": source_kind,
            "upstream_regulators": upstream,
        }

    return None


def _scan_path_for_component(
    tree: object,
    node_id: str,
    target_net_id: str,
    designator: str,
    path: list[str],
) -> bool:
    """Depth-first scan returning True if ``node_id``'s subtree feeds the component."""
    if node_id in path:
        return False
    path.append(node_id)
    try:
        node = tree.nodes[node_id]  # type: ignore[attr-defined]
        meta = node.metadata or {}
        if meta.get("net_id") == target_net_id:
            return True
        if meta.get("component_id") == designator:
            return True
        for child_id in tree.children.get(node_id, []):  # type: ignore[attr-defined]
            if _scan_path_for_component(tree, child_id, target_net_id, designator, path):
                return True
        return False
    finally:
        path.pop()


def _build_comm_context(ir: CircuitIR, designator: str) -> dict[str, object] | None:
    """Return a comm-bus context dict for ``designator``, or ``None``.

    The returned dict has a single key ``buses`` — a list of bus records
    containing protocol, role and fellow members.  Returns ``None`` when the
    component participates in no detected bus.
    """
    from circuit_graph.comm_topology import detect_comm_topology

    comp_map = {c.id: c for c in ir.components}
    if designator not in comp_map:
        return None

    result = detect_comm_topology(ir)
    buses: list[dict[str, object]] = []
    for bus in result.buses:
        # Does the focal component appear as a member?
        member = next((m for m in bus.members if m.component_id == designator), None)
        if member is None:
            continue
        buses.append(
            {
                "id": bus.id,
                "protocol": bus.protocol,
                "role": member.role,
                "master_id": bus.master_id,
                "slave_ids": list(bus.slave_ids),
                "members": [
                    {
                        "component_id": m.component_id,
                        "role": m.role,
                    }
                    for m in bus.members
                ],
                "nets": list(bus.nets),
                "confidence": bus.confidence,
                "evidence": list(bus.evidence),
            }
        )

    if not buses:
        return None
    return {"buses": buses}


def _build_blocks_context(ir: CircuitIR, view_result: dict[str, object]) -> list[dict[str, object]]:
    """Return functional blocks that touch the ego subgraph in ``view_result``.

    A block is included when at least one of its components appears in the
    view's component set, so the focal component's block membership plus any
    blocks spilling into the neighbourhood are both surfaced.  The list is
    empty (not absent) when nothing was recognised — callers always get the
    key when ``--with-blocks`` is passed.
    """
    ego_components = set(view_result.get("components", {}))
    blocks: list[dict[str, object]] = []
    for block in ir.blocks:
        if not ego_components.intersection(block.component_ids):
            continue
        blocks.append(
            {
                "id": block.id,
                "name": block.name,
                "role": block.role.value,
                "component_ids": list(block.component_ids),
                "description": block.description,
                "source": block.source,
            }
        )
    return blocks
