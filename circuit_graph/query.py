"""Query primitives over a CircuitGraph.

This module wraps graph traversal operations in a query-oriented API similar
to the TypeScript query package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from circuit_graph.graph import CircuitGraph
from circuit_graph.ir import Component, GraphEdge, GraphNode, Net, TraversalOptions


@dataclass
class ComponentTraceResult:
    component: Component
    pins: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NetTraceResult:
    net: Net
    connections: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NeighborResult:
    node: GraphNode
    edges: list[dict[str, Any]] = field(default_factory=list)


def trace_component(graph: CircuitGraph, component_id: str) -> ComponentTraceResult | None:
    """Return a component and each of its pins with the connected net."""
    comp_node = graph.get_component(component_id)
    if comp_node is None:
        return None
    return ComponentTraceResult(
        component=comp_node.data,
        pins=graph.trace_component(component_id),
    )


def trace_net(graph: CircuitGraph, net_id: str) -> NetTraceResult | None:
    """Return a net and all component-pin pairs connected to it."""
    net_node = graph.get_net(net_id)
    if net_node is None:
        return None
    return NetTraceResult(
        net=net_node.data,
        connections=graph.trace_net(net_id),
    )


def neighbors_of(graph: CircuitGraph, node_id: str) -> list[NeighborResult]:
    """Return adjacent nodes together with the edges that connect them."""
    node = (
        graph.get_component(node_id)
        or graph.get_net(node_id)
        or graph.get_pin(node_id)
        or graph.get_block(node_id)
    )
    if node is None:
        return []

    # Build a quick lookup from neighbor id to connecting edges.
    edges_by_neighbor: dict[str, list[GraphEdge]] = {}
    for edge in graph.edges():
        if edge.source == node_id:
            edges_by_neighbor.setdefault(edge.target, []).append(edge)
        elif edge.target == node_id:
            edges_by_neighbor.setdefault(edge.source, []).append(edge)

    results: list[NeighborResult] = []
    for neighbor in graph.neighbors(node_id):
        edge_infos = [
            {"kind": edge.kind.value, "target": edge.target, "source": edge.source}
            for edge in edges_by_neighbor.get(neighbor.id, [])
        ]
        results.append(NeighborResult(node=neighbor, edges=edge_infos))
    return results


def traverse_bfs(
    graph: CircuitGraph, start_id: str, options: TraversalOptions | None = None
) -> dict[str, Any]:
    """Run a BFS traversal and return nodes/edges/roots as plain structures."""
    subgraph = graph.traverse_bfs(start_id, options)
    return {
        "nodes": {nid: {"id": n.id, "kind": n.kind.value} for nid, n in subgraph.nodes.items()},
        "edges": [
            {"source": e.source, "target": e.target, "kind": e.kind.value} for e in subgraph.edges
        ],
        "roots": subgraph.roots,
    }


def traverse_dfs(
    graph: CircuitGraph, start_id: str, options: TraversalOptions | None = None
) -> dict[str, Any]:
    """Run a DFS traversal and return nodes/edges/roots as plain structures."""
    subgraph = graph.traverse_dfs(start_id, options)
    return {
        "nodes": {nid: {"id": n.id, "kind": n.kind.value} for nid, n in subgraph.nodes.items()},
        "edges": [
            {"source": e.source, "target": e.target, "kind": e.kind.value} for e in subgraph.edges
        ],
        "roots": subgraph.roots,
    }
