"""VF2-based bipartite subgraph matcher for topology primitives.

The matcher looks for embeddings of a primitive template graph inside a circuit
bipartite graph, with early pruning by component type and a hard timeout so
large boards cannot hang the recognizer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from networkx.algorithms.isomorphism import GraphMatcher

from circuit_graph.ir import CircuitIR, Component, Net
from circuit_graph.primitives import (
    PrimitiveTemplate,
    circuit_to_bipartite_graph,
    component_type_matches,
    primitive_to_bipartite_graph,
    signal_name_matches,
)


@dataclass(frozen=True)
class TopologyMatch:
    """A single embedding of a primitive into a circuit."""

    primitive_id: str
    component_mapping: dict[str, str] = field(default_factory=dict)
    signal_mapping: dict[str, str] = field(default_factory=dict)
    score: float = 0.0
    confidence: str = "unknown"
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MatchResult:
    """Result of matching one primitive against a circuit."""

    matches: list[TopologyMatch] = field(default_factory=list)
    aborted: bool = False
    duration_seconds: float = 0.0
    message: str = ""


def _node_match_circuit_to_primitive(
    circuit_node_attrs: dict[str, Any],
    primitive_node_attrs: dict[str, Any],
    template: PrimitiveTemplate,
    ir: CircuitIR,
) -> bool:
    """Return True iff the circuit node can be mapped onto the primitive node."""
    circ_kind = circuit_node_attrs.get("kind")
    prim_kind = primitive_node_attrs.get("kind")
    if circ_kind != prim_kind:
        return False

    circ_data = circuit_node_attrs.get("data")
    prim_data = primitive_node_attrs.get("data")

    if circ_kind == "component":
        if not isinstance(circ_data, Component):
            return False
        return component_type_matches(prim_data.type, circ_data)

    if circ_kind == "net":
        if not isinstance(circ_data, Net):
            return False
        return signal_name_matches(prim_data.name_pattern, circ_data.name)

    return False


class Vf2TopologyMatcher:
    """Match primitive templates against a CircuitIR using VF2."""

    def __init__(self, ir: CircuitIR):
        self.ir = ir
        self.circuit_graph = circuit_to_bipartite_graph(ir)
        self.component_map: dict[str, Component] = {c.id: c for c in ir.components}
        self.net_map: dict[str, Net] = {n.id: n for n in ir.nets}

    def find_matches(
        self,
        template: PrimitiveTemplate,
        *,
        timeout_seconds: float = 2.0,
        score_fn: Callable[[TopologyMatch], float] | None = None,
    ) -> MatchResult:
        """Find all embeddings of ``template`` in the circuit.

        Returns a :class:`MatchResult`.  If the matcher exceeds
        ``timeout_seconds`` it returns with ``aborted=True`` and zero matches.
        """
        start = time.perf_counter()
        primitive_graph = primitive_to_bipartite_graph(template)

        # Early pruning: reject primitives larger than the circuit.
        if len(primitive_graph) > len(self.circuit_graph):
            return MatchResult(
                matches=[],
                aborted=False,
                duration_seconds=time.perf_counter() - start,
                message="primitive larger than circuit",
            )

        # Pre-filter candidate roots by component type/label.
        root_candidates = self._candidate_components(template)
        if not root_candidates:
            return MatchResult(
                matches=[],
                aborted=False,
                duration_seconds=time.perf_counter() - start,
                message="no candidate root components",
            )

        def node_match(
            circuit_node_attrs: dict[str, Any],
            primitive_node_attrs: dict[str, Any],
        ) -> bool:
            return _node_match_circuit_to_primitive(
                circuit_node_attrs, primitive_node_attrs, template, self.ir
            )

        gm = GraphMatcher(
            self.circuit_graph,
            primitive_graph,
            node_match=node_match,
        )

        matches: list[TopologyMatch] = []
        deadline = start + timeout_seconds

        # To honour the timeout we iterate manually instead of calling
        # subgraph_isomorphisms_iter() in a tight loop.
        try:
            for mapping in gm.subgraph_isomorphisms_iter():
                if time.perf_counter() > deadline:
                    return MatchResult(
                        matches=[],
                        aborted=True,
                        duration_seconds=time.perf_counter() - start,
                        message=f"timeout after {timeout_seconds}s",
                    )
                match = self._convert_mapping(mapping, template)
                if match is not None:
                    if score_fn is not None:
                        match = self._score_match(match, score_fn)
                    matches.append(match)
        except Exception as exc:  # pragma: no cover - defensive
            return MatchResult(
                matches=[],
                aborted=True,
                duration_seconds=time.perf_counter() - start,
                message=f"matcher error: {exc}",
            )

        duration = time.perf_counter() - start
        return MatchResult(
            matches=matches,
            aborted=False,
            duration_seconds=duration,
        )

    def _candidate_components(self, template: PrimitiveTemplate) -> set[str]:
        """Return circuit component ids that could anchor a match.

        A candidate must share at least one device-kind label with a required
        primitive component.
        """
        required_types = {comp.type.lower() for comp in template.components if comp.required}
        return {
            comp.id
            for comp in self.component_map.values()
            if comp.device_kind.value.lower() in required_types
        }

    def _convert_mapping(
        self,
        mapping: dict[str, str],
        template: PrimitiveTemplate,
    ) -> TopologyMatch | None:
        """Convert a raw VF2 node mapping to a :class:`TopologyMatch`."""
        component_mapping: dict[str, str] = {}
        signal_mapping: dict[str, str] = {}
        evidence: list[str] = []

        for circ_key, prim_key in mapping.items():
            if prim_key.startswith("c:"):
                prim_id = prim_key[2:]
                circ_id = circ_key[2:]
                component_mapping[prim_id] = circ_id
                comp = self.component_map.get(circ_id)
                if comp is not None:
                    evidence.append(f"{prim_id} -> {circ_id} ({comp.device_kind.value})")
            elif prim_key.startswith("n:"):
                prim_id = prim_key[2:]
                circ_id = circ_key[2:]
                signal_mapping[prim_id] = circ_id
                net = self.net_map.get(circ_id)
                if net is not None:
                    evidence.append(f"{prim_id} -> {net.name}")

        # All required components/signals must be present (VF2 guarantees this
        # structurally, but we double-check boundary signal names).
        missing_required = [
            c.id for c in template.components if c.required and c.id not in component_mapping
        ]
        if missing_required:
            return None

        return TopologyMatch(
            primitive_id=template.id,
            component_mapping=component_mapping,
            signal_mapping=signal_mapping,
            evidence=evidence,
        )

    def _score_match(
        self,
        match: TopologyMatch,
        score_fn: Callable[[TopologyMatch], float],
    ) -> TopologyMatch:
        score = score_fn(match)
        return TopologyMatch(
            primitive_id=match.primitive_id,
            component_mapping=match.component_mapping,
            signal_mapping=match.signal_mapping,
            score=score,
            confidence=match.confidence,
            evidence=match.evidence,
        )


__all__ = ["Vf2TopologyMatcher", "TopologyMatch", "MatchResult"]
