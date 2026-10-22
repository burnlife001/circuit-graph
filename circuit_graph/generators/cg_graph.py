"""cg-graph circuit graph generator.

This module produces a single-file, LLM-friendly JSON view of a circuit from a
populated ``CircuitIR``.  The v2.1 format is reference-based: every component
and every net is defined exactly once, and relationships are expressed by
reference (pins reference nets by name; nets reference members by component
id).  Each net carries a ``degree`` (fanout) so consumers can judge rail-ness
without name-based heuristics.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from circuit_graph.ir import (
    CapacitanceValue,
    CircuitIR,
    Component,
    DeviceKind,
    InductanceValue,
    Net,
    NetType,
    ResistanceValue,
    UnknownValue,
)

if TYPE_CHECKING:
    from circuit_graph.knowledge.db import ComponentDatabase

# Nets at or above this fanout are treated as global rails during ``view_component``
# traversal: they are still listed (with full members + degree) but their members
# are NOT seeded as next-hop neighbours, preventing ego-graph explosion via GND etc.
RAIL_DEGREE_THRESHOLD = 16


def generate_cg_graph(ir: CircuitIR, db: ComponentDatabase | None = None) -> dict[str, Any]:
    """Generate the cg-graph (v2.1) JSON structure for ``ir``.

    When *db* is provided, non-passive components are enriched with
    ``kb_description``, ``kb_category``, ``kb_subcategory`` and
    ``kb_channel_type`` from the knowledge base.
    """
    ir = _ensure_enriched(ir)
    net_map = {net.id: net for net in ir.nets}
    comp_map = {comp.id: comp for comp in ir.components}
    net_members = _build_net_members(ir, comp_map)
    pin_to_net = _build_pin_to_net(ir)

    components: dict[str, dict[str, Any]] = {}
    for comp in ir.components:
        components[comp.id] = _component_entry(comp, net_map, pin_to_net, db=db)

    nets: dict[str, dict[str, Any]] = {}
    for net in ir.nets:
        nets[net.name] = _net_entry(net, net_members.get(net.id, []))

    return {
        "format": "cg-graph",
        "version": "2.1",
        "circuit": _circuit_block(ir, net_members),
        "components": components,
        "nets": nets,
    }


def view_component(
    ir: CircuitIR,
    designator: str,
    hops: int = 2,
    db: ComponentDatabase | None = None,
    max_members: int = 20,
) -> dict[str, Any] | None:
    """Return a scoped ego-subgraph for ``designator`` using the v2.1 model.

    Traversal expands ``hops`` levels but does NOT cross nets whose fanout
    degree is ``>= RAIL_DEGREE_THRESHOLD`` (rails such as GND): those nets are
    still listed in the ``nets`` block with full membership and degree, they
    are simply not used as next-hop seeds.  Representation is identical to the
    whole-board format (single component definition + single net definition).

    Size guard: on high-degree nets (more than ``max_members`` components)
    only ego members are listed, the rest folds to a trailing ``"(+N more)"``
    entry; components reached beyond the focal's direct neighbourhood are
    emitted as ``{"id": ...}`` stubs instead of full entries.  This keeps
    ``view`` output bounded on boards where a 26-pin IC touches rails with
    dozens of mounted components.

    When *db* is provided, every non-passive component with a part_number is
    enriched with ``kb_description``, ``kb_category``, ``kb_subcategory`` and
    ``kb_channel_type`` from the knowledge base.
    """
    ir = _ensure_enriched(ir)
    comp_map = {comp.id: comp for comp in ir.components}
    if designator not in comp_map:
        return None

    net_map = {net.id: net for net in ir.nets}
    net_members = _build_net_members(ir, comp_map)
    pin_to_net = _build_pin_to_net(ir)
    comp_nets = _build_component_nets(ir)

    # Breadth-first expansion, refusing to traverse through high-fanout rails.
    reached: set[str] = {designator}
    frontier: set[str] = {designator}
    for _ in range(max(hops, 0)):
        next_frontier: set[str] = set()
        for cid in frontier:
            for net_id in comp_nets.get(cid, ()):
                if len(net_members.get(net_id, [])) >= RAIL_DEGREE_THRESHOLD:
                    continue  # list-only rail: do not seed members as next hop
                for member in net_members.get(net_id, []):
                    if member not in reached:
                        reached.add(member)
                        next_frontier.add(member)
        frontier = next_frontier
        if not frontier:
            break

    # Full-detail entries go to the focal itself and to components on the
    # focal's own low-degree nets; neighbours reached only through high-degree
    # rails (e.g. everything hanging off GND) and multi-hop components become
    # ``{"id": ...}`` stubs.
    full_detail: set[str] = {designator}
    for net_id in comp_nets.get(designator, ()):
        if len(net_members.get(net_id, [])) <= max_members:
            full_detail.update(net_members.get(net_id, ()))

    # A net is view-relevant when it carries the focal or connects at least
    # two fully-detailed components; a big IC's private nets (only it sits on
    # them) stay out of the scoped view, so its entry does not expand all of
    # its pins.
    touched_net_ids: set[str] = {
        net_id
        for cid in full_detail
        for net_id in comp_nets.get(cid, ())
        if designator in net_members.get(net_id, ())
        or len(set(net_members.get(net_id, ())) & full_detail) >= 2
    }

    components: dict[str, dict[str, Any]] = {}
    for cid in sorted(reached):
        comp = comp_map[cid]
        if cid in full_detail:
            components[cid] = _component_entry(
                comp, net_map, pin_to_net, db=db, only_nets=touched_net_ids
            )
        else:
            components[cid] = {"id": cid}

    nets: dict[str, dict[str, Any]] = {}
    for net_id in sorted(touched_net_ids):
        net = net_map.get(net_id)
        if net is None:
            continue
        members = net_members.get(net_id, [])
        if len(members) > max_members:
            visible = [m for m in members if m in full_detail]
            entry = _net_entry(net, [*visible, f"(+{len(members) - len(visible)} more)"])
            entry["degree"] = len(members)  # true fanout, not the folded list
        else:
            entry = _net_entry(net, members)
        nets[net.name] = entry

    return {
        "format": "cg-graph",
        "version": "2.1",
        "designator": designator,
        "hops": hops,
        "focal": designator,
        "components": components,
        "nets": nets,
    }


def write_cg_graph(
    ir: CircuitIR,
    output_path: Path | str,
    *,
    pretty: bool = False,
    db: ComponentDatabase | None = None,
) -> None:
    """Write the cg-graph JSON representation of ``ir`` to ``output_path``.

    Defaults to compact JSON (``indent=None``) which roughly halves the byte
    size and is what an agent should consume.  Pass ``pretty=True`` for the
    human-readable, indented form.

    When *db* is provided, non-passive components are enriched with KB metadata.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = generate_cg_graph(ir, db=db)
    indent = 2 if pretty else None
    path.write_text(json.dumps(data, indent=indent, ensure_ascii=False), encoding="utf-8")


def _build_pin_to_net(ir: CircuitIR) -> dict[str, str]:
    """Map pin id -> net id using the connection list."""
    mapping: dict[str, str] = {}
    for conn in ir.connections:
        mapping[conn.pin_id] = conn.net_id
    return mapping


def _build_net_members(ir: CircuitIR, comp_map: dict[str, Component]) -> dict[str, list[str]]:
    """Map net id -> sorted list of distinct component id strings on that net."""
    members: dict[str, set[str]] = {}
    for conn in ir.connections:
        comp_id = _split_pin_ref(conn.pin_id)[0]
        if not comp_id or comp_id not in comp_map:
            continue
        members.setdefault(conn.net_id, set()).add(comp_id)
    return {net_id: sorted(ids) for net_id, ids in members.items()}


def _build_component_nets(ir: CircuitIR) -> dict[str, set[str]]:
    """Map component id -> set of net ids it connects to."""
    comp_nets: dict[str, set[str]] = {}
    for conn in ir.connections:
        comp_id = _split_pin_ref(conn.pin_id)[0]
        if not comp_id:
            continue
        comp_nets.setdefault(comp_id, set()).add(conn.net_id)
    return comp_nets


def _ensure_enriched(ir: CircuitIR) -> CircuitIR:
    """Return an enriched IR, running enrichers only when needed.

    The cg-graph format relies on normalized values, classified nets and
    topology blocks.  This helper makes the generator safe to call on a bare
    parsed IR while avoiding duplicate work when the caller already enriched it.
    Enrichers are imported lazily so that importing this module (and any
    warm-cache CLI path that skips enrichment) does not pull in the matcher.
    """
    from circuit_graph.enrichers import run_enrichers
    from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher
    from circuit_graph.enrichers.net_classifier import NetClassifier
    from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
    from circuit_graph.enrichers.value_normalizer import ValueNormalizer

    enrichers: list[Any] = []
    if any(c.raw_value and c.value is None for c in ir.components):
        enrichers.append(ValueNormalizer())
    if any(n.type is None for n in ir.nets):
        enrichers.append(NetClassifier())
    # EdgeSemanticEnricher only adds work when connections lack metadata; the
    # `can_enrich` predicate inside run_enrichers will skip it if everything is
    # already annotated.
    if any(not (c.metadata or {}).get("edge_semantic") for c in ir.connections):
        enrichers.append(EdgeSemanticEnricher())
    # TopologyRecognizer is idempotent by block id, so it is safe to run when
    # no blocks have been emitted yet.
    if not ir.blocks:
        enrichers.append(TopologyRecognizer())
    if enrichers:
        return run_enrichers(ir, enrichers).ir
    return ir


def _component_entry(
    comp: Component,
    net_map: dict[str, Net],
    pin_to_net: dict[str, str],
    db: ComponentDatabase | None = None,
    only_nets: set[str] | None = None,
) -> dict[str, Any]:
    """Build a self-contained cg-graph component entry (no neighbour inlining).

    When *db* is provided and the component carries a *raw_value* (non-passive
    part number), the entry is enriched with ``kb_description``, ``kb_category``,
    ``kb_subcategory`` and ``kb_channel_type`` from the knowledge base so that
    MOSFET channel type, IC function and other structured metadata are surfaced
    to consumers without a separate DB lookup.

    When *only_nets* is given (scoped views), only pins on those nets are
    listed — a 48-pin MCU contributing two PWM signals no longer expands all
    of its pins into the ego subgraph.
    """
    entry: dict[str, Any] = {"id": comp.id}

    comp_type = _normalize_type(comp.device_kind)
    if comp_type != "unknown":
        entry["type"] = comp_type

    if isinstance(comp.value, (ResistanceValue, CapacitanceValue, InductanceValue)):
        value_text = _format_component_value(comp)
        if value_text:
            entry["value"] = value_text
        if only_nets is None:  # scoped views stay lean: "value" already carries it
            numeric = _numeric_value_field(comp)
            if numeric is not None:
                entry[numeric["key"]] = numeric["value"]
    elif comp.raw_value:
        entry["part_number"] = comp.raw_value
        # ─ KB enrichment ──────────────────────────────────────────
        if db is not None:
            rec = db.get_component(comp.raw_value)
            if rec is not None:
                if rec.description:
                    entry["kb_description"] = rec.description
                if rec.category:
                    entry["kb_category"] = rec.category
                if rec.subcategory:
                    entry["kb_subcategory"] = rec.subcategory
                if rec.channel_type:
                    entry["kb_channel_type"] = rec.channel_type

    if comp.footprint:
        entry["package"] = comp.footprint

    pins_out: list[dict[str, Any]] = []
    for pin in comp.pins:
        net_id = pin_to_net.get(pin.id)
        if net_id is None:
            continue
        if only_nets is not None and net_id not in only_nets:
            continue
        net = net_map.get(net_id)
        if net is None:
            continue
        pin_entry: dict[str, Any] = {"pin": pin.number, "net": net.name}
        # Surface the KB-resolved pin function name (PinNameResolver) when
        # available.  This is what makes `cg view` reports list things like
        # "1 (GND)" / "2 (OUTPUT)" instead of just the net name.
        if pin.name:
            pin_entry["name"] = pin.name
        pins_out.append(pin_entry)
    entry["pins"] = pins_out

    return entry


def _net_entry(net: Net, members: list[str]) -> dict[str, Any]:
    """Build a cg-graph net entry: kind + degree + id-only members.

    When the NetClassifier recorded how the kind was derived, that
    provenance is surfaced under ``classification`` so consumers can tell
    rule-based guesses from facts.
    """
    entry: dict[str, Any] = {"degree": len(members), "members": members}
    kind = _normalize_net_kind(net.type)
    if kind != "unknown":
        entry["kind"] = kind
        classification = net.metadata.get("classification")
        if isinstance(classification, dict):
            entry["classification"] = classification
    return entry


def _circuit_block(ir: CircuitIR, net_members: dict[str, list[str]]) -> dict[str, Any]:
    """Build the top-level circuit metadata block."""
    name = Path(ir.meta.source).stem if ir.meta.source else "circuit"
    total_components = len(ir.components)
    total_nets = len(ir.nets)
    main_ics = sorted(comp.id for comp in ir.components if comp.device_kind == DeviceKind.IC)
    blocks = [block.name for block in ir.blocks if block.name]

    summary_parts = [f"{name}: {total_components} components, {total_nets} nets"]
    if main_ics:
        summary_parts.append(f"main ICs: {', '.join(main_ics)}")
    summary = "; ".join(summary_parts) + " (heuristic)"

    result: dict[str, Any] = {
        "name": name,
        "summary": summary,
        "total_components": total_components,
        "total_nets": total_nets,
    }
    if main_ics:
        result["main_ics"] = main_ics
    if blocks:
        result["blocks"] = blocks

    rails = sorted(
        net.name for net in ir.nets if len(net_members.get(net.id, [])) >= RAIL_DEGREE_THRESHOLD
    )
    if rails:
        result["rails"] = rails

    return result


def _normalize_type(device_kind: DeviceKind) -> str:
    return device_kind.value.lower()


def _normalize_net_kind(net_type: NetType | None) -> str:
    if net_type is None:
        return "unknown"
    if net_type == NetType.GND:
        return "ground"
    return net_type.value


def _format_component_value(comp: Component) -> str | None:
    """Render a compact human-readable value, omitting the field when absent."""
    if comp.value is None:
        return comp.raw_value or None
    if isinstance(comp.value, UnknownValue):
        return comp.raw_value or comp.value.raw or None
    val = comp.value
    if isinstance(val, ResistanceValue):
        mag, unit = _resistance_presentation(val)
        text = f"{_strip_trailing_zeros(mag)}{unit}"
        if val.tolerance is not None:
            text += f" ±{val.tolerance * 100:.0f}%"
        return text
    if isinstance(val, CapacitanceValue):
        text = f"{_strip_trailing_zeros(val.magnitude)}{val.unit}"
        if val.voltage_rating is not None:
            text += f" / {val.voltage_rating}V"
        if val.tolerance is not None:
            text += f" ±{val.tolerance * 100:.0f}%"
        return text
    if isinstance(val, InductanceValue):
        text = f"{_strip_trailing_zeros(val.magnitude)}{val.unit}"
        if val.tolerance is not None:
            text += f" ±{val.tolerance * 100:.0f}%"
        return text
    return comp.raw_value or None


def _resistance_presentation(val: ResistanceValue) -> tuple[float, str]:
    mag = val.magnitude
    if mag >= 1_000_000:
        return mag / 1_000_000, "MΩ"
    if mag >= 1000:
        return mag / 1000, "kΩ"
    return mag, "Ω"


def _strip_trailing_zeros(value: float) -> str:
    """Return ``value`` as a string without unnecessary trailing zeros."""
    text = f"{value:.3f}"
    text = text.rstrip("0").rstrip(".")
    return text or "0"


def _numeric_value_field(comp: Component) -> dict[str, Any] | None:
    """Return {"key": "ohms"/"farads"/"henries", "value": float} or None.

    The value is rounded to 6 significant digits so JSON output does not
    carry float-repr noise like ``9.999999999999999e-05``.
    """
    val = comp.value
    if isinstance(val, ResistanceValue):
        return {"key": "ohms", "value": _sig6(_to_ohms(val))}
    if isinstance(val, CapacitanceValue):
        return {"key": "farads", "value": _sig6(_to_farads(val))}
    if isinstance(val, InductanceValue):
        return {"key": "henries", "value": _sig6(_to_henries(val))}
    return None


def _sig6(value: float) -> float:
    """Round to 6 significant digits, keeping the result a plain float."""
    if value == 0:
        return 0.0
    digits = 6 - int(math.floor(math.log10(abs(value)))) - 1
    return round(value, max(digits, 0))


def _to_ohms(val: ResistanceValue) -> float:
    if val.unit == "kΩ":
        return val.magnitude * 1_000
    if val.unit == "MΩ":
        return val.magnitude * 1_000_000
    return val.magnitude


def _to_farads(val: CapacitanceValue) -> float:
    multipliers = {"pF": 1e-12, "nF": 1e-9, "µF": 1e-6}
    return val.magnitude * multipliers.get(val.unit, 1e-6)


def _to_henries(val: InductanceValue) -> float:
    multipliers = {"nH": 1e-9, "µH": 1e-6, "mH": 1e-3}
    return val.magnitude * multipliers.get(val.unit, 1e-6)


def _split_pin_ref(pin_id: str) -> tuple[str, str]:
    if "." not in pin_id:
        return "", ""
    comp_id, _, pin_num = pin_id.rpartition(".")
    return comp_id, pin_num
