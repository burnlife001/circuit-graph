"""Apply a layout result to a kicad_sch_api Schematic."""

from __future__ import annotations

from circuit_graph.layout.exceptions import LayoutError
from circuit_graph.layout.page_layout import PageLayoutResult
from circuit_graph.layout.wire_placer import WireSegment


def apply_layout(sch, result: PageLayoutResult) -> None:
    """Mutate ``sch`` with positions, wires, labels, and junctions."""
    try:
        import kicad_sch_api as ksa  # noqa: F401
    except ImportError as exc:
        raise LayoutError(
            "kicad_sch_api is required for apply_layout; install it with "
            "`pip install -e E:/__electric/__research/kicad-sch-api`"
        ) from exc

    if not isinstance(sch, ksa.Schematic):
        raise LayoutError(f"expected ksa.Schematic, got {type(sch).__name__}")

    for _unit_id, unit_result in result.units.items():
        _apply_unit(sch, unit_result)


def _apply_unit(sch, unit) -> None:

    # Move components.
    comp_by_id = {c.reference: c for c in sch.components}
    for comp_id, placement in unit.placements.items():
        comp = comp_by_id.get(comp_id)
        if comp is None:
            continue
        comp.move(placement.x, placement.y)
        comp.rotate(placement.rotation)

    # Reference/Value text is easier to read at 0° regardless of symbol rotation.
    for comp in comp_by_id.values():
        _normalize_property_rotations(comp)

    # Add wires.
    for seg in unit.wires:
        sch.add_wire(start=seg.start, end=seg.end)

    # Add labels (only net labels; reference/value are symbol properties).
    for label in unit.labels:
        if label.kind in ("reference", "value"):
            continue
        sch.add_label(label.text, position=(label.x, label.y))

    # Add junctions at T-nodes: a point that is an endpoint of one segment and
    # lies on the interior of another axis-aligned segment.
    junction_points = _find_junctions(unit.wires)
    for pos in junction_points:
        sch.junctions.add(position=pos)


def _find_junctions(wires: list[WireSegment]) -> set[tuple[float, float]]:
    """Return points where a wire segment ends on the interior of another."""

    def _on_segment_interior(pt: tuple[float, float], seg: WireSegment) -> bool:
        if seg.start[0] == seg.end[0]:
            # Vertical.
            x = seg.start[0]
            y_min, y_max = sorted((seg.start[1], seg.end[1]))
            return pt[0] == x and y_min < pt[1] < y_max
        if seg.start[1] == seg.end[1]:
            # Horizontal.
            y = seg.start[1]
            x_min, x_max = sorted((seg.start[0], seg.end[0]))
            return pt[1] == y and x_min < pt[0] < x_max
        return False

    junctions: set[tuple[float, float]] = set()
    for i, seg_a in enumerate(wires):
        for end in (seg_a.start, seg_a.end):
            for j, seg_b in enumerate(wires):
                if i == j:
                    continue
                if _on_segment_interior(end, seg_b):
                    junctions.add(end)
    return junctions


def _normalize_property_rotations(comp) -> None:
    """Force Reference/Value text effects to 0° for readability."""
    for prop_name in ("Reference", "Value"):
        try:
            comp.set_property_effects(prop_name, {"rotation": 0})
        except ValueError:
            pass  # property not present on this symbol
