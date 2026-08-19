"""KiCad .kicad_sch parser.

Reads a KiCad schematic file and produces a :class:`LayoutSchematic` side-car
model suitable for the ``cg layout-check`` rule engine. Unlike
:mod:`circuit_graph.parsers.protel` (logical netlist only, no geometry), this
parser preserves component positions and wire geometry.

Implementation notes
--------------------
- Uses the third-party ``kicad-sch-api`` package (already declared in
  ``pyproject.toml [project.optional-dependencies].dev``).
- The library emits a benign ``No KiCAD symbol libraries found...`` warning to
  stderr when symbol libraries aren't installed in the environment. Symbol
  libraries are not required for layout-check (we only need geometry / pin
  presence), so we suppress the warning via ``warnings.filterwarnings`` plus a
  ``contextlib.redirect_stderr`` block around ``Schematic.load``. The captured
  noise is discarded; ``redirect_stderr`` automatically restores the original
  stderr on context exit.

API surface observed on ``kicad-sch-api==0.5.5`` (probe-based):
- ``sch = ksa.Schematic.load(str(path))``
- ``sch.components.all()`` — iterator of component objects. Each component has
  ``.reference`` (str), ``.pins`` (list, often empty when symbol library
  missing), and a private ``._data`` namespace carrying ``lib_id`` (str like
  ``"Device:R"``), ``position`` (a ``Point`` with ``.x`` and ``.y`` in mm,
  KiCad y-up), and ``value`` (str|None). Rotation is NOT exposed through
  ``._data.position`` in 0.5.5, so ``rotation_deg`` stays ``None`` for now.
- ``sch.wires.all()`` — iterator of wire objects. Each has ``.points``
  (list[Point]) with at least 2 entries (we use ``[0]`` and ``[-1]``) and
  ``.uuid``.
- ``sch.labels.all()`` — iterator of label objects. Each has ``.text`` (str,
  the net name) and ``.position`` (a ``Point``). NOTE: the prompt described
  this as ``l.pos`` (tuple), but the actual API attribute is ``l.position``
  returning a ``Point``. We use ``l.position``.
- ``sch.junctions.all()`` and ``sch.texts.all()`` are not consumed for the
  MVP; pin-to-net membership is intentionally left empty (assertions rely on
  label-derived net names, see spec §1.1).
"""

from __future__ import annotations

import contextlib
import io
import re
import warnings
from pathlib import Path

import kicad_sch_api as ksa

from circuit_graph.layout_check.assertions.base import refdes_prefix
from circuit_graph.layout_check.model import (
    LayoutComponent,
    LayoutNet,
    LayoutPin,
    LayoutSchematic,
    LayoutWire,
)

# --- Net-kind heuristic ----------------------------------------------------
#
# Spec §1.1 lists two example heuristics; we expand them slightly to cover
# common KiCad power/ground conventions. The regexes are intentionally
# permissive: a name that matches both power and ground (unusual) defaults to
# power (power check runs first). Anything else is a plain signal net.

_POWER_NAME_RE = re.compile(
    r"(?i)\b("
    r"vcc|vdd|vbat|vin|vout|vref"
    r"|v\d+"
    r"|3v3|5v|12v|24v"
    r")"
)
_POWER_PLUS_DIGIT_RE = re.compile(r"\+\s*\d")
_GROUND_NAME_RE = re.compile(r"(?i)\b(gnd|vss|agnd|dgnd|earth)\b")


def _classify_net_kind(name: str) -> str:
    """Return one of ``"power"``, ``"ground"``, ``"signal"`` for a net name."""
    if _POWER_NAME_RE.search(name):
        return "power"
    if _POWER_PLUS_DIGIT_RE.search(name):
        return "power"
    if _GROUND_NAME_RE.search(name):
        return "ground"
    return "signal"


# --- Component-type inference ------------------------------------------------
#
# Strategy: prefer the KiCad library id (``lib:part``) when informative, else
# fall back to the designator's leading alpha. Keeps the dispatch table small
# and predictable; Power symbols always map to ``"power"``.

_REFDES_PREFIX_TYPE = {
    "R": "resistor",
    "C": "capacitor",
    "L": "inductor",
    "U": "ic",
    "Q": "mosfet",
    "D": "diode",
    "J": "connector",
}


def _infer_type_from_lib_id(lib_id: str) -> str:
    """Best-effort component type from a KiCad library id like ``"Device:R"``.

    Returns one of ``"resistor"``, ``"capacitor"``, ``"inductor"``, ``"ic"``,
    ``"mosfet"``, ``"diode"``, ``"connector"``, ``"power"``, or ``"unknown"``.
    The lookup is library-prefix sensitive (so an IC in any vendor library is
    still recognized as ``"ic"``).
    """
    if not lib_id:
        return "unknown"
    library, _, part = lib_id.partition(":")
    library_lower = library.lower()
    part_lower = part.lower()

    # Power symbols (ground, supply flags, anything in the power library) are
    # always categorized as ``"power"``.
    if library_lower == "power":
        return "power"

    # Passive device library carries the canonical passive kind in the part
    # name (R, C, L, LED, etc.). We accept any trailing kind token, e.g.
    # ``Device:R_Small`` still resolves to ``"resistor"``.
    if library_lower == "device":
        head = part_lower.split("_", 1)[0]
        if head == "r":
            return "resistor"
        if head == "c":
            return "capacitor"
        if head == "l":
            return "inductor"
        if head == "d" or head == "led":
            return "diode"
        return "unknown"

    # Amplifier / operational / regulator / MCU families are all ICs from a
    # layout-check perspective (no geometry distinction).
    if (
        library_lower.startswith("amplifier")
        or library_lower.startswith("regulator")
        or library_lower.startswith("microcontroller")
    ):
        return "ic"

    # Transistor libraries — split by part-name token to get mosfet vs bjt.
    if library_lower.startswith("transistor"):
        head = part_lower.split("_", 1)[0]
        if head == "q":
            return "mosfet"
        if head == "d":
            return "diode"
        # BC547-style BJT — fall through to refdes heuristic.

    return "unknown"


def _resolve_type(comp_id: str, lib_id: str) -> str:
    """Resolve component type using lib_id first, then refdes_prefix fallback."""
    by_lib = _infer_type_from_lib_id(lib_id)
    if by_lib != "unknown":
        return by_lib
    prefix = refdes_prefix(comp_id)
    return _REFDES_PREFIX_TYPE.get(prefix, "unknown")


# --- Pin extraction ---------------------------------------------------------


def _extract_pins(component: object) -> list[LayoutPin]:
    """Build :class:`LayoutPin` records from a kicad-sch-api component.

    The library returns an empty ``pins`` list when symbol libraries aren't
    installed. Per spec §2 (and the hard constraint in the task brief) we
    MUST NOT fabricate pin coordinates; we return an empty list instead.
    """
    raw_pins = getattr(component, "pins", None) or []
    pins: list[LayoutPin] = []
    for pin in raw_pins:
        number = getattr(pin, "number", None)
        if number is None:
            continue
        pos = getattr(pin, "position", None)
        x_mm: float | None = None
        y_mm: float | None = None
        if pos is not None:
            x_mm = getattr(pos, "x", None)
            y_mm = getattr(pos, "y", None)
        pins.append(
            LayoutPin(
                number=str(number),
                net="",  # MVP: pin-to-net wiring is not traced (spec §1.1)
                x_mm=x_mm,
                y_mm=y_mm,
            )
        )
    return pins


def _extract_position(component: object) -> tuple[float, float] | None:
    """Read ``(x, y)`` mm from a component's ``_data.position`` Point.

    Returns ``None`` if the field is missing or has no ``.x``/``.y`` attrs.
    """
    data = getattr(component, "_data", None)
    if data is None:
        return None
    pos = getattr(data, "position", None)
    if pos is None:
        return None
    x = getattr(pos, "x", None)
    y = getattr(pos, "y", None)
    if x is None or y is None:
        return None
    return (float(x), float(y))


# --- Parsing ----------------------------------------------------------------


def parse_kicad_sch(path: Path) -> LayoutSchematic:
    """Parse a KiCad ``.kicad_sch`` file into a :class:`LayoutSchematic`.

    See ``docs/superpowers/specs/2026-08-18-cg-layout-check-rule-engine-design.md``
    §2 (Architecture) and §5 (12 built-in kinds) for how this feeds the
    assertion layer.

    Behavior summary (MVP scope):

    - Components are taken 1:1 from ``sch.components.all()``. Each is mapped
      to a :class:`LayoutComponent` with ``position_mm`` populated when
      available, ``rotation_deg`` left ``None`` (kicad-sch-api 0.5.5 doesn't
      expose rotation through ``_data.position``), and ``pins`` empty when
      the symbol library is missing.
    - Wires are taken 1:1 from ``sch.wires.all()``. ``net`` is left ``None``
      because wire-to-net inference is out of scope for the MVP.
    - Nets are derived from ``sch.labels.all()`` text via a name-based
      heuristic (power / ground / signal). Multiple labels with the same text
      collapse into a single net record.

    Edge cases handled: empty schematic, components without position, wires
    with fewer than two points, and the benign kicad-sch-api symbol-library
    warning that gets captured into a local buffer.
    """
    warnings.filterwarnings("ignore")

    # Suppress the kicad-sch-api "No KiCAD symbol libraries found" stderr
    # noise — it's harmless for geometry extraction but pollutes capsys tests.
    # ``redirect_stderr`` automatically restores sys.stderr on context exit;
    # the captured buffer is discarded.
    err_buf = io.StringIO()
    with contextlib.redirect_stderr(err_buf):
        sch = ksa.Schematic.load(str(path))

    # --- Components ----------------------------------------------------------
    components: list[LayoutComponent] = []
    for comp in sch.components.all():
        ref_id: str = comp.reference
        lib_id: str = getattr(getattr(comp, "_data", None), "lib_id", "") or ""
        value: str | None = getattr(getattr(comp, "_data", None), "value", None)
        components.append(
            LayoutComponent(
                id=ref_id,
                type=_resolve_type(ref_id, lib_id),
                value=value if value else None,
                position_mm=_extract_position(comp),
                rotation_deg=None,  # kicad-sch-api 0.5.5: not exposed via _data
                refdes_prefix=refdes_prefix(ref_id),
                sheet=None,  # MVP: multi-sheet out of scope (spec §10)
                page=None,
                pins=_extract_pins(comp),
            )
        )

    # --- Wires ---------------------------------------------------------------
    wires: list[LayoutWire] = []
    for wire in sch.wires.all():
        pts = getattr(wire, "points", None) or []
        if len(pts) < 2:
            # Malformed wire; spec edge case says skip without crashing.
            continue
        start = pts[0]
        end = pts[-1]
        sx = getattr(start, "x", None)
        sy = getattr(start, "y", None)
        ex = getattr(end, "x", None)
        ey = getattr(end, "y", None)
        if None in (sx, sy, ex, ey):
            continue
        wires.append(
            LayoutWire(
                start_mm=(float(sx), float(sy)),
                end_mm=(float(ex), float(ey)),
                net=None,  # MVP: not traced (see spec §1.1)
            )
        )

    # --- Nets ----------------------------------------------------------------
    # Deduplicate by name; one LayoutNet per unique label text. Multiple
    # labels with the same text (e.g. two "GND" flags in different places)
    # collapse into a single net record.
    nets_by_name: dict[str, LayoutNet] = {}
    for label in sch.labels.all():
        text = getattr(label, "text", None)
        if not text:
            continue
        if text in nets_by_name:
            # Already have a record for this name; skip duplicate labels.
            continue
        nets_by_name[text] = LayoutNet(
            name=text,
            kind=_classify_net_kind(text),  # type: ignore[arg-type]
            members=[],  # MVP: label-based only; see spec §1.1
        )
    nets: list[LayoutNet] = list(nets_by_name.values())

    return LayoutSchematic(components=components, nets=nets, wires=wires)
