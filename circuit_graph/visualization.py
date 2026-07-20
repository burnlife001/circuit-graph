"""Minimal SVG visualization for power trees.

This module deliberately avoids any external drawing dependency
(``schemdraw``, ``matplotlib``, etc.) — the tree is small and tree-shaped,
so a hand-rolled SVG keeps the dependency footprint at zero while still
producing a file humans can open in a browser.

The output is a left-to-right tree where:

* sources are drawn as green diamonds;
* regulators are drawn as blue rectangles labelled with part number / kind;
* nets are drawn as white rectangles labelled with the net name;
* loads are drawn as grey rectangles labelled with the component id.

The function is intentionally compact: a single column of boxes connected
by lines. Wider, multi-column layouts can be layered on later when a real
schematic-quality output is needed; for now the goal is "open the SVG and
see the tree".
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

from circuit_graph.models.power_tree import PowerTree

# Layout constants — tuned for readability in a browser viewport of ~1200px.
_BOX_W = 160
_BOX_H = 48
_HPAD = 24
_VPAD = 36

_COLORS = {
    "source": "#1f9d55",  # green
    "regulator": "#2b6cb0",  # blue
    "net": "#4a5568",  # dark grey
    "load": "#718096",  # mid grey
    "line": "#a0aec0",  # light grey
    "text": "#1a202c",
    "source_text": "#ffffff",
}


def render_power_tree(
    tree: PowerTree,
    output_path: str | Path,
    *,
    title: str | None = None,
) -> Path:
    """Render ``tree`` as an SVG file at ``output_path``.

    Returns the resolved path so callers can echo it back to the user.
    """
    resolved = Path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    svg = _build_svg(tree, title or "Power tree")
    resolved.write_text(svg, encoding="utf-8")
    return resolved


def render_power_tree_to_string(tree: PowerTree, *, title: str | None = None) -> str:
    """Return the SVG payload as a string (used by tests and previews)."""
    return _build_svg(tree, title or "Power tree")


def _build_svg(tree: PowerTree, title: str) -> str:
    width, height, boxes, lines = _layout(tree)
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{escape(title)}">',
        "<style>",
        "  .pt-box { stroke-width: 1.5; }",
        "  .pt-text { font-family: -apple-system, Segoe UI, Helvetica, sans-serif; "
        "font-size: 12px; }",
        "  .pt-title { font-family: -apple-system, Segoe UI, Helvetica, sans-serif; "
        "font-size: 14px; font-weight: 600; }",
        "</style>",
        f'<text class="pt-title" x="{_HPAD}" y="20">{escape(title)}</text>',
    ]
    for line in lines:
        parts.append(
            f'<line x1="{line[0]}" y1="{line[1]}" x2="{line[2]}" y2="{line[3]}" '
            f'stroke="{_COLORS["line"]}" stroke-width="1.5" />'
        )
    for box in boxes:
        parts.append(
            f'<rect class="pt-box" x="{box.x}" y="{box.y}" '
            f'width="{_BOX_W}" height="{_BOX_H}" rx="6" ry="6" '
            f'fill="{box.fill}" stroke="{box.stroke}" />'
        )
        text_color = box.text_color
        parts.append(
            f'<text class="pt-text" x="{box.x + _BOX_W / 2}" y="{box.y + _BOX_H / 2 + 4}" '
            f'fill="{text_color}" text-anchor="middle">{escape(box.label)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


@dataclass
class _Box:  # noqa: D401 - tiny private holder
    x: float
    y: float
    label: str
    fill: str
    stroke: str
    text_color: str


def _layout(tree: PowerTree) -> tuple[int, int, list[_Box], list[tuple[int, int, int, int]]]:
    """Compute SVG dimensions, boxes and connector lines.

    Each tree root occupies its own row, and rows stretch vertically by the
    depth of the deepest subtree below them. ``_Box`` is reused because the
    shape is just (x, y, label, fill, stroke, text_color) — a dataclass would
    cost more in attribute lookups than the savings from typing.
    """
    if not tree.roots:
        return 320, 96, [], []

    boxes: list[_Box] = []
    lines: list[tuple[int, int, int, int]] = []
    cursor_y = 40
    max_right = 0
    for root_id in tree.roots:
        # Each root starts a new vertical lane; lanes stack downward.
        # Horizontal lanes (siblings under the same parent) sit side by side.
        depth = _subtree_depth(tree, root_id)
        col_y = cursor_y
        col_x = _HPAD
        # Render the root.
        col_x, col_y, sub_max_right, sub_height = _layout_node(
            tree, root_id, x=col_x, y=col_y, boxes=boxes, lines=lines, depth_left=depth
        )
        max_right = max(max_right, sub_max_right)
        cursor_y += sub_height + _VPAD

    width = max_right + _HPAD
    height = cursor_y
    return width, height, boxes, lines


def _collect_subtree(tree: PowerTree, root_id: str) -> list[str]:
    out = [root_id]
    queue = [root_id]
    while queue:
        nid = queue.pop(0)
        for child in tree.children.get(nid, []):
            out.append(child)
            queue.append(child)
    return out


def _subtree_depth(tree: PowerTree, node_id: str) -> int:
    children = tree.children.get(node_id, [])
    if not children:
        return 1
    return 1 + max(_subtree_depth(tree, c) for c in children)


def _layout_node(
    tree: PowerTree,
    node_id: str,
    *,
    x: float,
    y: float,
    boxes: list[_Box],
    lines: list[tuple[int, int, int, int]],
    depth_left: int,
) -> tuple[float, float, float, float]:
    """Place ``node_id`` at ``(x, y)``; recursively lay out its children.

    Returns ``(right_edge, next_y, max_right, used_height)``. The subtree
    grows rightward; siblings of this node will start at ``next_y``.
    """
    node = tree.nodes[node_id]
    fill, stroke, text_color = _node_style(node.kind)
    boxes.append(
        _Box(
            x=x,
            y=y,
            label=node.label,
            fill=fill,
            stroke=stroke,
            text_color=text_color,
        )
    )
    right = x + _BOX_W
    children = tree.children.get(node_id, [])
    if not children:
        return right, y + _BOX_H + _VPAD, right, _BOX_H + _VPAD

    child_x = right + _HPAD
    child_y = y
    max_right = right
    used_height = _BOX_H + _VPAD
    for child_id in children:
        child_x = max(child_x, right + _HPAD)
        child_right, next_y, sub_right, sub_height = _layout_node(
            tree,
            child_id,
            x=child_x,
            y=child_y,
            boxes=boxes,
            lines=lines,
            depth_left=depth_left - 1,
        )
        # Connector line from this node's right edge to the child's left.
        parent_mid_y = y + _BOX_H / 2
        child_mid_y = child_y + _BOX_H / 2
        lines.append((int(right), int(parent_mid_y), int(child_x), int(child_mid_y)))
        max_right = max(max_right, sub_right)
        child_y = next_y
        child_x = sub_right + _HPAD
        used_height = max(used_height, child_y - y)
    return max_right, child_y, max_right, used_height


def _node_style(kind: str) -> tuple[str, str, str]:
    if kind == "source":
        return _COLORS["source"], _COLORS["source"], _COLORS["source_text"]
    if kind == "regulator":
        return "#ffffff", _COLORS["regulator"], _COLORS["regulator"]
    if kind == "net":
        return "#ffffff", _COLORS["net"], _COLORS["text"]
    if kind == "load":
        return "#edf2f7", _COLORS["load"], _COLORS["text"]
    return "#ffffff", _COLORS["net"], _COLORS["text"]


__all__ = ["render_power_tree", "render_power_tree_to_string"]
