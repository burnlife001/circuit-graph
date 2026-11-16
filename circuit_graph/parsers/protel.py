"""Protel .NET netlist parser.

Supports two flavours of the Protel .NET format detected by content sniffing:

* **Legacy** (``PROTEL NETLIST 1.x`` or unmarked): component blocks are
  3-line ``[ designator / footprint / raw_value ]`` records; net blocks are
  ``( NETNAME  COMP-PIN ... )`` references.
* **v2** (``PROTEL NETLIST 2.0`` header): component blocks are
  ``[ key/value/key/value/.../* ]`` records with rich LCSC BOM metadata;
  net blocks carry three tokens per pin reference —
  ``COMP-PIN  DEVICE-PINNAME  ELEC_TYPE`` — so pin name and electrical
  type can be populated directly from the netlist.

The parser auto-detects the variant by scanning the first non-empty line of
the file.  The legacy path is unchanged for backwards compatibility; the
v2 path is additive.
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

# Encoding candidates tried in order.  UTF-8 first covers modern exports
# (including BOM); GBK covers Protel exports from Chinese-localised Altium
# Designer; Latin-1 is a final safety net that always succeeds.
_NETLIST_ENCODINGS: tuple[str, ...] = ("utf-8", "gbk", "latin-1")

# v2 net-block electrical-type strings → IR Pin.type literal (lowercased).
# Unknown / unrecognised tokens fall back to None so the PinNameResolver
# enricher can still drive enrichment from the KB if desired.
_ELEC_TYPE_MAP: dict[str, str] = {
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "passive": "passive",
    "power": "power",
    "gnd": "gnd",
    "ground": "gnd",
    "open_collector": "unknown",
    "open_emitter": "unknown",
    "tri_state": "unknown",
    "unspecified": "unknown",
    "unknown": "unknown",
}

# v2 metadata keys that are promoted into IR Component fields instead of
# being left in ``metadata``.  Order matters: the first key present wins.
_V2_PART_NUMBER_KEYS: tuple[str, ...] = (
    "Manufacturer Part",
    "Manufacturer_Part",
    "ManufacturerPart",
    "PARTTYPE",
    "Device",  # For ICs PARTTYPE == Device; for connector symbols they differ.
)

_V2_PASSIVE_NAME_KEYS: tuple[str, ...] = (
    "Name",
    "Value",
    "PARTTYPE",
)


def _read_text_with_fallback(path: Path) -> str:
    """Read ``path`` as text, trying each candidate encoding in order.

    Some Chinese-localised Altium exports emit byte sequences that are valid
    under BOTH UTF-8 and GBK but decode to mojibake under UTF-8 (e.g.
    ``FOJAN(\xe5\xaf\x8c\xe6\x8d\xb7)`` → ``FOJAN(帘捷)`` as UTF-8 vs the
    intended ``FOJAN(富捷)`` as GBK).  When both succeed, we pick the
    decoding that yields more CJK Unified Ideographs — that's a reliable
    proxy for "Chinese characters actually look Chinese" without bringing in
    chardet.
    """
    raw = path.read_bytes()

    decoded: dict[str, str] = {}
    for encoding in _NETLIST_ENCODINGS:
        try:
            decoded[encoding] = raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    if not decoded:
        return raw.decode("utf-8", errors="replace")

    if len(decoded) == 1:
        return next(iter(decoded.values()))

    def _cjk_count(text: str) -> int:
        return sum(1 for ch in text if 0x4E00 <= ord(ch) <= 0x9FFF)

    best_encoding = max(decoded, key=lambda enc: _cjk_count(decoded[enc]))
    return decoded[best_encoding]


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
            content = _read_text_with_fallback(Path(input_.file_path))
        lines = content.splitlines()

        is_v2 = self._is_v2(content)
        if is_v2:
            return self._parse_v2(input_, content, lines)

        # Legacy three-line component block + single-token net references.
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
                for num in sorted(draft.pin_numbers, key=lambda x: (0, int(x)) if x.isdigit() else (1, 0, x))
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

    # ---------- v2 (PROTEL NETLIST 2.0) ----------

    @staticmethod
    def _is_v2(content: str) -> bool:
        """Return True if the file begins with the v2 header marker.

        Only the first non-empty line is inspected — the rest of the file
        is treated as the v2 layout whenever that line matches.  This is the
        simplest and most robust heuristic given that no v1 export emits
        ``PROTEL NETLIST 2.0`` and v2 exports always emit it as the very
        first line.
        """
        for raw in content.splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            return stripped == "PROTEL NETLIST 2.0"
        return False

    def _parse_v2(
        self, input_: ParserInput, content: str, lines: list[str]
    ) -> CircuitIR:
        """Parse a PROTEL NETLIST 2.0 file into a CircuitIR.

        The schema is alternating ``key/value`` lines inside ``[ ... ]``
        blocks (terminated by a ``*`` line), plus the same ``( NETNAME … )``
        net blocks as legacy Protel — but each pin reference now carries
        three tokens: ``COMP-PIN  DEVICE-PINNAME  ELEC_TYPE``.
        """
        # Re-scan from scratch; the dispatcher only peeked at the header.
        lines = content.splitlines()

        component_map: dict[str, _ComponentDraftV2] = {}
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
                comp = self._parse_component_block_v2(block_lines)
                if comp and not self._is_blank_component_v2(comp):
                    # Last-wins on duplicate DESIGNATOR so the rich metadata
                    # block (which always appears after the bare designator
                    # line) overrides a minimal earlier declaration.
                    component_map[comp.id] = comp
            elif line == "(":
                block_lines = []
                i += 1
                while i < len(lines) and lines[i].strip() != ")":
                    block_lines.append(lines[i].strip())
                    i += 1
                net_result = self._parse_net_block_v2(block_lines)
                if net_result:
                    nets.append(net_result.net)
                    connections.extend(net_result.connections)
                    for entry in net_result.entries:
                        comp_id = entry["comp_id"]
                        if comp_id in component_map:
                            component_map[comp_id].pin_numbers.add(entry["pin_num"])
                            if entry["pin_name"]:
                                component_map[comp_id].pin_names[entry["pin_num"]] = entry[
                                    "pin_name"
                                ]
                            if entry["pin_type"]:
                                component_map[comp_id].pin_types[entry["pin_num"]] = entry[
                                    "pin_type"
                                ]
            i += 1

        components: list[Component] = []
        for draft in component_map.values():
            pins: list[Pin] = []
            for num in sorted(draft.pin_numbers, key=lambda x: int(x)):
                raw_name = draft.pin_names.get(num)
                # Only preserve the netlist-side pin name when it differs
                # from the pin number — that way the PinNameResolver KB
                # enricher can still overwrite with a richer label for ICs.
                pin_name = raw_name if (raw_name and raw_name != num) else None
                pin_type = draft.pin_types.get(num)
                pins.append(
                    Pin(
                        id=make_pin_id(draft.id, num),
                        number=num,
                        name=pin_name,
                        type=pin_type,
                    )
                )
            device_kind = self._infer_device_kind(
                draft.id, draft.footprint, draft.raw_value
            )
            components.append(
                Component(
                    id=draft.id,
                    device_kind=device_kind,
                    footprint=draft.footprint,
                    raw_value=draft.raw_value,
                    manufacturer=draft.manufacturer,
                    part_number=self._v2_part_number_for(draft, device_kind),
                    pins=pins,
                    metadata=draft.metadata,
                )
            )

        return CircuitIR(
            meta=CircuitMeta(
                source=input_.source_id or (input_.file_path or ""),
                format="protel-net-v2",
            ),
            components=components,
            nets=nets,
            connections=connections,
        )

    @staticmethod
    def _parse_component_block_v2(lines: list[str]) -> _ComponentDraftV2 | None:
        """Parse a key/value component block into a rich draft.

        v2 blocks end with a sentinel ``*`` line; everything between the
        opening ``[`` and the sentinel is treated as alternating
        ``key/value`` pairs.  Empty values are kept (becoming empty-string
        metadata) so that block structure is preserved losslessly.
        """
        if not lines:
            return None

        # Strip trailing "*" sentinel.
        body = [ln for ln in lines if ln != "*"]
        if not body:
            return None

        pairs: dict[str, str] = {}
        # Preserve insert order for diagnostics; first occurrence wins so
        # we don't get confused by v2 emitting duplicate keys (e.g. U2 has
        # two "Description" lines, both blank).
        order: list[str] = []
        for idx in range(0, len(body) - 1, 2):
            key = body[idx].strip()
            value = body[idx + 1].strip() if idx + 1 < len(body) else ""
            if key not in pairs:
                order.append(key)
            pairs.setdefault(key, value)
        # If the body has an odd number of lines, the trailing line is
        # treated as a key with an empty value (lenient fallback).
        if len(body) % 2 == 1:
            trailing = body[-1].strip()
            if trailing and trailing not in pairs:
                order.append(trailing)
                pairs.setdefault(trailing, "")

        designator = pairs.get("DESIGNATOR") or pairs.get("Designator")
        if not designator:
            return None

        footprint = pairs.get("FOOTPRINT") or None
        raw_value: str | None = None
        if footprint:
            # Pick the most informative raw-value field for non-ICs:
            #   Name/Value (e.g. "100Ω", "10uF") > PARTTYPE.
            for key in _V2_PASSIVE_NAME_KEYS:
                if pairs.get(key):
                    raw_value = pairs[key]
                    break

        # Promote only those keys that map to IR Component fields. Anything else
        # (LCSC Part Name, Datasheet, Unique ID, etc.) stays in metadata so
        # nothing from the source netlist is silently discarded.
        promoted = {
            "DESIGNATOR",
            "Designator",  # → id
            "FOOTPRINT",  # → footprint
            "PARTTYPE",
            "Device",  # → raw_value for ICs (else Name/Value wins for passives)
            "Name",
            "Value",  # → raw_value for passives
            "Manufacturer",  # → manufacturer (IR field)
            "Manufacturer Part",  # → part_number for non-passives
        }
        metadata = {
            key: pairs[key] for key in order if key not in promoted and pairs.get(key)
        }

        # Empty metadata block still acceptable — Component accepts {}.
        return _ComponentDraftV2(
            id=designator,
            footprint=footprint,
            raw_value=raw_value,
            manufacturer=pairs.get("Manufacturer") or None,
            metadata=metadata,
        )

    @staticmethod
    def _v2_part_number_for(draft: _ComponentDraftV2, device_kind: DeviceKind) -> str | None:
        """Return the most useful part_number for a v2 component.

        ICs / MOSFETs / connectors get the manufacturer part number when
        available — that's the lookup key for the knowledge base.  Passives
        (R / C / L) deliberately get ``None`` to avoid polluting the KB
        lookup path with resistor values, mirroring the legacy parser.
        """
        if device_kind in {DeviceKind.RESISTOR, DeviceKind.CAPACITOR, DeviceKind.INDUCTOR}:
            return None
        for key in _V2_PART_NUMBER_KEYS:
            value = draft.metadata.get(key)
            if value:
                return value
        # Fall back to PARTTYPE inside draft itself if no metadata match.
        return (
            draft.raw_value
            if device_kind in {
                DeviceKind.IC, DeviceKind.MOSFET, DeviceKind.DIODE, DeviceKind.CONNECTOR
            }
            else None
        )

    @staticmethod
    def _is_blank_component_v2(draft: _ComponentDraftV2) -> bool:
        """A v2 block is "blank" if every keyed field is empty.

        Some v2 exports emit a bare ``DESIGNATOR\n*\n`` block to anchor
        annotation before delivering the full record; we drop those.
        """
        return (
            not draft.footprint
            and not draft.raw_value
            and not draft.manufacturer
            and not draft.metadata
        )

    @staticmethod
    def _parse_net_block_v2(lines: list[str]) -> _NetResultV2 | None:
        """Parse a v2 net block, returning per-pin name and electrical type.

        Each non-empty line carries three whitespace-separated tokens:

        * ``COMP-PIN``  (e.g. ``R5-2``)
        * ``DEVICE-PINNAME`` (e.g. ``FRC0402F1503TS-1`` ⇒ name ``1``)
        * ``ELEC_TYPE``  (e.g. ``Input``, ``Passive``, ``Power``)

        Legacy single-token lines (``R5-2`` only) are still accepted —
        ``pin_name`` and ``pin_type`` stay None for those.
        """
        if not lines:
            return None
        name = lines[0].strip()
        if not name:
            return None
        net = Net(id=name, name=name)
        connections: list[Connection] = []
        entries: list[dict[str, str | None]] = []
        for raw in lines[1:]:
            line = raw.strip()
            if not line:
                continue
            parts = line.split()
            first = parts[0]
            match = re.match(r"^(.+)-(\d+)$", first)
            if not match:
                continue
            comp_id = match.group(1)
            pin_num = match.group(2)
            pin_id = make_pin_id(comp_id, pin_num)

            pin_name: str | None = None
            if len(parts) >= 2:
                dev_tok = parts[1]
                if "-" in dev_tok:
                    # Strip the device prefix; "<DEV>-<NAME>" → NAME.
                    _, _, suffix = dev_tok.rpartition("-")
                    pin_name = suffix or None
                else:
                    pin_name = dev_tok or None

            pin_type: str | None = None
            if len(parts) >= 3:
                pin_type = _ELEC_TYPE_MAP.get(parts[2].lower())

            connections.append(
                Connection(pin_id=pin_id, net_id=name, provenance=Provenance.NETLIST)
            )
            entries.append(
                {
                    "comp_id": comp_id,
                    "pin_num": pin_num,
                    "pin_name": pin_name,
                    "pin_type": pin_type,
                }
            )
        if not connections:
            return None
        return _NetResultV2(net=net, connections=connections, entries=entries)

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
            match = re.match(r"^(.+)-([A-Za-z0-9]+)$", token)
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

        # LED* designator override — must precede the L* inductor heuristic
        # below so LEDs don't get misclassified.  IR has no LED enum; DIODE
        # is the closest functional match.
        if ref and ref.upper().startswith("LED") and ref[:3].upper() == "LED":
            return DeviceKind.DIODE

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
        # "IC*" 与 "U*" 都是通用 IC 位号前缀（"IC" 必须先判，否则落到 UNKNOWN）。
        if prefix == "U" or (ref or "").upper().startswith("IC"):
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


@dataclass
class _ComponentDraftV2:
    """Component-side accumulator for the v2 (PROTEL NETLIST 2.0) parser.

    Holds rich metadata parsed from key/value pairs plus per-pin enrichments
    (pin name, electrical type) discovered while scanning net blocks.
    """

    id: str
    footprint: str | None = None
    raw_value: str | None = None
    manufacturer: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    pin_numbers: set[str] = field(default_factory=set)
    pin_names: dict[str, str] = field(default_factory=dict)
    pin_types: dict[str, str] = field(default_factory=dict)


@dataclass
class _NetResultV2:
    net: Net
    connections: list[Connection] = field(default_factory=list)
    entries: list[dict[str, str | None]] = field(default_factory=list)
