"""Multi-source scoring for topology primitive matches.

Each match is scored from four independent signals and then fused with fixed
weights (structural 0.30, naming 0.25, parametric 0.25, type 0.20) unless the
primitive overrides them.  The fused score is mapped to a confidence band.
"""

from __future__ import annotations

from dataclasses import dataclass

from circuit_graph.ir import Component, Net
from circuit_graph.primitives import (
    PrimitiveTemplate,
    component_value_matches,
    signal_name_matches,
)
from circuit_graph.vf2_matcher import TopologyMatch

DEFAULT_WEIGHTS = {
    "structural": 0.30,
    "naming": 0.25,
    "parametric": 0.25,
    "type": 0.20,
}

DEFAULT_THRESHOLDS = {
    "high": 0.80,
    "medium": 0.50,
    "low": 0.0,
}


@dataclass(frozen=True)
class ScoreBreakdown:
    """Per-signal scores for a topology match."""

    structural: float = 0.0
    naming: float = 0.0
    parametric: float = 0.0
    type: float = 0.0
    fused: float = 0.0
    confidence: str = "unknown"
    evidence: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.evidence is None:
            object.__setattr__(self, "evidence", [])


class TopologyScorer:
    """Score primitive matches using structural, naming, parametric and type signals."""

    def __init__(
        self,
        component_map: dict[str, Component],
        net_map: dict[str, Net],
        weights: dict[str, float] | None = None,
        thresholds: dict[str, float] | None = None,
    ):
        self.component_map = component_map
        self.net_map = net_map
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def score(
        self,
        match: TopologyMatch,
        template: PrimitiveTemplate,
    ) -> ScoreBreakdown:
        """Return a fully-populated :class:`ScoreBreakdown` for ``match``."""
        structural = self._structural_score(match, template)
        naming = self._naming_score(match, template)
        parametric = self._parametric_score(match, template)
        type_score = self._type_score(match, template)

        weights = {**self.weights, **template.scoring.weights}
        fused = (
            structural * weights.get("structural", 0.0)
            + naming * weights.get("naming", 0.0)
            + parametric * weights.get("parametric", 0.0)
            + type_score * weights.get("type", 0.0)
        )

        thresholds = {**self.thresholds, **template.scoring.thresholds}
        confidence = _to_confidence(fused, thresholds)
        evidence = [
            f"structural={structural:.2f}",
            f"naming={naming:.2f}",
            f"parametric={parametric:.2f}",
            f"type={type_score:.2f}",
            f"fused={fused:.2f}",
            f"confidence={confidence}",
        ]

        return ScoreBreakdown(
            structural=structural,
            naming=naming,
            parametric=parametric,
            type=type_score,
            fused=fused,
            confidence=confidence,
            evidence=evidence,
        )

    def _structural_score(self, match: TopologyMatch, template: PrimitiveTemplate) -> float:
        """Reward completeness of the embedding."""
        required_components = [c for c in template.components if c.required]
        required_signals = [s for s in template.signals if s.required]
        if not required_components and not required_signals:
            return 0.0

        comp_hits = sum(1 for c in required_components if c.id in match.component_mapping)
        sig_hits = sum(1 for s in required_signals if s.id in match.signal_mapping)
        total = len(required_components) + len(required_signals)
        return (comp_hits + sig_hits) / total if total else 0.0

    def _naming_score(self, match: TopologyMatch, template: PrimitiveTemplate) -> float:
        """Reward signal net names that strongly match the primitive pattern."""
        required_signals = [s for s in template.signals if s.required]
        if not required_signals:
            return 0.0

        hits = 0
        for sig in required_signals:
            net_id = match.signal_mapping.get(sig.id)
            if net_id is None:
                continue
            net = self.net_map.get(net_id)
            if net is not None and signal_name_matches(sig.name_pattern, net.name):
                hits += 1
        return hits / len(required_signals)

    def _parametric_score(self, match: TopologyMatch, template: PrimitiveTemplate) -> float:
        """Reward component values that match the primitive pattern."""
        scored_components = [c for c in template.components if c.value_pattern]
        if not scored_components:
            # No parametric constraints in the template: treat as neutral.
            return 1.0

        hits = 0
        for comp in scored_components:
            circ_id = match.component_mapping.get(comp.id)
            if circ_id is None:
                continue
            circ_comp = self.component_map.get(circ_id)
            if circ_comp is None:
                continue
            if component_value_matches(comp.value_pattern, circ_comp):
                hits += 1
        return hits / len(scored_components)

    def _type_score(self, match: TopologyMatch, template: PrimitiveTemplate) -> float:
        """Reward exact device-kind matches."""
        required_components = [c for c in template.components if c.required]
        if not required_components:
            return 0.0

        hits = 0
        for comp in required_components:
            circ_id = match.component_mapping.get(comp.id)
            if circ_id is None:
                continue
            circ_comp = self.component_map.get(circ_id)
            if circ_comp is None:
                continue
            if circ_comp.device_kind.value.lower() in _accepted_types(comp.type):
                hits += 1
        return hits / len(required_components)


def _accepted_types(type_spec: str | tuple[str, ...]) -> set[str]:
    """Normalize a primitive component type spec to a lowercase set."""
    if isinstance(type_spec, str):
        return {type_spec.lower()}
    return {t.lower() for t in type_spec}


def _to_confidence(score: float, thresholds: dict[str, float]) -> str:
    if score >= thresholds.get("high", 0.80):
        return "high"
    if score >= thresholds.get("medium", 0.50):
        return "medium"
    if score >= thresholds.get("low", 0.0):
        return "low"
    return "unknown"


def score_matches(
    matches: list[TopologyMatch],
    template: PrimitiveTemplate,
    component_map: dict[str, Component],
    net_map: dict[str, Net],
    weights: dict[str, float] | None = None,
    thresholds: dict[str, float] | None = None,
) -> list[tuple[TopologyMatch, ScoreBreakdown]]:
    """Convenience helper: score every match against ``template``."""
    scorer = TopologyScorer(component_map, net_map, weights, thresholds)
    return [(m, scorer.score(m, template)) for m in matches]


__all__ = [
    "TopologyScorer",
    "ScoreBreakdown",
    "score_matches",
    "DEFAULT_WEIGHTS",
    "DEFAULT_THRESHOLDS",
]
