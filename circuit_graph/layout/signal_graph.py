"""Build a signal-flow graph from CircuitIR and assign component columns."""

from __future__ import annotations

import logging

import networkx as nx

from circuit_graph.ir import CircuitIR, Component, Pin
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.exceptions import LayoutConfigError

logger = logging.getLogger(__name__)

PASSIVE_KINDS = {"Resistor", "Capacitor", "Inductor", "Diode"}


def build_signal_graph(ir: CircuitIR, config: LayoutConfig) -> nx.DiGraph:
    """Return a directed graph where edges follow signal flow.

    Nodes are component ids. Edge u → v means signal flows from u to v.
    Passive two-pin devices (R, C, L, D) are traversed bidirectionally;
    their incident edges are marked ``passive=True``.

    Implementation note: the original plan (2026-07-11-schematic-layout) used a
    simpler "same-net passive pins get bidirectional edges plus source→target"
    rule. The current implementation adds ``input_driven_comps``, a fixed-point
    ``driven_passives`` discovery, and ``true_sink_comps`` so that chains of
    passives and feedback-compensation paths (e.g. CF in the LM321 CCS) receive
    a consistent signal direction. Tests still pass; revisit if the simpler
    algorithm proves sufficient.
    """
    g = nx.DiGraph()
    for comp in ir.components:
        g.add_node(comp.id)

    net_pins = _group_pins_by_net(ir)
    comp_by_id = {c.id: c for c in ir.components}

    # Map net names → net ids for IO config lookups (io.inputs/outouts use names).
    net_name_to_id: dict[str, str] = {n.name: n.id for n in ir.nets}
    io_input_net_ids = {net_name_to_id[n] for n in config.io.inputs if n in net_name_to_id}
    io_output_net_ids = {net_name_to_id[n] for n in config.io.outputs if n in net_name_to_id}

    # Components that have a pin on an IO input net are "input-driven" —
    # they receive signal from outside and forward it downstream.
    input_driven_comps: set[str] = set()
    for net_id, pins in net_pins.items():
        if net_id in io_input_net_ids:
            for p in pins:
                input_driven_comps.add(p.component_id)

    # Discover passives that are "driven" by a source on any of their nets.
    # Fixed-point to handle chains of passives.
    driven_passives: set[str] = set()
    changed = True
    while changed:
        changed = False
        for _net_id, pins in net_pins.items():
            has_source = any(
                _is_source_pin(p, config) or p.component_id in driven_passives
                for p in pins
            )
            if has_source:
                for p in pins:
                    cid = p.component_id
                    if (
                        _is_passive_component(comp_by_id.get(cid))
                        and cid not in driven_passives
                    ):
                        driven_passives.add(cid)
                        changed = True

    # Identify true-sink components: those without any output/power pins.
    true_sink_comps: set[str] = set()
    for comp in ir.components:
        if not any(p.type in ("output", "power") for p in comp.pins):
            true_sink_comps.add(comp.id)

    # Build edges.
    for net_id, pins in net_pins.items():
        source_pins = [p for p in pins if _is_source_pin(p, config)]
        passive_pins = [
            p
            for p in pins
            if _is_passive_component(comp_by_id.get(p.component_id))
        ]
        sink_pins = [
            p
            for p in pins
            if not _is_source_pin(p, config)
            and not _is_passive_component(comp_by_id.get(p.component_id))
        ]

        # Passive-passive edges. Use IO config to prefer a direction:
        # input-driven passive → other passive on the same net.
        if len(passive_pins) >= 2:
            for i, src in enumerate(passive_pins):
                for tgt in passive_pins[i + 1 :]:
                    if src.component_id == tgt.component_id:
                        continue
                    src_input = src.component_id in input_driven_comps
                    tgt_input = tgt.component_id in input_driven_comps
                    if src_input and not tgt_input:
                        g.add_edge(
                            src.component_id, tgt.component_id, net=net_id, passive=True
                        )
                    elif tgt_input and not src_input:
                        g.add_edge(
                            tgt.component_id, src.component_id, net=net_id, passive=True
                        )
                    else:
                        g.add_edge(
                            src.component_id, tgt.component_id, net=net_id, passive=True
                        )
                        g.add_edge(
                            tgt.component_id, src.component_id, net=net_id, passive=True
                        )

        # Source → sink.
        for src in source_pins:
            for tgt in sink_pins:
                if src.component_id != tgt.component_id:
                    g.add_edge(src.component_id, tgt.component_id, net=net_id)

        # Source → passive (signal enters a passive from an active source).
        for src in source_pins:
            for pt in passive_pins:
                if src.component_id != pt.component_id:
                    g.add_edge(src.component_id, pt.component_id, net=net_id)

        # Driven passive → true-sink (signal exits passive to downstream load).
        for pt in passive_pins:
            if pt.component_id in driven_passives:
                for tgt in sink_pins:
                    if pt.component_id != tgt.component_id and tgt.component_id in true_sink_comps:
                        g.add_edge(pt.component_id, tgt.component_id, net=net_id)

    return g


def assign_columns(g: nx.DiGraph, config: LayoutConfig) -> dict[str, int]:
    """Return component_id → column index (0 = inputs, increasing to right).

    Column assignment follows the design spec:
      - column(comp) = longest directed path length from any input-source node
      - nodes with no input path are placed in a center column between inputs
        and outputs
      - feedback cycles are broken by dominant direction (warning emitted)
    """
    if g.number_of_nodes() == 0:
        return {}

    # Break cycles using dominant direction heuristic.
    try:
        topo = list(nx.topological_sort(g))
        acyclic_g = g
    except nx.NetworkXUnfeasible:
        acyclic_g = _break_cycles_by_dominant_direction(g)
        topo = list(nx.topological_sort(acyclic_g))

    # Identify input-source nodes: no predecessors AND at least one successor.
    # Isolated nodes (in=0, out=0) are not input sources.
    input_sources = [
        n for n in topo if acyclic_g.in_degree(n) == 0 and acyclic_g.out_degree(n) > 0
    ]

    # Longest path length from any input source.
    columns: dict[str, int] = {}
    for node in topo:
        preds = list(acyclic_g.predecessors(node))
        if preds:
            columns[node] = max(columns[p] for p in preds) + 1
        else:
            columns[node] = 0

    # Center-column handling for nodes unreachable from inputs.
    max_col = max(columns.values()) if columns else 0
    center_col = max(1, max_col // 2)
    for node, col in columns.items():
        if node not in input_sources and col == 0:
            if acyclic_g.out_degree(node) > 0:
                # Has outputs but no inputs: shift toward output side.
                dist_to_output = _longest_distance_to_outputs(acyclic_g, node)
                if dist_to_output is not None:
                    columns[node] = max(0, max_col - dist_to_output)
                if columns[node] == 0:
                    columns[node] = center_col
            else:
                # Isolated: no inputs, no outputs — place in center.
                columns[node] = center_col

    return columns


def _break_cycles_by_dominant_direction(g: nx.DiGraph) -> nx.DiGraph:
    """Return an acyclic copy of ``g`` by removing one edge from each cycle.

    v1 heuristic: remove the edge whose target has the highest total in-degree
    within the cycle (i.e. the node that is most "driven" by other cycle
    members is treated as the sink side). Emit a warning for each removed edge.
    """
    acyclic = g.copy()
    while True:
        try:
            cycle = nx.find_cycle(acyclic, orientation="original")
        except nx.NetworkXNoCycle:
            break

        cycle_nodes = {u for u, _, _ in cycle} | {v for _, v, _ in cycle}
        # Score each edge by how many cycle-internal predecessors the target has.
        def edge_score(edge):
            _, v, _ = edge
            return sum(1 for p in acyclic.predecessors(v) if p in cycle_nodes)

        victim = max(cycle, key=edge_score)
        u, v, _ = victim
        logger.warning("Breaking feedback cycle by removing edge %s -> %s", u, v)
        acyclic.remove_edge(u, v)
    return acyclic


def _longest_distance_to_outputs(g: nx.DiGraph, node: str) -> int | None:
    """Return longest path length from ``node`` to any sink (node with no successors).

    Assumes ``g`` is a DAG.
    """
    if g.out_degree(node) == 0:
        return 0
    try:
        # Longest path in a DAG = negate weights and find shortest path.
        lengths = nx.single_source_bellman_ford_path_length(
            g, node, weight=lambda u, v, d: -1
        )
        return int(-min(lengths.values()))
    except Exception:
        return None


def _group_pins_by_net(ir: CircuitIR) -> dict[str, list[PinWithComp]]:
    pins_by_net: dict[str, list[PinWithComp]] = {}
    pin_by_id = {p.id: p for comp in ir.components for p in comp.pins}

    for conn in ir.connections:
        pin = pin_by_id.get(conn.pin_id)
        if pin is None:
            continue
        comp_id, _ = conn.pin_id.rsplit(".", 1)
        entry = PinWithComp(pin=pin, component_id=comp_id)
        pins_by_net.setdefault(conn.net_id, []).append(entry)

    return pins_by_net


class PinWithComp:
    def __init__(self, pin: Pin, component_id: str) -> None:
        self.pin = pin
        self.component_id = component_id


def _is_source_pin(pc: PinWithComp, config: LayoutConfig) -> bool:
    pin = pc.pin
    if pin.type in ("output", "power"):
        return True
    # Treat explicit power-source names as sources even if pin.type is passive/unknown.
    if pin.name and pin.name.upper() in {"VCC", "VDD", "VSUP", "+12V", "OUT", "OUTPUT"}:
        return True
    return False


def _is_passive_component(comp: Component | None) -> bool:
    """True if the component is a passive two-pin device."""
    if comp is None:
        return False
    return comp.device_kind.value in PASSIVE_KINDS


def validate_io_nets(ir: CircuitIR, config: LayoutConfig) -> None:
    """Raise LayoutConfigError if configured IO nets are missing."""
    net_names = {n.name for n in ir.nets}
    missing = []
    for name in config.io.inputs + config.io.outputs + config.io.power_top + config.io.power_bottom:
        if name not in net_names:
            missing.append(name)
    if missing:
        raise LayoutConfigError(
            f"configured IO nets not found in IR: {missing}; available: {sorted(net_names)}"
        )
