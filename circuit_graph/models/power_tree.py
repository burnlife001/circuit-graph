"""Data classes for the power-tree recognizer.

A power tree describes how power flows from sources (battery, USB connectors,
bench supplies) through regulators (DCDC buck/boost, LDO) down to loads. The
shape is intentionally tree-shaped: every load is reachable from exactly one
source, and cycles are detection failures rather than silent loops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

RegulatorKind = Literal["dcdc_buck", "dcdc_boost", "ldo", "unknown"]
SourceKind = Literal["usb", "battery", "barrel_jack", "header_pin", "unknown"]
ConfidenceLevel = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class RegulatorIdentification:
    """Evidence a component is acting as a regulator.

    ``evidence_pins`` captures the pin names/numbers that triggered the
    classification (e.g. SW for DCDC, VIN/VOUT for LDO). ``input_net_id`` and
    ``output_net_id`` are populated after the regulator has been plugged into
    its surrounding nets. ``confidence`` is a discrete level driven by the
    recognition path:

    * ``high``   — KB prefix matched AND structural evidence complete
    * ``medium`` — KB prefix matched but a structural signal is missing
    * ``low``    — KB prefix did NOT match; classification is structural only
    """

    component_id: str
    part_number: Optional[str]
    kind: RegulatorKind
    evidence_pins: list[str] = field(default_factory=list)
    input_net_id: Optional[str] = None
    output_net_id: Optional[str] = None
    confidence: ConfidenceLevel = "low"

    @property
    def is_dcdc(self) -> bool:
        return self.kind in {"dcdc_buck", "dcdc_boost"}

    @property
    def is_ldo(self) -> bool:
        return self.kind == "ldo"

    @property
    def confidence_score(self) -> float:
        """Numeric confidence in [0.00, 1.00] rounded to two decimals.

        Stable mapping for spec §Scores are reported with fixed precision.
        """
        return {
            "high": 0.95,
            "medium": 0.75,
            "low": 0.50,
        }[self.confidence]


@dataclass(frozen=True)
class PowerTreeNode:
    """One node in a power tree.

    ``kind`` distinguishes the role: ``"source"`` for external supply feeds,
    ``"regulator"`` for DCDC/LDO components, ``"net"`` for plain power-rail
    segments (e.g. 5V net wiring downstream loads), and ``"load"`` for a
    consumer component on a power net.
    """

    id: str
    kind: Literal["source", "regulator", "net", "load"]
    label: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class PowerTree:
    """A rooted, hierarchical view of power distribution.

    Roots are external sources (USB/Battery/...). Children are either
    regulators (which produce another net) or load components attached to the
    current net. ``warnings`` captures anything the recognizer could not
    classify with confidence (unknown regulator prefix, mis-tagged ground-
    like net that snuck past the NetType filter, etc.). ``to_text`` renders
    the tree as an indented ASCII string so humans can compare it to a
    schematic reference; warnings are emitted as a trailing ``# warning:``
    block.
    """

    nodes: dict[str, PowerTreeNode] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=dict)
    roots: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_warning(self, message: str) -> None:
        """Append a human-readable warning (de-duplicated)."""
        if message not in self.warnings:
            self.warnings.append(message)

    def add_root(self, node: PowerTreeNode) -> None:
        self.nodes[node.id] = node
        self.children.setdefault(node.id, [])
        if node.id not in self.roots:
            self.roots.append(node.id)

    def add_child(self, parent_id: str, node: PowerTreeNode) -> None:
        if parent_id not in self.nodes:
            raise KeyError(f"Cannot add child under missing parent {parent_id!r}")
        self.nodes[node.id] = node
        self.children.setdefault(parent_id, []).append(node.id)
        self.children.setdefault(node.id, [])

    def to_text(self) -> str:
        """Render the tree as a human-readable indented string.

        Warnings, if any, are appended as a ``# warnings:`` block so callers
        can see uncertainty at a glance.
        """
        if not self.roots:
            tree_part = "(empty power tree)"
        else:
            lines: list[str] = []
            visited: set[str] = set()
            for root_id in self.roots:
                self._render(root_id, depth=0, prefix="", lines=lines, visited=visited)
            tree_part = "\n".join(lines)
        if not self.warnings:
            return tree_part
        warn_block = "\n".join(f"# warning: {w}" for w in self.warnings)
        return f"{tree_part}\n\n{warn_block}"

    def _render(
        self,
        node_id: str,
        depth: int,
        prefix: str,
        lines: list[str],
        visited: set[str],
    ) -> None:
        if node_id in visited:
            lines.append(f"{prefix}(cycle detected at {node_id})")
            return
        visited.add(node_id)
        node = self.nodes[node_id]
        kind_marker = {
            "source": "[SRC]",
            "regulator": "[REG]",
            "net": "[NET]",
            "load": "[LOAD]",
        }.get(node.kind, "[???]")
        indent = "  " * depth
        # Append a fixed-precision confidence score to regulator lines so
        # downstream tooling can parse the output without re-deriving the
        # rounding rule.
        suffix = ""
        if node.kind == "regulator":
            reg = node.metadata.get("regulator") if node.metadata else None
            if isinstance(reg, dict) and "confidence" in reg:
                score = _SCORE_MAP.get(reg["confidence"])
                if score is not None:
                    suffix = f"  conf={score:.2f}"
        lines.append(f"{indent}{kind_marker} {node.label}{suffix}")
        children = self.children.get(node_id, [])
        for idx, child_id in enumerate(children):
            is_last = idx == len(children) - 1
            child_prefix = prefix + ("    " if is_last else "│   ")
            connector = "└── " if is_last else "├── "
            child_lines_before = len(lines)
            self._render(child_id, depth + 1, child_prefix, lines, visited)
            # Inject the connector glyph on the first line we just wrote.
            lines[child_lines_before] = f"{indent}{connector}{lines[child_lines_before].lstrip()}"


# Mirror of RegulatorIdentification.confidence_score, kept here so the
# rendering layer does not have to import from the recognizer (which would
# create a circular dependency). Update both together.
_SCORE_MAP = {"high": 0.95, "medium": 0.75, "low": 0.50}


__all__ = [
    "RegulatorIdentification",
    "RegulatorKind",
    "SourceKind",
    "PowerTreeNode",
    "PowerTree",
]
