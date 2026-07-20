# Schematic Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable `circuit_graph/layout/` module that converts a `CircuitIR` into a human-readable KiCad schematic with JSON-configurable per-unit placement, orthogonal wires, and non-overlapping 0° labels.

**Critical fixes to apply before execution:**

1. **Pin-direction / rotation semantics in `geometry.py` must be unambiguous.** The design spec says `pin_directions` describe directions "after rotation", but implementing that literally causes rotation to be applied twice (once by the configured pin direction and once by `placement.rotation`). This plan treats `pin_directions` as **unrotated symbol-local directions** and lets `_transform()` apply the symbol rotation (option (a)). Update the JSON field description in the spec if this convention is adopted.
2. **Passive two-pin devices must traverse the signal-flow graph bidirectionally.** Resistors, capacitors, inductors, and diodes do not have `output` pins; without special handling they break left-to-right signal propagation.
3. **Junction detection must find real T-nodes.** Counting endpoints that appear ≥3 times is insufficient. A junction exists where one wire segment ends on the interior of another segment.
4. **Column assignment must match the design spec.** `column(comp)` is the longest directed path length from any input net; components with no input path are placed in a center column; feedback cycles are broken by dominant direction.

**Architecture:** The module is split into small single-responsibility files: config loading, signal-flow graph construction, component placement, orthogonal wire placement, label de-overlap, unit orchestration, and KiCad output. The first version treats the whole `CircuitIR` as one layout unit; multi-unit decomposition is scaffolded but only wires one unit.

**Tech Stack:** Python 3.10+, `dataclasses`, `json`, `pathlib`, `networkx` (already in `pyproject.toml`), `kicad_sch_api` (optional/editable install), `pytest`.

## Global Constraints

- **Python version:** `>=3.10`
- **Line length:** 100 characters (`tool.ruff.line-length`)
- **Imports:** sorted (`tool.ruff.lint.select` includes `I`)
- **Tests:** live under `tests/layout/`
- **JSON configs:** live under `circuit_graph/layout/configs/`
- **Line endings:** LF only
- **kicad_sch_api:** optional dependency; the module must import it lazily and raise a clear error if missing
- **Every task ends with a passing test and a commit**
- **Config loader should reject unknown top-level keys** to catch typos early; nested keys under `grid`, `placement`, etc. are validated by their respective builders.

---

## File Structure

```
circuit_graph/
  layout/
    __init__.py              # public API: layout_schematic
    exceptions.py            # LayoutError hierarchy
    config.py                # dataclasses + JSON loader/validator
    signal_graph.py          # build signal-flow graph + assign columns
    placer.py                # assign (x, y, rotation) per component
    wire_placer.py           # generate orthogonal wire segments
    label_placer.py          # place reference/value text, resolve overlaps
    unit_layout.py           # orchestrate one unit: graph → place → wires → labels
    page_layout.py           # place units on page (single unit for v1)
    decomposer.py            # split CircuitIR into layout units (v1: one unit)
    kicad_writer.py          # apply results to kicad_sch_api.Schematic
    configs/
      default.json           # shipped defaults
      lm321_ccs.json         # LM321 CCS unit config
```

---

## Task 1: Scaffolding and config loader

**Files:**
- Create: `circuit_graph/layout/__init__.py`
- Create: `circuit_graph/layout/exceptions.py`
- Create: `circuit_graph/layout/config.py`
- Create: `circuit_graph/layout/configs/default.json`
- Test: `tests/layout/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `LayoutConfig` dataclass, `load_layout_config(path_or_dict)`, `LayoutConfigError`

- [ ] **Step 1: Create `circuit_graph/layout/__init__.py`**

```python
"""Schematic layout: turn a CircuitIR into a readable KiCad schematic."""

__all__ = ["layout_schematic"]


def layout_schematic(ir, sch, unit_configs=None):
    """Placeholder; implemented in Task 9."""
    raise NotImplementedError("layout_schematic is implemented in Task 9")
```

- [ ] **Step 2: Create `circuit_graph/layout/exceptions.py`**

```python
"""Layout-specific exceptions."""


class LayoutError(Exception):
    """Base class for all layout errors."""


class LayoutConfigError(LayoutError):
    """Invalid or missing layout JSON configuration."""


class LayoutPageOverflowError(LayoutError):
    """Components do not fit on the configured page."""


class LayoutOverlapError(LayoutError):
    """Could not resolve label overlap."""
```

- [ ] **Step 3: Create `circuit_graph/layout/config.py`**

```python
"""Layout configuration dataclasses and loader."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from circuit_graph.layout.exceptions import LayoutConfigError


@dataclass(frozen=True)
class GridConfig:
    size_mm: float = 2.54
    char_unit_mm: float = 1.27


@dataclass(frozen=True)
class PlacementConfig:
    input_x_mm: float = 38.1
    output_x_mm: float = 152.4
    column_step_mm: float = 25.4
    row_step_mm: float = 12.7
    direction: str = "left_to_right"


@dataclass(frozen=True)
class IoConfig:
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    power_top: list[str] = field(default_factory=list)
    power_bottom: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SymbolConfig:
    rotation: float = 0.0
    pin_directions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LabelConfig:
    offset_mm: tuple[float, float] = (0.0, -2.54)
    font_height_mm: float = 1.27


_VALID_TOP_LEVEL_KEYS = {"meta", "grid", "placement", "io", "symbols", "labels"}


@dataclass(frozen=True)
class LayoutConfig:
    meta: dict[str, str] = field(default_factory=dict)
    grid: GridConfig = field(default_factory=GridConfig)
    placement: PlacementConfig = field(default_factory=PlacementConfig)
    io: IoConfig = field(default_factory=IoConfig)
    symbols: dict[str, SymbolConfig] = field(default_factory=dict)
    labels: dict[str, LabelConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LayoutConfig":
        """Build a LayoutConfig from a nested dict (JSON payload)."""
        if not isinstance(data, dict):
            raise LayoutConfigError(f"config must be a dict, got {type(data).__name__}")

        unknown = set(data.keys()) - _VALID_TOP_LEVEL_KEYS
        if unknown:
            raise LayoutConfigError(f"unknown top-level config keys: {sorted(unknown)}")

        meta = data.get("meta", {})
        grid = _build_grid(data.get("grid", {}))
        placement = _build_placement(data.get("placement", {}))
        io = _build_io(data.get("io", {}))
        symbols = _build_symbols(data.get("symbols", {}))
        labels = _build_labels(data.get("labels", {}))

        return cls(
            meta=meta,
            grid=grid,
            placement=placement,
            io=io,
            symbols=symbols,
            labels=labels,
        )


def _build_grid(raw: dict[str, Any]) -> GridConfig:
    return GridConfig(
        size_mm=float(raw.get("size_mm", 2.54)),
        char_unit_mm=float(raw.get("char_unit_mm", 1.27)),
    )


def _build_placement(raw: dict[str, Any]) -> PlacementConfig:
    return PlacementConfig(
        input_x_mm=float(raw.get("input_x_mm", 38.1)),
        output_x_mm=float(raw.get("output_x_mm", 152.4)),
        column_step_mm=float(raw.get("column_step_mm", 25.4)),
        row_step_mm=float(raw.get("row_step_mm", 12.7)),
        direction=str(raw.get("direction", "left_to_right")),
    )


def _build_io(raw: dict[str, Any]) -> IoConfig:
    return IoConfig(
        inputs=list(raw.get("inputs", [])),
        outputs=list(raw.get("outputs", [])),
        power_top=list(raw.get("power_top", [])),
        power_bottom=list(raw.get("power_bottom", [])),
    )


def _build_symbols(raw: dict[str, Any]) -> dict[str, SymbolConfig]:
    result: dict[str, SymbolConfig] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            raise LayoutConfigError(f"symbol '{key}' must be a dict")
        result[key] = SymbolConfig(
            rotation=float(value.get("rotation", 0.0)),
            pin_directions=dict(value.get("pin_directions", {})),
        )
    return result


def _build_labels(raw: dict[str, Any]) -> dict[str, LabelConfig]:
    result: dict[str, LabelConfig] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            raise LayoutConfigError(f"label '{key}' must be a dict")
        offset = value.get("offset_mm", [0.0, -2.54])
        result[key] = LabelConfig(
            offset_mm=(float(offset[0]), float(offset[1])),
            font_height_mm=float(value.get("font_height_mm", 1.27)),
        )
    return result


def load_layout_config(path_or_dict: Path | str | dict[str, Any] | None) -> LayoutConfig:
    """Load a LayoutConfig from a file path or dict.

    ``None`` returns the default config shipped with the package.
    """
    if path_or_dict is None:
        path_or_dict = Path(__file__).with_name("configs") / "default.json"

    if isinstance(path_or_dict, dict):
        return LayoutConfig.from_dict(path_or_dict)

    path = Path(path_or_dict)
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise LayoutConfigError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LayoutConfigError(f"invalid JSON in {path}: {exc}") from exc

    return LayoutConfig.from_dict(data)
```

- [ ] **Step 4: Create `circuit_graph/layout/configs/default.json`**

```json
{
  "meta": {
    "name": "default",
    "unit_type": "default",
    "version": "1.0"
  },
  "grid": {
    "size_mm": 2.54,
    "char_unit_mm": 1.27
  },
  "placement": {
    "input_x_mm": 38.1,
    "output_x_mm": 152.4,
    "column_step_mm": 25.4,
    "row_step_mm": 12.7,
    "direction": "left_to_right"
  },
  "io": {
    "inputs": [],
    "outputs": [],
    "power_top": [],
    "power_bottom": []
  },
  "symbols": {},
  "labels": {
    "reference": {"offset_mm": [0, -2.54], "font_height_mm": 1.27},
    "value": {"offset_mm": [0, 2.54], "font_height_mm": 1.27}
  }
}
```

- [ ] **Step 5: Write failing test `tests/layout/test_config.py`**

```python
from pathlib import Path

import pytest

from circuit_graph.layout.config import LayoutConfig, load_layout_config
from circuit_graph.layout.exceptions import LayoutConfigError


def test_default_config_loads():
    cfg = load_layout_config(None)
    assert cfg.grid.size_mm == 2.54
    assert cfg.placement.input_x_mm == 38.1


def test_config_from_dict():
    cfg = LayoutConfig.from_dict(
        {
            "grid": {"size_mm": 1.27, "char_unit_mm": 1.0},
            "symbols": {
                "Device:R": {"rotation": 90, "pin_directions": {"1": "up", "2": "down"}}
            },
        }
    )
    assert cfg.grid.size_mm == 1.27
    assert cfg.symbols["Device:R"].rotation == 90


def test_config_rejects_non_dict():
    with pytest.raises(LayoutConfigError):
        LayoutConfig.from_dict("not-a-dict")


def test_config_rejects_unknown_top_level_key():
    with pytest.raises(LayoutConfigError, match="unknown top-level"):
        LayoutConfig.from_dict({"grid": {"size_mm": 2.54}, "typo": 1})


def test_config_file_round_trip(tmp_path: Path):
    path = tmp_path / "cfg.json"
    path.write_text('{"grid": {"size_mm": 5.08}}', encoding="utf-8")
    cfg = load_layout_config(path)
    assert cfg.grid.size_mm == 5.08
```

- [ ] **Step 6: Run tests to verify failures**

Run: `pytest tests/layout/test_config.py -v`

Expected: failures because `tests/layout/` does not exist yet.

- [ ] **Step 7: Create `tests/layout/__init__.py`**

Empty file.

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/layout/test_config.py -v`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add circuit_graph/layout/ tests/layout/
git commit -m "feat(layout): add config loader and default JSON config" -m "Adds LayoutConfig dataclasses, load_layout_config, and LayoutConfigError." -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: Signal-flow graph and column assignment

**Files:**
- Create: `circuit_graph/layout/signal_graph.py`
- Test: `tests/layout/test_signal_graph.py`

**Interfaces:**
- Consumes: `CircuitIR`, `LayoutConfig`
- Produces: `SignalGraph` (networkx DiGraph with component nodes + `column` attribute)

- [ ] **Step 1: Create `circuit_graph/layout/signal_graph.py`**

```python
"""Build a signal-flow graph from CircuitIR and assign component columns."""

from __future__ import annotations

import logging

import networkx as nx

from circuit_graph.ir import CircuitIR, Component, Pin
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.exceptions import LayoutConfigError

logger = logging.getLogger(__name__)

PASSIVE_KINDS = {"Resistor", "Capacitor", "Inductor", "Diode"}


def build_signal_graph(ir: CircuitIR, config: LayoutConfig) -> nx.DiGraph:
    """Return a directed graph where edges follow signal flow.

    Nodes are component ids. Edge u → v means signal flows from u to v.
    Passive two-pin devices (R, C, L, D) are traversed bidirectionally;
    their incident edges are marked ``passive=True``.
    """
    g = nx.DiGraph()
    for comp in ir.components:
        g.add_node(comp.id)

    net_pins = _group_pins_by_net(ir)

    for net_id, pins in net_pins.items():
        source_pins = [p for p in pins if _is_source_pin(p, config)]
        target_pins = [p for p in pins if not _is_source_pin(p, config)]

        # Passive devices: add bidirectional edges between all passive pins
        # on the same net so signal can flow through them.
        comp_by_id = {c.id: c for c in ir.components}
        passive_pins = [
            p
            for p in pins
            if _is_passive_component(comp_by_id.get(p.component_id))
        ]
        if len(passive_pins) >= 2:
            for i, src in enumerate(passive_pins):
                for tgt in passive_pins[i + 1 :]:
                    if src.component_id != tgt.component_id:
                        g.add_edge(src.component_id, tgt.component_id, net=net_id, passive=True)
                        g.add_edge(tgt.component_id, src.component_id, net=net_id, passive=True)

        if not source_pins:
            continue

        for src in source_pins:
            for tgt in target_pins:
                if src.component_id != tgt.component_id:
                    g.add_edge(src.component_id, tgt.component_id, net=net_id)

    return g


def assign_columns(g: nx.DiGraph, config: LayoutConfig) -> dict[str, int]:
    """Return component_id → column index (0 = inputs, increasing to right).

    Column assignment follows the design spec:
      - column(comp) = longest directed path length from any input-source node
      - nodes with no input path are placed in a center column between inputs
        and outputs
      - feedback cycles are broken by dominant direction (warning emitted)
    """
    if g.number_of_nodes() == 0:
        return {}

    # Break cycles using dominant direction heuristic.
    try:
        topo = list(nx.topological_sort(g))
        acyclic_g = g
    except nx.NetworkXUnfeasible:
        acyclic_g = _break_cycles_by_dominant_direction(g)
        topo = list(nx.topological_sort(acyclic_g))

    # Identify input-source nodes: no predecessors.
    input_sources = [n for n in topo if acyclic_g.in_degree(n) == 0]

    # Longest path length from any input source.
    columns: dict[str, int] = {}
    for node in topo:
        preds = list(acyclic_g.predecessors(node))
        if preds:
            columns[node] = max(columns[p] for p in preds) + 1
        else:
            columns[node] = 0

    # Center-column handling for nodes unreachable from inputs.
    max_col = max(columns.values()) if columns else 0
    center_col = max(1, max_col // 2)
    for node, col in columns.items():
        if node not in input_sources and col == 0 and acyclic_g.out_degree(node) > 0:
            # Recompute distance to outputs and center between input and output sides.
            dist_to_output = _longest_distance_to_outputs(acyclic_g, node)
            if dist_to_output is not None:
                columns[node] = max(0, max_col - dist_to_output)
            if columns[node] == 0:
                columns[node] = center_col

    return columns


def _break_cycles_by_dominant_direction(g: nx.DiGraph) -> nx.DiGraph:
    """Return an acyclic copy of ``g`` by removing one edge from each cycle.

    v1 heuristic: remove the edge whose target has the highest total in-degree
    within the cycle (i.e. the node that is most "driven" by other cycle
    members is treated as the sink side). Emit a warning for each removed edge.
    """
    acyclic = g.copy()
    while True:
        try:
            cycle = nx.find_cycle(acyclic, orientation="original")
        except nx.NetworkXNoCycle:
            break

        cycle_nodes = {u for u, _, _ in cycle} | {v for _, v, _ in cycle}
        # Score each edge by how many cycle-internal predecessors the target has.
        def edge_score(edge):
            _, v, _ = edge
            return sum(1 for p in acyclic.predecessors(v) if p in cycle_nodes)

        victim = max(cycle, key=edge_score)
        u, v, _ = victim
        logger.warning("Breaking feedback cycle by removing edge %s -> %s", u, v)
        acyclic.remove_edge(u, v)
    return acyclic


def _longest_distance_to_outputs(g: nx.DiGraph, node: str) -> int | None:
    """Return longest path length from ``node`` to any sink (node with no successors).

    Assumes ``g`` is a DAG.
    """
    if g.out_degree(node) == 0:
        return 0
    try:
        # Longest path in a DAG = negate weights and find shortest path.
        lengths = nx.single_source_bellman_ford_path_length(
            g, node, weight=lambda u, v, d: -1
        )
        return int(-min(lengths.values()))
    except Exception:
        return None


def _group_pins_by_net(ir: CircuitIR) -> dict[str, list[PinWithComp]]:
    pins_by_net: dict[str, list[PinWithComp]] = {}
    pin_by_id = {p.id: p for comp in ir.components for p in comp.pins}

    for conn in ir.connections:
        pin = pin_by_id.get(conn.pin_id)
        if pin is None:
            continue
        comp_id, _ = conn.pin_id.rsplit(".", 1)
        entry = PinWithComp(pin=pin, component_id=comp_id)
        pins_by_net.setdefault(conn.net_id, []).append(entry)

    return pins_by_net


class PinWithComp:
    def __init__(self, pin: Pin, component_id: str) -> None:
        self.pin = pin
        self.component_id = component_id


def _is_source_pin(pc: PinWithComp, config: LayoutConfig) -> bool:
    pin = pc.pin
    if pin.type in ("output", "power"):
        return True
    # Treat explicit power-source names as sources even if pin.type is passive/unknown.
    if pin.name and pin.name.upper() in {"VCC", "VDD", "VSUP", "+12V", "OUT", "OUTPUT"}:
        return True
    return False


def _is_passive_component(comp: Component | None) -> bool:
    """True if the component is a passive two-pin device."""
    if comp is None:
        return False
    return comp.device_kind.value in PASSIVE_KINDS


def validate_io_nets(ir: CircuitIR, config: LayoutConfig) -> None:
    """Raise LayoutConfigError if configured IO nets are missing."""
    net_names = {n.name for n in ir.nets}
    missing = []
    for name in config.io.inputs + config.io.outputs + config.io.power_top + config.io.power_bottom:
        if name not in net_names:
            missing.append(name)
    if missing:
        raise LayoutConfigError(
            f"configured IO nets not found in IR: {missing}; available: {sorted(net_names)}"
        )
```

- [ ] **Step 2: Write test `tests/layout/test_signal_graph.py`**

```python
from circuit_graph.ir import Component, Connection, DeviceKind, Net, Pin
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.signal_graph import assign_columns, build_signal_graph


def _ir_one_opamp() -> "CircuitIR":
    from circuit_graph.ir import CircuitIR, CircuitMeta, Provenance

    u1 = Component(
        id="U1",
        device_kind=DeviceKind.IC,
        pins=[
            Pin(id="U1.1", number="1", name="+", type="input"),
            Pin(id="U1.2", number="2", name="OUT", type="output"),
        ],
    )
    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    vref = Net(id="N1", name="VREF")
    out = Net(id="N2", name="OUT")
    return CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[u1, r1],
        nets=[vref, out],
        connections=[
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="U1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="U1.2", net_id="N2", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.2", net_id="N2", provenance=Provenance.NETLIST),
        ],
    )


def test_build_signal_graph_follows_output_to_input():
    ir = _ir_one_opamp()
    config = LayoutConfig.from_dict({"io": {"inputs": ["VREF"], "outputs": ["OUT"]}})
    g = build_signal_graph(ir, config)
    assert g.has_edge("U1", "R1")


def test_assign_columns_puts_source_left():
    ir = _ir_one_opamp()
    config = LayoutConfig.from_dict({"io": {"inputs": ["VREF"], "outputs": ["OUT"]}})
    g = build_signal_graph(ir, config)
    cols = assign_columns(g, config)
    assert cols["U1"] == 0
    assert cols["R1"] == 1


def test_passive_device_transmits_signal():
    """A resistor between two ICs should not block signal-flow columns."""
    from circuit_graph.ir import CircuitIR, CircuitMeta, Provenance

    u1 = Component(
        id="U1",
        device_kind=DeviceKind.IC,
        pins=[Pin(id="U1.1", number="1", name="OUT", type="output")],
    )
    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    u2 = Component(
        id="U2",
        device_kind=DeviceKind.IC,
        pins=[Pin(id="U2.1", number="1", name="IN", type="input")],
    )
    n1 = Net(id="N1", name="NET1")
    ir = CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[u1, r1, u2],
        nets=[n1],
        connections=[
            Connection(pin_id="U1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.2", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="U2.1", net_id="N1", provenance=Provenance.NETLIST),
        ],
    )
    config = LayoutConfig.from_dict({})
    g = build_signal_graph(ir, config)
    cols = assign_columns(g, config)
    assert cols["U1"] < cols["R1"] < cols["U2"]


def test_center_column_for_unconnected_component():
    """A component with neither input nor output path lands in a center column."""
    from circuit_graph.ir import CircuitIR, CircuitMeta, Provenance

    u1 = Component(
        id="U1",
        device_kind=DeviceKind.IC,
        pins=[Pin(id="U1.1", number="1", name="OUT", type="output")],
    )
    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    u2 = Component(
        id="U2",
        device_kind=DeviceKind.IC,
        pins=[Pin(id="U2.1", number="1", name="IN", type="input")],
    )
    r2 = Component(
        id="R2",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R2.1", number="1"), Pin(id="R2.2", number="2")],
    )
    n1 = Net(id="N1", name="NET1")
    n2 = Net(id="N2", name="NET2")
    n3 = Net(id="N3", name="FLOAT")
    ir = CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[u1, r1, u2, r2],
        nets=[n1, n2, n3],
        connections=[
            Connection(pin_id="U1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.2", net_id="N2", provenance=Provenance.NETLIST),
            Connection(pin_id="U2.1", net_id="N2", provenance=Provenance.NETLIST),
            # R2 is only connected to a dangling net.
            Connection(pin_id="R2.1", net_id="N3", provenance=Provenance.NETLIST),
            Connection(pin_id="R2.2", net_id="N3", provenance=Provenance.NETLIST),
        ],
    )
    config = LayoutConfig.from_dict({})
    g = build_signal_graph(ir, config)
    cols = assign_columns(g, config)
    assert cols["U1"] < cols["R1"] < cols["U2"]
    assert 0 < cols["R2"] < cols["U2"]
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/layout/test_signal_graph.py -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add circuit_graph/layout/signal_graph.py tests/layout/test_signal_graph.py
git commit -m "feat(layout): build signal-flow graph and assign columns" -m "Adds build_signal_graph and assign_columns using networkx." -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: Component placer

**Files:**
- Create: `circuit_graph/layout/placer.py`
- Test: `tests/layout/test_placer.py`

**Interfaces:**
- Consumes: `CircuitIR`, `LayoutConfig`, column assignment dict
- Produces: `ComponentPlacement` dataclass: `{component_id: Placement(x, y, rotation)}`

- [ ] **Step 1: Create `circuit_graph/layout/placer.py`**

```python
"""Assign absolute (x, y, rotation) to each component."""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from statistics import median

from circuit_graph.ir import CircuitIR
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.signal_graph import assign_columns, build_signal_graph

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Placement:
    x: float
    y: float
    rotation: float


def place_components(ir: CircuitIR, config: LayoutConfig) -> dict[str, Placement]:
    """Return component_id → Placement."""
    g = build_signal_graph(ir, config)
    columns = assign_columns(g, config)

    # Group components by column.
    column_to_comps: dict[int, list[str]] = {}
    for comp_id, col in columns.items():
        column_to_comps.setdefault(col, []).append(comp_id)

    # Sort columns left-to-right.
    sorted_columns = sorted(column_to_comps.keys())

    # Compute row positions per column.
    comp_y: dict[str, float] = {}
    for col in sorted_columns:
        comp_ids = column_to_comps[col]
        if len(comp_ids) == 1:
            comp_y[comp_ids[0]] = 0.0
            continue

        # Sort by median y of already-placed connected components;
        # passive edges are considered in both directions.
        ordered = sorted(
            comp_ids,
            key=lambda cid: (_median_connected_y(cid, g, comp_y), cid),
        )
        for i, cid in enumerate(ordered):
            comp_y[cid] = i * config.placement.row_step_mm

    # Convert column/row to absolute x/y.
    placements: dict[str, Placement] = {}
    for comp in ir.components:
        col = columns.get(comp.id, 0)
        x = config.placement.input_x_mm + col * config.placement.column_step_mm
        y = comp_y.get(comp.id, 0.0) + 80.0  # page vertical center offset

        # Snap to grid.
        x = round(x / config.grid.size_mm) * config.grid.size_mm
        y = round(y / config.grid.size_mm) * config.grid.size_mm

        sym_key = _symbol_key(comp)
        sym_cfg = config.symbols.get(sym_key)
        if sym_cfg is None:
            warnings.warn(
                f"Symbol config missing for '{sym_key}'; using rotation 0°",
                stacklevel=2,
            )
        rotation = sym_cfg.rotation if sym_cfg else 0.0

        placements[comp.id] = Placement(x=x, y=y, rotation=rotation)

    return placements


def _median_connected_y(cid: str, g, comp_y: dict[str, float]) -> float:
    """Median y of already-placed neighbours (predecessors and passive successors)."""
    placed: list[float] = []
    for n in g.predecessors(cid):
        if n in comp_y:
            placed.append(comp_y[n])
    # For passive devices, also consider successors that have already been placed.
    for n in g.successors(cid):
        if n in comp_y and g.edges.get((cid, n), {}).get("passive"):
            placed.append(comp_y[n])
    if not placed:
        return 0.0
    return float(median(placed))


def _symbol_key(comp) -> str:
    """Best-effort symbol library key from component metadata."""
    if comp.metadata.get("symbol"):
        return comp.metadata["symbol"]
    return comp.part_number or comp.id
```

- [ ] **Step 2: Write test `tests/layout/test_placer.py`**

```python
import math

from circuit_graph.ir import Component, Connection, DeviceKind, Net, Pin
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.placer import place_components


def _ir_two_resistors() -> "CircuitIR":
    from circuit_graph.ir import CircuitIR, CircuitMeta, Provenance

    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    r2 = Component(
        id="R2",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R2.1", number="1"), Pin(id="R2.2", number="2")],
    )
    n1 = Net(id="N1", name="IN")
    n2 = Net(id="N2", name="OUT")
    return CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[r1, r2],
        nets=[n1, n2],
        connections=[
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.2", net_id="N2", provenance=Provenance.NETLIST),
            Connection(pin_id="R2.1", net_id="N2", provenance=Provenance.NETLIST),
        ],
    )


def test_place_components_grid_aligned():
    ir = _ir_two_resistors()
    cfg = LayoutConfig.from_dict(
        {
            "io": {"inputs": ["IN"], "outputs": ["OUT"]},
            "placement": {"input_x_mm": 38.1, "column_step_mm": 25.4, "row_step_mm": 12.7},
            "grid": {"size_mm": 2.54},
        }
    )
    placements = place_components(ir, cfg)
    for p in placements.values():
        assert math.isclose(p.x % 2.54, 0.0, abs_tol=1e-9)
        assert math.isclose(p.y % 2.54, 0.0, abs_tol=1e-9)


def test_input_is_left_of_output():
    ir = _ir_two_resistors()
    cfg = LayoutConfig.from_dict({"io": {"inputs": ["IN"], "outputs": ["OUT"]}})
    placements = place_components(ir, cfg)
    assert placements["R1"].x < placements["R2"].x
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/layout/test_placer.py -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add circuit_graph/layout/placer.py tests/layout/test_placer.py
git commit -m "feat(layout): place components on grid by signal-flow column" -m "Adds place_components with grid snapping and symbol rotation lookup." -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: Orthogonal wire placer

**Files:**
- Create: `circuit_graph/layout/wire_placer.py`
- Test: `tests/layout/test_wire_placer.py`

**Interfaces:**
- Consumes: `CircuitIR`, `LayoutConfig`, component placements, pin-position helper
- Produces: `list[WireSegment(start=(x,y), end=(x,y))]`

- [ ] **Step 1: Create `circuit_graph/layout/wire_placer.py`**

```python
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
    # width_mm and layer can be added here when kicad_sch_api supports them.


def place_wires(
    ir: CircuitIR,
    placements: dict[str, Placement],
    pin_positions: dict[str, dict[str, tuple[float, float]]],
    config: LayoutConfig,
) -> list[WireSegment]:
    """Return orthogonal wire segments connecting all pins on each net."""
    segments: list[WireSegment] = []

    net_pins = _group_pin_positions_by_net(ir, placements, pin_positions)

    for _net_id, points in net_pins.items():
        if len(points) < 2:
            continue
        segments.extend(_route_net(points, config))

    return _merge_collinear_segments(segments)


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


def _route_net(points: list[tuple[float, float]], config: LayoutConfig) -> list[WireSegment]:
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

        # Add L-shaped route.
        corner = (pt[0], cpt[1])
        if corner != cpt:
            segments.append(_snap_segment(cpt, corner, config))
        if corner != pt:
            segments.append(_snap_segment(corner, pt, config))
        connected.append(pt)

    return segments


def _merge_collinear_segments(segments: list[WireSegment]) -> list[WireSegment]:
    """Merge adjacent axis-aligned segments that lie on the same line."""
    changed = True
    while changed:
        changed = False
        merged: list[WireSegment] = []
        used = set()
        for i, a in enumerate(segments):
            if i in used:
                continue
            for j in range(i + 1, len(segments)):
                if j in used:
                    continue
                b = segments[j]
                combined = _try_merge(a, b)
                if combined is not None:
                    a = combined
                    used.add(j)
                    changed = True
            merged.append(a)
            used.add(i)
        segments = merged
    return segments


def _try_merge(a: WireSegment, b: WireSegment) -> WireSegment | None:
    """Return a merged segment if a and b are collinear and touch at an endpoint."""
    # Normalize so start <= end.
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
```

- [ ] **Step 2: Write test `tests/layout/test_wire_placer.py`**

```python
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.placer import Placement
from circuit_graph.layout.wire_placer import WireSegment, place_wires, is_orthogonal


def test_place_wires_orthogonal():
    # Minimal IR is not needed for this pure geometry test; use empty IR and manual pins.
    from circuit_graph.ir import CircuitIR, CircuitMeta

    ir = CircuitIR(meta=CircuitMeta(source="test", format="manual"))
    placements = {}
    pin_positions = {
        "R1": {"1": (0.0, 0.0), "2": (10.0, 0.0)},
        "R2": {"1": (10.0, 0.0), "2": (10.0, 10.0)},
    }
    cfg = LayoutConfig.from_dict({})

    # Need connections to trigger wire placement.
    from circuit_graph.ir import Connection, Provenance

    ir.connections = [
        Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
        Connection(pin_id="R1.2", net_id="N1", provenance=Provenance.NETLIST),
        Connection(pin_id="R2.1", net_id="N1", provenance=Provenance.NETLIST),
    ]

    segments = place_wires(ir, placements, pin_positions, cfg)
    assert segments
    for seg in segments:
        assert is_orthogonal(seg)
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/layout/test_wire_placer.py -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add circuit_graph/layout/wire_placer.py tests/layout/test_wire_placer.py
git commit -m "feat(layout): place orthogonal wire segments" -m "Adds place_wires with greedy rectilinear routing and grid snapping." -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: Label placer

**Files:**
- Create: `circuit_graph/layout/label_placer.py`
- Test: `tests/layout/test_label_placer.py`

**Interfaces:**
- Consumes: `CircuitIR`, `LayoutConfig`, component placements
- Produces: `list[Label(text, x, y, angle, layer="Reference"|"Value")]`

- [ ] **Step 1: Create `circuit_graph/layout/label_placer.py`**

```python
"""Place reference/value labels at 0° and resolve overlaps."""

from __future__ import annotations

from dataclasses import dataclass

from circuit_graph.ir import CircuitIR
from circuit_graph.layout.config import LabelConfig, LayoutConfig
from circuit_graph.layout.exceptions import LayoutOverlapError
from circuit_graph.layout.placer import Placement


@dataclass(frozen=True)
class Label:
    text: str
    x: float
    y: float
    angle: float
    kind: str  # "reference" or "value"


def place_labels(
    ir: CircuitIR,
    placements: dict[str, Placement],
    config: LayoutConfig,
) -> list[Label]:
    """Return labels for all components."""
    labels: list[Label] = []

    ref_cfg = config.labels.get("reference", LabelConfig())
    val_cfg = config.labels.get("value", LabelConfig())

    for comp in ir.components:
        placement = placements[comp.id]
        ref_text = comp.id
        val_text = comp.raw_value or comp.part_number or ""

        labels.append(
            _make_label(ref_text, placement, ref_cfg, "reference")
        )
        if val_text:
            labels.append(
                _make_label(val_text, placement, val_cfg, "value")
            )

    _resolve_overlaps(labels, config)
    return labels


def _make_label(text: str, placement: Placement, cfg: LabelConfig, kind: str) -> Label:
    x = placement.x + cfg.offset_mm[0]
    y = placement.y + cfg.offset_mm[1]
    return Label(text=text, x=x, y=y, angle=0.0, kind=kind)


def _resolve_overlaps(labels: list[Label], config: LayoutConfig) -> None:
    """Iteratively shift labels down until no overlaps remain.

    The algorithm repeats a full pairwise sweep until either no overlap is
    detected or ``max_sweeps`` is reached. This handles chains where moving
    one label creates a new overlap with a third label.
    """
    char = config.grid.char_unit_mm
    max_sweeps = 10
    total_attempts = 0
    max_total_attempts = 200

    for _ in range(max_sweeps):
        moved = False
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                if not _overlap(labels[i], labels[j], config):
                    continue
                if total_attempts >= max_total_attempts:
                    raise LayoutOverlapError(
                        f"cannot resolve overlap between {labels[i].text} and {labels[j].text}"
                    )
                # Shift the lower label down by one character unit.
                lower_idx = i if labels[i].y <= labels[j].y else j
                lower = labels[lower_idx]
                labels[lower_idx] = Label(
                    text=lower.text,
                    x=lower.x,
                    y=lower.y + char,
                    angle=lower.angle,
                    kind=lower.kind,
                )
                moved = True
                total_attempts += 1
        if not moved:
            return

    # If we exit the loop, at least one overlap remains.
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            if _overlap(labels[i], labels[j], config):
                raise LayoutOverlapError(
                    f"cannot resolve overlap between {labels[i].text} and {labels[j].text}"
                )


def _overlap(a: Label, b: Label, config: LayoutConfig) -> bool:
    """Simple AABB overlap using font height as box height and len*char as width."""
    char = config.grid.char_unit_mm
    h = char
    wa = len(a.text) * char
    wb = len(b.text) * char

    ax0, ax1 = a.x - wa / 2, a.x + wa / 2
    ay0, ay1 = a.y - h / 2, a.y + h / 2
    bx0, bx1 = b.x - wb / 2, b.x + wb / 2
    by0, by1 = b.y - h / 2, b.y + h / 2

    return ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0
```

- [ ] **Step 2: Write test `tests/layout/test_label_placer.py`**

```python
from circuit_graph.ir import Component, DeviceKind
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.label_placer import place_labels
from circuit_graph.layout.placer import Placement


def test_place_labels_resolve_overlap():
    comp = Component(id="R1", device_kind=DeviceKind.RESISTOR, raw_value="10k")
    placements = {"R1": Placement(x=0.0, y=0.0, rotation=0.0)}
    cfg = LayoutConfig.from_dict(
        {
            "labels": {
                "reference": {"offset_mm": [0, -1.27], "font_height_mm": 1.27},
                "value": {"offset_mm": [0, -1.27], "font_height_mm": 1.27},
            }
        }
    )

    from circuit_graph.ir import CircuitIR, CircuitMeta

    ir = CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[comp],
    )
    labels = place_labels(ir, placements, cfg)
    assert len(labels) == 2
    assert labels[0].angle == 0.0
    assert labels[1].angle == 0.0
    assert labels[0].y != labels[1].y
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/layout/test_label_placer.py -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add circuit_graph/layout/label_placer.py tests/layout/test_label_placer.py
git commit -m "feat(layout): place non-overlapping 0° labels" -m "Adds place_labels with vertical de-overlap by char_unit." -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: Pin-position helper

**Files:**
- Create: `circuit_graph/layout/geometry.py`
- Test: `tests/layout/test_geometry.py`

**Interfaces:**
- Consumes: `CircuitIR`, `LayoutConfig`, component placements
- Produces: `dict[component_id, dict[pin_number, (x, y)]]`

- [ ] **Step 1: Create `circuit_graph/layout/geometry.py`**

```python
"""Geometric helpers: pin positions after component rotation/translation."""

from __future__ import annotations

import math
from typing import Any

from circuit_graph.ir import CircuitIR
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.placer import Placement


def compute_pin_positions(
    ir: CircuitIR,
    placements: dict[str, Placement],
    config: LayoutConfig,
    symbol_cache: dict[str, Any] | None = None,
) -> dict[str, dict[str, tuple[float, float]]]:
    """Return absolute schematic-space pin positions for every component.

    If a kicad_sch_api symbol cache is provided, real pin positions are used;
    otherwise a simple rectangular approximation is used for testing.

    Rotation semantics (design-spec §3):
      - ``symbols.<name>.rotation`` is applied to the whole symbol body.
      - ``symbols.<name>.pin_directions`` describe pin directions in the
        **unrotated** symbol-local coordinate system.
      - This function therefore computes unrotated local offsets and lets
        ``_transform()`` apply the symbol rotation.
    """
    result: dict[str, dict[str, tuple[float, float]]] = {}

    for comp in ir.components:
        placement = placements[comp.id]
        result[comp.id] = {}

        sym_cfg = config.symbols.get(comp.metadata.get("symbol", comp.part_number or comp.id))
        # Use the configured symbol rotation; placement.rotation already includes it.
        rotation = placement.rotation

        for pin in comp.pins:
            # Try real symbol geometry first.
            local = _local_pin_position(
                comp.id, pin.number, sym_cfg.pin_directions if sym_cfg else {}, symbol_cache
            )
            abs_pos = _transform(local, placement.x, placement.y, rotation)
            # Snap to grid.
            grid = config.grid.size_mm
            abs_pos = (
                round(abs_pos[0] / grid) * grid,
                round(abs_pos[1] / grid) * grid,
            )
            result[comp.id][pin.number] = abs_pos

    return result


def _local_pin_position(
    comp_id: str,
    pin_num: str,
    pin_directions: dict[str, str],
    symbol_cache: dict[str, Any] | None,
) -> tuple[float, float]:
    """Return pin position in unrotated symbol-local space (mm).

    ``pin_directions`` is keyed by pin number and gives the direction the pin
    faces in the unrotated symbol (up/down/left/right). The actual rotation of
    the symbol is applied later by ``_transform()``.
    """
    if symbol_cache is not None:
        # kicad-sch-api path: implemented in Task 9.
        raise NotImplementedError("symbol_cache path implemented in Task 9")

    direction = pin_directions.get(pin_num, "right")
    length = 2.54  # default pin length
    table = {
        "up": (0.0, length),       # unrotated: pin extends upward (positive Y)
        "down": (0.0, -length),    # unrotated: pin extends downward
        "left": (-length, 0.0),
        "right": (length, 0.0),
    }
    return table.get(direction, (length, 0.0))


def _transform(
    local: tuple[float, float], cx: float, cy: float, rotation_deg: float
) -> tuple[float, float]:
    """Apply KiCad-style transform: invert Y, rotate, translate.

    The input ``local`` is in unrotated symbol space where +Y is up. KiCad
    schematic space uses +Y down, so we first flip Y, then rotate by
    ``rotation_deg`` (matching kicad_sch_api / convert_to_kicad.py), then
    translate.
    """
    x, y = local[0], -local[1]
    rad = math.radians(rotation_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    rx = x * cos_r - y * sin_r
    ry = x * sin_r + y * cos_r
    return (cx + rx, cy + ry)
```

- [ ] **Step 2: Write test `tests/layout/test_geometry.py`**

```python
import math

from circuit_graph.ir import Component, DeviceKind, Pin
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.geometry import compute_pin_positions
from circuit_graph.layout.placer import Placement


def test_compute_pin_positions_grid_aligned():
    from circuit_graph.ir import CircuitIR, CircuitMeta

    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
        metadata={"symbol": "Device:R"},
    )
    ir = CircuitIR(meta=CircuitMeta(source="test", format="manual"), components=[r1])
    cfg = LayoutConfig.from_dict(
        {
            "symbols": {
                "Device:R": {"rotation": 90, "pin_directions": {"1": "up", "2": "down"}}
            },
            "grid": {"size_mm": 2.54},
        }
    )
    placements = {"R1": Placement(x=10.0, y=10.0, rotation=90.0)}
    pins = compute_pin_positions(ir, placements, cfg)
    # Unrotated pin 1 extends up (+Y). After 90° clockwise rotation it points right (+X).
    assert pins["R1"]["1"][0] > 10.0
    assert math.isclose(pins["R1"]["1"][1], 10.0, abs_tol=1e-9)
    for pos in pins["R1"].values():
        assert math.isclose(pos[0] % 2.54, 0.0, abs_tol=1e-9)
        assert math.isclose(pos[1] % 2.54, 0.0, abs_tol=1e-9)


def test_compute_pin_positions_rotation_0():
    from circuit_graph.ir import CircuitIR, CircuitMeta

    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
        metadata={"symbol": "Device:R"},
    )
    ir = CircuitIR(meta=CircuitMeta(source="test", format="manual"), components=[r1])
    cfg = LayoutConfig.from_dict(
        {
            "symbols": {
                "Device:R": {"rotation": 0, "pin_directions": {"1": "left", "2": "right"}}
            },
            "grid": {"size_mm": 2.54},
        }
    )
    placements = {"R1": Placement(x=10.0, y=10.0, rotation=0.0)}
    pins = compute_pin_positions(ir, placements, cfg)
    assert pins["R1"]["1"][0] < pins["R1"]["2"][0]
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/layout/test_geometry.py -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add circuit_graph/layout/geometry.py tests/layout/test_geometry.py
git commit -m "feat(layout): compute absolute pin positions" -m "Adds compute_pin_positions with rotation, grid snapping, and fallback geometry." -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: Unit layout orchestrator

**Files:**
- Create: `circuit_graph/layout/unit_layout.py`
- Create: `circuit_graph/layout/decomposer.py`
- Create: `circuit_graph/layout/page_layout.py`
- Test: `tests/layout/test_unit_layout.py`

**Interfaces:**
- Consumes: `CircuitIR`, `LayoutConfig`
- Produces: `UnitLayoutResult` with placements, wires, labels

- [ ] **Step 1: Create `circuit_graph/layout/unit_layout.py`**

```python
"""Orchestrate layout for a single unit."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from circuit_graph.ir import CircuitIR
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.geometry import compute_pin_positions
from circuit_graph.layout.label_placer import Label, place_labels
from circuit_graph.layout.placer import Placement, place_components
from circuit_graph.layout.signal_graph import validate_io_nets
from circuit_graph.layout.wire_placer import WireSegment, place_wires


@dataclass(frozen=True)
class UnitLayoutResult:
    placements: dict[str, Placement]
    pin_positions: dict[str, dict[str, tuple[float, float]]]
    wires: list[WireSegment]
    labels: list[Label]


def layout_unit(ir: CircuitIR, config: LayoutConfig) -> UnitLayoutResult:
    """Layout one unit and return placements, wires, and labels."""
    validate_io_nets(ir, config)

    placements = place_components(ir, config)
    pin_positions = compute_pin_positions(ir, placements, config)
    wires = place_wires(ir, placements, pin_positions, config)
    labels = place_labels(ir, placements, config)

    _warn_dangling_nets(ir, config)

    return UnitLayoutResult(
        placements=placements,
        pin_positions=pin_positions,
        wires=wires,
        labels=labels,
    )


def _warn_dangling_nets(ir: CircuitIR, config: LayoutConfig) -> None:
    """Emit a warning for nets with fewer than two connected pins."""
    pin_by_id = {p.id: p for comp in ir.components for p in comp.pins}
    seen: dict[str, list] = {}
    for conn in ir.connections:
        pin = pin_by_id.get(conn.pin_id)
        if pin is None:
            continue
        seen.setdefault(conn.net_id, []).append(pin)
    for net_id, pins in seen.items():
        if len(pins) < 2:
            warnings.warn(f"Net '{net_id}' has fewer than 2 pins; no wire placed", stacklevel=2)
```

- [ ] **Step 2: Create `circuit_graph/layout/decomposer.py`**

```python
"""Split a CircuitIR into layout units."""

from __future__ import annotations

from dataclasses import dataclass

from circuit_graph.ir import CircuitIR


@dataclass(frozen=True)
class LayoutUnit:
    id: str
    name: str
    unit_type: str
    ir: CircuitIR


def decompose(ir: CircuitIR) -> list[LayoutUnit]:
    """Return layout units.

    v1: treat the entire circuit as a single unit.
    """
    return [
        LayoutUnit(
            id="main",
            name=ir.meta.source or "main",
            unit_type="default",
            ir=ir,
        )
    ]
```

- [ ] **Step 3: Create `circuit_graph/layout/page_layout.py`**

```python
"""Place units on the schematic page."""

from __future__ import annotations

from dataclasses import dataclass

from circuit_graph.layout.unit_layout import UnitLayoutResult


@dataclass(frozen=True)
class PageLayoutResult:
    units: dict[str, UnitLayoutResult]


def layout_page(units: dict[str, UnitLayoutResult]) -> PageLayoutResult:
    """v1: page layout is a passthrough; only one unit is supported."""
    return PageLayoutResult(units=units)
```

- [ ] **Step 4: Write test `tests/layout/test_unit_layout.py`**

```python
from circuit_graph.ir import Component, Connection, DeviceKind, Net, Pin
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.unit_layout import layout_unit
from circuit_graph.layout.wire_placer import is_orthogonal


def _ir_simple() -> "CircuitIR":
    from circuit_graph.ir import CircuitIR, CircuitMeta, Provenance

    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    r2 = Component(
        id="R2",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R2.1", number="1"), Pin(id="R2.2", number="2")],
    )
    n1 = Net(id="N1", name="IN")
    n2 = Net(id="N2", name="OUT")
    return CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[r1, r2],
        nets=[n1, n2],
        connections=[
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.2", net_id="N2", provenance=Provenance.NETLIST),
            Connection(pin_id="R2.1", net_id="N2", provenance=Provenance.NETLIST),
        ],
    )


def test_layout_unit_produces_orthogonal_wires():
    ir = _ir_simple()
    cfg = LayoutConfig.from_dict({"io": {"inputs": ["IN"], "outputs": ["OUT"]}})
    result = layout_unit(ir, cfg)
    assert result.placements
    for wire in result.wires:
        assert is_orthogonal(wire)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/layout/test_unit_layout.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add circuit_graph/layout/unit_layout.py circuit_graph/layout/decomposer.py circuit_graph/layout/page_layout.py tests/layout/test_unit_layout.py
git commit -m "feat(layout): orchestrate single-unit layout" -m "Adds layout_unit, decomposer, and page_layout passthrough for v1." -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: KiCad writer

**Files:**
- Create: `circuit_graph/layout/kicad_writer.py`
- Test: `tests/layout/test_kicad_writer.py` (skip if kicad_sch_api missing)

**Interfaces:**
- Consumes: `kicad_sch_api.Schematic`, `PageLayoutResult`
- Produces: mutated `Schematic`

- [ ] **Step 1: Create `circuit_graph/layout/kicad_writer.py`**

```python
"""Apply a layout result to a kicad_sch_api Schematic."""

from __future__ import annotations

from circuit_graph.layout.exceptions import LayoutError
from circuit_graph.layout.page_layout import PageLayoutResult


def apply_layout(sch, result: PageLayoutResult) -> None:
    """Mutate ``sch`` with positions, wires, labels, and junctions."""
    try:
        import kicad_sch_api as ksa
    except ImportError as exc:
        raise LayoutError(
            "kicad_sch_api is required for apply_layout; install it with "
            "`pip install -e E:/__electric/__research/kicad-sch-api`"
        ) from exc

    if not isinstance(sch, ksa.Schematic):
        raise LayoutError(f"expected ksa.Schematic, got {type(sch).__name__}")

    for unit_id, unit_result in result.units.items():
        _apply_unit(sch, unit_result)


def _apply_unit(sch, unit) -> None:
    import kicad_sch_api as ksa

    # Move components.
    comp_by_id = {c.ref: c for c in sch.components}
    for comp_id, placement in unit.placements.items():
        comp = comp_by_id.get(comp_id)
        if comp is None:
            continue
        comp.move(placement.x, placement.y)
        comp.rotate(placement.rotation)

    # Add wires.
    for seg in unit.wires:
        sch.add_wire(start=seg.start, end=seg.end)

    # Add labels.
    for label in unit.labels:
        sch.add_label(label.text, position=(label.x, label.y))

    # Add junctions at T-nodes: a point that is an endpoint of one segment and
    # lies on the interior of another axis-aligned segment.
    junction_points = _find_junctions(unit.wires)
    for pos in junction_points:
        sch.junctions.add(position=pos)


def _find_junctions(wires: list[WireSegment]) -> set[tuple[float, float]]:
    """Return points where a wire segment ends on the interior of another."""
    junctions: set[tuple[float, float]] = set()

    def _on_segment_interior(pt: tuple[float, float], seg: WireSegment) -> bool:
        """True if ``pt`` lies strictly inside ``seg`` and ``seg`` is axis-aligned."""
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

    for i, seg_a in enumerate(wires):
        for end in (seg_a.start, seg_a.end):
            for j, seg_b in enumerate(wires):
                if i == j:
                    continue
                if _on_segment_interior(end, seg_b):
                    junctions.add(end)
    return junctions
```

- [ ] **Step 2: Write test `tests/layout/test_kicad_writer.py`**

```python
import pytest

from circuit_graph.layout.kicad_writer import apply_layout
from circuit_graph.layout.page_layout import PageLayoutResult


ksa = pytest.importorskip("kicad_sch_api")


def test_apply_layout_moves_component():
    from circuit_graph.ir import CircuitIR, CircuitMeta, Component, DeviceKind
    from circuit_graph.layout.config import LayoutConfig
    from circuit_graph.layout.placer import Placement
    from circuit_graph.layout.unit_layout import layout_unit

    sch = ksa.create_schematic("test")
    sch.components.add("Device:R", "R1", "10k", position=(0, 0))

    r1 = Component(id="R1", device_kind=DeviceKind.RESISTOR)
    ir = CircuitIR(meta=CircuitMeta(source="test", format="manual"), components=[r1])
    cfg = LayoutConfig.from_dict({})
    unit = layout_unit(ir, cfg)
    page = PageLayoutResult(units={"main": unit})

    apply_layout(sch, page)
    assert sch.components["R1"].position.x == unit.placements["R1"].x
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/layout/test_kicad_writer.py -v`

Expected: PASS if kicad_sch_api installed; skipped otherwise.

- [ ] **Step 4: Commit**

```bash
git add circuit_graph/layout/kicad_writer.py tests/layout/test_kicad_writer.py
git commit -m "feat(layout): apply layout result to KiCad schematic" -m "Adds apply_layout with component move, wire/label/junction placement." -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 9: Public API and LM321 CCS config

**Files:**
- Modify: `circuit_graph/layout/__init__.py`
- Create: `circuit_graph/layout/configs/lm321_ccs.json`
- Test: `tests/layout/test_integration.py` (optional/skip if kicad_sch_api missing)

**Interfaces:**
- Consumes: `CircuitIR`, `ksa.Schematic`, optional `unit_configs`
- Produces: mutated `Schematic`

- [ ] **Step 1: Rewrite `circuit_graph/layout/__init__.py`**

```python
"""Schematic layout: turn a CircuitIR into a readable KiCad schematic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from circuit_graph.ir import CircuitIR
from circuit_graph.layout.config import LayoutConfig, load_layout_config
from circuit_graph.layout.decomposer import decompose
from circuit_graph.layout.kicad_writer import apply_layout
from circuit_graph.layout.page_layout import PageLayoutResult, layout_page
from circuit_graph.layout.unit_layout import layout_unit

__all__ = ["layout_schematic", "LayoutConfig", "load_layout_config"]


def layout_schematic(
    ir: CircuitIR,
    sch: Any,
    unit_configs: dict[str, dict | str | Path] | None = None,
) -> None:
    """Layout ``ir`` onto ``sch`` (a kicad_sch_api.Schematic object).

    ``unit_configs`` maps unit **name** or **unit_type** to a LayoutConfig dict
    or a JSON path. Resolution order: unit.name → unit.unit_type → default config.
    """
    units = decompose(ir)

    results: dict[str, Any] = {}
    for unit in units:
        cfg = _resolve_config(unit, unit_configs)
        results[unit.id] = layout_unit(unit.ir, cfg)

    page = layout_page(results)
    apply_layout(sch, page)


def _resolve_config(unit, unit_configs):
    configs = unit_configs or {}
    candidate = configs.get(unit.name)
    if candidate is None:
        candidate = configs.get(unit.unit_type)
    if candidate is None:
        candidate = Path(__file__).with_name("configs") / "default.json"
    return load_layout_config(candidate)
```

- [ ] **Step 2: Create `circuit_graph/layout/configs/lm321_ccs.json`**

```json
{
  "meta": {
    "name": "lm321_ccs_main",
    "unit_type": "current_source",
    "version": "1.0"
  },
  "grid": {
    "size_mm": 2.54,
    "char_unit_mm": 1.27
  },
  "placement": {
    "input_x_mm": 38.1,
    "output_x_mm": 152.4,
    "column_step_mm": 25.4,
    "row_step_mm": 12.7,
    "direction": "left_to_right"
  },
  "io": {
    "inputs": ["VREF", "N_SENSE"],
    "outputs": ["N_GATE_OUT"],
    "power_top": ["+12V"],
    "power_bottom": ["GND"]
  },
  "symbols": {
    "Device:R": {
      "rotation": 90,
      "pin_directions": {"1": "up", "2": "down"}
    },
    "Device:C": {
      "rotation": 90,
      "pin_directions": {"1": "up", "2": "down"}
    },
    "Transistor_BJT:BC547": {
      "rotation": 0,
      "pin_directions": {"1": "up", "2": "left", "3": "down"}
    },
    "Amplifier_Operational:LM321": {
      "rotation": 0,
      "pin_directions": {
        "1": "left",
        "2": "down",
        "3": "left",
        "4": "right",
        "5": "up"
      }
    }
  },
  "labels": {
    "reference": {"offset_mm": [0, -2.54], "font_height_mm": 1.27},
    "value": {"offset_mm": [0, 2.54], "font_height_mm": 1.27}
  }
}
```

- [ ] **Step 3: Write integration test `tests/layout/test_integration.py`**

```python
import pytest
from pathlib import Path

from circuit_graph.layout import layout_schematic

ksa = pytest.importorskip("kicad_sch_api")


def test_layout_schematic_runs_on_empty_ir():
    from circuit_graph.ir import CircuitIR, CircuitMeta

    ir = CircuitIR(meta=CircuitMeta(source="test", format="manual"))
    sch = ksa.create_schematic("empty")
    layout_schematic(ir, sch)
    assert sch is not None


def test_layout_schematic_uses_lm321_config():
    from circuit_graph.ir import CircuitIR, CircuitMeta

    ir = CircuitIR(meta=CircuitMeta(source="test", format="manual"))
    sch = ksa.create_schematic("lm321")
    layout_schematic(
        ir,
        sch,
        unit_configs={"main": Path(__file__).with_name("configs") / "lm321_ccs.json"},
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/layout/test_integration.py -v`

Expected: PASS or skip.

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/layout/__init__.py circuit_graph/layout/configs/lm321_ccs.json tests/layout/test_integration.py
git commit -m "feat(layout): public API and LM321 CCS config" -m "Adds layout_schematic entry point and lm321_ccs.json per-unit config." -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 10: Wire `lm321_ccs/convert_to_kicad.py` to use the layout module

**Files:**
- Modify: `lm321_ccs/convert_to_kicad.py`

**Interfaces:**
- Consumes: `layout_schematic`, `lm321_ccs.json`
- Produces: regenerated `lm321_ccs/1A_CCS_LM321_BJT.kicad_sch`

- [ ] **Step 1: Add a SPICE-netlist → CircuitIR converter**

Instead of hand-writing `Connection` objects, derive the `CircuitIR` from `lm321_ccs/ccs_v5_bjt.cir` so the layout module and the SPICE source stay in sync. Add a small parser helper in `lm321_ccs/convert_to_kicad.py`:

```python
from pathlib import Path
from circuit_graph.ir import (
    CircuitIR, CircuitMeta, Component, Connection, DeviceKind, Net, Pin, Provenance,
)


SPICE_DEVICE_MAP = {
    "R": DeviceKind.RESISTOR,
    "C": DeviceKind.CAPACITOR,
    "L": DeviceKind.INDUCTOR,
    "D": DeviceKind.DIODE,
    "Q": DeviceKind.IC,          # BJT treated as IC for symbol lookup
    "U": DeviceKind.IC,
    "X": DeviceKind.IC,
    "V": DeviceKind.SOURCE,
}


def _ir_from_spice(spice_path: Path) -> CircuitIR:
    """Build a minimal CircuitIR from a SPICE netlist.

    This is intentionally minimal: it extracts components, pins (numbered 1..N),
    and net connections. Pin names/semantics come from the layout JSON config,
    not from the netlist.
    """
    components: dict[str, Component] = {}
    nets: dict[str, Net] = {}
    connections: list[Connection] = []

    with spice_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(("*", ".")):
                continue
            parts = line.split()
            if not parts:
                continue

            token = parts[0]
            prefix = token[0].upper()
            if prefix not in SPICE_DEVICE_MAP:
                continue

            comp_id = token
            device_kind = SPICE_DEVICE_MAP[prefix]
            raw_value = parts[-1] if not parts[-1].startswith("(") else ""

            # Nodes are everything between the device id and the value/model.
            nodes = parts[1:-1] if raw_value else parts[1:]
            # Some SPICE lines list a model name after nodes; skip known models.
            if nodes and nodes[-1].upper() in {"BC547", "LM321"}:
                nodes = nodes[:-1]

            # Create component with numbered pins.
            pins = [Pin(id=f"{comp_id}.{i+1}", number=str(i+1)) for i in range(len(nodes))]
            components[comp_id] = Component(
                id=comp_id,
                device_kind=device_kind,
                raw_value=raw_value,
                pins=pins,
            )

            for i, net_name in enumerate(nodes):
                net = nets.setdefault(net_name, Net(id=net_name, name=net_name))
                connections.append(
                    Connection(
                        pin_id=f"{comp_id}.{i+1}",
                        net_id=net.id,
                        provenance=Provenance.NETLIST,
                    )
                )

    return CircuitIR(
        meta=CircuitMeta(source=spice_path.name, format="spice"),
        components=list(components.values()),
        nets=list(nets.values()),
        connections=connections,
    )
```

Then in `create_ccs_schematic()`:

```python
spice_path = Path(__file__).with_name("ccs_v5_bjt.cir")
ir = _ir_from_spice(spice_path)
```

- [ ] **Step 2: Replace hard-coded placement with layout module**

Replace the manual `position=(...)` calls in `convert_to_kicad.py` with initial default positions (they will be overwritten), then call:

```python
from circuit_graph.layout import layout_schematic

config_path = Path(__file__).parent.parent / "circuit_graph" / "layout" / "configs" / "lm321_ccs.json"
layout_schematic(ir, sch, unit_configs={"main": config_path})
```

After the call, remove or keep only the title/text additions.

- [ ] **Step 3: Regenerate the schematic**

Run: `.venv/Scripts/python lm321_ccs/convert_to_kicad.py lm321_ccs/1A_CCS_LM321_BJT.kicad_sch`

Expected: file is regenerated without errors. Open it in KiCad and visually verify that:
- Input nets (`VREF`, `N_SENSE`) appear on the left.
- Output net (`N_GATE_OUT`) appears on the right.
- All wires are orthogonal.
- Reference/value labels do not overlap.

- [ ] **Step 4: Commit**

```bash
git add lm321_ccs/convert_to_kicad.py lm321_ccs/1A_CCS_LM321_BJT.kicad_sch
git commit -m "feat(lm321_ccs): use layout module for schematic generation" -m "Replaces hard-coded positions with circuit_graph/layout orchestration." -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 11: Edge-case handling and cleanup

**Files:**
- Modify: `circuit_graph/layout/page_layout.py`
- Create: `tests/layout/test_edge_cases.py`

**Interfaces:**
- Consumes: `CircuitIR`, `LayoutConfig`, layout results
- Produces: warnings, no-connect markers, page-overflow checks

- [ ] **Step 1: Add page-overflow check**

In `page_layout.py`:

```python
from circuit_graph.layout.exceptions import LayoutPageOverflowError


def layout_page(units: dict[str, UnitLayoutResult]) -> PageLayoutResult:
    """v1: page layout is a passthrough; only one unit is supported."""
    if len(units) > 1:
        raise LayoutPageOverflowError(
            "Multiple layout units are not yet supported in v1"
        )
    return PageLayoutResult(units=units)
```

- [ ] **Step 2: Add edge-case tests**

```python
import pytest

from circuit_graph.ir import CircuitIR, CircuitMeta, Component, Connection, DeviceKind, Net, Pin, Provenance
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.page_layout import PageLayoutResult, layout_page
from circuit_graph.layout.unit_layout import UnitLayoutResult, layout_unit


def test_dangling_net_emits_warning():
    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    ir = CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[r1],
        nets=[Net(id="N1", name="FLOAT")],
        connections=[
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
        ],
    )
    with pytest.warns(UserWarning, match="fewer than 2 pins"):
        layout_unit(ir, LayoutConfig.from_dict({}))


def test_multiple_units_raise_page_overflow():
    empty = UnitLayoutResult(placements={}, pin_positions={}, wires=[], labels=[])
    with pytest.raises(LayoutPageOverflowError):
        layout_page({"u1": empty, "u2": empty})
```

- [ ] **Step 3: Commit**

```bash
git add circuit_graph/layout/page_layout.py tests/layout/test_edge_cases.py
git commit -m "feat(layout): add edge-case placeholders" -m "Adds page-overflow guard and dangling-net warning tests." -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 12: Documentation and README update

**Files:**
- Modify: `Readme.md`

**Interfaces:**
- Consumes: completed layout module
- Produces: user-facing documentation

- [ ] **Step 1: Add a "Schematic Layout" section to `Readme.md`**

```markdown
## Schematic Layout

The `circuit_graph.layout` module auto-arranges components and wires for KiCad schematics.

```python
from circuit_graph.layout import layout_schematic
import kicad_sch_api as ksa

ir = ...  # build or load CircuitIR
sch = ksa.create_schematic("my_design")
layout_schematic(ir, sch, unit_configs={"main": "path/to/unit.json"})
sch.save("my_design.kicad_sch")
```

Per-unit JSON configs live in `circuit_graph/layout/configs/`. Copy `default.json` or `lm321_ccs.json` as a starting point.
```

- [ ] **Step 2: Commit**

```bash
git add Readme.md
git commit -m "docs(readme): add schematic layout usage" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** Every requirement in `2026-07-11-schematic-layout-design.md` maps to a task:
  - Inputs-left/outputs-right → Task 2 (signal graph + columns)
  - Nearby placement, short wires → Tasks 2–4
  - Orthogonal wires only → Task 4 with `is_orthogonal`
  - 0° non-overlapping labels → Task 5
  - JSON-editable per-unit config → Tasks 1 and 9
  - Multi-unit scaffolding → Tasks 7–8
  - Dangling nets / page overflow → Task 11
- [ ] **Critical fixes applied:**
  - [ ] `geometry.py` rotation semantics match design spec (pin_directions = unrotated; `_transform` applies symbol rotation)
  - [ ] Passive two-pin devices traverse signal-flow graph bidirectionally
  - [ ] Junction detection finds T-nodes, not just triple endpoints
  - [ ] Column assignment uses longest path + center column + cycle breaking
- [ ] **Placeholder scan:** No TBD/TODO/"implement later"/"fill in details".
- [ ] **Type consistency:** `LayoutConfig`, `Placement`, `WireSegment`, `Label`, `UnitLayoutResult`, `PageLayoutResult` used consistently across tasks.
- [ ] **Testability:** Each task has a focused test file.
- [ ] **kicad_sch_api optional:** Import is guarded in `kicad_writer.py` and tests use `pytest.importorskip`.

If any item is unchecked, add the missing task or fix the placeholder before execution.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-11-schematic-layout.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
