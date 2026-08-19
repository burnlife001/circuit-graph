"""VF2-based bipartite subgraph matcher for topology primitives.

The matcher looks for embeddings of a primitive template graph inside a circuit
bipartite graph, with early pruning by component type and a hard timeout so
large boards cannot hang the recognizer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

import networkx as nx
from networkx.algorithms.isomorphism import GraphMatcher

from circuit_graph.ir import CircuitIR, Component, Net, parse_pin_id
from circuit_graph.primitives import (
    PrimitiveSignal,
    PrimitiveTemplate,
    circuit_to_bipartite_graph,
    component_type_matches,
    primitive_to_bipartite_graph,
    signal_name_matches,
    signal_semantic_matches,
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


def _optional_pin_keepsets(
    optional_pins: list[tuple[str, str]],
) -> list[set[tuple[str, str]]]:
    """All subsets of the optional pins — one per template variant.

    A pin is identified by ``(component_id, pin_number)``. With ``n`` optional
    pins the template is matched against ``2**n`` variants, so keep the count
    small (1-2) in practice.
    """
    keepsets: list[set[tuple[str, str]]] = []
    for mask in range(1 << len(optional_pins)):
        keep = {
            optional_pins[i] for i in range(len(optional_pins)) if mask & (1 << i)
        }
        keepsets.append(keep)
    return keepsets


def _concretize_template(
    template: PrimitiveTemplate,
    keep_optional: set[tuple[str, str]],
) -> PrimitiveTemplate:
    """Return a copy of ``template`` keeping only the selected optional pins.

    ``keep_optional`` maps ``(component_id, pin_number)`` of the pins that must
    stay in the variant; all other ``optional`` pins are dropped. The copy is
    re-validated through ``__post_init__`` so the signal-to-component index is
    rebuilt for the reduced pin set.
    """
    components = []
    for comp in template.components:
        pins = [
            pin
            for pin in comp.pins
            if not pin.optional or (comp.id, pin.number) in keep_optional
        ]
        components.append(replace(comp, pins=pins))
    return replace(template, components=components)


def _node_match_circuit_to_primitive(
    circuit_node_attrs: dict[str, Any],
    primitive_node_attrs: dict[str, Any],
    template: PrimitiveTemplate,
    ir: CircuitIR,
    *,
    ignore_pin_numbers: bool = False,
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
        # Check component type
        if not component_type_matches(prim_data.type, circ_data):
            return False
        # Check pin number if both nodes have pin info (unless ignored)
        if ignore_pin_numbers:
            return True
        circ_pin = circuit_node_attrs.get("pin")
        prim_pin = primitive_node_attrs.get("pin")
        if circ_pin is not None and prim_pin is not None:
            # Exact match
            if circ_pin.number == prim_pin.number:
                return True
            # Fuzzy match: map logical names to common aliases
            aliases = {
                "+": ["IN+", "IN_P", "NONINV"],
                "-": ["IN-", "IN_N", "INV"],
                "OUT": ["OUTPUT", "O"],
                "V+": ["VCC", "VDD", "VPOS"],
                "V-": ["GND", "VSS", "VEE", "VNEG"],
            }
            for canonical, alias_list in aliases.items():
                if prim_pin.number == canonical and circ_pin.number in alias_list:
                    return True
                if circ_pin.number == canonical and prim_pin.number in alias_list:
                    return True
            return False
        return True

    if circ_kind == "net":
        if not isinstance(circ_data, Net):
            return False
        return signal_name_matches(
            prim_data.name_pattern, circ_data.name, signal_id=prim_data.id
        )

    return False


def _match_signal_semantics(
    template: PrimitiveTemplate,
    prim_signal: PrimitiveSignal,
    circ_net_id: str,
    partial_mapping: dict[str, str],
    edge_semantics: dict[tuple[str, str], dict[str, Any]],
) -> bool:
    """Verify the candidate circuit net satisfies the primitive signal's expected_*.

    For each template-side component linked to ``prim_signal``, look up its
    mapped circuit-side component in ``partial_mapping`` (which maps
    ``"c:<template_comp_id>" -> "c:<circuit_comp_id>"``). Components not yet
    mapped are skipped — VF2 will map them later, and feasibility will be
    re-evaluated then.

    For each mapped pair, look up ``(circuit_comp_id, circ_net_id)`` in
    ``edge_semantics``; absence is a strict-mode rejection. Otherwise call
    :func:`signal_semantic_matches` to compare fields.

    Returns True iff every mapped pair passes.
    """
    linked_template_comp_ids = template._signal_to_components.get(prim_signal.id, [])
    for template_comp_id in linked_template_comp_ids:
        # Find any pin of this component in the partial mapping
        # Node IDs are now "c:<comp_id>:<pin_number>"
        circuit_comp_id = None
        for key, value in partial_mapping.items():
            if key.startswith(f"c:{template_comp_id}:"):
                # value is "c:<circuit_comp_id>:<pin_number>"
                if value.startswith("c:"):
                    circuit_comp_id = value[2:].rsplit(":", 1)[0]
                else:
                    circuit_comp_id = value.rsplit(":", 1)[0]
                break

        if circuit_comp_id is None:
            continue  # not yet mapped — defer judgement

        sem = edge_semantics.get((circuit_comp_id, circ_net_id))
        if sem is None:
            return False  # strict mode: edge_semantic required
        if not signal_semantic_matches(
            prim_signal.expected_role,
            prim_signal.expected_direction,
            prim_signal.expected_level,
            sem,
        ):
            return False
    return True


class _DeadlineExceeded(Exception):
    """Raised inside semantic_feasibility to abort the VF2 iterator."""


class _SemanticGraphMatcher(GraphMatcher):
    """GraphMatcher that enforces primitive expected_* semantics during VF2.

    NetworkX's ``node_match`` callback receives only the two node attribute
    dicts and cannot see the current partial mapping. ``semantic_feasibility``
    is the hook NetworkX calls every time it considers extending the mapping;
    it has access to ``self.core_1`` / ``self.core_2`` (the partial mapping),
    which lets us validate edge semantics at each step and prune early.

    ``deadline`` (perf_counter epoch) is checked on every feasibility call so
    the VF2 iterator terminates promptly even when the generator does not
    yield for a long stretch (the caller-side check alone is not enough).
    """

    def __init__(
        self,
        circuit_graph: nx.Graph,
        primitive_graph: nx.Graph,
        *,
        template: PrimitiveTemplate,
        edge_semantics: dict[tuple[str, str], dict[str, Any]],
        deadline: float,
        node_match: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
    ):
        super().__init__(circuit_graph, primitive_graph, node_match=node_match)
        self._template = template
        self._edge_semantics = edge_semantics
        self._deadline = deadline

    def semantic_feasibility(self, G1_node: str, G2_node: str) -> bool:  # noqa: N803
        if time.perf_counter() > self._deadline:
            raise _DeadlineExceeded()
        if not super().semantic_feasibility(G1_node, G2_node):
            return False
        # G1 = circuit graph, G2 = primitive graph (NetworkX convention).
        prim_attrs = self.G2.nodes[G2_node]
        kind = prim_attrs.get("kind")

        # Component-pin consistency: all pins of a template component must map
        # to pins of the same circuit component. Without this check VF2 can
        # explore cross-component pin assignments and blow up the search space.
        if kind == "component":
            prim_comp_id = G2_node[2:].rsplit(":", 1)[0]
            circ_comp_id = G1_node[2:].rsplit(":", 1)[0]
            # core_2: primitive node -> circuit node
            for other_prim_node, other_circ_node in self.core_2.items():
                if not other_prim_node.startswith(f"c:{prim_comp_id}:"):
                    continue
                other_circ_comp_id = other_circ_node[2:].rsplit(":", 1)[0]
                if other_circ_comp_id != circ_comp_id:
                    return False
            return True

        if kind != "net":
            return True
        prim_signal: PrimitiveSignal = prim_attrs["data"]
        if (
            prim_signal.expected_role is None
            and prim_signal.expected_direction is None
            and prim_signal.expected_level is None
        ):
            return True
        circ_net_id = G1_node[2:] if G1_node.startswith("n:") else G1_node
        # core_2 maps primitive-graph node -> circuit-graph node.
        return _match_signal_semantics(
            template=self._template,
            prim_signal=prim_signal,
            circ_net_id=circ_net_id,
            partial_mapping=self.core_2,
            edge_semantics=self._edge_semantics,
        )


class Vf2TopologyMatcher:
    """Match primitive templates against a CircuitIR using VF2."""

    def __init__(self, ir: CircuitIR, *, ignore_pin_numbers: bool = False):
        self.ir = ir
        self.circuit_graph = circuit_to_bipartite_graph(ir)
        self.component_map: dict[str, Component] = {c.id: c for c in ir.components}
        self.net_map: dict[str, Net] = {n.id: n for n in ir.nets}
        self.ignore_pin_numbers = ignore_pin_numbers
        # Index edge_semantic metadata by (component_id, net_id) for strict-mode checks.
        self._edge_semantics: dict[tuple[str, str], dict[str, Any]] = {}
        for conn in ir.connections:
            sem = (conn.metadata or {}).get("edge_semantic")
            if sem is None:
                continue
            comp_id, _ = parse_pin_id(conn.pin_id)
            if comp_id is None:
                continue
            self._edge_semantics[(comp_id, conn.net_id)] = sem

    def find_matches(
        self,
        template: PrimitiveTemplate,
        *,
        timeout_seconds: float = 2.0,
        score_fn: Callable[[TopologyMatch], float] | None = None,
    ) -> MatchResult:
        """Find all embeddings of ``template`` in the circuit.

        Templates with ``optional`` pins are matched once per pin-combination
        variant (e.g. a crystal shield pin that may or may not be grounded):
        each variant is a concrete template where some optional pins are kept,
        so induced-subgraph semantics still apply per variant. Duplicate
        matches across variants are collapsed.

        Returns a :class:`MatchResult`.  If the matcher exceeds
        ``timeout_seconds`` it returns with ``aborted=True`` and zero matches.
        """
        start = time.perf_counter()
        deadline = start + timeout_seconds

        optional_pins = [
            (comp.id, pin.number)
            for comp in template.components
            for pin in comp.pins
            if pin.optional
        ]
        if not optional_pins:
            variants = [template]
        else:
            variants = [
                _concretize_template(template, keep)
                for keep in _optional_pin_keepsets(optional_pins)
            ]

        matches: list[TopologyMatch] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        for variant in variants:
            result = self._match_variant(variant, deadline, score_fn)
            if result.aborted:
                return MatchResult(
                    matches=[],
                    aborted=True,
                    duration_seconds=time.perf_counter() - start,
                    message=result.message,
                )
            for match in result.matches:
                key = (
                    tuple(sorted(match.component_mapping.items())),
                    tuple(sorted(match.signal_mapping.items())),
                )
                if key in seen:
                    continue
                seen.add(key)
                matches.append(match)

        return MatchResult(
            matches=matches,
            aborted=False,
            duration_seconds=time.perf_counter() - start,
        )

    def _match_variant(
        self,
        template: PrimitiveTemplate,
        deadline: float,
        score_fn: Callable[[TopologyMatch], float] | None,
    ) -> MatchResult:
        """Match a single concrete (optional pins already resolved) template."""
        primitive_graph = primitive_to_bipartite_graph(template)

        # Early pruning: reject primitives larger than the circuit.
        if len(primitive_graph) > len(self.circuit_graph):
            return MatchResult(matches=[], aborted=False, duration_seconds=0.0)

        # Pre-filter candidate roots by component type/label.
        root_candidates = self._candidate_components(template)
        if not root_candidates:
            return MatchResult(matches=[], aborted=False, duration_seconds=0.0)

        def node_match(
            circuit_node_attrs: dict[str, Any],
            primitive_node_attrs: dict[str, Any],
        ) -> bool:
            return _node_match_circuit_to_primitive(
                circuit_node_attrs, primitive_node_attrs, template, self.ir,
                ignore_pin_numbers=self.ignore_pin_numbers,
            )

        gm = _SemanticGraphMatcher(
            self.circuit_graph,
            primitive_graph,
            template=template,
            edge_semantics=self._edge_semantics,
            deadline=deadline,
            node_match=node_match,
        )

        matches: list[TopologyMatch] = []

        # To honour the timeout we iterate manually instead of calling
        # subgraph_isomorphisms_iter() in a tight loop. The deadline is also
        # enforced inside semantic_feasibility via _DeadlineExceeded so the
        # generator aborts even when no mapping is being yielded.
        try:
            for mapping in gm.subgraph_isomorphisms_iter():
                if time.perf_counter() > deadline:
                    return MatchResult(
                        matches=[],
                        aborted=True,
                        duration_seconds=0.0,
                        message="timeout",
                    )
                match = self._convert_mapping(mapping, template)
                if match is not None:
                    if score_fn is not None:
                        match = self._score_match(match, score_fn)
                    matches.append(match)
        except _DeadlineExceeded:
            return MatchResult(
                matches=[],
                aborted=True,
                duration_seconds=0.0,
                message="timeout",
            )
        except Exception as exc:  # pragma: no cover - defensive
            return MatchResult(
                matches=[],
                aborted=True,
                duration_seconds=0.0,
                message=f"matcher error: {exc}",
            )

        return MatchResult(matches=matches, aborted=False, duration_seconds=0.0)

    def _candidate_components(self, template: PrimitiveTemplate) -> set[str]:
        """Return circuit component ids that could anchor a match.

        A candidate must share at least one device-kind label with a required
        primitive component.
        """
        required_types: set[str] = set()
        for comp in template.components:
            if not comp.required:
                continue
            accepted = (comp.type,) if isinstance(comp.type, str) else comp.type
            required_types.update(t.lower() for t in accepted)
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
                # prim_key: "c:opamp:OUT" -> prim_id: "opamp", prim_pin: "OUT"
                # circ_key: "c:U2A:OUT" -> circ_id: "U2A", circ_pin: "OUT"
                prim_parts = prim_key[2:].rsplit(":", 1)
                prim_id = prim_parts[0]
                circ_parts = circ_key[2:].rsplit(":", 1)
                circ_id = circ_parts[0]
                # All pins of the same template component must resolve to the
                # same circuit component. Pin-level encoding removed this
                # constraint, so we enforce it here.
                if prim_id in component_mapping and component_mapping[prim_id] != circ_id:
                    return None
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
