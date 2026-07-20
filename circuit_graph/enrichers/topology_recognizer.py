"""Topology recognizer enricher.

Detects common circuit topologies and emits FunctionalBlock nodes using the
primitive library, VF2 subgraph matcher, and multi-source scoring.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from circuit_graph.ir import BlockRole, CircuitIR, FunctionalBlock
from circuit_graph.primitives import load_primitives
from circuit_graph.scoring import _to_confidence, score_matches
from circuit_graph.vf2_matcher import Vf2TopologyMatcher


@dataclass
class TopologyRecognizer:
    """Recognize circuit topologies and add functional blocks."""

    name: str = "TopologyRecognizer"
    priority: int = 30
    requires: list[str] | None = None
    primitives_dir: Path | str | None = None
    warnings: list[str] = field(default_factory=list, repr=False)

    def can_enrich(self, ir: CircuitIR) -> bool:
        return len(ir.components) > 0

    def enrich(self, ir: CircuitIR) -> CircuitIR:
        blocks: list[FunctionalBlock] = list(ir.blocks)
        self.warnings = []

        try:
            templates = load_primitives(self.primitives_dir)
        except FileNotFoundError as exc:
            self.warnings.append(f"PRIMITIVE_LIBRARY_MISSING: {exc}")
            return self._with_blocks(ir, blocks)

        component_map = {c.id: c for c in ir.components}
        net_map = {n.id: n for n in ir.nets}
        matcher = Vf2TopologyMatcher(ir)

        for template in templates:
            result = matcher.find_matches(template, timeout_seconds=2.0)
            if result.aborted:
                self.warnings.append(f"TOPOLOGY_MATCH_ABORTED: {template.id} — {result.message}")
                continue

            scored = score_matches(
                result.matches,
                template,
                component_map,
                net_map,
                weights=template.scoring.weights,
                thresholds=template.scoring.thresholds,
            )

            # Group matches that share the same boundary-signal mapping so a
            # single FunctionalBlock can represent all components that realize
            # the same primitive instance (e.g. two parallel CC pull-downs).
            groups: dict[
                tuple[str, tuple[tuple[str, str], ...]],
                list[tuple[Any, Any]],
            ] = defaultdict(list)
            for match, breakdown in scored:
                if breakdown.confidence == "unknown":
                    continue
                key = (
                    template.id,
                    tuple(sorted(match.signal_mapping.items())),
                )
                groups[key].append((match, breakdown))

            thresholds = template.scoring.thresholds
            for group in groups.values():
                component_ids = sorted(
                    {cid for match, _ in group for cid in match.component_mapping.values()}
                )
                if not component_ids:
                    continue

                anchor = component_ids[0]
                block_id = f"{template.id}-{anchor.lower()}"
                if any(b.id == block_id for b in blocks):
                    continue

                fused_scores = [breakdown.fused for _, breakdown in group]
                group_fused = sum(fused_scores) / len(fused_scores)
                group_confidence = _to_confidence(min(fused_scores), thresholds)

                evidence: list[str] = []
                for match, breakdown in group:
                    evidence.extend(match.evidence)
                    evidence.extend(breakdown.evidence)

                role = _role_from_string(template.role)
                blocks.append(
                    FunctionalBlock(
                        id=block_id,
                        name=template.name,
                        role=role,
                        component_ids=component_ids,
                        description=(
                            f"{template.description} "
                            f"(confidence={group_confidence}, score={group_fused:.2f})"
                        ),
                        source="rule",
                    )
                )

        return self._with_blocks(ir, blocks)

    def _with_blocks(self, ir: CircuitIR, blocks: list[FunctionalBlock]) -> CircuitIR:
        return CircuitIR(
            meta=ir.meta,
            components=ir.components,
            nets=ir.nets,
            connections=ir.connections,
            blocks=blocks,
            semantic_edges=ir.semantic_edges,
            version=ir.version,
        )


def _role_from_string(role: str) -> BlockRole:
    try:
        return BlockRole(role)
    except ValueError:
        return BlockRole.UNKNOWN
