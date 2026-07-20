"""Generate orthogonal wire segments for a placed unit."""

from __future__ import annotations

from dataclasses import dataclass

from circuit_graph.ir import CircuitIR
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.placer import Placement


@dataclass(frozen=True)
class WireSegment:
    start: tuple[float, float]
    end: tuple[float, float]


def place_wires(
    ir: CircuitIR,
    placements: dict[str, Placement],
    pin_positions: dict[str, dict[str, tuple[float, float]]],
    config: LayoutConfig,
) -> list[WireSegment]:
    """Return orthogonal wire segments connecting all pins on each net."""
    all_segments: list[WireSegment] = []

    net_pins = _group_pin_positions_by_net(ir, placements, pin_positions)

    for _net_id, points in net_pins.items():
        if len(points) < 2:
            continue
        net_segments = _route_net(points, config)
        # Merge collinear segments within this net only, but never merge
        # through a pin position — merging would turn that pin endpoint into
        # an interior point of the merged segment, making it invisible to the
        # ERC extractor (which only traces wire endpoints).
        all_segments.extend(_merge_collinear_segments(net_segments, points))

    return all_segments


def _group_pin_positions_by_net(ir, placements, pin_positions):
    result: dict[str, list[tuple[float, float]]] = {}
    comp_ids = {c.id: c for c in ir.components}

    for conn in ir.connections:
        comp_id, pin_num = conn.pin_id.rsplit(".", 1)
        comp = comp_ids.get(comp_id)
        if comp is None:
            continue
        pos = pin_positions.get(comp_id, {}).get(pin_num)
        if pos is None:
            continue
        result.setdefault(conn.net_id, []).append(pos)

    return result


def _route_net(
    points: list[tuple[float, float]], config: LayoutConfig
) -> list[WireSegment]:
    """Greedy rectilinear Steiner tree.

    Picks the leftmost point as root and repeatedly connects the nearest
    unconnected point with an L-shape. After all pins are connected,
    collinear touching segments are merged to reduce visual clutter.
    """
    segments: list[WireSegment] = []
    remaining = list(points)

    # Start from leftmost point (design spec §4.4).
    remaining.sort(key=lambda p: (p[0], p[1]))
    root = remaining.pop(0)
    connected = [root]

    while remaining:
        # Find closest unconnected point to any connected point.
        best = None
        best_dist = float("inf")
        for cpt in connected:
            for i, pt in enumerate(remaining):
                dist = abs(cpt[0] - pt[0]) + abs(cpt[1] - pt[1])
                if dist < best_dist:
                    best_dist = dist
                    best = (i, cpt, pt)

        assert best is not None
        i, cpt, pt = best
        remaining.pop(i)

        # Add L-shaped route using exact pin positions so wires meet the pins.
        # Prefer vertical-first: it avoids running horizontal trunk lines through
        # pins that happen to share the same y coordinate on other nets.
        corner = (cpt[0], pt[1])
        if corner != cpt:
            seg = WireSegment(cpt, corner)
            if seg.start != seg.end:
                segments.append(seg)
        if corner != pt:
            seg = WireSegment(corner, pt)
            if seg.start != seg.end:
                segments.append(seg)
        connected.append(pt)

    return segments


def _merge_collinear_segments(
    segments: list[WireSegment],
    pin_points: list[tuple[float, float]] | None = None,
) -> list[WireSegment]:
    """Merge adjacent axis-aligned segments that lie on the same line.

    Never merges through a pin position — turning a pin endpoint into an
    interior point would make that pin invisible to the ERC extractor.
    """
    pin_set = set(pin_points) if pin_points else set()
    changed = True
    while changed:
        changed = False
        merged: list[WireSegment] = []
        used: set[int] = set()
        for i, a in enumerate(segments):
            if i in used:
                continue
            for j in range(i + 1, len(segments)):
                if j in used:
                    continue
                b = segments[j]
                combined = _try_merge(a, b)
                if combined is not None:
                    # Check the junction point (where a and b meet) — if
                    # it's a pin position, don't merge so the pin stays
                    # visible as a wire endpoint.
                    if _merge_junction_is_pin(a, b, pin_set):
                        continue
                    a = combined
                    used.add(j)
                    changed = True
            merged.append(a)
            used.add(i)
        segments = merged
    return segments


def _merge_junction_is_pin(
    a: WireSegment, b: WireSegment, pin_set: set[tuple[float, float]],
) -> bool:
    """True if a and b share a junction that is also a pin position."""
    eps = 1e-6
    # a's endpoints
    for ax, ay in (a.start, a.end):
        for bx, by in (b.start, b.end):
            if abs(ax - bx) < eps and abs(ay - by) < eps:
                if (ax, ay) in pin_set:
                    return True
    return False


def _try_merge(a: WireSegment, b: WireSegment) -> WireSegment | None:
    """Return a merged segment if a and b are collinear and touch at an endpoint."""

    def norm(s: WireSegment):
        if s.start <= s.end:
            return s
        return WireSegment(start=s.end, end=s.start)

    a = norm(a)
    b = norm(b)

    if a.start[0] == a.end[0] and b.start[0] == b.end[0] and a.start[0] == b.start[0]:
        # Vertical.
        ay = sorted([a.start[1], a.end[1]])
        by = sorted([b.start[1], b.end[1]])
        if ay[1] >= by[0] and by[1] >= ay[0]:
            y0, y1 = min(ay[0], by[0]), max(ay[1], by[1])
            return WireSegment(start=(a.start[0], y0), end=(a.start[0], y1))
    if a.start[1] == a.end[1] and b.start[1] == b.end[1] and a.start[1] == b.start[1]:
        # Horizontal.
        ax = sorted([a.start[0], a.end[0]])
        bx = sorted([b.start[0], b.end[0]])
        if ax[1] >= bx[0] and bx[1] >= ax[0]:
            x0, x1 = min(ax[0], bx[0]), max(ax[1], bx[1])
            return WireSegment(start=(x0, a.start[1]), end=(x1, a.start[1]))
    return None


def _snap_segment(
    a: tuple[float, float], b: tuple[float, float], config: LayoutConfig
) -> WireSegment:
    grid = config.grid.size_mm
    a = (round(a[0] / grid) * grid, round(a[1] / grid) * grid)
    b = (round(b[0] / grid) * grid, round(b[1] / grid) * grid)
    return WireSegment(start=a, end=b)


def is_orthogonal(segment: WireSegment) -> bool:
    """True if segment is horizontal or vertical."""
    return segment.start[0] == segment.end[0] or segment.start[1] == segment.end[1]
