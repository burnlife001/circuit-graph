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

# Default per-template match timeout.  With candidate prescreening the matcher
# never comes close to this; it is only a safety net against pathological
# templates (see todolist P0-1).
DEFAULT_TIMEOUT_SECONDS = 10.0
# Stop enumerating embeddings after this many; the result is flagged truncated.
MAX_MATCHES_PER_TEMPLATE = 1000
# Templates producing more groups than this are treated as floods: matches are
# downgraded to candidates (visible via warnings) instead of becoming blocks.
FLOOD_GROUP_THRESHOLD = 7


@dataclass
class TopologyRecognizer:
    """Recognize circuit topologies and add functional blocks."""

    name: str = "TopologyRecognizer"
    priority: int = 30
    requires: list[str] | None = None
    primitives_dir: Path | str | None = None
    timeout_seconds: float | None = None
    max_matches: int = MAX_MATCHES_PER_TEMPLATE
    flood_group_threshold: int = FLOOD_GROUP_THRESHOLD
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

        timeout = self.timeout_seconds if self.timeout_seconds is not None else DEFAULT_TIMEOUT_SECONDS

        # The matcher only depends on (ir, ignore_pin_numbers), so build one
        # per strict-mode variant instead of rebuilding it per template.
        matchers: dict[bool, Vf2TopologyMatcher] = {}

        for template in templates:
            strict_mode = getattr(template, "strict_pin_matching", False)
            matcher = matchers.get(strict_mode)
            if matcher is None:
                matcher = Vf2TopologyMatcher(ir, ignore_pin_numbers=not strict_mode)
                matchers[strict_mode] = matcher
            result = matcher.find_matches(
                template,
                timeout_seconds=timeout,
                max_matches=self.max_matches,
            )

            if result.aborted:
                self.warnings.append(f"TOPOLOGY_MATCH_ABORTED: {template.id} — {result.message}")
                continue
            if result.truncated:
                self.warnings.append(
                    f"TOPOLOGY_MATCH_TRUNCATED: {template.id} — "
                    f"capped at {self.max_matches} matches"
                )

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
            if len(groups) > self.flood_group_threshold:
                # Match flood: emitting all of these as functional blocks would
                # bury the real structure in noise.  With pin numbers ignored,
                # the same device also shows up once per pin permutation, all
                # scoring identically — so rank groups by score and keep the
                # best group per distinct component set; the rest are
                # downgraded to candidates (visible via the warnings channel).
                ranked = sorted(
                    groups.values(),
                    key=lambda g: max(bd.fused for _, bd in g),
                    reverse=True,
                )
                kept: list[list[tuple[Any, Any]]] = []
                covered: set[str] = set()
                for g in ranked:
                    comps = {
                        cid
                        for match, _ in g
                        for cid in match.component_mapping.values()
                    }
                    if comps <= covered:
                        continue
                    covered |= comps
                    kept.append(g)
                    if len(kept) >= self.flood_group_threshold:
                        break
                self.warnings.append(
                    f"TOPOLOGY_MATCH_FLOOD: {template.id} — "
                    f"{len(groups)} match groups (> {self.flood_group_threshold}), "
                    f"kept top {len(kept)} by score, "
                    f"{len(groups) - len(kept)} downgraded to candidates"
                )
                groups_iter: Any = kept
            else:
                groups_iter = groups.values()
            for group in groups_iter:
                component_ids = sorted(
                    {cid for match, _ in group for cid in match.component_mapping.values()}
                )
                if not component_ids:
                    continue

                # Prefer IC components as anchor (e.g. U2B instead of R4)
                ic_components = [
                    cid for cid in component_ids
                    if component_map[cid].device_kind.value.lower() == "ic"
                ]
                anchor = ic_components[0] if ic_components else component_ids[0]
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
            metadata=ir.metadata,
        )


def _role_from_string(role: str) -> BlockRole:
    try:
        return BlockRole(role)
    except ValueError:
        return BlockRole.UNKNOWN
