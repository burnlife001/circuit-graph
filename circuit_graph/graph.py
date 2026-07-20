"""NetworkX-based heterogeneous circuit graph builder.

The graph contains ``component``, ``pin``, ``net`` and ``functional_block``
nodes connected by typed edges.  All graph operations are derived from the
authoritative CircuitIR without mutating it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from circuit_graph.ir import (
    CircuitIR,
    Component,
    EdgeKind,
    GraphEdge,
    GraphNode,
    Net,
    NodeKind,
    Provenance,
    Subgraph,
    TraversalOptions,
    parse_pin_id,
)


@dataclass
class GraphLoadResult:
    graph: CircuitGraph
    warnings: list[str] = field(default_factory=list)


class CircuitGraph:
    """Heterogeneous circuit graph backed by NetworkX."""

    def __init__(self) -> None:
        self._g = nx.DiGraph()
        self._node_map: dict[str, GraphNode] = {}
        self._edge_map: dict[str, GraphEdge] = {}

    def load(self, ir: CircuitIR) -> GraphLoadResult:
        """Populate the graph from ``ir`` and return warnings."""
        self._g.clear()
        self._node_map.clear()
        self._edge_map.clear()
        warnings: list[str] = []

        # Component and pin nodes with has_pin edges.
        pin_set: set[str] = set()
        component_map: dict[str, Component] = {}
        for comp in ir.components:
            component_map[comp.id] = comp
            self._add_node(comp.id, NodeKind.COMPONENT, comp)
            for pin in comp.pins:
                pin_set.add(pin.id)
                self._add_node(pin.id, NodeKind.PIN, pin)
                self._add_edge(comp.id, pin.id, EdgeKind.HAS_PIN, Provenance.NETLIST)

        # Net nodes.
        net_map: dict[str, Net] = {}
        for net in ir.nets:
            net_map[net.id] = net
            self._add_node(net.id, NodeKind.NET, net)

        # Connects_to edges from Connection list; also populate pin.net_id.
        connection_pin_ids: set[str] = set()
        for conn in ir.connections:
            connection_pin_ids.add(conn.pin_id)
            comp_id, pin_num = parse_pin_id(conn.pin_id)
            if comp_id is None or pin_num is None:
                warnings.append(f"Invalid pinId format: {conn.pin_id}")
                continue
            comp = component_map.get(comp_id)
            if comp is None:
                warnings.append(
                    f"Connection references unknown component: {comp_id} (pinId={conn.pin_id})"
                )
                continue
            pin = next((p for p in comp.pins if p.number == pin_num), None)
            if pin is None:
                warnings.append(f"Connection references unknown pin: {conn.pin_id}")
                continue
            if conn.net_id not in net_map:
                warnings.append(
                    f"Connection references unknown net: {conn.net_id} (pinId={conn.pin_id})"
                )
                continue
            # Note: we do not mutate the IR; net_id is looked up on demand.
            self._add_edge(
                conn.pin_id,
                conn.net_id,
                EdgeKind.CONNECTS_TO,
                conn.provenance,
                metadata=dict(conn.metadata or {}),
            )

        # Orphan pin detection.
        for comp in ir.components:
            for pin in comp.pins:
                if pin.id not in connection_pin_ids:
                    warnings.append(f"GRAPH_ORPHAN_PIN: {pin.id} has no connections")

        # Functional block nodes and belongs_to edges.
        for block in ir.blocks:
            self._add_node(block.id, NodeKind.FUNCTIONAL_BLOCK, block)
            for comp_id in block.component_ids:
                if comp_id in self._node_map:
                    self._add_edge(
                        comp_id,
                        block.id,
                        EdgeKind.BELONGS_TO,
                        provenance=None,
                    )

        # Semantic edges.
        seq = 0
        for se in ir.semantic_edges:
            kind = EdgeKind(se.kind) if se.kind in EdgeKind.__members__.values() else None
            if kind is None:
                warnings.append(f"Unknown semantic edge kind: {se.kind}")
                continue
            self._add_edge(se.source, se.target, kind, se.provenance, seq=seq)
            seq += 1

        return GraphLoadResult(graph=self, warnings=warnings)

    def _add_node(self, node_id: str, kind: NodeKind, data: Any) -> None:
        node = GraphNode(id=node_id, kind=kind, data=data)
        self._node_map[node_id] = node
        self._g.add_node(node_id, kind=kind.value, data=data)

    def _add_edge(
        self,
        source: str,
        target: str,
        kind: EdgeKind,
        provenance: Provenance | None = None,
        seq: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        edge_id = f"{source}::{target}::{kind.value}::{seq}"
        edge = GraphEdge(
            id=edge_id,
            source=source,
            target=target,
            kind=kind,
            provenance=provenance,
            metadata=dict(metadata) if metadata else {},
        )
        self._edge_map[edge_id] = edge
        self._g.add_edge(
            source,
            target,
            kind=kind.value,
            provenance=str(provenance) if provenance else None,
            edge_id=edge_id,
        )

    def get_component(self, node_id: str) -> GraphNode | None:
        node = self._node_map.get(node_id)
        return node if node and node.kind == NodeKind.COMPONENT else None

    def get_net(self, node_id: str) -> GraphNode | None:
        node = self._node_map.get(node_id)
        return node if node and node.kind == NodeKind.NET else None

    def get_pin(self, node_id: str) -> GraphNode | None:
        node = self._node_map.get(node_id)
        return node if node and node.kind == NodeKind.PIN else None

    def get_block(self, node_id: str) -> GraphNode | None:
        node = self._node_map.get(node_id)
        return node if node and node.kind == NodeKind.FUNCTIONAL_BLOCK else None

    def is_component(self, node: GraphNode) -> bool:
        return node.kind == NodeKind.COMPONENT

    def is_net(self, node: GraphNode) -> bool:
        return node.kind == NodeKind.NET

    def is_pin(self, node: GraphNode) -> bool:
        return node.kind == NodeKind.PIN

    def is_block(self, node: GraphNode) -> bool:
        return node.kind == NodeKind.FUNCTIONAL_BLOCK

    def nodes(self) -> list[GraphNode]:
        return list(self._node_map.values())

    def edges(self) -> list[GraphEdge]:
        return list(self._edge_map.values())

    def neighbors(self, node_id: str, edge_kinds: list[EdgeKind] | None = None) -> list[GraphNode]:
        edges = self._get_edges(node_id, "both", edge_kinds)
        neighbor_ids: set[str] = set()
        for edge in edges:
            if edge.source != node_id:
                neighbor_ids.add(edge.source)
            if edge.target != node_id:
                neighbor_ids.add(edge.target)
        return [self._node_map[nid] for nid in neighbor_ids if nid in self._node_map]

    def trace_net(self, net_id: str) -> list[dict[str, Any]]:
        net = self.get_net(net_id)
        if net is None:
            return []
        result: list[dict[str, Any]] = []
        for edge in self._get_edges(net_id, "both", [EdgeKind.CONNECTS_TO]):
            pin_id = edge.target if edge.source == net_id else edge.source
            pin_node = self.get_pin(pin_id)
            if pin_node is None:
                continue
            comp_id = parse_pin_id(pin_id)[0]
            comp_node = self.get_component(comp_id) if comp_id else None
            if comp_node is None:
                continue
            result.append({"pin": pin_node.data, "component": comp_node.data})
        return result

    def trace_component(self, component_id: str) -> list[dict[str, Any]]:
        comp = self.get_component(component_id)
        if comp is None:
            return []
        result: list[dict[str, Any]] = []
        for pin in comp.data.pins:
            # Find the net connected to this pin via connects_to edges.
            for edge in self._get_edges(pin.id, "outgoing", [EdgeKind.CONNECTS_TO]):
                net_node = self.get_net(edge.target)
                if net_node is not None:
                    result.append({"pin": pin, "net": net_node.data})
                    break
        return result

    def path_between(self, start_pin_id: str, end_pin_id: str) -> list[str] | None:
        """Return the shortest path from ``start_pin_id`` to ``end_pin_id`` using BFS.

        The path traverses the underlying undirected view of the graph so that
        pins, nets and components can be reached in either direction.
        """
        if start_pin_id not in self._node_map or end_pin_id not in self._node_map:
            return None
        undirected = self._g.to_undirected()
        try:
            path = nx.shortest_path(undirected, source=start_pin_id, target=end_pin_id)
        except nx.NetworkXNoPath:
            return None
        return list(path)

    def traverse_bfs(self, start_id: str, options: TraversalOptions | None = None) -> Subgraph:
        return self._traverse(start_id, "bfs", options or TraversalOptions())

    def traverse_dfs(self, start_id: str, options: TraversalOptions | None = None) -> Subgraph:
        return self._traverse(start_id, "dfs", options or TraversalOptions())

    def _traverse(
        self,
        start_id: str,
        mode: str,
        options: TraversalOptions,
    ) -> Subgraph:
        direction = options.direction
        edge_kinds = options.edge_kinds
        node_kinds = options.node_kinds
        max_depth = options.max_depth if options.max_depth is not None else float("inf")
        limit = options.limit if options.limit is not None else float("inf")
        include_start = options.include_start

        visited: dict[str, int] = {}
        result_nodes: dict[str, GraphNode] = {}
        result_edges: list[GraphEdge] = []
        queue: list[tuple[str, int, list[str]]] = [(start_id, 0, [start_id])]

        while queue:
            item = queue.pop(0) if mode == "bfs" else queue.pop()
            node_id, depth, path = item
            if node_id in visited:
                continue
            if depth > max_depth:
                continue
            if len(result_nodes) >= limit:
                break
            visited[node_id] = depth

            node = self._node_map.get(node_id)
            if node is None:
                continue
            is_start = node_id == start_id
            if node_kinds and node.kind not in node_kinds:
                if is_start and not include_start:
                    continue
                if not is_start:
                    continue
            if options.net_types is not None and node.kind == NodeKind.NET:
                net_type = getattr(node.data, "type", None)
                if net_type not in options.net_types:
                    if is_start and include_start:
                        # Preserve the anchor node even if filtered
                        pass
                    else:
                        continue
            if options.device_kinds is not None and node.kind == NodeKind.COMPONENT:
                comp_kind = getattr(node.data, "device_kind", None)
                if comp_kind not in options.device_kinds:
                    if is_start and include_start:
                        pass
                    else:
                        continue

            result_nodes[node_id] = node

            for edge in self._get_edges(node_id, direction, edge_kinds):
                next_id = edge.target if edge.source == node_id else edge.source
                if next_id not in visited:
                    result_edges.append(edge)
                    queue.append((next_id, depth + 1, path + [next_id]))

        if not include_start:
            result_nodes.pop(start_id, None)
        return Subgraph(nodes=result_nodes, edges=result_edges, roots=[start_id])

    def _get_edges(
        self,
        node_id: str,
        direction: str,
        edge_kinds: list[EdgeKind] | None,
    ) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        if direction in {"outgoing", "both"} and node_id in self._g:
            for _u, _v, attrs in self._g.out_edges(node_id, data=True):
                edge_id = attrs.get("edge_id")
                edge = self._edge_map.get(edge_id) if edge_id else None
                if edge is not None:
                    edges.append(edge)
        if direction in {"incoming", "both"} and node_id in self._g:
            for _u, _v, attrs in self._g.in_edges(node_id, data=True):
                edge_id = attrs.get("edge_id")
                edge = self._edge_map.get(edge_id) if edge_id else None
                if edge is not None:
                    edges.append(edge)
        if edge_kinds:
            kind_values = {k.value for k in edge_kinds}
            edges = [e for e in edges if e.kind.value in kind_values]
        return edges

    def to_dot(self) -> str:
        lines = ["digraph circuit {"]
        for node in self._node_map.values():
            label = node.data.id if isinstance(node.data, Component) else node.id
            lines.append(f'  "{node.id}" [label="{label}" kind="{node.kind.value}"];')
        for edge in self.edges():
            lines.append(f'  "{edge.source}" -> "{edge.target}" [label="{edge.kind.value}"];')
        lines.append("}")
        return "\n".join(lines)


def create_graph() -> CircuitGraph:
    return CircuitGraph()
