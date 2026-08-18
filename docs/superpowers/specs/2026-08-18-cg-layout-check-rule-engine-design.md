# cg layout-check Rule Engine Design

**Date:** 2026-08-18
**Status:** Draft (awaiting user review)
**Source rules:** `lm321_ccs/布局.md` (8 sections; MVP covers §1, §3, §5, §6)
**Related:**
- `2026-07-11-schematic-layout-design.md` — placement *generator* (`circuit_graph.layout`, JSON config). Complementary: that one *writes* positions, this one *validates* them.
- `lm321_ccs/convert_to_kicad.py`, `circuit_graph.parsers.*`

## 1. Goal

Provide a strictly-enforced layout-rule engine for schematics, runnable as a `cg` CLI subcommand. Rules live in a user-editable `.schlayout` YAML file; the engine is **declarative + parameterized**, not embedded-code.

Strict means **binary pass/fail**: every rule has severity `error`, any violation causes exit code `1`. There are no warning/info levels in MVP. Selectively disabling a rule is done with `enabled: false` in the rule file (not via severity).

The MVP covers four of the eight sections of `布局.md`:
- **§1 信号流向** — `signal-flow-axis`
- **§3 电源/地线布局** — `power-at-top`, `gnd-at-bottom`, `power-layer-hierarchy`, `single-star-point`
- **§5 元件排列对齐** — `same-orientation`, `grid-alignment`, `decoup-near-ic-power-pin`
- **§6 标注可读性** — `designator-present`, `value-present`, `signal-name-on-net`

Sections §2 (功能模块化分区), §4 (走线优化), §7 (层次化分页), §8 (复用/标准化) are out of scope for v1.

### 1.1 Input format matrix

Layout rules require **spatial data** (position, rotation, sheet, page, wires) that not all schematic formats carry. The MVP supports two input formats at different rule coverage:

| Input format | Applicable rules | Why |
|---|---|---|
| `.NET` (Protel netlist) | §6 only: `designator-present`, `value-present`, `signal-name-on-net` | `.NET` is a pure logical netlist — no coordinates, no rotations, no wires |
| `.kicad_sch` | All 12 rules | KiCad schematics carry full geometry (position, rotation, wire segments, hierarchical sheets) |

Spatial rules in `.schlayout` are **auto-skipped** when the input format lacks the required data; the rule is counted in `summary.rules_skipped_unsupported_input`. The CLI exits 1 only if any *applicable* rule has a violation.

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│ CLI: cg layout-check <sch> [--rules R.schlayout]         │
├──────────────────────────────────────────────────────────┤
│ LayoutChecker           # orchestrator                    │
│   ├── load_rules()      # YAML → RuleSpec[]               │
│   ├── parse_schematic() # dispatch by extension           │
│   │     ├── .NET   → parsers.protel → LayoutSchematic    │
│   │     └── .kicad_sch → parsers.kicad_sch → LayoutSchematic │
│   ├── evaluate()        # RuleSpec × LayoutSchematic → Violation[] │
│   └── report()          # Violation[] → JSON              │
├──────────────────────────────────────────────────────────┤
│ Assertions (12 built-in kinds, one class each)            │
│   power.py        power-at-top, gnd-at-bottom,            │
│                   power-layer-hierarchy                   │
│   ground.py       single-star-point                       │
│   alignment.py    same-orientation, grid-alignment        │
│   decoupling.py   decoup-near-ic-power-pin                │
│   annotation.py   designator-present, value-present,      │
│                   signal-name-on-net                      │
│   flow.py         signal-flow-axis                        │
│   routing.py      no-trace-through-component (best-effort)│
│   base.py         Assertion ABC + Region dataclass        │
├──────────────────────────────────────────────────────────┤
│ Schematic Model (LAYOUT-GEOMETRY-AWARE, NEW)               │
│   components[].{id, type, value, position_mm,             │
│                  rotation_deg, refdes_prefix, sheet, page}│
│   nets[].{name, kind, members[]}                          │
│   wires[].{start_mm, end_mm, net}                         │ (kicad only)
└──────────────────────────────────────────────────────────┘
```

### New files

```
circuit_graph/
  layout_check/
    __init__.py
    checker.py            # LayoutChecker orchestrator
    loader.py             # .schlayout → RuleSpec[], schema errors
    model.py              # RuleSpec / Violation / Region / LayoutSchematic dataclasses
    assertions/
        __init__.py
        base.py           # Assertion ABC + spatial helpers
        power.py        ground.py        alignment.py        decoupling.py        annotation.py        flow.py        routing.py

  parsers/
    kicad_sch.py          # NEW: extracts geometry-preserving IR from .kicad_sch
                            # wraps kicad-sch-api (already a main dep) and
                            # circuit_graph.layout.geometry helpers

fixtures/
  rules/
    default.schlayout     # shipped default rule set
                            # contains all 12 rules; spatial rules auto-skip on .NET

tests/
  layout_check/
    conftest.py           # fixtures: minimal .NET and .kicad_sch synthetics
    test_loader.py
    test_power.py
    test_ground.py
    test_alignment.py
    test_decoupling.py
    test_annotation.py
    test_flow.py
    test_routing.py
    test_checker.py
    test_cli.py

  parsers/
    test_kicad_sch.py     # parses real lm321_ccs schematic + synthetic edge cases
```

### Why a separate package, not `circuit_graph.layout.*`

The 2026-07-11 spec defined `circuit_graph.layout` as the **placement generator** that *writes* component positions. This spec defines `circuit_graph.layout_check` that *validates* positions. Sharing a package would force one to import the other; the orchestrator independence is cleaner.

### Why a new parser, not extend `parsers/protel.py`

The existing `parsers/protel.py` strips position metadata because the current `CircuitIR` does not model geometry. Reusing it would mean augmenting `CircuitIR` (large blast radius: every exporter, every primitive recognizer, every simulator depends on `CircuitIR`). The layout-check pipeline uses its own `LayoutSchematic` model with geometry fields, and `parsers/kicad_sch.py` is a new sibling that emits geometry directly. `parsers/protel.py` stays untouched.

### Why not augment `CircuitIR`

The `circuit_graph.ir.Component` dataclass is the canonical model consumed by `primitives`, `erc`, `sim`, `generators`, `exporters`, and `visualization`. Adding optional `position_mm` / `rotation_deg` / `sheet` fields would either (a) require every downstream consumer to ignore unknown fields (risky) or (b) cascade changes through the whole package. The layout-check side-car with its own model is the lowest-risk path to deliver the MVP.

## 3. CLI Surface

```
Usage: cg layout-check [OPTIONS] SCHEMATIC...

  Run declarative layout rules against schematic file(s).

Arguments:
  SCHEMATIC... One or more schematic files (.NET, .kicad_sch).

Options:
  --rules PATH     Path to .schlayout rules file
                   (default: <repo>/fixtures/rules/default.schlayout)
  --compact        Emit JSON on a single line
  --baseline PATH  Write baseline violation snapshot to PATH
  --diff-baseline PATH
                   Fail only on violations NOT present in baseline
  -v, --verbose    Print per-rule PASS/SKIP summary to stderr
  --help           Show this message and exit.

Exit codes:
  0  All rules passed
  1  One or more violations
  2  Configuration or parse error
```

### stdout / stderr split

| Stream | Contents |
|--------|----------|
| stdout | JSON report (default: pretty multi-line; `--compact`: single line) |
| stderr | Human-readable progress (`Checking rule X/Y...`), summary lines |
| Exit code | 0 / 1 / 2 as documented above |

This split lets CI/scripts consume only stdout + exit code; humans read stderr.

## 4. `.schlayout` DSL

### 4.1 Top-level schema

```yaml
schema_version: 1                 # required; current value: 1
schematic_globs:                  # optional; alternative to CLI arg
  - "fixtures/**/*.kicad_sch"
  - "fixtures/**/*.NET"

rules:
  - id: power-rail-on-top
    enabled: true                 # optional; default true
    applies_to:
      refdes_prefix: ["U*"]      # glob list; OR semantics
      type: ["regulator", "ldo"]
      net: ["5V*"]                # net-name glob
      kind: ["power"]             # cg net kind: power | ground | signal
    assert:
      kind: power-at-top          # one of 12 built-in kinds
      tolerance_mm: 5
```

### 4.2 Field contract

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | int | yes | Current `1`; mismatch → exit 2 |
| `schematic_globs` | list[str] | no | Top-level convenience for multi-file inputs |
| `rules` | list | yes | Non-empty in practice |
| `rules[].id` | str | yes | kebab-case, unique within file |
| `rules[].enabled` | bool | no | Default `true`; `false` skips with no violation |
| `rules[].applies_to` | object | no | Absent = applies to all |
| `rules[].applies_to.refdes_prefix` | list[glob] | no | Matches against `refdes_prefix` (leading alphabetic prefix of `id`, e.g. `R96` → `R`); `R*` matches `R96`, `R5` |
| `rules[].applies_to.type` | list[str] | no | Recognized type names |
| `rules[].applies_to.net` | list[glob] | no | Net-name glob |
| `rules[].applies_to.kind` | list[str] | no | Net kind: `power`, `ground`, `signal` |
| `rules[].assert.kind` | str | yes | One of 12 built-in kinds |
| `rules[].assert.<param>` | per-kind | per-kind | see §5 |

Glob syntax: `*` matches any string (including empty); `?` matches one char. Same semantics as Python `fnmatch`.

Glob patterns are matched against the **derived `refdes_prefix`** (the leading alphabetic run of the component `id`), not the full `id`. So `R*` matches `R96` (prefix `R`), and `R?6` matches `R96` while a full-id glob would too — this matches user intuition ("resistors are `R*`"). A user who really wants to match the full id can write a custom glob like `*-OUT`.

### 4.3 Filter semantics

All four `applies_to` keys are **AND**-ed within one rule and **OR**-ed across list elements. So `{refdes_prefix: ["R*"], type: ["resistor"]}` matches resistors named `R*`.

If a filter matches zero components, the rule produces zero violations (not an error). This lets users write conservative rules without failing on empty fixtures.

## 5. Built-in Assertion Kinds (12)

| `assert.kind` | Source § | Required params | Optional params | Semantics |
|---|---|---|---|---|
| `power-at-top` | §3 | `tolerance_mm` | `region_top_pct` (default 0.15) | All matched components whose connected net has `kind=power` must lie within `tolerance_mm` of the page top (KiCad: y ≤ tolerance_mm). |
| `gnd-at-bottom` | §3 | `tolerance_mm` | — | Mirror of `power-at-top` for `kind=ground` nets. |
| `power-layer-hierarchy` | §3 | `voltage_tolerance` | `forbidden_mixing` | Distinct voltage rails (parsed from net name suffix, e.g. `5V`, `3V3`) must not share a y-band; conflicting nets within `voltage_tolerance` mm raises. |
| `single-star-point` | §3 | `max_merge_distance_mm` | — | AGND/DGND nets must converge to one physical point within `max_merge_distance_mm`. |
| `same-orientation` | §5 | `tolerance_deg` (default 5) | — | All matched components must have the same rotation (in degrees, KiCad convention) within `tolerance_deg`. |
| `grid-alignment` | §5 | `grid_mm` | — | All component positions must snap to `grid_mm` increments. |
| `decoup-near-ic-power-pin` | §5 | `max_distance_mm` | `capacitor_refdes_prefix` (default `C*`) | Decoupling caps must lie within `max_distance_mm` of any IC VDD pin (matched by net `kind=power` plus the cap's other pin `kind=ground`). |
| `designator-present` | §6 | — | — | `Designator` field must be non-empty for every matched component. |
| `value-present` | §6 | `forbidden_default` (default `["0", "NC", "?", ""]`) | — | `Value` field must be non-empty and not in the forbidden list. |
| `signal-name-on-net` | §6 | `forbidden_default_names` (default `["Net*", "NetU?_?", "NetC?_?"]`) | — | Net names must not match any forbidden pattern. |
| `signal-flow-axis` | §1 | `input_refdes_prefix`, `output_refdes_prefix`, `tolerance_mm` | `axis` (default `x`) | Inputs must lie at smaller axis-coordinate than outputs, within `tolerance_mm`. |
| `no-trace-through-component` | §4 | `tolerance_mm` | — | (Best-effort, KiCad only) No wire segment may pass through any matched component bbox + `tolerance_mm`. Auto-skips on `.NET` input. |

### Type inference for `decoup-near-ic-power-pin`

A decoupling capacitor is identified by:
1. `refdes_prefix` ∈ `capacitor_refdes_prefix` (default `C*`)
2. `value` parses to ≤ 100 nF (heuristic threshold, configurable later)
3. One pin on `kind=power` net, other pin on `kind=ground` net

This avoids naming a new "decoupling_cap" type and reuses existing data.

## 6. Report JSON Schema

### 6.1 Pass/fail report (exit 0 or 1)

```json
{
  "$schema": "cg-layout-check-v1",
  "tool": {
    "name": "cg-layout-check",
    "version": "0.1.0"
  },
  "input": {
    "schematic": "fixtures/protel/FX-T268-V6-10米-5065.NET",
    "rules_file": "fixtures/rules/default.schlayout",
    "rules_file_sha256": "a1b2c3...",
    "schematic_globs": null
  },
  "summary": {
    "rules_total": 12,
    "rules_evaluated": 11,
    "rules_skipped_disabled": 1,
    "rules_skipped_unsupported_input": 0,
    "violations": 4,
    "checked_components": 87,
    "checked_nets": 23,
    "duration_ms": 142
  },
  "violations": [
    {
      "rule_id": "power-rail-on-top",
      "assert_kind": "power-at-top",
      "severity": "error",
      "message": "Power-rail component U7 at (45.2, 32.0)mm is below top tolerance (5mm from page top).",
      "location": {
        "component": "U7",
        "net": "5V2",
        "x_mm": 45.2,
        "y_mm": 32.0,
        "sheet": "/",
        "page": 1
      },
      "context": {
        "tolerance_mm": 5,
        "actual_distance_mm": 27.0,
        "matched_components": ["U7", "U3"]
      },
      "doc": "circuit-graph://rules#power-at-top"
    }
  ]
}
```

### 6.2 Error report (exit 2)

```json
{
  "$schema": "cg-layout-check-v1",
  "tool": { "name": "cg-layout-check", "version": "0.1.0" },
  "error": {
    "kind": "yaml_parse_error",
    "message": "rules[3].applies_to.type: expected list[str], got str",
    "location": "fixtures/rules/default.schlayout:23:7",
    "cause_path": "$.rules[3].applies_to.type"
  }
}
```

`error.kind` enumeration:

| kind | Trigger |
|---|---|
| `yaml_parse_error` | YAML syntax error |
| `schema_version_mismatch` | `schema_version` ≠ 1 |
| `unknown_assert_kind` | `assert.kind` not in 12 built-in kinds |
| `duplicate_rule_id` | Same `id` appears twice in one file |
| `schematic_parse_error` | Schematic file unreadable or invalid |
| `rules_file_not_found` | `--rules` path does not exist |

### 6.3 Field constraints

- `violations[].rule_id` — required, must match a `rules[].id` in the input
- `violations[].location.component` — required (or `location.net` for net-only rules)
- `violations[].location.x_mm` / `y_mm` — KiCad coords (origin = bottom-left, Y up)
- `violations[].context` — assertion-specific debug payload, machine-friendly
- `violations[].doc` — stable URI to the rule documentation (placeholder until docs system lands)

## 7. Error Handling

| Scenario | Behavior |
|---|---|
| `.schlayout` YAML parse failure | exit 2, JSON `error.kind=yaml_parse_error` |
| `schema_version` mismatch | exit 2, JSON `error.kind=schema_version_mismatch` |
| Unknown `assert.kind` | exit 2, JSON `error.kind=unknown_assert_kind` |
| Duplicate rule `id` | exit 2, JSON `error.kind=duplicate_rule_id` |
| `refdes_prefix` / `net` glob matches nothing | Rule produces 0 violations, no error |
| Schematic parse failure | exit 2, JSON `error.kind=schematic_parse_error` |
| ≥1 violation | exit 1, JSON `violations` populated |
| 0 violations | exit 0, JSON `violations: []` |
| Required assertion param missing | exit 2, JSON `error.kind=unknown_assert_kind` (or `yaml_parse_error` if loader catches it) |
| `.NET` input + `no-trace-through-component` | Rule auto-skipped, counted in `rules_skipped_unsupported_input` |

## 8. Testing Strategy

### 8.1 Unit tests per assertion

For each of the 12 assertion kinds, **≥3 test cases** in `tests/layout_check/test_<area>.py`:
1. **Pass case** — synthetic schematic where the rule holds (e.g., cap well within tolerance)
2. **Fail case** — minimal schematic where the rule fails, asserts violation message + location
3. **Boundary case** — exactly at the threshold (distance == `max_distance_mm`); the spec treats the threshold as **inclusive on the pass side** (`<= max` passes), so boundary tests assert pass

Boundary semantics (`<= tolerance_mm` passes; `> tolerance_mm` fails) is uniform across all 12 assertions and documented in the Assertion base class.

### 8.2 Loader tests (`test_loader.py`)

- Valid YAML → expected `RuleSpec[]`
- `schema_version` missing or wrong → `schema_version_mismatch`
- Duplicate `id` → `duplicate_rule_id`
- Unknown `assert.kind` → `unknown_assert_kind`
- `enabled: false` produces no violations
- Empty `rules:` list → exit 0, zero violations

### 8.3 Checker tests (`test_checker.py`)

- Empty rule set + empty schematic → exit 0
- Mixed pass/fail → exit 1 with correct count
- Glob filter matches zero components → rule skipped silently
- `no-trace-through-component` on `.NET` input → counted as skipped

### 8.4 CLI tests (`test_cli.py`)

- `cg layout-check --help` exits 0
- `--rules /nonexistent` exits 2 with `rules_file_not_found`
- `--baseline` writes a JSON snapshot
- `--diff-baseline` accepts previously-baselined violations without failing

### 8.5 Integration test on lm321_ccs

Run `cg layout-check lm321_ccs/1A_CCS_LM321_BJT.kicad_sch --rules fixtures/rules/default.schlayout` and record baseline violation count. This is a smoke test, not a pass/fail gate; the user is expected to fix violations or baseline them.

### 8.6 Snapshot for `--baseline` / `--diff-baseline`

`--baseline PATH` writes a sorted list of `{rule_id, location_signature}` tuples (one per current violation). `--diff-baseline PATH` reads that file and skips any violation whose signature is present. This lets teams adopt rules incrementally.

## 9. Success Criteria

- `cg layout-check --help` shows the documented interface.
- `cg layout-check <sch> --rules <file>` returns exit 0 / 1 / 2 per the contract.
- All 12 built-in assertions have unit coverage and integration tests pass.
- `default.schlayout` ships with the repo and runs against `lm321_ccs/1A_CCS_LM321_BJT.kicad_sch` without crashing.
- `--baseline` / `--diff-baseline` workflow is exercisable end-to-end.
- No regression in other `cg` subcommands.

## 10. Out of Scope (MVP)

- §2 功能模块化分区 — requires semantic block grouping
- §4 走线优化 (other than `no-trace-through-component`) — `.NET` lacks wire data; full KiCad-only rules deferred
- §7 层次化分页 — multi-sheet handling deferred
- §8 复用/标准化 — not machine-checkable from schematic
- `warning` / `info` severity levels — MVP is strict binary
- Component-tag system beyond glob matching
- Auto-fix / interactive remediation
- Hot-reload of `.schlayout` (file is parsed once per invocation)
- Localization of violation messages (English only in MVP)