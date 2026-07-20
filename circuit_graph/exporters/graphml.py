"""GraphML exporter for CircuitGraph.

Exports the heterogeneous circuit graph to a yEd-compatible GraphML file.
The output includes:

- Standard GraphML node/edge attributes (kind, device_kind, value, net_name, ...)
  so NetworkX/Gephi/Cytoscape can still read the data.
- yEd visual extensions (``y:ShapeNode`` / ``y:PolyLineEdge``) with layout,
  shapes, colors and labels so the graph renders immediately in yEd.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import networkx as nx

from circuit_graph.graph import create_graph
from circuit_graph.ir import CircuitIR, Component, FunctionalBlock, Net, Pin

# yFiles / yEd namespace.
_Y_NS = "http://www.yworks.com/xml/graphml"
_Y = f"{{{_Y_NS}}}"

# GraphML namespace.
_G_NS = "http://graphml.graphdrawing.org/xmlns"
_G = f"{{{_G_NS}}}"

# Visual style by node kind.
_STYLE = {
    "component": {
        "shape": "rectangle",
        "fill": "#A0C8FF",
        "width": 90,
        "height": 40,
    },
    "pin": {
        "shape": "ellipse",
        "fill": "#FFE680",
        "width": 35,
        "height": 35,
    },
    "net": {
        "shape": "roundrectangle",
        "fill": "#80E080",
        "width": 80,
        "height": 35,
    },
    "functional_block": {
        "shape": "rectangle",
        "fill": "#D0D0D0",
        "width": 120,
        "height": 45,
    },
}


def write_graphml(ir: CircuitIR, output_path: str | Path) -> None:
    """Export ``ir`` to a yEd-compatible GraphML file at ``output_path``."""
    graph = create_graph()
    graph.load(ir)

    out = nx.DiGraph()
    node_attrs: dict[str, dict[str, Any]] = {}

    for node in graph.nodes():
        attrs: dict[str, Any] = {"kind": node.kind.value}
        if isinstance(node.data, Component):
            attrs.update(_component_attrs(node.data))
        elif isinstance(node.data, Pin):
            attrs.update(_pin_attrs(node.data))
        elif isinstance(node.data, Net):
            attrs.update(_net_attrs(node.data))
        elif isinstance(node.data, FunctionalBlock):
            attrs.update(_block_attrs(node.data))
        sanitized = _sanitize_attrs(attrs)
        node_attrs[node.id] = sanitized
        out.add_node(node.id, **sanitized)

    for edge in graph.edges():
        out.add_edge(edge.source, edge.target, kind=edge.kind.value)

    # Compute a layout so nodes do not all stack at the origin.
    # Multipartite layout groups nodes by kind into horizontal layers, which is
    # intuitive for component/pin/net/block graphs and does not require scipy.
    if len(out.nodes) > 0:
        for node_id, attrs in node_attrs.items():
            out.nodes[node_id]["layer"] = attrs.get("kind", "unknown")
        pos = nx.multipartite_layout(out, subset_key="layer", align="vertical")
        # Scale to screen-friendly coordinates.
        scale = 180.0
        for node_id in pos:
            pos[node_id] = (pos[node_id][0] * scale, pos[node_id][1] * scale)
    else:
        pos = {}

    _write_yed_graphml(out, node_attrs, pos, Path(output_path))


def _write_yed_graphml(
    nx_graph: nx.DiGraph,
    node_attrs: dict[str, dict[str, Any]],
    positions: dict[str, tuple[float, float]],
    output_path: Path,
) -> None:
    """Serialize the NetworkX graph to a yEd-compatible GraphML XML file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    root = ET.Element("graphml")
    root.set("xmlns", _G_NS)
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.set("xmlns:y", _Y_NS)
    root.set(
        "xsi:schemaLocation",
        f"{_G_NS} http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd",
    )

    # Collect all attribute keys used on nodes and edges.
    node_keys: dict[str, str] = {}
    edge_keys: dict[str, str] = {}
    for attrs in node_attrs.values():
        for key in attrs:
            if key not in node_keys:
                node_keys[key] = _attr_type(attrs[key])
    for _u, _v, attrs in nx_graph.edges(data=True):
        for key in attrs:
            if key not in edge_keys:
                edge_keys[key] = _attr_type(attrs[key])

    key_ids: dict[str, str] = {}
    key_counter = 0

    def add_key(for_type: str, attr_name: str, attr_type: str) -> str:
        nonlocal key_counter
        key_id = f"d{key_counter}"
        key_counter += 1
        key_el = ET.SubElement(root, "key")
        key_el.set("id", key_id)
        key_el.set("for", for_type)
        key_el.set("attr.name", attr_name)
        key_el.set("attr.type", attr_type)
        return key_id

    for attr_name, attr_type in node_keys.items():
        key_ids[f"node:{attr_name}"] = add_key("node", attr_name, attr_type)
    for attr_name, attr_type in edge_keys.items():
        key_ids[f"edge:{attr_name}"] = add_key("edge", attr_name, attr_type)

    # yEd graphics keys.
    node_graphics_key = f"d{key_counter}"
    key_counter += 1
    ng_key = ET.SubElement(root, "key")
    ng_key.set("id", node_graphics_key)
    ng_key.set("for", "node")
    ng_key.set("yfiles.type", "nodegraphics")

    edge_graphics_key = f"d{key_counter}"
    key_counter += 1
    eg_key = ET.SubElement(root, "key")
    eg_key.set("id", edge_graphics_key)
    eg_key.set("for", "edge")
    eg_key.set("yfiles.type", "edgegraphics")

    graph_el = ET.SubElement(root, "graph")
    graph_el.set("id", "G")
    graph_el.set("edgedefault", "directed")

    for node_id in nx_graph.nodes():
        attrs = node_attrs[node_id]
        node_el = ET.SubElement(graph_el, "node")
        node_el.set("id", node_id)

        for attr_name, value in attrs.items():
            data_el = ET.SubElement(node_el, "data")
            data_el.set("key", key_ids[f"node:{attr_name}"])
            data_el.text = str(value)

        # yEd visual representation.
        gfx_el = ET.SubElement(node_el, "data")
        gfx_el.set("key", node_graphics_key)
        shape_node = ET.SubElement(gfx_el, f"{_Y}ShapeNode")

        style = _STYLE.get(attrs.get("kind", ""), _STYLE["component"])
        x, y = positions.get(node_id, (0.0, 0.0))
        width = float(style["width"])
        height = float(style["height"])

        geometry = ET.SubElement(shape_node, f"{_Y}Geometry")
        geometry.set("height", str(height))
        geometry.set("width", str(width))
        geometry.set("x", str(x))
        geometry.set("y", str(y))

        fill = ET.SubElement(shape_node, f"{_Y}Fill")
        fill.set("color", style["fill"])
        fill.set("transparent", "false")

        border = ET.SubElement(shape_node, f"{_Y}BorderStyle")
        border.set("color", "#000000")
        border.set("type", "line")
        border.set("width", "1.0")

        label = ET.SubElement(shape_node, f"{_Y}NodeLabel")
        label.text = _node_label(node_id, attrs)
        label.set("alignment", "center")
        label.set("autoSizePolicy", "content")
        label.set("fontFamily", "Dialog")
        label.set("fontSize", "12")
        label.set("fontStyle", "plain")
        label.set("hasBackgroundColor", "false")
        label.set("hasLineColor", "false")
        label.set("height", "18.0")
        label.set("horizontalTextPosition", "center")
        label.set("iconTextGap", "4")
        label.set("modelName", "internal")
        label.set("modelPosition", "c")
        label.set("textColor", "#000000")
        label.set("verticalTextPosition", "bottom")
        label.set("visible", "true")
        label.set("width", "40.0")
        label.set("x", "0.0")
        label.set("y", "0.0")

        shape = ET.SubElement(shape_node, f"{_Y}Shape")
        shape.set("type", style["shape"])

    for source, target, attrs in nx_graph.edges(data=True):
        edge_el = ET.SubElement(graph_el, "edge")
        edge_el.set("source", source)
        edge_el.set("target", target)

        for attr_name, value in attrs.items():
            data_el = ET.SubElement(edge_el, "data")
            data_el.set("key", key_ids[f"edge:{attr_name}"])
            data_el.text = str(value)

        gfx_el = ET.SubElement(edge_el, "data")
        gfx_el.set("key", edge_graphics_key)
        poly_edge = ET.SubElement(gfx_el, f"{_Y}PolyLineEdge")

        path = ET.SubElement(poly_edge, f"{_Y}Path")
        path.set("sx", "0.0")
        path.set("sy", "0.0")
        path.set("tx", "0.0")
        path.set("ty", "0.0")

        line_style = ET.SubElement(poly_edge, f"{_Y}LineStyle")
        line_style.set("color", "#404040")
        line_style.set("type", "line")
        line_style.set("width", "1.0")

        arrows = ET.SubElement(poly_edge, f"{_Y}Arrows")
        arrows.set("source", "none")
        arrows.set("target", "none")

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def _node_label(node_id: str, attrs: dict[str, Any]) -> str:
    """Build a concise label for a node based on its attributes."""
    kind = attrs.get("kind", "")
    if kind == "component":
        value = attrs.get("value", "")
        raw = attrs.get("raw_value", "")
        footprint = attrs.get("footprint", "")
        parts = [node_id]
        if value:
            parts.append(value)
        elif raw:
            parts.append(raw)
        if footprint:
            parts.append(footprint)
        return "\n".join(parts)
    if kind == "pin":
        return f"{node_id}\n{attrs.get('pin_number', '')}"
    if kind == "net":
        return attrs.get("net_name", node_id)
    if kind == "functional_block":
        return attrs.get("block_name", node_id)
    return node_id


def _attr_type(value: Any) -> str:
    """Return the GraphML attr.type for a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "double"
    return "string"


def _component_attrs(component: Component) -> dict[str, Any]:
    """Return serializable attributes for a component node."""
    value = component.value
    attrs: dict[str, Any] = {
        "device_kind": component.device_kind.value,
        "footprint": component.footprint or "",
        "raw_value": component.raw_value or "",
        "value": _format_value(value),
        "value_kind": value.kind if value else "unknown",
        "manufacturer": component.manufacturer or "",
        "part_number": component.part_number or "",
    }
    if value is not None and hasattr(value, "magnitude"):
        attrs["value_magnitude"] = float(value.magnitude)
    if value is not None and hasattr(value, "unit"):
        attrs["value_unit"] = str(value.unit)
    return attrs


def _pin_attrs(pin: Pin) -> dict[str, Any]:
    """Return serializable attributes for a pin node."""
    return {
        "pin_number": pin.number,
        "pin_name": pin.name or "",
        "pin_type": pin.type or "unknown",
    }


def _net_attrs(net: Net) -> dict[str, Any]:
    """Return serializable attributes for a net node."""
    return {
        "net_name": net.name,
        "net_type": net.type.value if net.type else "unknown",
    }


def _block_attrs(block: FunctionalBlock) -> dict[str, Any]:
    """Return serializable attributes for a functional block node."""
    role = block.role.value if hasattr(block.role, "value") else block.role
    return {
        "block_name": block.name,
        "role": role,
        "description": block.description or "",
        "source": block.source,
    }


def _format_value(value: Any) -> str:
    """Best-effort string serialization of a normalized component value."""
    if value is None:
        return ""
    if hasattr(value, "magnitude") and hasattr(value, "unit"):
        magnitude = value.magnitude
        unit = value.unit
        return f"{magnitude} {unit}"
    return str(value)


def _sanitize_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    """Coerce attribute values to GraphML-safe basic types."""
    result: dict[str, Any] = {}
    for key, value in attrs.items():
        if value is None:
            result[key] = ""
        elif isinstance(value, bool):
            result[key] = value
        elif isinstance(value, (int, float)):
            result[key] = value
        elif isinstance(value, str):
            result[key] = value
        else:
            result[key] = str(value)
    return result
