"""Protel .NET netlist parser.

The Protel .NET format consists of two block types:

- Component blocks wrapped in ``[ ... ]`` with lines:
  ``designator``, ``footprint``, ``raw_value``.
- Net blocks wrapped in ``( ... )`` with the net name on the first line
  followed by ``component-pin`` references like ``R70-1``.

The parser performs a two-pass scan: collect component metadata first, then
scan net blocks to build nets, connections, and infer each component's pins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from circuit_graph.ir import (
    CircuitIR,
    CircuitMeta,
    Component,
    Connection,
    DeviceKind,
    Net,
    Pin,
    Provenance,
    make_pin_id,
)


@dataclass
class ParserInput:
    source_id: str = ""
    file_path: str | None = None
    content: str | None = None
    hints: dict[str, Any] = field(default_factory=dict)


class ProtelNetParser:
    """Parser for Protel .NET files."""

    name = "Protel Netlist Parser"
    format_id = "protel"
    extensions = {".net", ".NET"}

    def can_parse(self, input_: ParserInput) -> bool:
        if input_.file_path:
            suffix = Path(input_.file_path).suffix.lower()
            if suffix in {".net"}:
                return True
        if input_.content:
            has_component = re.search(r"^\[\s*$", input_.content, re.MULTILINE) is not None
            has_net = re.search(r"^\(\s*$", input_.content, re.MULTILINE) is not None
            if has_component and has_net:
                return True
        return False

    def parse(self, input_: ParserInput) -> CircuitIR:
        content = input_.content
        if content is None:
            if input_.file_path is None:
                raise ValueError("ParserInput must provide content or file_path")
            content = Path(input_.file_path).read_text(encoding="utf-8")
        lines = content.splitlines()

        # First pass: collect component metadata and pin numbers from net blocks.
        component_map: dict[str, _ComponentDraft] = {}
        nets: list[Net] = []
        connections: list[Connection] = []

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line == "[":
                block_lines: list[str] = []
                i += 1
                while i < len(lines) and lines[i].strip() != "]":
                    block_lines.append(lines[i].strip())
                    i += 1
                comp_info = self._parse_component_block(block_lines)
                if comp_info:
                    component_map[comp_info.id] = comp_info
            elif line == "(":
                block_lines = []
                i += 1
                while i < len(lines) and lines[i].strip() != ")":
                    block_lines.append(lines[i].strip())
                    i += 1
                net_result = self._parse_net_block(block_lines)
                if net_result:
                    nets.append(net_result.net)
                    connections.extend(net_result.connections)
                    for conn in net_result.connections:
                        comp_id, pin_num = self._split_pin_ref(conn.pin_id)
                        if comp_id and pin_num and comp_id in component_map:
                            component_map[comp_id].pin_numbers.add(pin_num)
            i += 1

        # Build components with sorted pins.
        components: list[Component] = []
        for draft in component_map.values():
            pins = [
                Pin(id=make_pin_id(draft.id, num), number=num)
                for num in sorted(draft.pin_numbers, key=lambda x: int(x))
            ]
            device_kind = self._infer_device_kind(draft.id, draft.footprint, draft.raw_value)
            components.append(
                Component(
                    id=draft.id,
                    device_kind=device_kind,
                    footprint=draft.footprint,
                    raw_value=draft.raw_value,
                    part_number=self._extract_part_number(device_kind, draft.raw_value),
                    pins=pins,
                )
            )

        return CircuitIR(
            meta=CircuitMeta(
                source=input_.source_id or (input_.file_path or ""),
                format="protel-net",
            ),
            components=components,
            nets=nets,
            connections=connections,
        )

    @staticmethod
    def _parse_component_block(lines: list[str]) -> _ComponentDraft | None:
        if not lines:
            return None
        return _ComponentDraft(
            id=lines[0],
            footprint=lines[1] if len(lines) > 1 else None,
            raw_value=lines[2] if len(lines) > 2 else None,
        )

    @staticmethod
    def _extract_part_number(device_kind: DeviceKind, raw_value: str | None) -> str | None:
        """Pull a part number from the third line of a Protel component block.

        Protel stores everything on one line, so for ICs that line carries the
        vendor part number (e.g. ``HT7533 -23``), while for passives it carries
        the value (e.g. ``4.7UF16V``). We only propagate the line as a
        ``part_number`` for ICs to avoid leaking resistor/capacitor values into
        the knowledge-base lookup path.

        ``None`` (rather than ``""``) is returned when no part number is
        available — that keeps downstream ``kb.get_category`` semantics clean.
        """
        if device_kind is not DeviceKind.IC:
            return None
        if not raw_value:
            return None
        cleaned = raw_value.strip()
        return cleaned or None

    @staticmethod
    def _parse_net_block(lines: list[str]) -> _NetResult | None:
        if not lines:
            return None
        name = lines[0]
        net = Net(id=name, name=name)
        connections: list[Connection] = []
        for token in lines[1:]:
            token = token.strip()
            if not token:
                continue
            match = re.match(r"^(.+)-(\d+)$", token)
            if not match:
                continue
            comp_id = match.group(1)
            pin_number = match.group(2)
            pin_id = make_pin_id(comp_id, pin_number)
            connections.append(
                Connection(pin_id=pin_id, net_id=name, provenance=Provenance.NETLIST)
            )
        return _NetResult(net=net, connections=connections)

    @staticmethod
    def _split_pin_ref(pin_id: str) -> tuple[str, str] | tuple[None, None]:
        if "." not in pin_id:
            return None, None
        comp_id, _, pin_num = pin_id.rpartition(".")
        if not comp_id or not pin_num:
            return None, None
        return comp_id, pin_num

    @staticmethod
    def _infer_device_kind(ref: str, footprint: str | None, raw_value: str | None) -> DeviceKind:
        fp = (footprint or "").upper()
        rv = (raw_value or "").lower()

        # Footprint/value overrides prefix heuristic.
        if fp.startswith("L") and len(fp) > 1 and fp[1].isdigit():
            return DeviceKind.INDUCTOR
        if rv == "coil":
            return DeviceKind.INDUCTOR

        # Integrated bridge rectifier: footprint evidence (e.g. "Bridge",
        # "BRIDGE-4PIN") is reliable regardless of reference designator.
        if "BRIDGE" in fp:
            return DeviceKind.BRIDGE_RECTIFIER

        # Connector signature (e.g. USB-C with a U-prefixed designator) takes
        # precedence over the designator-prefix heuristic below: the footprint
        # or value is stronger evidence than the reference letter.
        rv_upper = (raw_value or "").upper()
        connector_signatures = ("USB", "TYPE-C", "TYPE_C", "TYPEC")
        if any(sig in fp or sig in rv_upper for sig in connector_signatures):
            return DeviceKind.CONNECTOR

        prefix = ref[0].upper() if ref else ""
        if prefix == "R":
            return DeviceKind.RESISTOR
        if prefix == "C":
            return DeviceKind.CAPACITOR
        if prefix == "L":
            return DeviceKind.INDUCTOR
        if prefix == "U":
            return DeviceKind.IC
        if prefix == "Q":
            return DeviceKind.MOSFET
        if prefix == "D":
            return DeviceKind.DIODE
        if prefix in {"J", "P"}:
            return DeviceKind.CONNECTOR

        if rv == "nc":
            return DeviceKind.UNKNOWN
        return DeviceKind.UNKNOWN


@dataclass
class _ComponentDraft:
    id: str
    footprint: str | None = None
    raw_value: str | None = None
    pin_numbers: set[str] = field(default_factory=set)


@dataclass
class _NetResult:
    net: Net
    connections: list[Connection] = field(default_factory=list)
