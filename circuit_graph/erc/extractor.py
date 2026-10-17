"""S-expression -> NetMap extractor for .kicad_sch files.

Parses a KiCad 9 schematic using ``sexpdata`` and produces:

- :class:`ComponentContext` - one per ``(symbol ...)`` instance
- :class:`ExtractedSchematic` - bundles components, nets, warnings

Strategy:
1. Parse the top-level S-expression.
2. Extract ``(lib_symbols ...)`` for pin definitions (number / name /
   electrical type / relative position).
3. Extract each top-level ``(symbol ...)`` instance - reference, value,
   position.
4. Compute absolute pin positions.
5. Collect wires / junctions / labels into a connectivity graph.
6. Run union-find to group pin endpoints into nets; assign names from
   labels and power-symbol values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sexpdata

from circuit_graph.erc.models import ElectricalPinType, PinContext

# Map KiCad's pin type strings (as they appear inside (pin ...) blocks)
# to our ElectricalPinType enum.
KICAD_PIN_TYPE_MAP: dict[str, ElectricalPinType] = {
    "input": ElectricalPinType.INPUT,
    "output": ElectricalPinType.OUTPUT,
    "bidirectional": ElectricalPinType.BIDIRECTIONAL,
    "tri_state": ElectricalPinType.TRISTATE,
    "passive": ElectricalPinType.PASSIVE,
    "free": ElectricalPinType.FREE,
    "unspecified": ElectricalPinType.UNSPECIFIED,
    "power_in": ElectricalPinType.POWER_IN,
    "power_out": ElectricalPinType.POWER_OUT,
    "open_collector": ElectricalPinType.OPEN_COLLECTOR,
    "open_emitter": ElectricalPinType.OPEN_EMITTER,
    "no_connect": ElectricalPinType.NO_CONNECT,
}

# Map KiCad (pin ...) type strings found in the wild.
# Some versions use slightly different names; this dict covers both.
KICAD_PIN_TYPE_FALLBACK = {
    "pt_input": ElectricalPinType.INPUT,
    "pt_output": ElectricalPinType.OUTPUT,
    "pt_bidi": ElectricalPinType.BIDIRECTIONAL,
    "pt_tristate": ElectricalPinType.TRISTATE,
    "pt_passive": ElectricalPinType.PASSIVE,
    "pt_nic": ElectricalPinType.FREE,
    "pt_unspecified": ElectricalPinType.UNSPECIFIED,
    "pt_power_in": ElectricalPinType.POWER_IN,
    "pt_power_out": ElectricalPinType.POWER_OUT,
    "pt_opencollector": ElectricalPinType.OPEN_COLLECTOR,
    "pt_openemitter": ElectricalPinType.OPEN_EMITTER,
    "pt_nc": ElectricalPinType.NO_CONNECT,
}


def _coerce_str(value: Any) -> str:
    """sexpdata.Symbol or string -> plain str."""
    if isinstance(value, sexpdata.Symbol):
        return str(value)
    return str(value)


def _coerce_float(value: Any) -> float:
    return float(str(value).strip('"'))


def _sym(s: str) -> sexpdata.Symbol:
    return sexpdata.Symbol(s)


@dataclass
class LibSymbolPin:
    """Pin definition inside a lib_symbols entry."""

    number: str
    name: str
    electrical_type: ElectricalPinType
    rel_x: float
    rel_y: float
    rotation: int = 0


@dataclass
class LibSymbol:
    """A lib_symbols entry: defines a symbol's pin layout."""

    lib_id: str
    pins: list[LibSymbolPin] = field(default_factory=list)
    is_power: bool = False  # contains "(power ...)" flag


@dataclass
class ComponentContext:
    """A (symbol ...) instance in the schematic."""

    reference: str
    value: str
    lib_id: str
    position: tuple[float, float]
    rotation: int
    pins: list[PinContext] = field(default_factory=list)
    is_power_symbol: bool = False  # lib_id starts with "power:" or unit 0 has no real ref


@dataclass
class ExtractedSchematic:
    """Output of NetlistExtractor."""

    source_file: str
    components: list[ComponentContext]
    nets: dict[str, list[PinContext]]
    warnings: list[str] = field(default_factory=list)

    # The internal union-find connectivity (kept for the connectivity validator).
    # Keys are stable identifiers of the form "REF.NUMBER" so that downstream
    # consumers can look up a pin's net without holding onto a Python object id.
    pin_net_id: dict[str, str] = field(default_factory=dict)

    # Nets that have a label / hierarchical label / global label attached.
    # A labelled net is an intentional I/O port — a single-pin net here is not
    # an error (the label provides the off-sheet connection).
    labeled_nets: set[str] = field(default_factory=set)


class NetlistExtractor:
    """Parse a .kicad_sch file into an :class:`ExtractedSchematic`.

    Usage::

        extractor = NetlistExtractor()
        sch = extractor.extract(Path("circuit.kicad_sch"))
        for net_name, pins in sch.nets.items():
            ...
    """

    def __init__(self) -> None:
        self._warnings: list[str] = []

    def extract(self, sch_path: Path) -> ExtractedSchematic:
        text = sch_path.read_text(encoding="utf-8")
        sexp = sexpdata.loads(text)
        self._warnings = []

        lib_symbols = self._parse_lib_symbols(sexp)
        components = self._parse_symbol_instances(sexp, lib_symbols)

        nets, pin_net_id, labeled_nets = self._build_nets(sexp, components)

        return ExtractedSchematic(
            source_file=str(sch_path),
            components=components,
            nets=nets,
            warnings=list(self._warnings),
            pin_net_id=pin_net_id,
            labeled_nets=labeled_nets,
        )

    # ------------------------------------------------------------------ lib

    def _parse_lib_symbols(self, sexp: list) -> dict[str, LibSymbol]:
        """Return ``lib_id -> LibSymbol`` for all entries in (lib_symbols ...)."""
        result: dict[str, LibSymbol] = {}
        lib_block = self._find_top(sexp, "lib_symbols")
        if lib_block is None:
            self._warnings.append("no (lib_symbols ...) block in schematic")
            return result

        for item in lib_block[1:]:
            if not isinstance(item, list) or len(item) == 0:
                continue
            head = _coerce_str(item[0])
            if head != "symbol":
                continue
            # (symbol "lib_id" ...)
            if len(item) < 2:
                continue
            lib_id = _coerce_str(item[1]).strip('"')
            sym = LibSymbol(lib_id=lib_id)
            # Scan sublists for (power ...) flag and nested (symbol ...)
            # pin-definition blocks.
            for sub in item[2:]:
                if not isinstance(sub, list) or len(sub) == 0:
                    continue
                sub_head = _coerce_str(sub[0])
                if sub_head == "power":
                    sym.is_power = True
                elif sub_head == "symbol":
                    # (symbol "name_0_1" ...subgraph with (pin ...) entries...)
                    self._parse_lib_pins(sub, sym)
            result[lib_id] = sym
        return result

    def _parse_lib_pins(self, sub: list, sym: LibSymbol) -> None:
        """Walk a lib symbol body, collecting (pin ...) entries.

        Pin block format (KiCad 9):
            (pin <type> <shape> (at x y rot) (length L)
                (name "..." ...) (number "N" ...))

        The brief's draft expected a slightly different shape; we accept both
        the "type then shape" format above and the alternate format where the
        name/number precede (at ...).
        """
        for item in sub[1:]:
            if not isinstance(item, list) or len(item) == 0:
                continue
            if _coerce_str(item[0]) != "pin":
                continue
            # Walk the sublist looking for (at ...) (type ...) (name ...) (number ...)
            x, y, rot = 0.0, 0.0, 0
            electrical = ElectricalPinType.PASSIVE
            pin_name = ""
            pin_num = ""
            for prop in item[1:]:
                if not isinstance(prop, list) or len(prop) == 0:
                    # Bare symbol children can appear (e.g. pin type word);
                    # skip them.
                    continue
                head = _coerce_str(prop[0])
                if head == "at" and len(prop) >= 3:
                    x = _coerce_float(prop[1])
                    y = _coerce_float(prop[2])
                    if len(prop) >= 4:
                        rot = int(_coerce_float(prop[3]))
                elif head == "type" and len(prop) >= 2:
                    raw = _coerce_str(prop[1])
                    electrical = KICAD_PIN_TYPE_MAP.get(
                        raw,
                        KICAD_PIN_TYPE_FALLBACK.get(raw, ElectricalPinType.PASSIVE),
                    )
                elif head == "name" and len(prop) >= 2:
                    pin_name = _coerce_str(prop[1]).strip('"')
                elif head == "number" and len(prop) >= 2:
                    pin_num = _coerce_str(prop[1]).strip('"')
            # If neither name nor number was found in nested blocks, fall back
            # to the positional form used by older KiCad: (pin "name" "num" ...).
            if not pin_num and len(item) >= 3 and not isinstance(item[1], list):
                pin_name = _coerce_str(item[1]).strip('"')
                pin_num = _coerce_str(item[2]).strip('"')
            if not pin_num:
                continue
            sym.pins.append(
                LibSymbolPin(
                    number=pin_num,
                    name=pin_name,
                    electrical_type=electrical,
                    rel_x=x,
                    rel_y=y,
                    rotation=rot,
                )
            )

    # -------------------------------------------------------------- instances

    def _parse_symbol_instances(
        self,
        sexp: list,
        lib_symbols: dict[str, LibSymbol],
    ) -> list[ComponentContext]:
        """Parse top-level (symbol ...) blocks into ComponentContexts.

        KiCad 9 instance format:
            (symbol
                (lib_id "...")
                (at x y rot)
                ...
                (property "Reference" "R1" ...)
                (property "Value" "10k" ...)
                (pin "1" (uuid ...))
                (pin "2" (uuid ...))
                ...
            )
        """
        result: list[ComponentContext] = []
        for item in sexp[1:]:
            if not isinstance(item, list) or len(item) == 0:
                continue
            if _coerce_str(item[0]) != "symbol":
                continue
            comp = self._parse_one_symbol(item, lib_symbols)
            if comp is not None:
                result.append(comp)
        return result

    def _parse_one_symbol(
        self,
        item: list,
        lib_symbols: dict[str, LibSymbol],
    ) -> ComponentContext | None:
        """Parse a single top-level (symbol ...) instance."""
        # Extract lib_id from the first nested (lib_id "...") block.
        lib_id_block = self._find_sub(item, "lib_id")
        if lib_id_block is None or len(lib_id_block) < 2:
            return None
        lib_id = _coerce_str(lib_id_block[1]).strip('"')
        if not lib_id:
            return None

        reference = ""
        value = ""
        pos_x, pos_y, rot = 0.0, 0.0, 0
        # Power symbol detection: lib_id starts with "power:" OR a (power ...)
        # flag appears inside the instance.
        is_power = lib_id.startswith("power:")
        if not is_power:
            for sub in item[1:]:
                if isinstance(sub, list) and len(sub) > 0 and _coerce_str(sub[0]) == "power":
                    is_power = True
                    break

        # Walk properties.
        for sub in item[1:]:
            if not isinstance(sub, list) or len(sub) == 0:
                continue
            head = _coerce_str(sub[0])
            if head == "property" and len(sub) >= 3:
                pname = _coerce_str(sub[1])
                pval = _coerce_str(sub[2]).strip('"')
                if pname == "Reference":
                    reference = pval
                elif pname == "Value":
                    value = pval
            elif head == "at" and len(sub) >= 3:
                pos_x = _coerce_float(sub[1])
                pos_y = _coerce_float(sub[2])
                if len(sub) >= 4:
                    rot = int(_coerce_float(sub[3]))

        if not reference:
            # Power symbols may have no explicit Reference property in some
            # edge cases; synthesize one based on the value.
            if is_power and value:
                reference = f"#PWR_{value}"
            else:
                self._warnings.append(
                    f"symbol lib_id={lib_id!r} missing Reference; skipped"
                )
                return None

        comp = ComponentContext(
            reference=reference,
            value=value,
            lib_id=lib_id,
            position=(pos_x, pos_y),
            rotation=rot,
            is_power_symbol=is_power,
        )

        # Build PinContexts by combining the lib_symbols pin layout with the
        # instance position.
        lib = lib_symbols.get(lib_id)
        if lib is not None:
            for lib_pin in lib.pins:
                abs_x, abs_y = self._rotate_translate(
                    lib_pin.rel_x, lib_pin.rel_y, pos_x, pos_y, rot
                )
                comp.pins.append(
                    PinContext(
                        pin_number=lib_pin.number,
                        pin_name=lib_pin.name,
                        electrical_type=lib_pin.electrical_type,
                        component_ref=reference,
                        component_lib_id=lib_id,
                        position=(abs_x, abs_y),
                    )
                )
        else:
            self._warnings.append(
                f"symbol {reference!r} lib_id={lib_id!r} not in lib_symbols; "
                "pin positions will be approximate"
            )
        return comp

    @staticmethod
    def _rotate_translate(
        rel_x: float, rel_y: float, base_x: float, base_y: float, rotation: int
    ) -> tuple[float, float]:
        """Apply KiCad transform: Y-invert, rotate, translate.

        KiCad symbol-local coordinates use +Y up, but schematic space uses
        +Y down.  We mirror Y first (y → -y), then apply the instance
        rotation (CW convention), then translate to the absolute position.
        This matches ``circuit_graph.layout.geometry._transform``.
        """
        import math

        # Step 1: Y-inversion (symbol +Y up → schematic +Y down)
        x, y = rel_x, -rel_y

        # Step 2: rotate (KiCad uses CW angles; with Y already mirrored,
        # standard CCW rotation gives the correct orientation)
        if rotation != 0:
            rad = math.radians(rotation)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            x, y = (
                x * cos_r - y * sin_r,
                x * sin_r + y * cos_r,
            )

        # Step 3: translate
        return base_x + x, base_y + y

    # ------------------------------------------------------------------ nets

    def _build_nets(
        self,
        sexp: list,
        components: list[ComponentContext],
    ) -> tuple[dict[str, list[PinContext]], dict[str, str]]:
        """Build NetMap from wires, junctions, labels, and power symbols.

        Algorithm:
        1. Collect pin endpoints (one point per pin) plus power-symbol anchor
           points (whose names carry the power value).
        2. Collect wire endpoints, junction anchor points, and label positions.
           Reuse an existing point if a new one is within ``tol`` of it.
        3. Union wire endpoints pairwise into a connectivity graph.
        4. For each unique root, determine the net name (label > power value >
           auto).
        5. Group pin endpoints into nets and emit a stable ``pin_net_id`` map.
        """
        tol = 1e-3
        # Each "point" is a dict {x, y}; we look them up by integer id.
        points: list[dict[str, float]] = []
        # point_id -> (component, pin)
        point_pin: dict[int, tuple[ComponentContext, PinContext]] = {}
        # root -> net name from a label or power value
        root_label: dict[int, str] = {}
        # Track power-symbol anchor roots so we can apply the value as net name.
        point_power: dict[int, str] = {}

        # 1. Add pin endpoints.
        for comp in components:
            for pin in comp.pins:
                pid = len(points)
                points.append({"x": pin.position[0], "y": pin.position[1]})
                point_pin[pid] = (comp, pin)

        # 2. Walk the schematic for wires, junctions, labels.
        edges: list[tuple[int, int]] = []  # wire endpoints to union

        for item in sexp[1:]:
            if not isinstance(item, list) or len(item) == 0:
                continue
            head = _coerce_str(item[0])
            if head == "wire":
                pts = self._parse_wire_pts(item)
                if pts is not None:
                    (x1, y1), (x2, y2) = pts
                    p1 = self._add_or_match_point(points, x1, y1, tol)
                    p2 = self._add_or_match_point(points, x2, y2, tol)
                    edges.append((p1, p2))
            elif head == "junction":
                at = self._find_sub(item, "at")
                if at is not None and len(at) >= 3:
                    x = _coerce_float(at[1])
                    y = _coerce_float(at[2])
                    # Junctions don't add edges by themselves; they're just
                    # anchor points that will be matched by other endpoints
                    # via the tolerance in _add_or_match_point.
                    self._add_or_match_point(points, x, y, tol)
            elif head in ("label", "hierarchical_label", "global_label"):
                at = self._find_sub(item, "at")
                if at is None or len(at) < 3:
                    continue
                x = _coerce_float(at[1])
                y = _coerce_float(at[2])
                name = self._extract_label_name(item)
                if name:
                    pid = self._add_or_match_point(points, x, y, tol)
                    root_label[pid] = name

        # 3. Union-find over points.
        parent = list(range(len(points)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for a, b in edges:
            union(a, b)

        # 4. Mark power-symbol anchors. For each power symbol, the pin point
        # carries the value as a candidate net name. The pin is already a
        # point; we just attach the value to its root. PWR_FLAG is a KiCad
        # ERC marker (it tells ERC where power comes from) — its value
        # ("PWR_FLAG") would clobber the real rail name when both happen to
        # share a root, so we skip those.
        for comp in components:
            if (
                comp.is_power_symbol
                and comp.value
                and comp.pins
                and not comp.lib_id.endswith(":PWR_FLAG")
            ):
                # Power symbol pin 1 is the only pin; its absolute position is
                # the anchor we just registered.
                pin = comp.pins[0]
                # Find the point by coordinate.
                target = (pin.position[0], pin.position[1])
                pid = self._find_point(points, target[0], target[1], tol)
                if pid is not None:
                    point_power[find(pid)] = comp.value

        # 5. Group pins into nets.
        # ------------------------------------------------------------------
        # Propagate label names from point ids to their union-find roots so
        # that root_label.get(r) works below.  root_label was keyed by raw
        # point id during label processing, but after union-find the root may
        # differ.
        for pid, name in list(root_label.items()):
            r = find(pid)
            if r not in root_label:
                root_label[r] = name

        net_pins: dict[str, list[PinContext]] = {}
        root_names: dict[int, str] = {}
        labeled_roots: set[int] = set()
        for pid, (comp, pin) in point_pin.items():
            r = find(pid)
            if r not in root_names:
                name: str | None = root_label.get(r)
                if name is not None:
                    labeled_roots.add(r)
                else:
                    name = point_power.get(r)
                if name is None:
                    name = f"net_{r}"
                root_names[r] = name
            net_pins.setdefault(root_names[r], []).append(pin)

        labeled_nets: set[str] = {root_names[r] for r in labeled_roots}

        # 6. Stable pin_net_id map: keys are "REF.NUMBER" so that downstream
        # tasks (connectivity validator, ERC engine) can look up a pin's net
        # without holding onto a Python object id.
        pin_net_id: dict[str, str] = {}
        for pid, (comp, pin) in point_pin.items():
            r = find(pid)
            pin_net_id[f"{comp.reference}.{pin.pin_number}"] = root_names[r]

        if not net_pins:
            self._warnings.append("no nets discovered in schematic")

        return net_pins, pin_net_id, labeled_nets

    def _add_or_match_point(
        self, points: list[dict[str, float]], x: float, y: float, tol: float
    ) -> int:
        """Find an existing point within tolerance, or add a new one."""
        idx = self._find_point(points, x, y, tol)
        if idx is not None:
            return idx
        points.append({"x": x, "y": y})
        return len(points) - 1

    @staticmethod
    def _find_point(
        points: list[dict[str, float]], x: float, y: float, tol: float
    ) -> int | None:
        for i, p in enumerate(points):
            if abs(p["x"] - x) <= tol and abs(p["y"] - y) <= tol:
                return i
        return None

    def _parse_wire_pts(
        self, item: list
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """Parse a (wire (pts (xy x1 y1) (xy x2 y2)) ...) block."""
        pts_block = self._find_sub(item, "pts")
        if pts_block is None:
            return None
        xy_blocks = [
            s for s in pts_block[1:]
            if isinstance(s, list) and len(s) > 0 and _coerce_str(s[0]) == "xy"
        ]
        if len(xy_blocks) < 2:
            return None
        try:
            x1 = _coerce_float(xy_blocks[0][1])
            y1 = _coerce_float(xy_blocks[0][2])
            x2 = _coerce_float(xy_blocks[1][1])
            y2 = _coerce_float(xy_blocks[1][2])
            return (x1, y1), (x2, y2)
        except (IndexError, ValueError, TypeError):
            return None

    def _find_sub(self, item: list, name: str) -> list | None:
        """Return the first sublist whose head is ``name``, or None."""
        for sub in item[1:]:
            if isinstance(sub, list) and len(sub) > 0 and _coerce_str(sub[0]) == name:
                return sub
        return None

    def _extract_label_name(self, item: list) -> str | None:
        """Pull the label name out of a (label ...) / (hierarchical_label ...) block."""
        # KiCad 9: the name is the first string after (label ...).
        # Older format: same.
        if len(item) >= 2:
            return _coerce_str(item[1]).strip('"')
        return None

    def _find_top(self, sexp: list, name: str) -> list | None:
        """Return the first top-level child whose head is ``name``."""
        for item in sexp[1:]:
            if isinstance(item, list) and len(item) > 0 and _coerce_str(item[0]) == name:
                return item
        return None
