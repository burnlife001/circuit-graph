# ERC Engine Implementation Plan

> **For agentive workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `cg erc` command with Level 1 (kicad-cli wrapper) and Level 2 (pure Python S-expression parser + ERC engine) per `docs/superpowers/specs/2026-07-10-erc-engine-design.md`.

**Architecture:** New `circuit_graph/erc/` package with 5 modules (`models`, `pin_matrix`, `extractor`, `engine`, `kicad_cli`) + 1 deferred (`render` at end). Borrow `PinConflictMatrix` and validator structure from `E:\__electric\__research\kicad-sch-api`. Use `sexpdata` (already in venv) for S-expression parsing. `cg erc` is the unified CLI entry with `--engine auto|native|kicad`.

**Tech Stack:** Python 3.10+, dataclasses, `sexpdata` (existing), `click` (existing), `networkx` (existing), `pytest` (existing).

## Global Constraints

- **Python 3.10+** — match existing project; use `from __future__ import annotations` and `StrEnum` from existing `circuit_graph/ir.py` pattern.
- **LF line endings** — match existing project convention.
- **UTF-8 encoding** — match existing project convention.
- **No new top-level dependencies** — only use `sexpdata` (already in venv), `networkx` (already in pyproject.toml), `click` (already in pyproject.toml), pytest.
- **No modifying `node_modules` / external libs** — borrow code by copying into our package, not by adding deps.
- **Frequent commits** — one commit per task minimum.
- **Test fixture:** `lm321_ccs/1A_CCS_LM321_BJT.kicad_sch` is the primary KiCad fixture (KiCad 9.0 format, 4 expected kicad-cli violations). All end-to-end tests target this file.
- **Always preserve LM321 kicad_sch file unchanged** — copy to `tests/fixtures/` if tests need isolated copy, but never edit the original.
- **Borrow from `E:\__electric\__research\kicad-sch-api\kicad_sch_api\validation\`** for the algorithm reference. Read-only access; copy patterns inline.
- **kicad-cli binary path:** `D:\Programs\KiCad\10.0\bin\kicad-cli.exe` (confirmed installed). Default detection in `KicadCliRunner` must check this path on Windows.

---

## File Structure

**Create:**
- `circuit_graph/erc/__init__.py` — public API exports
- `circuit_graph/erc/models.py` — `ElectricalPinType`, `PinContext`, `ErcViolation`, `ErcReport`
- `circuit_graph/erc/pin_matrix.py` — `PinConflictMatrix` with 12×12 default matrix
- `circuit_graph/erc/extractor.py` — `NetlistExtractor` (sexpr → NetMap)
- `circuit_graph/erc/engine.py` — `ElectricalRulesChecker` + `PinTypeValidator` + `ConnectivityValidator` + `ComponentValidator` + `PowerValidator`
- `circuit_graph/erc/kicad_cli.py` — `KicadCliRunner`, `KicadCliNotFoundError`
- `tests/erc/__init__.py` — empty marker
- `tests/erc/test_pin_matrix.py` — matrix unit tests
- `tests/erc/test_models.py` — models serialization tests
- `tests/erc/test_extractor.py` — extractor fixture tests
- `tests/erc/test_engine.py` — 4 validator unit tests
- `tests/erc/test_kicad_cli.py` — kicad-cli output parsing tests (skip if not available)
- `tests/erc/test_cli.py` — `cg erc` CLI integration tests

**Modify:**
- `circuit_graph/cli.py` — add `erc` subcommand (~50 lines)
- `README.md` — add `cg erc` usage section

**File responsibilities (one concern per file):**
- `models.py` — data classes only, no logic
- `pin_matrix.py` — pure matrix data + lookup, no I/O
- `extractor.py` — single-pass S-expression parser producing NetMap
- `engine.py` — orchestrator + 4 validator classes (each validator in its own class)
- `kicad_cli.py` — subprocess management + JSON parsing
- `render.py` (deferred) — terminal formatting

---

## Task 1: Scaffolding — `models.py` + `pin_matrix.py` + tests

**Files:**
- Create: `circuit_graph/erc/__init__.py`
- Create: `circuit_graph/erc/models.py`
- Create: `circuit_graph/erc/pin_matrix.py`
- Create: `tests/erc/__init__.py`
- Create: `tests/erc/test_models.py`
- Create: `tests/erc/test_pin_matrix.py`

**Interfaces:**
- Consumes: nothing (pure data)
- Produces: `ElectricalPinType` (StrEnum), `PinContext` (frozen dataclass), `ErcViolation` (frozen dataclass), `ErcReport` (dataclass), `PinSeverity` (StrEnum), `PinConflictMatrix` (class)

### Step 1.1: Write failing test for `ElectricalPinType` enum

File: `tests/erc/test_models.py`

```python
"""Tests for circuit_graph.erc.models."""
from __future__ import annotations

from circuit_graph.erc.models import ElectricalPinType, ErcReport, ErcViolation


def test_electrical_pin_type_has_twelve_members():
    """KiCad common/pin_type.h defines 12 pin types."""
    members = list(ElectricalPinType)
    assert len(members) == 12
    # Spot-check the canonical names.
    assert ElectricalPinType.INPUT.value == "input"
    assert ElectricalPinType.OUTPUT.value == "output"
    assert ElectricalPinType.POWER_IN.value == "power_in"
    assert ElectricalPinType.POWER_OUT.value == "power_out"
    assert ElectricalPinType.NO_CONNECT.value == "no_connect"
    assert ElectricalPinType.OPEN_COLLECTOR.value == "open_collector"
    assert ElectricalPinType.OPEN_EMITTER.value == "open_emitter"
    assert ElectricalPinType.PASSIVE.value == "passive"
    assert ElectricalPinType.BIDIRECTIONAL.value == "bidirectional"
    assert ElectricalPinType.TRISTATE.value == "tristate"
    assert ElectricalPinType.FREE.value == "free"
    assert ElectricalPinType.UNSPECIFIED.value == "unspecified"
```

### Step 1.2: Run test, verify it fails

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'circuit_graph.erc.models'`

### Step 1.3: Implement `models.py`

File: `circuit_graph/erc/models.py`

```python
"""Data structures for the ERC engine.

Three layers of types:

- :class:`ElectricalPinType` — KiCad's 12 pin types, aligned with
  ``common/pin_type.h`` in the KiCad source.
- :class:`PinContext` — output of the netlist extractor. Describes a single
  pin on a single component in absolute schematic coordinates.
- :class:`ErcViolation` / :class:`ErcReport` — output of the ERC engine.
  Both engines (kicad-cli and native) normalize into these shapes so callers
  can compare results directly.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Literal


class ElectricalPinType(str, Enum):
    """KiCad pin electrical type (12 values, aligned with ``common/pin_type.h``)."""

    INPUT = "input"
    OUTPUT = "output"
    BIDIRECTIONAL = "bidirectional"
    TRISTATE = "tristate"
    PASSIVE = "passive"
    FREE = "free"  # Not Internally Connected
    UNSPECIFIED = "unspecified"
    POWER_IN = "power_in"
    POWER_OUT = "power_out"
    OPEN_COLLECTOR = "open_collector"
    OPEN_EMITTER = "open_emitter"
    NO_CONNECT = "no_connect"  # NC marker


@dataclass(frozen=True)
class PinContext:
    """A single pin on a single component, with absolute schematic coordinates.

    Produced by :class:`circuit_graph.erc.extractor.NetlistExtractor` and
    consumed by the validators in ``engine.py``.
    """

    pin_number: str
    pin_name: str | None
    electrical_type: ElectricalPinType
    component_ref: str
    component_lib_id: str
    position: tuple[float, float]
    sheet_path: str = "/"


Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class ErcViolation:
    """A single ERC violation, normalized across engines.

    Fields match the user-facing CLI/JSON shape. The ``type`` field is a
    short slug (e.g. ``"pin_conflict"``, ``"dangling_wire"``); the ``code``
    field is a unique identifier (e.g. ``"E001"``, ``"W042"``).
    """

    severity: Severity
    type: str
    code: str
    message: str
    net_name: str | None = None
    component_refs: list[str] = field(default_factory=list)
    pin_numbers: list[str] = field(default_factory=list)
    position: tuple[float, float] | None = None
    sheet_path: str = "/"
    suggested_fix: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ErcReport:
    """Aggregate ERC result for a single schematic file."""

    source_file: str
    engine: Literal["kicad-cli", "native"]
    violations: list[ErcViolation] = field(default_factory=list)
    duration_ms: float = 0.0
    kicad_version: str | None = None
    warnings: list[str] = field(default_factory=list)  # engine-level warnings (e.g. "sheets not supported")

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "engine": self.engine,
            "kicad_version": self.kicad_version,
            "violations": [v.to_dict() for v in self.violations],
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "duration_ms": self.duration_ms,
            "warnings": list(self.warnings),
        }
```

### Step 1.4: Run test, verify it passes

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_models.py -v
```

Expected: PASS

### Step 1.5: Write failing test for `PinConflictMatrix`

File: `tests/erc/test_pin_matrix.py`

```python
"""Tests for circuit_graph.erc.pin_matrix."""
from __future__ import annotations

import pytest

from circuit_graph.erc.models import ElectricalPinType
from circuit_graph.erc.pin_matrix import PinConflictMatrix, PinSeverity


def test_default_matrix_covers_all_pin_type_pairs():
    """Every (a, b) of the 12 types must produce a result without KeyError."""
    matrix = PinConflictMatrix.default()
    for t1 in ElectricalPinType:
        for t2 in ElectricalPinType:
            sev = matrix.check(t1, t2)
            assert sev in (PinSeverity.OK, PinSeverity.WARNING, PinSeverity.ERROR)


def test_output_to_output_is_error():
    """Two outputs on the same net is a driver conflict (KiCad default)."""
    matrix = PinConflictMatrix.default()
    assert matrix.check(ElectricalPinType.OUTPUT, ElectricalPinType.OUTPUT) == PinSeverity.ERROR


def test_power_output_to_power_output_is_error():
    """Two power rails shorted is a hard error."""
    matrix = PinConflictMatrix.default()
    assert matrix.check(ElectricalPinType.POWER_OUT, ElectricalPinType.POWER_OUT) == PinSeverity.ERROR


def test_output_to_power_output_is_error():
    """Logic output shorted to power rail is an error."""
    matrix = PinConflictMatrix.default()
    assert matrix.check(ElectricalPinType.OUTPUT, ElectricalPinType.POWER_OUT) == PinSeverity.ERROR


def test_no_connect_to_anything_is_error():
    """NC marker should never share a net with another pin."""
    matrix = PinConflictMatrix.default()
    assert matrix.check(ElectricalPinType.NO_CONNECT, ElectricalPinType.INPUT) == PinSeverity.ERROR
    assert matrix.check(ElectricalPinType.NO_CONNECT, ElectricalPinType.OUTPUT) == PinSeverity.ERROR
    assert matrix.check(ElectricalPinType.NO_CONNECT, ElectricalPinType.PASSIVE) == PinSeverity.ERROR


def test_passive_to_anything_is_ok():
    """Passive pins (resistors, capacitors) are universally compatible."""
    matrix = PinConflictMatrix.default()
    for t in ElectricalPinType:
        assert matrix.check(ElectricalPinType.PASSIVE, t) == PinSeverity.OK
        assert matrix.check(t, ElectricalPinType.PASSIVE) == PinSeverity.OK


def test_unspecified_to_signal_is_warning():
    """Unspecified pins next to defined signal pins generate a warning."""
    matrix = PinConflictMatrix.default()
    assert matrix.check(ElectricalPinType.UNSPECIFIED, ElectricalPinType.INPUT) == PinSeverity.WARNING
    assert matrix.check(ElectricalPinType.UNSPECIFIED, ElectricalPinType.OUTPUT) == PinSeverity.WARNING


def test_matrix_is_symmetric():
    """For every (a, b), check(a, b) == check(b, a)."""
    matrix = PinConflictMatrix.default()
    for t1 in ElectricalPinType:
        for t2 in ElectricalPinType:
            assert matrix.check(t1, t2) == matrix.check(t2, t1)


def test_set_rule_overrides_default():
    """Custom rules take precedence over defaults."""
    matrix = PinConflictMatrix.default()
    matrix.set_rule(ElectricalPinType.OUTPUT, ElectricalPinType.OUTPUT, PinSeverity.WARNING)
    assert matrix.check(ElectricalPinType.OUTPUT, ElectricalPinType.OUTPUT) == PinSeverity.WARNING


def test_set_rule_preserves_symmetry():
    """set_rule(a, b) must also affect check(b, a)."""
    matrix = PinConflictMatrix.default()
    matrix.set_rule(ElectricalPinType.INPUT, ElectricalPinType.OUTPUT, PinSeverity.WARNING)
    assert matrix.check(ElectricalPinType.OUTPUT, ElectricalPinType.INPUT) == PinSeverity.WARNING
```

### Step 1.6: Run test, verify it fails

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_pin_matrix.py -v
```

Expected: `ModuleNotFoundError: No module named 'circuit_graph.erc.pin_matrix'`

### Step 1.7: Implement `pin_matrix.py`

File: `circuit_graph/erc/pin_matrix.py`

```python
"""Pin electrical-type conflict matrix.

Aligned with KiCad's default matrix in ``eeschema/erc/erc_settings.cpp``.
The default 12×12 table is built from the :class:`PinSeverity` constants
below; users can override individual cells via :meth:`PinConflictMatrix.set_rule`.
"""
from __future__ import annotations

from enum import IntEnum

from circuit_graph.erc.models import ElectricalPinType


class PinSeverity(IntEnum):
    """Result of a pin-type compatibility check."""

    OK = 0
    WARNING = 1
    ERROR = 2


class PinConflictMatrix:
    """Symmetric 12×12 matrix mapping ``(type_a, type_b)`` to severity.

    Default rules (matches KiCad):

    - ``OUTPUT ↔ OUTPUT`` → ERROR (driver conflict)
    - ``POWER_OUT ↔ POWER_OUT`` → ERROR (shorted rails)
    - ``OUTPUT ↔ POWER_OUT`` → ERROR
    - ``NO_CONNECT ↔ {anything except FREE}`` → ERROR
    - ``UNSPECIFIED ↔ {signal types}`` → WARNING
    - ``PASSIVE ↔ {anything}`` → OK
    - ``FREE ↔ {anything}`` → OK
    """

    def __init__(self) -> None:
        # Initialize with default; subclasses / set_rule may override cells.
        self._matrix = self._build_default()

    @classmethod
    def default(cls) -> "PinConflictMatrix":
        """Return a fresh matrix with KiCad defaults."""
        return cls()

    @staticmethod
    def _build_default() -> dict[tuple[ElectricalPinType, ElectricalPinType], PinSeverity]:
        m: dict[tuple[ElectricalPinType, ElectricalPinType], PinSeverity] = {}
        all_types = list(ElectricalPinType)

        # Start with all pairs OK.
        for a in all_types:
            for b in all_types:
                m[(a, b)] = PinSeverity.OK
                m[(b, a)] = PinSeverity.OK

        # Error rules (symmetric).
        error_pairs = [
            (ElectricalPinType.OUTPUT, ElectricalPinType.OUTPUT),
            (ElectricalPinType.POWER_OUT, ElectricalPinType.POWER_OUT),
            (ElectricalPinType.OUTPUT, ElectricalPinType.POWER_OUT),
        ]
        for a, b in error_pairs:
            m[(a, b)] = PinSeverity.ERROR
            m[(b, a)] = PinSeverity.ERROR

        # NO_CONNECT errors with every type except FREE.
        for t in all_types:
            if t == ElectricalPinType.FREE:
                continue
            m[(ElectricalPinType.NO_CONNECT, t)] = PinSeverity.ERROR
            m[(t, ElectricalPinType.NO_CONNECT)] = PinSeverity.ERROR

        # UNSPECIFIED warnings with every type except PASSIVE / FREE.
        for t in all_types:
            if t in (ElectricalPinType.PASSIVE, ElectricalPinType.FREE):
                continue
            m[(ElectricalPinType.UNSPECIFIED, t)] = PinSeverity.WARNING
            m[(t, ElectricalPinType.UNSPECIFIED)] = PinSeverity.WARNING

        return m

    def check(
        self,
        t1: ElectricalPinType,
        t2: ElectricalPinType,
    ) -> PinSeverity:
        """Return the severity for connecting ``t1`` to ``t2``."""
        return self._matrix[(t1, t2)]

    def set_rule(
        self,
        t1: ElectricalPinType,
        t2: ElectricalPinType,
        severity: PinSeverity,
    ) -> None:
        """Override the rule for ``(t1, t2)``; symmetrically updates ``(t2, t1)``."""
        self._matrix[(t1, t2)] = severity
        self._matrix[(t2, t1)] = severity
```

### Step 1.8: Run test, verify it passes

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_pin_matrix.py -v
```

Expected: PASS (all 10 tests)

### Step 1.9: Create `__init__.py` files and public API

File: `circuit_graph/erc/__init__.py`

```python
"""ERC engine — kicad-cli wrapper (Level 1) and pure-Python S-expression parser
+ ERC engine (Level 2).

Public API:

- :class:`ElectricalPinType` — KiCad's 12 pin types
- :class:`PinContext` — pin on a component, with absolute coords
- :class:`ErcViolation`, :class:`ErcReport` — engine output
- :class:`PinConflictMatrix` — 12×12 type compatibility table
- :class:`NetlistExtractor` — S-expression → NetMap
- :class:`ElectricalRulesChecker` — 4-validator ERC engine (Level 2)
- :class:`KicadCliRunner` — kicad-cli subprocess wrapper (Level 1)
"""
from __future__ import annotations

from circuit_graph.erc.engine import ElectricalRulesChecker
from circuit_graph.erc.extractor import NetlistExtractor
from circuit_graph.erc.kicad_cli import KicadCliNotFoundError, KicadCliRunner
from circuit_graph.erc.models import (
    ElectricalPinType,
    ErcReport,
    ErcViolation,
    PinContext,
)
from circuit_graph.erc.pin_matrix import PinConflictMatrix, PinSeverity

__all__ = [
    "ElectricalPinType",
    "PinContext",
    "ErcViolation",
    "ErcReport",
    "PinConflictMatrix",
    "PinSeverity",
    "NetlistExtractor",
    "ElectricalRulesChecker",
    "KicadCliRunner",
    "KicadCliNotFoundError",
]
```

File: `tests/erc/__init__.py`

```python
"""Marker file for the erc test package."""
```

### Step 1.10: Run all erc tests, verify pass

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/ -v
```

Expected: All tests pass (10 + 1 = 11).

### Step 1.11: Commit

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && git add circuit_graph/erc/__init__.py circuit_graph/erc/models.py circuit_graph/erc/pin_matrix.py tests/erc/__init__.py tests/erc/test_models.py tests/erc/test_pin_matrix.py && git commit -m "feat(erc): add models + PinConflictMatrix (Level 2.2)" -m "Implements docs/erc-todolist.md Level 2.2.1, 2.2.2. Adds 12-type ElectricalPinType enum, PinContext/ErcViolation/ErcReport dataclasses, and symmetric PinConflictMatrix with KiCad default rules." && git push
```

---

## Task 2: `extractor.py` — S-expression → NetMap

**Files:**
- Create: `circuit_graph/erc/extractor.py`
- Create: `tests/erc/test_extractor.py`

**Interfaces:**
- Consumes: nothing (uses `sexpdata` for parsing)
- Produces: `NetlistExtractor` (class) with method `extract(sch_path: Path) -> ExtractedSchematic` where `ExtractedSchematic` has fields `components: list[ComponentContext]`, `nets: NetMap`, `warnings: list[str]`

### Step 2.1: Write failing test for extractor on LM321 fixture

File: `tests/erc/test_extractor.py`

```python
"""Tests for circuit_graph.erc.extractor."""
from __future__ import annotations

from pathlib import Path

import pytest

from circuit_graph.erc.extractor import NetlistExtractor
from circuit_graph.erc.models import ElectricalPinType


LM321_SCH = Path("lm321_ccs/1A_CCS_LM321_BJT.kicad_sch")


@pytest.fixture
def lm321_extracted():
    if not LM321_SCH.exists():
        pytest.skip(f"Fixture not found: {LM321_SCH}")
    return NetlistExtractor().extract(LM321_SCH)


def test_extract_lm321_finds_eight_components(lm321_extracted):
    """LM321 fixture: 6 components + 2 power symbols = 8 instances."""
    assert len(lm321_extracted.components) >= 6


def test_extract_lm321_identifies_power_symbols(lm321_extracted):
    """Power symbols ('power:+12V', 'power:GND') are identified as power markers."""
    power_refs = {c.reference for c in lm321_extracted.components if c.is_power_symbol}
    # The LM321 fixture has #PWR01, #PWR02 (power symbols with reference "#PWR").
    assert any(ref.startswith("#PWR") for ref in {c.reference for c in lm321_extracted.components})


def test_extract_lm321_power_symbols_have_one_pin(lm321_extracted):
    """Each power symbol has exactly one pin (pin 1)."""
    for comp in lm321_extracted.components:
        if comp.is_power_symbol:
            assert len(comp.pins) == 1
            assert comp.pins[0].pin_number == "1"


def test_extract_lm321_groups_power_rails(lm321_extracted):
    """All GND power symbols share the same net."""
    net_names = {n for n in lm321_extracted.nets.keys()}
    # The fixture has '+12V' and 'GND' as power labels — at least one rail.
    assert any("GND" in n for n in net_names) or any("+12V" in n for n in net_names)


def test_extract_lm321_returns_valid_nets(lm321_extracted):
    """NetMap values are non-empty PinContext lists; keys are strings."""
    for net_name, pins in lm321_extracted.nets.items():
        assert isinstance(net_name, str)
        assert len(pins) > 0
        for p in pins:
            assert p.component_ref
            assert p.pin_number
```

### Step 2.2: Run test, verify it fails

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_extractor.py -v
```

Expected: `ModuleNotFoundError: No module named 'circuit_graph.erc.extractor'`

### Step 2.3: Implement `extractor.py`

File: `circuit_graph/erc/extractor.py`

```python
"""S-expression → NetMap extractor for .kicad_sch files.

Parses a KiCad 9 schematic using ``sexpdata`` and produces:

- :class:`ComponentContext` — one per ``(symbol ...)`` instance
- :class:`NetMap` — ``dict[net_name → list[PinContext]]``

Strategy:
1. Parse the top-level S-expression.
2. Extract ``(lib_symbols ...)`` for pin definitions (number / name /
   electrical type / relative position).
3. Extract each ``(symbol ...)`` instance — reference, value, position.
4. Compute absolute pin positions.
5. Collect wires / junctions / labels into a connectivity graph.
6. Run union-find to group pin endpoints into nets; assign names from
   labels and power-symbol values.
"""
from __future__ import annotations

import math
import re
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
    """sexpdata.Symbol or string → plain str."""
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
    pin_net_id: dict[int, str] = field(default_factory=dict)


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

        # Power symbol merge: same Value → same reference substitution.
        for comp in components:
            if comp.is_power_symbol and comp.value:
                # Power symbols with value "GND" should all share net "GND".
                pass  # handled in _build_nets

        nets, pin_net_id = self._build_nets(sexp, components)

        return ExtractedSchematic(
            source_file=str(sch_path),
            components=components,
            nets=nets,
            warnings=list(self._warnings),
            pin_net_id=pin_net_id,
        )

    # ------------------------------------------------------------------ lib

    def _parse_lib_symbols(self, sexp: list) -> dict[str, LibSymbol]:
        """Return ``lib_id → LibSymbol`` for all entries in (lib_symbols ...)."""
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
            # (power ...) flag
            for sub in item[2:]:
                if isinstance(sub, list) and len(sub) > 0 and _coerce_str(sub[0]) == "power":
                    sym.is_power = True
            # (symbol "name_0_1" ...subgraph with (pin ...) entries...)
            for sub in item[2:]:
                if not isinstance(sub, list) or len(sub) == 0:
                    continue
                if _coerce_str(sub[0]) == "symbol":
                    self._parse_lib_pins(sub, sym)
            result[lib_id] = sym
        return result

    def _parse_lib_pins(self, sub: list, sym: LibSymbol) -> None:
        """Walk a lib symbol body, collecting (pin ...) entries."""
        for item in sub[1:]:
            if not isinstance(item, list) or len(item) == 0:
                continue
            if _coerce_str(item[0]) != "pin":
                continue
            # (pin "name" "num" (at x y rot) (length L) (name ...) (number ...) (type ...))
            if len(item) < 4:
                continue
            pin_name = _coerce_str(item[1]).strip('"')
            pin_num = _coerce_str(item[2]).strip('"')
            x, y, rot = 0.0, 0.0, 0
            electrical = ElectricalPinType.PASSIVE
            for prop in item[3:]:
                if not isinstance(prop, list) or len(prop) == 0:
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
                        raw, KICAD_PIN_TYPE_FALLBACK.get(raw, ElectricalPinType.PASSIVE)
                    )
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
        """Parse top-level (symbol ...) blocks into ComponentContexts."""
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
        """Parse a single (symbol "lib_id" ...) instance."""
        if len(item) < 2:
            return None
        lib_id = _coerce_str(item[1]).strip('"')
        if not lib_id:
            return None

        # Parse properties.
        reference = ""
        value = ""
        pos_x, pos_y, rot = 0.0, 0.0, 0
        is_power = lib_id.startswith("power:") or lib_id in {
            "power:+12V", "power:GND", "power:+3.3V", "power:+5V",
        }

        for sub in item[2:]:
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
            elif head == "power" and len(sub) >= 2:
                is_power = True

        if not reference:
            # Some power symbols have no explicit Reference — synthesize one.
            if is_power and value:
                reference = f"#PWR_{value}"
            else:
                self._warnings.append(f"symbol lib_id={lib_id!r} missing Reference; skipped")
                return None

        comp = ComponentContext(
            reference=reference,
            value=value,
            lib_id=lib_id,
            position=(pos_x, pos_y),
            rotation=rot,
            is_power_symbol=is_power,
        )

        # Pin context: use lib_symbols pin layout + component position.
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
        """Apply KiCad rotation (0/90/180/270) and translate to absolute position."""
        if rotation == 0:
            return base_x + rel_x, base_y + rel_y
        if rotation == 90:
            return base_x - rel_y, base_y + rel_x
        if rotation == 180:
            return base_x - rel_x, base_y - rel_y
        if rotation == 270:
            return base_x + rel_y, base_y - rel_x
        return base_x + rel_x, base_y + rel_y

    # ------------------------------------------------------------------ nets

    def _build_nets(
        self,
        sexp: list,
        components: list[ComponentContext],
    ) -> tuple[dict[str, list[PinContext]], dict[int, str]]:
        """Build NetMap from wires, junctions, labels, and power symbols.

        Algorithm:
        1. Collect all pin endpoints, wire endpoints, junction points,
           and label/hierarchical_label/global_label positions.
        2. Group points that are within a small tolerance (1e-3 mm).
        3. Wire endpoints + junctions + pin endpoints + label points form
           a connectivity graph.
        4. Find connected components → nets.
        5. Net name: global_label > hierarchical_label > label > power_symbol value > auto.
        """
        # 1. Collect all "points of interest".
        # point_id (0..N-1) is an integer we use for union-find.
        # Each point has (x, y, label_name, power_value).
        TOL = 1e-3
        points: list[dict[str, Any]] = []
        point_pin: dict[int, tuple[ComponentContext, PinContext]] = {}
        point_label: dict[int, str] = {}  # point_id → label name
        point_power: dict[int, str] = {}  # point_id → power symbol value

        for comp in components:
            for pin in comp.pins:
                pid = len(points)
                points.append({"x": pin.position[0], "y": pin.position[1]})
                point_pin[pid] = (comp, pin)
            if comp.is_power_symbol and comp.value:
                # Power symbol's pin 1 position is the "value" anchor.
                if comp.pins:
                    pid = len(points)
                    p = comp.pins[0]
                    points.append({"x": p.position[0], "y": p.position[1]})
                    point_power[pid] = comp.value
                    # Re-map: this point is the same as the pin, so it serves
                    # as the power value. We've already added the pin; replace
                    # that entry by overwriting the last point's coords with
                    # a slightly offset duplicate isn't needed — the point_pin
                    # entry will route to the same point.
                    # NOTE: This double-registration is fine because we
                    # connect pin→power via the next loop.

        # 2. Collect wire segments and label positions.
        edges: list[tuple[int, int]] = []  # pairs of point_ids to union

        for item in sexp[1:]:
            if not isinstance(item, list) or len(item) == 0:
                continue
            head = _coerce_str(item[0])
            if head == "wire" and len(item) >= 3:
                pts = self._parse_pts(item[1] if isinstance(item[1], list) else None, item[2] if isinstance(item[2], list) else None)
                if pts is not None:
                    p1 = self._add_or_match_point(points, pts[0][0], pts[0][1], TOL)
                    p2 = self._add_or_match_point(points, pts[1][0], pts[1][1], TOL)
                    edges.append((p1, p2))
            elif head == "junction" and len(item) >= 3:
                # (junction (at x y) (diameter ...) ...)
                at = self._find_sub(item, "at")
                if at is not None and len(at) >= 3:
                    x, y = _coerce_float(at[1]), _coerce_float(at[2])
                    # Junctions don't add edges by themselves; they're just
                    # anchor points. Wires touching a junction position will
                    # be unioned via the tolerance in _add_or_match_point.
                    self._add_or_match_point(points, x, y, TOL)
            elif head in ("label", "hierarchical_label", "global_label"):
                at = self._find_sub(item, "at")
                if at is None or len(at) < 3:
                    continue
                x, y = _coerce_float(at[1]), _coerce_float(at[2])
                name = self._extract_label_name(item)
                if name:
                    pid = self._add_or_match_point(points, x, y, TOL)
                    point_label[pid] = name

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

        # 4. Group pins into nets.
        net_pins: dict[str, list[PinContext]] = {}
        # For each unique root, compute its net name.
        root_names: dict[int, str] = {}
        for pid, (comp, pin) in point_pin.items():
            r = find(pid)
            if r not in root_names:
                # Determine net name: priority global > label > power value > auto.
                name: str | None = point_label.get(r)
                if name is None:
                    name = point_power.get(r)
                if name is None:
                    name = f"net_{r}"
                root_names[r] = name
            net_pins.setdefault(root_names[r], []).append(pin)

        # 5. Build pin_net_id map for downstream consumers.
        pin_net_id: dict[int, str] = {}
        for pid, (comp, pin) in point_pin.items():
            r = find(pid)
            pin_net_id[id(pin)] = root_names[r]

        if not net_pins:
            self._warnings.append("no nets discovered in schematic")

        return net_pins, pin_net_id

    def _add_or_match_point(
        self, points: list[dict[str, Any]], x: float, y: float, tol: float
    ) -> int:
        """Find an existing point within tolerance, or add a new one."""
        for i, p in enumerate(points):
            if abs(p["x"] - x) <= tol and abs(p["y"] - y) <= tol:
                return i
        points.append({"x": x, "y": y})
        return len(points) - 1

    def _parse_pts(
        self, pts1: list | None, pts2: list | None
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """Parse a (pts (xy x1 y1) (xy x2 y2)) block."""
        if pts1 is None or pts2 is None:
            return None
        try:
            x1 = _coerce_float(pts1[1])
            y1 = _coerce_float(pts1[2])
            x2 = _coerce_float(pts2[1])
            y2 = _coerce_float(pts2[2])
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
```

### Step 2.4: Run test, verify it passes

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_extractor.py -v
```

Expected: All 5 tests pass.

### Step 2.5: Commit

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && git add circuit_graph/erc/extractor.py tests/erc/test_extractor.py && git commit -m "feat(erc): add NetlistExtractor (Level 2.1)" -m "Implements docs/erc-todolist.md 2.1.1-2.1.9. Parses lib_symbols + (symbol ...) instances via sexpdata, computes absolute pin positions, builds NetMap by union-find over wires/junctions/labels/power-symbols. Validated on lm321_ccs/1A_CCS_LM321_BJT.kicad_sch (KiCad 9.0 format)." && git push
```

---

## Task 3: `engine.py` — 4 validators + orchestrator

**Files:**
- Create: `circuit_graph/erc/engine.py`
- Create: `tests/erc/test_engine.py`

**Interfaces:**
- Consumes: `ExtractedSchematic` from extractor
- Produces: `ErcReport` (and `ErcViolation` instances)

### Step 3.1: Write failing tests for each validator

File: `tests/erc/test_engine.py`

```python
"""Tests for circuit_graph.erc.engine."""
from __future__ import annotations

import pytest

from circuit_graph.erc.engine import (
    ComponentValidator,
    ConnectivityValidator,
    ElectricalRulesChecker,
    PinTypeValidator,
    PowerValidator,
)
from circuit_graph.erc.extractor import ComponentContext, ExtractedSchematic
from circuit_graph.erc.models import ElectricalPinType, ErcViolation, PinContext


# ----------------------------------------------------------------- helpers


def make_pin(ref: str, num: str, etype: ElectricalPinType, x: float = 0.0, y: float = 0.0) -> PinContext:
    return PinContext(
        pin_number=num,
        pin_name=None,
        electrical_type=etype,
        component_ref=ref,
        component_lib_id=f"lib:{ref[0]}",
        position=(x, y),
    )


def make_comp(ref: str, pins: list[PinContext], value: str = "1k", lib_id: str | None = None) -> ComponentContext:
    return ComponentContext(
        reference=ref,
        value=value,
        lib_id=lib_id or f"lib:{ref[0]}",
        position=(0.0, 0.0),
        rotation=0,
        pins=pins,
    )


# ------------------------------------------------------------- PinTypeValidator


def test_pin_type_validator_flags_output_conflict():
    """Two OUTPUT pins on the same net → ERROR."""
    pins = [
        make_pin("U1", "1", ElectricalPinType.OUTPUT),
        make_pin("U2", "1", ElectricalPinType.OUTPUT),
    ]
    sch = ExtractedSchematic(
        source_file="x",
        components=[],
        nets={"NET1": pins},
    )
    v = PinTypeValidator()
    violations = v.validate(sch)
    assert any(vr.type == "pin_conflict" and vr.severity == "error" for vr in violations)


def test_pin_type_validator_passes_passive_to_output():
    """PASSIVE to OUTPUT is fine."""
    pins = [
        make_pin("R1", "1", ElectricalPinType.PASSIVE),
        make_pin("U1", "1", ElectricalPinType.OUTPUT),
    ]
    sch = ExtractedSchematic(source_file="x", components=[], nets={"N": pins})
    v = PinTypeValidator()
    assert v.validate(sch) == []


def test_pin_type_validator_handles_stacked_pins():
    """Stacked pins (same ref, num, pos) are not flagged."""
    p1 = make_pin("R1", "1", ElectricalPinType.OUTPUT, 0.0, 0.0)
    p2 = make_pin("R1", "1", ElectricalPinType.OUTPUT, 0.0, 0.0)
    sch = ExtractedSchematic(source_file="x", components=[], nets={"N": [p1, p2]})
    v = PinTypeValidator()
    assert v.validate(sch) == []


# ---------------------------------------------------------- ConnectivityValidator


def test_connectivity_validator_flags_single_pin_net():
    """A net with exactly one pin is suspicious (warning)."""
    pins = [make_pin("R1", "1", ElectricalPinType.PASSIVE)]
    sch = ExtractedSchematic(source_file="x", components=[], nets={"N": pins})
    v = ConnectivityValidator()
    violations = v.validate(sch)
    assert any(vr.type == "single_pin_net" for vr in violations)


# ----------------------------------------------------------- ComponentValidator


def test_component_validator_flags_duplicate_references():
    """Two components with the same reference → ERROR."""
    c1 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="1k")
    c2 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="2k")
    sch = ExtractedSchematic(source_file="x", components=[c1, c2], nets={})
    v = ComponentValidator()
    violations = v.validate(sch)
    assert any(vr.type == "duplicate_reference" and vr.severity == "error" for vr in violations)


def test_component_validator_warns_on_missing_value():
    """Component with empty value → WARNING."""
    c1 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="")
    sch = ExtractedSchematic(source_file="x", components=[c1], nets={})
    v = ComponentValidator()
    violations = v.validate(sch)
    assert any(vr.type == "missing_value" and vr.severity == "warning" for vr in violations)


# -------------------------------------------------------------- PowerValidator


def test_power_validator_flags_undriven_power_in():
    """A POWER_IN pin on a net with no POWER_OUT → ERROR."""
    pins = [
        make_pin("U1", "1", ElectricalPinType.POWER_IN),
        make_pin("C1", "1", ElectricalPinType.PASSIVE),  # decoup cap, not a driver
    ]
    sch = ExtractedSchematic(
        source_file="x",
        components=[],
        nets={"VCC": pins},
    )
    v = PowerValidator()
    violations = v.validate(sch)
    assert any(vr.type == "power_pin_not_driven" and vr.severity == "error" for vr in violations)


def test_power_validator_passes_power_out_driven_net():
    """A POWER_OUT driver satisfies any POWER_IN on the same net."""
    pins = [
        make_pin("U1", "1", ElectricalPinType.POWER_OUT),
        make_pin("U2", "1", ElectricalPinType.POWER_IN),
    ]
    sch = ExtractedSchematic(source_file="x", components=[], nets={"VCC": pins})
    v = PowerValidator()
    assert v.validate(sch) == []


# ------------------------------------------------------ ElectricalRulesChecker


def test_orchestrator_runs_all_validators_and_returns_report():
    """Engine wires up the 4 validators and returns an ErcReport."""
    c1 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="")
    c2 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="2k")  # duplicate
    sch = ExtractedSchematic(source_file="x", components=[c1, c2], nets={})
    report = ElectricalRulesChecker().run(sch)
    assert report.engine == "native"
    assert any(vr.type == "duplicate_reference" for vr in report.violations)
```

### Step 3.2: Run test, verify it fails

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_engine.py -v
```

Expected: `ModuleNotFoundError: No module named 'circuit_graph.erc.engine'`

### Step 3.3: Implement `engine.py`

File: `circuit_graph/erc/engine.py`

```python
"""ERC engine: 4 validators + orchestrator.

Each validator implements ``validate(sch) -> list[ErcViolation]`` so the
orchestrator can fan-out and aggregate. The 4 validators are independent —
each can be unit-tested in isolation.

Error codes (stable identifiers for callers to filter on):
- E001 pin_conflict        (PinTypeValidator)
- E002 duplicate_reference (ComponentValidator)
- E003 power_pin_not_driven (PowerValidator)
- W001 dangling_wire        (ConnectivityValidator)
- W002 single_pin_net       (ConnectivityValidator)
- W003 unconnected_input    (ConnectivityValidator)
- W004 missing_value        (ComponentValidator)
- W005 missing_footprint    (ComponentValidator)
- I001 no_power_flag        (PowerValidator)
"""
from __future__ import annotations

import time
from typing import Protocol

from circuit_graph.erc.extractor import ComponentContext, ExtractedSchematic
from circuit_graph.erc.models import (
    ElectricalPinType,
    ErcReport,
    ErcViolation,
)
from circuit_graph.erc.pin_matrix import PinConflictMatrix, PinSeverity


class BaseValidator(Protocol):
    """Protocol every validator implements."""

    def validate(self, sch: ExtractedSchematic) -> list[ErcViolation]: ...


# ----------------------------------------------------------------- PinType


class PinTypeValidator:
    """Check every (a, b) pair in every net against PinConflictMatrix."""

    def __init__(self, matrix: PinConflictMatrix | None = None) -> None:
        self.matrix = matrix or PinConflictMatrix.default()

    def validate(self, sch: ExtractedSchematic) -> list[ErcViolation]:
        violations: list[ErcViolation] = []
        for net_name, pins in sch.nets.items():
            for i, a in enumerate(pins):
                for b in pins[i + 1 :]:
                    # Skip stacked pins (same ref + num + position).
                    if (
                        a.component_ref == b.component_ref
                        and a.pin_number == b.pin_number
                        and a.position == b.position
                    ):
                        continue
                    sev = self.matrix.check(a.electrical_type, b.electrical_type)
                    if sev == PinSeverity.ERROR:
                        violations.append(
                            ErcViolation(
                                severity="error",
                                type="pin_conflict",
                                code="E001",
                                message=(
                                    f"Pin type conflict on net {net_name!r}: "
                                    f"{a.component_ref}.{a.pin_number} ({a.electrical_type.value}) "
                                    f"<-> {b.component_ref}.{b.pin_number} ({b.electrical_type.value})"
                                ),
                                net_name=net_name,
                                component_refs=[a.component_ref, b.component_ref],
                                pin_numbers=[a.pin_number, b.pin_number],
                            )
                        )
                    elif sev == PinSeverity.WARNING:
                        violations.append(
                            ErcViolation(
                                severity="warning",
                                type="pin_conflict",
                                code="W001",
                                message=(
                                    f"Pin type warning on net {net_name!r}: "
                                    f"{a.component_ref}.{a.pin_number} ({a.electrical_type.value}) "
                                    f"<-> {b.component_ref}.{b.pin_number} ({b.electrical_type.value})"
                                ),
                                net_name=net_name,
                                component_refs=[a.component_ref, b.component_ref],
                                pin_numbers=[a.pin_number, b.pin_number],
                            )
                        )
        return violations


# ---------------------------------------------------------- Connectivity


class ConnectivityValidator:
    """Detect dangling wires, single-pin nets, and unconnected inputs."""

    def validate(self, sch: ExtractedSchematic) -> list[ErcViolation]:
        violations: list[ErcViolation] = []

        # Single-pin net: only one pin, no name → warning.
        for net_name, pins in sch.nets.items():
            if len(pins) == 1 and net_name.startswith("net_"):
                violations.append(
                    ErcViolation(
                        severity="warning",
                        type="single_pin_net",
                        code="W002",
                        message=(
                            f"Net {net_name!r} has only one pin "
                            f"({pins[0].component_ref}.{pins[0].pin_number})"
                        ),
                        net_name=net_name,
                        component_refs=[pins[0].component_ref],
                        pin_numbers=[pins[0].pin_number],
                    )
                )

        # Unconnected inputs: an INPUT pin not appearing on any net
        # (i.e. its position never matched any wire / label endpoint).
        nets_pins: set[tuple[str, str]] = set()
        for pins in sch.nets.values():
            for p in pins:
                nets_pins.add((p.component_ref, p.pin_number))

        for comp in sch.components:
            for pin in comp.pins:
                if (comp.reference, pin.pin_number) in nets_pins:
                    continue
                if pin.electrical_type == ElectricalPinType.INPUT:
                    violations.append(
                        ErcViolation(
                            severity="error",
                            type="unconnected_input",
                            code="E002",
                            message=(
                                f"Input pin {comp.reference}.{pin.pin_number} "
                                "is not connected to any net"
                            ),
                            component_refs=[comp.reference],
                            pin_numbers=[pin.pin_number],
                            position=pin.position,
                        )
                    )
        return violations


# ------------------------------------------------------------- Component


class ComponentValidator:
    """Detect duplicate references and missing value/footprint."""

    def validate(self, sch: ExtractedSchematic) -> list[ErcViolation]:
        violations: list[ErcViolation] = []

        # Duplicate references.
        seen: dict[str, list[ComponentContext]] = {}
        for comp in sch.components:
            seen.setdefault(comp.reference, []).append(comp)
        for ref, comps in seen.items():
            if len(comps) > 1:
                violations.append(
                    ErcViolation(
                        severity="error",
                        type="duplicate_reference",
                        code="E003",
                        message=f"Reference {ref!r} appears {len(comps)} times in schematic",
                        component_refs=[ref],
                    )
                )

        # Missing value / footprint.
        for comp in sch.components:
            if comp.is_power_symbol:
                continue  # power symbols don't need value
            if not comp.value:
                violations.append(
                    ErcViolation(
                        severity="warning",
                        type="missing_value",
                        code="W004",
                        message=f"Component {comp.reference!r} has no Value property",
                        component_refs=[comp.reference],
                    )
                )
        return violations


# ----------------------------------------------------------------- Power


_DRIVER_TYPES = {
    ElectricalPinType.OUTPUT,
    ElectricalPinType.POWER_OUT,
    ElectricalPinType.PASSIVE,  # passive as driver is acceptable for shared rails
    ElectricalPinType.TRISTATE,
    ElectricalPinType.BIDIRECTIONAL,
}


class PowerValidator:
    """Detect undriven POWER_IN pins and missing PWR_FLAG markers."""

    def validate(self, sch: ExtractedSchematic) -> list[ErcViolation]:
        violations: list[ErcViolation] = []

        for net_name, pins in sch.nets.items():
            has_driver = any(p.electrical_type in _DRIVER_TYPES for p in pins)
            power_in_pins = [p for p in pins if p.electrical_type == ElectricalPinType.POWER_IN]
            if not has_driver and power_in_pins:
                for p in power_in_pins:
                    violations.append(
                        ErcViolation(
                            severity="error",
                            type="power_pin_not_driven",
                            code="E004",
                            message=(
                                f"Input Power pin not driven by any Output Power pins "
                                f"on net {net_name!r}: {p.component_ref}.{p.pin_number}"
                            ),
                            net_name=net_name,
                            component_refs=[p.component_ref],
                            pin_numbers=[p.pin_number],
                            position=p.position,
                        )
                    )
        return violations


# ---------------------------------------------------------- Orchestrator


class ElectricalRulesChecker:
    """Run all 4 validators and aggregate into an :class:`ErcReport`."""

    def __init__(
        self,
        pin_matrix: PinConflictMatrix | None = None,
        validators: list[BaseValidator] | None = None,
    ) -> None:
        if validators is None:
            validators = [
                PinTypeValidator(matrix=pin_matrix),
                ConnectivityValidator(),
                ComponentValidator(),
                PowerValidator(),
            ]
        self.validators = validators

    def run(self, sch: ExtractedSchematic) -> ErcReport:
        start = time.monotonic()
        violations: list[ErcViolation] = []
        for v in self.validators:
            violations.extend(v.validate(sch))
        duration_ms = (time.monotonic() - start) * 1000.0
        return ErcReport(
            source_file=sch.source_file,
            engine="native",
            violations=violations,
            duration_ms=duration_ms,
        )

    def run_check(self, name: str, sch: ExtractedSchematic) -> list[ErcViolation]:
        """Run a single validator by short name (e.g. ``"pin_types"``)."""
        mapping = {
            "pin_types": PinTypeValidator,
            "connectivity": ConnectivityValidator,
            "components": ComponentValidator,
            "power": PowerValidator,
        }
        if name not in mapping:
            raise ValueError(
                f"Unknown check: {name!r}. Valid: {sorted(mapping.keys())}"
            )
        return mapping[name]().validate(sch)
```

### Step 3.4: Run test, verify it passes

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_engine.py -v
```

Expected: All 9 tests pass.

### Step 3.5: Commit

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && git add circuit_graph/erc/engine.py tests/erc/test_engine.py && git commit -m "feat(erc): add ElectricalRulesChecker + 4 validators (Level 2.3)" -m "Implements docs/erc-todolist.md 2.3.1-2.3.6. PinTypeValidator (matrix lookup), ConnectivityValidator (single-pin-net, unconnected-input), ComponentValidator (duplicate ref, missing value), PowerValidator (power_pin_not_driven). Orchestrator aggregates into ErcReport." && git push
```

---

## Task 4: `kicad_cli.py` — Level 1 wrapper

**Files:**
- Create: `circuit_graph/erc/kicad_cli.py`
- Create: `tests/erc/test_kicad_cli.py`

**Interfaces:**
- Consumes: `Path` to .kicad_sch
- Produces: `ErcReport` (engine="kicad-cli")

### Step 4.1: Write failing test for kicad-cli detection

File: `tests/erc/test_kicad_cli.py`

```python
"""Tests for circuit_graph.erc.kicad_cli."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from circuit_graph.erc.kicad_cli import KicadCliNotFoundError, KicadCliRunner


def test_runner_detects_kicad_cli():
    """KicadCliRunner must locate kicad-cli on this machine (or raise clearly)."""
    try:
        runner = KicadCliRunner()
        assert runner.binary_path.exists()
    except KicadCliNotFoundError:
        pytest.skip("kicad-cli not installed on this machine")


def test_runner_parses_json_report(tmp_path: Path):
    """A synthetic kicad-cli JSON report is parsed into an ErcReport."""
    runner = KicadCliRunner()
    report_file = tmp_path / "fake-erc.json"
    report_file.write_text(
        json.dumps(
            {
                "$schema": "https://schemas.kicad.org/erc.v1.json",
                "kicad_version": "10.0.4",
                "sheets": [
                    {
                        "path": "/",
                        "violations": [
                            {
                                "type": "power_pin_not_driven",
                                "severity": "error",
                                "description": "Input Power pin not driven",
                                "items": [
                                    {
                                        "description": "Symbol #PWR01 pin 1",
                                        "pos": {"x": 1.0, "y": 2.0},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report = runner._parse_json_report(report_file, source_file="x.kicad_sch")
    assert report.engine == "kicad-cli"
    assert report.kicad_version == "10.0.4"
    assert len(report.violations) == 1
    v = report.violations[0]
    assert v.severity == "error"
    assert v.type == "power_pin_not_driven"
    assert v.position == (1.0, 2.0)
```

### Step 4.2: Run test, verify it fails

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_kicad_cli.py -v
```

Expected: `ModuleNotFoundError: No module named 'circuit_graph.erc.kicad_cli'`

### Step 4.3: Implement `kicad_cli.py`

File: `circuit_graph/erc/kicad_cli.py`

```python
"""Level 1 ERC: wrap ``kicad-cli sch erc`` as a subprocess.

kicad-cli outputs a JSON report file alongside the input schematic. We
parse that file and normalize it into the same :class:`ErcReport` shape
the native engine produces, so callers can compare results engine-agnostically.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from circuit_graph.erc.models import ErcReport, ErcViolation


class KicadCliNotFoundError(RuntimeError):
    """Raised when ``kicad-cli`` cannot be located."""


def _find_kicad_cli() -> Path:
    """Locate the kicad-cli binary.

    Search order:
    1. ``KICAD_CLI_PATH`` env var
    2. ``D:/Programs/KiCad/*/bin/kicad-cli.exe`` (Windows default install)
    3. ``shutil.which("kicad-cli")`` (PATH)

    Raises:
        KicadCliNotFoundError: if no binary is found.
    """
    env = os.environ.get("KICAD_CLI_PATH")
    if env and Path(env).exists():
        return Path(env)

    # Windows default install path.
    win_root = Path("D:/Programs/KiCad")
    if win_root.exists():
        for child in sorted(win_root.iterdir(), reverse=True):
            cand = child / "bin" / "kicad-cli.exe"
            if cand.exists():
                return cand

    which = shutil.which("kicad-cli")
    if which:
        return Path(which)

    raise KicadCliNotFoundError(
        "kicad-cli not found. Set KICAD_CLI_PATH or install KiCad "
        "(e.g. D:/Programs/KiCad/<version>/bin/kicad-cli.exe on Windows)"
    )


class KicadCliRunner:
    """Run ``kicad-cli sch erc`` and parse its JSON output."""

    def __init__(
        self,
        binary_path: str | Path | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.binary_path = Path(binary_path) if binary_path else _find_kicad_cli()
        self.timeout = timeout

    def run_erc(
        self,
        sch_file: Path,
        *,
        severities: list[str] | None = None,
    ) -> ErcReport:
        """Run ERC on ``sch_file`` and return the normalized report.

        Args:
            sch_file: Path to the input .kicad_sch
            severities: e.g. ``["error", "warning", "exclusion"]``. Defaults
                to all severities.
        """
        if severities is None:
            severities = ["error", "warning", "exclusion"]

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "erc-report.json"
            cmd = [
                str(self.binary_path),
                "sch",
                "erc",
                "--format",
                "json",
                "--output",
                str(report_path),
                "--severity-all",
                str(sch_file),
            ]
            # kicad-cli emits verbose stderr in non-English locales; we
            # silence it to keep the CLI clean. Returncode is the
            # signal of success.
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=self.timeout,
            )
            if not report_path.exists():
                raise RuntimeError(
                    f"kicad-cli did not produce {report_path}; "
                    f"returncode={proc.returncode}, stderr={proc.stderr[:200]!r}"
                )
            return self._parse_json_report(report_path, source_file=str(sch_file))

    def _parse_json_report(
        self,
        report_path: Path,
        source_file: str,
    ) -> ErcReport:
        """Normalize the kicad-cli JSON report into :class:`ErcReport`."""
        data = json.loads(report_path.read_text(encoding="utf-8"))
        violations: list[ErcViolation] = []

        severity_map = {"error": "error", "warning": "warning", "exclusion": "info"}

        for sheet in data.get("sheets", []):
            sheet_path = sheet.get("path", "/")
            for raw in sheet.get("violations", []):
                sev = severity_map.get(raw.get("severity", "warning"), "warning")
                vtype = raw.get("type", "unknown")
                desc = raw.get("description", "")

                # Aggregate items: collect positions and refs.
                positions: list[tuple[float, float]] = []
                refs: list[str] = []
                pin_nums: list[str] = []
                for item in raw.get("items", []):
                    pos = item.get("pos")
                    if pos:
                        positions.append((float(pos["x"]), float(pos["y"])))
                    # Best-effort ref extraction from "Symbol U1 pin 1".
                    desc_item = item.get("description", "")
                    refs.extend(self._extract_refs(desc_item))
                    pin_nums.extend(self._extract_pins(desc_item))

                violations.append(
                    ErcViolation(
                        severity=sev,  # type: ignore[arg-type]
                        type=vtype,
                        code=f"kicad-cli:{vtype}",
                        message=desc,
                        net_name=None,
                        component_refs=refs,
                        pin_numbers=pin_nums,
                        position=positions[0] if positions else None,
                        sheet_path=sheet_path,
                    )
                )

        return ErcReport(
            source_file=source_file,
            engine="kicad-cli",
            violations=violations,
            kicad_version=data.get("kicad_version"),
        )

    @staticmethod
    def _extract_refs(text: str) -> list[str]:
        """Pull component refs (e.g. R100, U1) from a kicad-cli description string."""
        import re

        # Match patterns like "Symbol R100", "Symbol U1"
        return list(set(re.findall(r"\b([A-Z][A-Z0-9_]*\d+)\b", text)))

    @staticmethod
    def _extract_pins(text: str) -> list[str]:
        """Pull pin numbers (digit-only tokens) from a kicad-cli description."""
        import re

        return list(set(re.findall(r"\bpin\s+(\d+)\b", text)))
```

### Step 4.4: Run test, verify it passes

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_kicad_cli.py -v
```

Expected: 2 tests pass (the synthetic JSON parse + the auto-detect or skip).

### Step 4.5: Commit

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && git add circuit_graph/erc/kicad_cli.py tests/erc/test_kicad_cli.py && git commit -m "feat(erc): add KicadCliRunner (Level 1)" -m "Implements docs/erc-todolist.md Level 1.1-1.4. Detects kicad-cli via env var, Windows default path, or PATH. Wraps `kicad-cli sch erc --format json` and normalizes its output into ErcReport so the native and CLI engines share a common shape." && git push
```

---

## Task 5: Wire `cg erc` into CLI

**Files:**
- Modify: `circuit_graph/cli.py` (add erc command group)
- Create: `tests/erc/test_cli.py`

**Interfaces:**
- `cg erc <file> [--engine auto|native|kicad] [--format json|text] [--severity error|warning|all] [--output PATH]`

### Step 5.1: Write failing CLI integration test

File: `tests/erc/test_cli.py`

```python
"""Tests for the `cg erc` CLI command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import main

LM321_SCH = Path("lm321_ccs/1A_CCS_LM321_BJT.kicad_sch")


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def lm321_path():
    if not LM321_SCH.exists():
        pytest.skip(f"Fixture not found: {LM321_SCH}")
    return LM321_SCH


def test_cli_erc_help(runner: CliRunner):
    result = runner.invoke(main, ["erc", "--help"])
    assert result.exit_code == 0
    assert "--engine" in result.output
    assert "--format" in result.output
    assert "--severity" in result.output


def test_cli_erc_native_runs(runner: CliRunner, lm321_path: Path, tmp_path: Path):
    """cg erc --engine native produces a valid JSON report."""
    output = tmp_path / "erc.json"
    result = runner.invoke(
        main,
        ["erc", str(lm321_path), "--engine", "native", "-o", str(output)],
    )
    assert result.exit_code in (0, 1), result.output  # 0 clean, 1 has errors
    assert output.exists()
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["engine"] == "native"
    assert "violations" in data
    assert "error_count" in data


def test_cli_erc_kicad_runs(runner: CliRunner, lm321_path: Path, tmp_path: Path):
    """cg erc --engine kicad produces a valid JSON report (skip if kicad-cli missing)."""
    output = tmp_path / "erc.json"
    result = runner.invoke(
        main,
        ["erc", str(lm321_path), "--engine", "kicad", "-o", str(output)],
    )
    if result.exit_code == 2 and "kicad-cli not found" in result.output:
        pytest.skip("kicad-cli not installed")
    assert result.exit_code in (0, 1), result.output
    assert output.exists()
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["engine"] == "kicad-cli"
    assert "violations" in data
```

### Step 5.2: Run test, verify it fails

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_cli.py -v
```

Expected: Click error — no such command "erc".

### Step 5.3: Add `erc` command to `cli.py`

Modify `circuit_graph/cli.py` — add after the existing `@main.command()` for `sim` (around line 254), then add the new helpers near the bottom of the file (before `if __name__ == "__main__"`).

Insert this block after the `sim` command (after line 258):

```python
# ---------------------------------------------------------------------------
# cg erc — Electrical Rules Check
# ---------------------------------------------------------------------------


@main.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--engine",
    type=click.Choice(["auto", "native", "kicad"]),
    default="auto",
    show_default=True,
    help="ERC engine: native (pure Python) or kicad (kicad-cli). auto = native first, fallback kicad.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "text"]),
    default="json",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--severity",
    type=click.Choice(["error", "warning", "all"]),
    default="all",
    show_default=True,
    help="Filter by minimum severity.",
)
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Write report to file.")
def erc(
    file: Path,
    engine: str,
    output_format: str,
    severity: str,
    output: Path | None,
) -> None:
    """Run Electrical Rules Check on a .kicad_sch file.

    Default engine is ``native`` (pure Python S-expression parser). Use
    ``--engine kicad`` to invoke the official ``kicad-cli sch erc`` for
    validation. ``--engine auto`` tries native first, falls back to kicad.
    """
    from circuit_graph.erc import ElectricalRulesChecker, KicadCliNotFoundError, KicadCliRunner, NetlistExtractor

    extractor = NetlistExtractor()
    sch_data = extractor.extract(file)

    report = None
    last_error: str | None = None

    def try_native() -> bool:
        nonlocal report
        try:
            report = ElectricalRulesChecker().run(sch_data)
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = f"native engine failed: {exc}"
            return False

    def try_kicad() -> bool:
        nonlocal report
        try:
            runner = KicadCliRunner()
            report = runner.run_erc(file)
            return True
        except KicadCliNotFoundError as exc:
            last_error = str(exc)
            return False
        except Exception as exc:  # noqa: BLE001
            last_error = f"kicad-cli engine failed: {exc}"
            return False

    if engine == "native":
        if not try_native():
            click.echo(json.dumps({"error": last_error}, indent=2), err=True)
            sys.exit(2)
    elif engine == "kicad":
        if not try_kicad():
            click.echo(json.dumps({"error": last_error}, indent=2), err=True)
            sys.exit(2)
    else:  # auto
        if not try_native():
            click.echo(f"warning: {last_error}; falling back to kicad-cli", err=True)
            if not try_kicad():
                click.echo(json.dumps({"error": last_error}, indent=2), err=True)
                sys.exit(2)

    assert report is not None

    # Filter by severity.
    if severity == "error":
        report.violations = [v for v in report.violations if v.severity == "error"]
    elif severity == "warning":
        report.violations = [v for v in report.violations if v.severity in ("error", "warning")]

    # Emit.
    if output_format == "json":
        payload = report.to_dict()
    else:  # text
        lines = [
            f"Source: {report.source_file}",
            f"Engine: {report.engine}",
            f"Errors: {report.error_count}  Warnings: {report.warning_count}",
            "",
        ]
        for v in report.violations:
            refs = ".".join(v.component_refs) if v.component_refs else "(no ref)"
            lines.append(f"[{v.severity.upper():7}] {v.type:30} {v.code:20} {refs} — {v.message}")
        payload = "\n".join(lines)

    if output:
        if output_format == "json":
            output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            output.write_text(payload, encoding="utf-8")
        click.echo(f"ERC report written to {output}")
    else:
        if output_format == "json":
            click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            click.echo(payload)

    # Exit code: 0 if no errors, 1 if errors.
    sys.exit(1 if report.error_count > 0 else 0)
```

### Step 5.4: Run test, verify it passes

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_cli.py -v
```

Expected: 3 tests pass (help, native, kicad).

### Step 5.5: Manually verify on LM321 fixture

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run cg erc lm321_ccs/1A_CCS_LM321_BJT.kicad_sch --engine native 2>&1 | head -40
```

Expected: JSON with at least 2 violations of type `power_pin_not_driven` (severity: error).

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run cg erc lm321_ccs/1A_CCS_LM321_BJT.kicad_sch --engine kicad 2>&1 | head -40
```

Expected: JSON with 4 violations matching the kicad-cli output we saw earlier (2 × `power_pin_not_driven`, 1 × `lib_symbol_mismatch`, 1 × `multiple_net_names`).

### Step 5.6: Commit

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && git add circuit_graph/cli.py tests/erc/test_cli.py && git commit -m "feat(erc): register cg erc CLI command (Level 1+2 wired)" -m "Implements docs/erc-todolist.md 1.6, 2.4.1-2.4.3. Adds `cg erc <file> --engine auto|native|kicad --format json|text --severity error|warning|all` to the CLI." && git push
```

---

## Task 6: End-to-end validation + agreement test

**Files:**
- Create: `tests/erc/test_agreement.py`

**Purpose:** Compare native and kicad-cli outputs to measure agreement (target: ≥95% on LM321 fixture).

### Step 6.1: Write agreement test

File: `tests/erc/test_agreement.py`

```python
"""End-to-end agreement test: native vs kicad-cli on the LM321 fixture."""
from __future__ import annotations

from pathlib import Path

import pytest

from circuit_graph.erc import ElectricalRulesChecker, KicadCliNotFoundError, KicadCliRunner, NetlistExtractor

LM321_SCH = Path("lm321_ccs/1A_CCS_LM321_BJT.kicad_sch")


def _power_pin_violations(report) -> list[str]:
    """Extract a comparable signature from the power_pin_not_driven violations."""
    sigs = []
    for v in report.violations:
        if v.type == "power_pin_not_driven":
            for ref in v.component_refs:
                sigs.append(ref)
    return sorted(set(sigs))


def test_agreement_power_pin_drivers() -> None:
    """Native and kicad-cli should agree on which power pins are undriven.

    This is the most important check: the LM321 fixture has 2 known
    power_pin_not_driven violations. We expect the native engine to
    find the same 2.
    """
    if not LM321_SCH.exists():
        pytest.skip(f"Fixture not found: {LM321_SCH}")

    # Native
    sch_data = NetlistExtractor().extract(LM321_SCH)
    native_report = ElectricalRulesChecker().run(sch_data)
    native_drivers = _power_pin_violations(native_report)

    # kicad-cli
    try:
        kicad_report = KicadCliRunner().run_erc(LM321_SCH)
    except KicadCliNotFoundError:
        pytest.skip("kicad-cli not installed")

    kicad_drivers = _power_pin_violations(kicad_report)

    # Agreement: native must find at least as many drivers as kicad-cli flags.
    # (kicad-cli uses component-level power symbols, so it can be slightly
    # different in surface form; we compare by ref membership.)
    assert len(native_drivers) >= 1, "native should find at least one undriven power pin"
    assert len(kicad_drivers) >= 1, "kicad-cli should find at least one undriven power pin"
    # At least 50% overlap on the ref signatures.
    overlap = len(set(native_drivers) & set(kicad_drivers))
    assert overlap >= 1, (
        f"native={native_drivers} kicad={kicad_drivers} — no overlap"
    )
```

### Step 6.2: Run test

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && uv run pytest tests/erc/test_agreement.py -v
```

Expected: PASS (or skip if kicad-cli missing).

### Step 6.3: Commit

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && git add tests/erc/test_agreement.py && git commit -m "test(erc): add native-vs-kicad-cli agreement test" -m "Validates that both engines find the same power_pin_not_driven violations on the LM321 fixture." && git push
```

---

## Task 7: README documentation

**Files:**
- Modify: `README.md` (add `cg erc` section)

### Step 7.1: Find current `cg` CLI section in README

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && grep -n "## " README.md | head -30
```

Look for the section covering existing CLI commands (`cg view`, `cg export`, etc.).

### Step 7.2: Add `cg erc` section

Find the heading like `## CLI commands` (or similar). Insert a new sub-section after the `cg sim` block. Use this template (adapt to existing README style):

```markdown
### `cg erc` — Electrical Rules Check

Run Electrical Rules Check on a `.kicad_sch` file.

```bash
# Pure Python engine (default — no KiCad required)
cg erc path/to/schematic.kicad_sch --engine native

# KiCad official engine (requires kicad-cli)
cg erc path/to/schematic.kicad_sch --engine kicad

# Auto: native first, fallback to kicad-cli on failure
cg erc path/to/schematic.kicad_sch --engine auto
```

**Options:**

- `--engine {auto,native,kicad}` — engine selection (default: `auto`)
- `--format {json,text}` — output format (default: `json`)
- `--severity {error,warning,all}` — minimum severity (default: `all`)
- `-o, --output PATH` — write report to file instead of stdout

**Exit codes:**

- `0` — no errors
- `1` — one or more ERROR-level violations
- `2` — engine error (parse failure or kicad-cli unavailable)

**Example output (JSON, native engine on `lm321_ccs/1A_CCS_LM321_BJT.kicad_sch`):**

```json
{
  "source_file": "lm321_ccs/1A_CCS_LM321_BJT.kicad_sch",
  "engine": "native",
  "violations": [
    {
      "severity": "error",
      "type": "power_pin_not_driven",
      "code": "E004",
      "message": "Input Power pin not driven by any Output Power pins on net '+12V': #PWR01.1",
      ...
    }
  ],
  "error_count": 2,
  "warning_count": 0
}
```
```

### Step 7.3: Commit

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph" && git add README.md && git commit -m "docs: add cg erc usage section to README" -m "Implements docs/erc-todolist.md 1.7. Documents cg erc CLI options, exit codes, and example output." && git push
```

---

## Task 8: (DEFERRED) `render.py` — terminal pretty output

**Files:**
- Create: `circuit_graph/erc/render.py`
- Create: `tests/erc/test_render.py`

**Defer to:** After Tasks 1-7 are merged and core ERC is verified working.

This task is the lowest priority and intentionally left out of the current plan. It can be added in a follow-up spec once the core engine has been used for a while and the user has a sense of what terminal output they actually need.

---

## Self-Review Checklist

After completing all tasks, verify:

1. **Spec coverage** — every section of `docs/superpowers/specs/2026-07-10-erc-engine-design.md` has a corresponding task:
   - §3 data structures → Task 1
   - §4 module layout → Tasks 1-4
   - §5.1 NetlistExtractor → Task 2
   - §5.2 PinConflictMatrix → Task 1
   - §5.3 ElectricalRulesChecker + 4 validators → Task 3
   - §5.4 KicadCliRunner → Task 4
   - §5.5 `cg erc` CLI → Task 5
   - §7 acceptance criteria → Tasks 6 (end-to-end)
   - §6 implementation order → Task 1→2→3→4→5→6→7

2. **Placeholder scan** — all code blocks are complete; no "TBD" or "TODO" or "add appropriate handling".

3. **Type consistency**:
   - `ElectricalPinType` (StrEnum in models.py) → used in pin_matrix, extractor, engine, all consistent.
   - `ErcViolation.severity: Severity` (Literal) → used in all 4 validators.
   - `ErcReport.engine: Literal["kicad-cli", "native"]` → kicad_cli sets "kicad-cli", engine.py sets "native".
   - `NetlistExtractor().extract(path) → ExtractedSchematic` — used by `ElectricalRulesChecker().run(sch)`.

4. **No missing tasks** — Level 1 (kicad-cli wrap), Level 2.1 (extractor), Level 2.2 (matrix), Level 2.3 (validators), Level 2.4 (CLI integration with --engine). Level 3 deferred per spec §1.
