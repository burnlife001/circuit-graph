# Schematic Layout Design

**Date:** 2026-07-11  
**Status:** Approved for implementation  
**Related:** `lm321_ccs/convert_to_kicad.py`, `circuit_graph/ir.py`

## 1. Goal

Add reusable schematic-layout logic to `circuit_graph` that turns a `CircuitIR` into a human-readable KiCad schematic. The layout must:

1. Place input signals on the left and output signals on the right.
2. Place components near the signals they connect to, keeping wires short.
3. Use only 0°/90° wire segments; no other angles.
4. Keep reference designators and values at 0° without overlapping, spacing vertically by one character unit derived from font height.

The configuration for each layout unit is stored in an editable JSON file.

## 2. Architecture

New package: `circuit_graph/layout/`

```
circuit_graph/
  layout/
    __init__.py          # public API: layout_schematic(ir, sch, unit_configs)
    config.py            # dataclasses + JSON loader/validator + defaults
    decomposer.py        # split CircuitIR into layout units
    unit_layout.py       # lay out one unit given its JSON config
    page_layout.py       # place units on the page and route between units
    wire_placer.py       # orthogonal wire segment placement
    label_placer.py      # reference/value text placement (0°, no overlap)
    kicad_writer.py      # apply results to kicad_sch_api Schematic
    configs/             # shipped per-unit config templates
      default.json
      lm321_ccs.json
```

### Public API

```python
from circuit_graph.layout import layout_schematic

layout_schematic(
    ir: CircuitIR,
    sch: ksa.Schematic,
    unit_configs: dict[str, dict | Path] | None = None,
)
```

`unit_configs` is keyed by **unit name**; if a name is missing, the loader falls back to `unit_type`. Values are either a parsed `dict` or a `Path` to a JSON file.

The function mutates the `kicad_sch_api.Schematic` object in-place by setting component positions/rotations, adding orthogonal wires, adding labels, and adding junctions where needed.

### Multi-unit workflow

1. **Decompose** `CircuitIR` into layout units using existing `FunctionalBlock`s when present; otherwise use connected-component / cut-point heuristics.
2. **Per-unit placement**: load the JSON config for each unit (by unit type or name), then run single-unit placement.
3. **Page-level layout**: arrange units left-to-right on the page according to inter-unit signal direction.
4. **Inter-unit routing**: connect units with orthogonal wire segments.

## 3. Per-unit JSON Configuration Schema

Each layout unit has its own JSON config file. Example: `circuit_graph/layout/configs/lm321_ccs.json`.

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

### Field semantics

- `grid.size_mm`: alignment grid for all positions and wire endpoints.
- `grid.char_unit_mm`: one character height; used for vertical label de-overlap.
- `placement.input_x_mm` / `output_x_mm`: x-coordinate of the leftmost / rightmost component column.
- `placement.column_step_mm`: horizontal distance between columns.
- `placement.row_step_mm`: vertical distance between rows.
- `io.inputs` / `io.outputs`: net names treated as unit inputs/outputs.
- `io.power_top` / `io.power_bottom`: power net names placed at top/bottom.
- `symbols.<lib>:<name>.rotation`: default rotation of the symbol body.
- `symbols.<lib>:<name>.pin_directions`: direction each pin faces **after rotation**.
- `labels.reference` / `labels.value`: offset and font size for reference/value text.

## 4. Placement & Wire Algorithm

### 4.1 Build signal-flow graph

- Nodes = components in the unit.
- For each net, inspect connected pin types:
  - If the net connects an `output` or `power` pin to `input`/`passive` pins, draw directed edges source → targets.
  - Passive two-pin devices (R, C, L, D) are traversed bidirectionally but marked as passive.
- Cycles/feedback: if a component is part of a cycle, determine the dominant signal direction (more outputs feed it from the left than from the right) and break the cycle accordingly.

### 4.2 Assign columns

- `column(comp)` = longest directed path length from any input net to that component.
- Components with no input path are placed in a center column between inputs and outputs.
- Convert column index to x-coordinate using `input_x_mm + column * column_step_mm`.

### 4.3 Assign rows

- Process columns left-to-right.
- For each column, sort components by the median y of already-placed pins they connect to.
- Tie-break by component reference (stable ordering).
- Convert row index to y-coordinate using a vertical center line; all coordinates are snapped to `grid.size_mm`.

### 4.4 Place orthogonal wires

- For each net, collect all absolute pin positions.
- Build a rectilinear Steiner tree with a simple greedy algorithm:
  1. Pick the leftmost source pin as root.
  2. Connect nearest remaining pin with an L-shape (one horizontal + one vertical segment).
  3. Repeat until all pins are connected.
- All segments must be axis-aligned (0°/90°).
- Add junctions at every T-shaped branch.

### 4.5 Place labels

1. Compute absolute position for each reference/value label using component position + configured offset.
2. Ensure all label angles are 0°.
3. Detect bounding-box overlaps.
4. If overlap occurs, shift the lower label downward by `char_unit_mm` until no overlap remains.

## 5. Error Handling

All layout errors inherit from `LayoutError`.

| Edge case | Handling |
|-----------|----------|
| No inputs/outputs matched in config | Raise `LayoutConfigError` with unmatched net names and IR candidates. |
| Symbol missing from JSON config | Use rotation 0° and log a warning; do not fail. |
| Component in a feedback cycle | Place by dominant signal direction; emit a warning. |
| Single-pin / dangling net | Add a no-connect marker or warning; skip wire placement. |
| Page overflow | Raise `LayoutPageOverflowError` with suggested page size/scale. |
| Unresolvable label overlap | Raise `LayoutOverlapError` with conflicting labels and positions. |
| Multi-unit symbol | Treat each unit as a separate component; pin directions come from config per unit type. |

## 6. Testing Strategy

### Unit tests (`tests/layout/`)

- `test_config.py`: load/validate default JSON, detect missing required fields.
- `test_signal_graph.py`: build graph from a tiny IR and verify column assignments.
- `test_placer.py`: assert component positions are grid-aligned and inputs are left of outputs.
- `test_wire_placer.py`: assert all wire segments are axis-aligned and junctions exist at T-nodes.
- `test_label_placer.py`: assert no label bounding boxes overlap.

### Integration test

- Feed the LM321 CCS circuit IR through `layout_schematic()` and verify the output `.kicad_sch` opens in KiCad without layout-related ERC errors.

### Snapshot test

- Save expected component positions and wire endpoints for the LM321 CCS case; fail on unexpected drift.

### Manual verification

- Open the generated `.kicad_sch` in KiCad to confirm the schematic is human-readable.

## 7. Success Criteria

- `layout_schematic()` runs successfully on the LM321 CCS circuit.
- Generated schematic uses only 0°/90° wire segments.
- Reference/value labels do not overlap.
- Input nets appear on the left, output nets on the right.
- Configuration is fully editable via JSON per layout unit.
- All tests pass.

## 8. Out of Scope

- Auto-routing that minimizes crossings globally (this is layout, not detailed routing).
- Aesthetic component grouping beyond signal-flow columns.
- Automatic unit decomposition heuristics for arbitrary circuits (first version uses existing `FunctionalBlock`s).
- Interactive/manual adjustment UI.
