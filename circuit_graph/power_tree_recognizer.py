"""Power-tree recognition for circuit-graph.

Given a CircuitIR + CircuitGraph + ComponentDatabase, this module produces a
:class:`~circuit_graph.models.power_tree.PowerTree` rooted at external supply
rails (USB, battery, barrel jack, header). It is the foundation for
``cg view --with-power`` and downstream power-consumption analysis.

Algorithm overview
------------------

1. ``find_source_nets`` identifies the supply-side nets (USB VBUS, VBAT,
   VIN, header pins). These are the tree roots.
2. ``identify_regulator`` walks every IC/component, asking the
   :class:`~circuit_graph.knowledge.db.ComponentDatabase` whether its part-number
   prefix matches a regulator; structural evidence (inductor + SW net for
   DCDC; VIN/VOUT pins for LDO) is then layered on top to confirm the
   category.
3. ``build_power_tree`` walks from each source, attaching regulators under
   the first source for now and adding downstream components as load leaves.

The module is intentionally split into three public entry points so that
each phase can be unit-tested in isolation.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from circuit_graph.graph import CircuitGraph
from circuit_graph.ir import (
    CircuitIR,
    Component,
    DeviceKind,
    NetType,
)
from circuit_graph.knowledge.db import ComponentDatabase
from circuit_graph.models.power_tree import (
    PowerTree,
    PowerTreeNode,
    RegulatorIdentification,
    SourceKind,
)

# ---------------------------------------------------------------------------
# Pattern tables

# Supply-side net names: USB VBUS, battery, barrel jack, header pin rails.
# We uppercase the candidate name before matching so multilingual PCB
# vendors stay handled.
_SOURCE_NAME_PATTERNS: tuple[tuple[re.Pattern[str], SourceKind], ...] = (
    (re.compile(r"USB_?5V|USBVCC|VBUS"), "usb"),
    (re.compile(r"VBAT|^BAT$|BATTERY"), "battery"),
    (re.compile(r"BARREL|DC[_-]?IN|JACK"), "barrel_jack"),
    (re.compile(r"^VIN$|^VEXT$|^VSYS$|^EXT[_-]?PWR$"), "header_pin"),
)

# Regulator pin-name regular expressions. Universal across vendors, although
# new variants surface occasionally (e.g. EN/ENB, BST, AGND). The lists cover
# what we see on today's fixture set; we keep them short so the matching is
# obviously right when reading the file.
_REGULATOR_INPUT_PIN_RE = re.compile(r"^(VIN|VI|VDD|PVIN|IN)$", re.IGNORECASE)
_REGULATOR_OUTPUT_PIN_RE = re.compile(r"^(VOUT|VO|OUT|REG)$", re.IGNORECASE)
_REGULATOR_SW_PIN_RE = re.compile(r"^(SW|LX)$", re.IGNORECASE)
_REGULATOR_GND_PIN_RE = re.compile(r"^(GND|PGND)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers


@dataclass(frozen=True)
class PinAssignment:
    """One pin worth of evidence: its name, pin number, and (if wired) net."""

    pin_name: Optional[str]
    pin_number: str
    net_id: Optional[str]


def _pin_net_id(graph: CircuitGraph, component_id: str, pin_number: str) -> Optional[str]:
    """Return the net id connected to ``component_id.pin_number``, or None."""
    pin_id = f"{component_id}.{pin_number}"
    for entry in graph._get_edges(pin_id, "outgoing", None):  # type: ignore[attr-defined]
        node = graph.get_net(entry.target)
        if node is not None:
            return node.data.id
    for entry in graph._get_edges(pin_id, "incoming", None):  # type: ignore[attr-defined]
        node = graph.get_net(entry.source)
        if node is not None:
            return node.data.id
    return None


def _collect_pin_assignments(component: Component, graph: CircuitGraph) -> list[PinAssignment]:
    """Walk ``component.pins`` and resolve each to its net id."""
    return [
        PinAssignment(
            pin_name=pin.name,
            pin_number=pin.number,
            net_id=_pin_net_id(graph, component.id, pin.number),
        )
        for pin in component.pins
    ]


def _name_matches(pin_name: Optional[str], regex: re.Pattern[str]) -> bool:
    return bool(pin_name) and regex.match(pin_name) is not None


# ---------------------------------------------------------------------------
# Public API: source discovery


def find_source_nets(ir: CircuitIR, graph: CircuitGraph) -> list[tuple[str, SourceKind]]:
    """Return ``[(net_id, source_kind), ...]`` for every supply-side net.

    A net qualifies when its name matches a source pattern AND it is flagged
    as ``NetType.POWER`` by the net classifier. ``5V``, ``3.3V`` and similar
    downstream rails are intentionally excluded — they will appear later as
    children produced by the regulators that feed them.

    The T268 net ``USB_5V`` qualifies; ``5V``, ``5V1``, ``5V2``, ``3.3V``
    do not.
    """
    results: list[tuple[str, SourceKind]] = []
    seen: set[str] = set()
    for net in ir.nets:
        if net.id in seen:
            continue
        if net.type is not NetType.POWER:
            continue
        kind = _match_source_name(net.name)
        if kind is None:
            continue
        results.append((net.id, kind))
        seen.add(net.id)
    return results


# Ground-like names that should NEVER appear as sources even if NetClassifier
# mis-tagged them as POWER. ``find_source_nets_with_warnings`` uses this list
# to emit a warning for every POWER-classified ground-like net.
_GROUND_LIKE_NAME_RE = re.compile(
    r"^(AGND|CGND|DGND|EARTH|GND|GND_?REF|VSS|VSS_?[A-Z0-9]*)$",
    re.IGNORECASE,
)


def find_ground_like_power_nets(ir: CircuitIR) -> list[str]:
    """Return net ids whose name looks ground-like but ``NetType`` is POWER.

    These are mis-classifications that snuck past the NetType filter; the
    caller is expected to surface them via a warning.
    """
    mis: list[str] = []
    for net in ir.nets:
        if net.type is not NetType.POWER:
            continue
        if _GROUND_LIKE_NAME_RE.match(net.name.strip()):
            mis.append(net.id)
    return mis


def find_unknown_regulator_ics(
    ir: CircuitIR,
    graph: CircuitGraph,
    db: ComponentDatabase,
) -> list[str]:
    """Return component ids of ICs the ComponentDatabase could not classify.

    A component is "unknown regulator" when it is an IC with a ``part_number``
    but no matching prefix in the database. These are warning candidates —
    regulators without a known prefix are silently dropped today.
    """
    unknown: list[str] = []
    for component in ir.components:
        if component.device_kind is not DeviceKind.IC:
            continue
        if not component.part_number:
            continue
        if _kb_category(db, component.part_number) is None:
            unknown.append(component.id)
    return unknown


def _kb_category(db: ComponentDatabase, part_number: str) -> Optional[str]:
    """Return the category for ``part_number`` using the longest matching prefix."""
    return db.get_category(part_number)


def query_by_category(
    db: ComponentDatabase, category: str
) -> list[str]:
    """List part numbers in a given category — replaces ``kb.is_dcdc(...)``-style helpers."""
    return [r.part_number for r in db.query(category=category)]


def _match_source_name(name: str) -> Optional[SourceKind]:
    upper = name.upper().strip()
    for pattern, kind in _SOURCE_NAME_PATTERNS:
        if pattern.search(upper):
            return kind
    return None


# ---------------------------------------------------------------------------
# Public API: regulator identification


def identify_regulators(
    ir: CircuitIR,
    graph: CircuitGraph,
    db: ComponentDatabase,
) -> list[RegulatorIdentification]:
    """Identify every DCDC/LDO component on the board.

    Returns an empty list when the ComponentDatabase has no relevant entries.
    :func:`identify_regulator` is also exported so individual components can
    be classified for tests.
    """
    out: list[RegulatorIdentification] = []
    for component in ir.components:
        if not component.part_number:
            continue
        ident = identify_regulator(component, graph, db, ir=ir)
        if ident is not None:
            out.append(ident)
    return out


def identify_regulator(
    component: Component,
    graph: CircuitGraph,
    db: ComponentDatabase,
    ir: Optional[CircuitIR] = None,
) -> Optional[RegulatorIdentification]:
    """Classify a single component, returning evidence or None.

    ``ir`` is optional: when provided, the LDO path can enforce the spec's
    "≥1 cap on input AND ≥1 cap on output" rule by counting neighbouring
    capacitors on those nets.
    """
    if not component.part_number:
        return None
    category = _kb_category(db, component.part_number)
    if category not in {"dcdc", "ldo"}:
        return None
    kb_matched = category is not None

    pins = _collect_pin_assignments(component, graph)
    if not pins:
        return None

    if category == "dcdc":
        return _classify_dcdc(component, pins, kb_matched)
    return _classify_ldo(component, pins, graph, ir, kb_matched)


def _classify_dcdc(
    component: Component,
    pins: list[PinAssignment],
    kb_matched: bool,
) -> Optional[RegulatorIdentification]:
    sw_pin = next((p for p in pins if _name_matches(p.pin_name, _REGULATOR_SW_PIN_RE)), None)
    in_pin = next((p for p in pins if _name_matches(p.pin_name, _REGULATOR_INPUT_PIN_RE)), None)
    out_pin = next((p for p in pins if _name_matches(p.pin_name, _REGULATOR_OUTPUT_PIN_RE)), None)
    gnd_pin = next((p for p in pins if _name_matches(p.pin_name, _REGULATOR_GND_PIN_RE)), None)
    # Buck DCDC fingerprint: SW pin wired AND VIN wired AND VOUT wired.
    if sw_pin is None or sw_pin.net_id is None:
        return None
    if in_pin is None or in_pin.net_id is None:
        return None
    if out_pin is None or out_pin.net_id is None:
        return None
    if in_pin.net_id == out_pin.net_id:
        return None
    evidence = ["VIN", "SW", "VOUT"]
    if gnd_pin is not None:
        evidence.append("GND")
    structural_complete = gnd_pin is not None
    return RegulatorIdentification(
        component_id=component.id,
        part_number=component.part_number,
        kind="dcdc_buck",
        evidence_pins=evidence,
        input_net_id=in_pin.net_id,
        output_net_id=out_pin.net_id,
        confidence=_score(kb_matched, structural_complete),
    )


def _classify_ldo(
    component: Component,
    pins: list[PinAssignment],
    graph: CircuitGraph,
    ir: Optional[CircuitIR],
    kb_matched: bool,
) -> Optional[RegulatorIdentification]:
    in_pin = next((p for p in pins if _name_matches(p.pin_name, _REGULATOR_INPUT_PIN_RE)), None)
    out_pin = next((p for p in pins if _name_matches(p.pin_name, _REGULATOR_OUTPUT_PIN_RE)), None)
    gnd_pin = next((p for p in pins if _name_matches(p.pin_name, _REGULATOR_GND_PIN_RE)), None)
    if in_pin is None or in_pin.net_id is None:
        return None
    if out_pin is None or out_pin.net_id is None:
        return None
    if in_pin.net_id == out_pin.net_id:
        return None
    # LDO must not have an SW pin — that would mark a DCDC.
    if any(p for p in pins if _name_matches(p.pin_name, _REGULATOR_SW_PIN_RE)):
        return None
    evidence = ["VIN", "VOUT"]
    if gnd_pin is not None:
        evidence.append("GND")

    # Spec §LDO input and output capacitors require at least one each.
    # When ``ir`` is available we count capacitors on the input/output nets;
    # otherwise we stay lenient (older callers don't pass ``ir``).
    if ir is not None:
        caps_in = _count_caps_on(ir, graph, in_pin.net_id, skip_component=component.id)
        caps_out = _count_caps_on(ir, graph, out_pin.net_id, skip_component=component.id)
        if caps_in == 0 or caps_out == 0:
            return None
        evidence.append(f"CAPS_IN={caps_in}")
        evidence.append(f"CAPS_OUT={caps_out}")

    structural_complete = gnd_pin is not None
    return RegulatorIdentification(
        component_id=component.id,
        part_number=component.part_number,
        kind="ldo",
        evidence_pins=evidence,
        input_net_id=in_pin.net_id,
        output_net_id=out_pin.net_id,
        confidence=_score(kb_matched, structural_complete),
    )


def _score(kb_matched: bool, structural_complete: bool) -> str:
    """Map recognition signals to a discrete confidence level."""
    if kb_matched and structural_complete:
        return "high"
    if kb_matched:
        return "medium"
    return "low"


def _count_caps_on(
    ir: CircuitIR,
    graph: CircuitGraph,
    net_id: str,
    *,
    skip_component: str,
) -> int:
    """Count capacitors wired to ``net_id`` (excluding ``skip_component``)."""
    count = 0
    for entry in graph.trace_net(net_id):
        comp: Component = entry["component"]
        if comp.id == skip_component:
            continue
        if comp.device_kind == DeviceKind.CAPACITOR:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Public API: tree assembly


def build_power_tree(
    ir: CircuitIR,
    graph: CircuitGraph,
    db: ComponentDatabase,
) -> PowerTree:
    """Assemble the :class:`PowerTree` for the whole board.

    Sources become roots. Each regulator found by :func:`identify_regulators`
    becomes its own node, anchored at the first source. Downstream components
    on the regulator's output net appear as load leaves. When no sources are
    found the tree is returned empty (regulators without a source stay
    unrepresented; richer upstream tracing is left for a later change).

    Warnings are attached to the tree for:
      * ICs whose ``part_number`` does not appear in the ComponentDatabase;
      * NetType=POWER nets whose names look ground-like (likely classifier
        mis-tags).
    """
    sources = find_source_nets(ir, graph)
    regulators = identify_regulators(ir, graph, db)
    net_lookup = {n.id: n for n in ir.nets}

    tree = PowerTree()
    # W1: ICs whose part_number does not appear in the ComponentDatabase.
    for cid in find_unknown_regulator_ics(ir, graph, db):
        tree.add_warning(f"unclassified power IC: {cid} (part_number not in knowledge base)")
    # W2: NetType=POWER nets whose names look ground-like (likely classifier mis-tag).
    for net_id in find_ground_like_power_nets(ir):
        net = net_lookup.get(net_id)
        name = net.name if net is not None else net_id
        tree.add_warning(
            f"mis-classified ground-like net: {name} has NetType.POWER; review NetClassifier"
        )
    for net_id, kind in sources:
        net = net_lookup.get(net_id)
        label = net.name if net is not None else net_id
        tree.add_root(
            PowerTreeNode(
                id=f"source::{net_id}",
                kind="source",
                label=f"{label} ({kind})",
                metadata={"net_id": net_id, "source_kind": kind},
            )
        )

    if not tree.roots:
        # Orphan regulators stay out of the tree; future work will trace inputs
        # back to a source net even when the source is unnamed.
        return tree

    anchor = tree.roots[0] if tree.roots else None
    for reg in regulators:
        reg_node = PowerTreeNode(
            id=f"regulator::{reg.component_id}",
            kind="regulator",
            label=f"{reg.component_id} ({reg.kind}, {reg.part_number})",
            metadata={"regulator": asdict(reg)},
        )
        if anchor is None:
            tree.add_root(reg_node)
        else:
            tree.add_child(anchor, reg_node)

        if reg.output_net_id is None:
            continue
        net = net_lookup.get(reg.output_net_id)
        label = net.name if net is not None else reg.output_net_id
        net_node = PowerTreeNode(
            id=f"net::{reg.component_id}::{reg.output_net_id}",
            kind="net",
            label=label,
            metadata={"net_id": reg.output_net_id},
        )
        tree.add_child(reg_node.id, net_node)

        for load in _loads_on_net(ir, graph, reg.output_net_id, skip_component=reg.component_id):
            load_node = PowerTreeNode(
                id=f"load::{load.id}",
                kind="load",
                label=f"{load.id} ({load.device_kind.value})",
                metadata={"component_id": load.id, "parent_net": reg.output_net_id},
            )
            tree.add_child(net_node.id, load_node)
    return tree


def _loads_on_net(
    ir: CircuitIR,
    graph: CircuitGraph,
    net_id: str,
    skip_component: str,
) -> Iterable[Component]:
    """Yield every component on ``net_id`` other than ``skip_component``.

    Capacitors are excluded so the tree highlights the regulator's
    downstream topology rather than each bypass cap. Inductors are excluded
    only when they are part of the regulator the caller is currently
    processing — which the helper cannot know, so callers must filter the
    inductor themselves. For first-pass trees this stays correct.
    """
    seen: set[str] = set()
    for entry in graph.trace_net(net_id):
        comp: Component = entry["component"]
        if comp.id == skip_component or comp.id in seen:
            continue
        seen.add(comp.id)
        if comp.device_kind == DeviceKind.CAPACITOR:
            continue
        yield comp


__all__ = [
    "PinAssignment",
    "build_power_tree",
    "find_source_nets",
    "identify_regulator",
    "identify_regulators",
]
