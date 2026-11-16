"""Repeated-structure (symmetry) grouping.

Boards like the T268 dual-channel buck carry the same functional substructure
several times — the RC-snubber triplet (R + 4.7nF cap) appears once per switch
node (SW1..SW4).  Whole-net comparison does NOT group those channels (SW1/SW3
and SW2/SW4 differ in extra components); the repeated unit is the substructure
itself.  Grouping therefore keys on the primitive template, not the net.

A template opts in via a ``symmetry`` block in its JSON::

    "symmetry": {"axis_signal": "sw", "include_axis_extras": true}

``axis_signal`` names the signal whose net is the channel axis (e.g. the
switch node).  Each instance lists the components realising one occurrence,
the boundary nets recovered from the matcher, and — when
``include_axis_extras`` is set — the non-IC components sitting on the axis
net that are NOT part of the occurrence (test points, extra NPO caps, ...),
which is exactly where per-channel differences show up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from circuit_graph.ir import CircuitIR, DeviceKind, FunctionalBlock
from circuit_graph.primitives import PrimitiveTemplate, load_primitives
from circuit_graph.vf2_matcher import Vf2TopologyMatcher


@dataclass(frozen=True)
class SymmetryInstance:
    """One occurrence of a repeated substructure."""

    block_id: str
    components: tuple[str, ...]
    signals: dict[str, str]  # template signal id -> net name
    extras: tuple[str, ...]  # axis-net components not in the block (non-IC)


@dataclass(frozen=True)
class SymmetryGroup:
    """Several occurrences of the same primitive template on one board."""

    template_id: str
    name: str
    instances: tuple[SymmetryInstance, ...]

    @property
    def count(self) -> int:
        return len(self.instances)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template_id,
            "name": self.name,
            "count": self.count,
            "instances": [
                {
                    "block_id": inst.block_id,
                    "components": list(inst.components),
                    "signals": dict(inst.signals),
                    "extras": list(inst.extras),
                }
                for inst in self.instances
            ],
        }


def _template_of_block(block: FunctionalBlock, template_ids: list[str]) -> str | None:
    """Recover the primitive id from a block id (``<template>-<anchor>``)."""
    for tid in template_ids:
        if block.id.startswith(f"{tid}-"):
            return tid
    return None


def detect_symmetry_groups(
    ir: CircuitIR,
    *,
    primitives_dir: str | None = None,
) -> list[SymmetryGroup]:
    """Group repeated substructures declared via ``symmetry`` in templates.

    Only templates that declare a symmetry block participate; each group needs
    at least two recognised instances (blocks) to be reported.
    """
    templates = load_primitives(primitives_dir)
    by_id: dict[str, PrimitiveTemplate] = {t.id: t for t in templates}
    template_ids = sorted(by_id, key=len, reverse=True)

    # Blocks per template, preserving IR order.
    blocks_by_template: dict[str, list[FunctionalBlock]] = {}
    for block in ir.blocks:
        tid = _template_of_block(block, template_ids)
        if tid is None or by_id[tid].symmetry is None:
            continue
        blocks_by_template.setdefault(tid, []).append(block)

    net_map = {n.id: n for n in ir.nets}
    comp_map = {c.id: c for c in ir.components}
    # net id -> sorted member component ids
    net_members: dict[str, set[str]] = {}
    for conn in ir.connections:
        comp_id, _pin = conn.pin_id.rsplit(".", 1) if "." in conn.pin_id else ("", "")
        if comp_id:
            net_members.setdefault(conn.net_id, set()).add(comp_id)

    groups: list[SymmetryGroup] = []
    for tid, blocks in blocks_by_template.items():
        if len(blocks) < 2:
            continue
        template = by_id[tid]
        axis_signal = str(template.symmetry.get("axis_signal", ""))
        want_extras = bool(template.symmetry.get("include_axis_extras", True))

        # Recover per-instance signal nets from the matcher (fast join path).
        matcher = Vf2TopologyMatcher(
            ir, ignore_pin_numbers=not template.strict_pin_matching
        )
        result = matcher.find_matches(template, timeout_seconds=10.0)
        sig_by_compset: dict[frozenset[str], dict[str, str]] = {}
        for match in result.matches:
            comps = frozenset(match.component_mapping.values())
            if comps not in sig_by_compset:
                sig_by_compset[comps] = {
                    sig: net_map[net_id].name
                    for sig, net_id in match.signal_mapping.items()
                    if net_id in net_map
                }

        instances: list[SymmetryInstance] = []
        for block in blocks:
            comps = frozenset(block.component_ids)
            signals = sig_by_compset.get(comps, {})
            extras: list[str] = []
            if want_extras and axis_signal:
                axis_name = signals.get(axis_signal)
                axis_net = next(
                    (n for n in ir.nets if n.name == axis_name), None
                )
                if axis_net is not None:
                    extras = sorted(
                        cid
                        for cid in net_members.get(axis_net.id, set())
                        if cid not in comps
                        and comp_map.get(cid) is not None
                        and comp_map[cid].device_kind is not DeviceKind.IC
                    )
            instances.append(
                SymmetryInstance(
                    block_id=block.id,
                    components=tuple(sorted(block.component_ids)),
                    signals=signals,
                    extras=tuple(extras),
                )
            )

        groups.append(
            SymmetryGroup(
                template_id=tid,
                name=template.name,
                instances=tuple(instances),
            )
        )

    return groups


def groups_touching(
    groups: list[SymmetryGroup], component_ids: set[str]
) -> list[SymmetryGroup]:
    """Keep only groups with at least one instance touching ``component_ids``."""
    kept: list[SymmetryGroup] = []
    for group in groups:
        if any(set(inst.components) & component_ids for inst in group.instances):
            kept.append(group)
    return kept


__all__ = ["SymmetryGroup", "SymmetryInstance", "detect_symmetry_groups", "groups_touching"]
