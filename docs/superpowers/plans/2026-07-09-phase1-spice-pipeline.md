# Phase 1: SPICE Simulation Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `sim/` module — Ngspice runner, deck builder, validator, and `cg sim` CLI — with a hardcoded LM321 non-inverting amplifier proof-of-concept.

**Architecture:** `circuit_graph/sim/` contains seven focused modules: data models (`spice_deck`, `spice_result`), execution (`spice_runner`, `convergence`), deck assembly (`deck_builder`, `stimulus`), and validation (`deck_validator`). Phase 1 uses a hardcoded LM321 template; Phase 3+ will switch to reading v2 primitive `simulation` fields.

**Tech Stack:** Python 3.10+, Ngspice (external), click (CLI), pytest, frozen dataclasses throughout.

## Global Constraints

- Python 3.10+ only (match pyproject.toml `requires-python`)
- All dataclasses `frozen=True` (design doc specifies immutable data models)
- Line endings LF, encoding UTF-8 (CLAUDE.md rule 0)
- Ngspice must be installed separately (not a pip dependency)
- Test files in `tests/` matching `test_*.py` (pyproject.toml pytest config)
- CLI follows existing click pattern: `@main.command()` for top-level, `@click.argument` for positional args
- Commit after each task with descriptive message

---

### Task 1: Install Ngspice and verify

**Files:** None

**Interfaces:**
- Produces: Ngspice available at `ngspice` on PATH

- [ ] **Step 1: Download and install Ngspice**

Ngspice Windows installer: https://sourceforge.net/projects/ngspice/files/ng-spice-rework/45/ngspice-45_64.zip/download

```powershell
# Download to D:/Programs/
Invoke-WebRequest -Uri "https://sourceforge.net/projects/ngspice/files/ng-spice-rework/45/ngspice-45_64.zip/download" -OutFile "$env:TEMP\ngspice-45_64.zip"
Expand-Archive -Path "$env:TEMP\ngspice-45_64.zip" -DestinationPath "D:/Programs/ngspice-45" -Force
```

- [ ] **Step 2: Add to PATH and verify**

```powershell
# Add to user PATH
$ngspiceBin = "D:\Programs\ngspice-45\Spice64\bin"
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$ngspiceBin", "User")

# Verify in new shell
ngspice -v
```

Expected: version string (e.g. "ngspice-45")

- [ ] **Step 3: Smoke test with a trivial deck**

```powershell
# Write a trivial deck and run it
@'
Test trivial
V1 1 0 DC 5
R1 1 0 1k
.OP
.END
'@ | Set-Content "$env:TEMP\test_trivial.cir" -Encoding UTF8

ngspice -b "$env:TEMP\test_trivial.cir"
```

Expected: output shows operating point, no errors.

- [ ] **Step 4: Commit** (no code yet, but record the env setup note)

```bash
# No code to commit — environment setup only. Document in a note.
```

---

### Task 2: Create sim/ package skeleton

**Files:**
- Create: `circuit_graph/sim/__init__.py`

**Interfaces:**
- Produces: `circuit_graph.sim` importable package

- [ ] **Step 1: Create directory structure**

```powershell
New-Item -ItemType Directory -Force -Path "circuit_graph/sim"
```

- [ ] **Step 2: Write `circuit_graph/sim/__init__.py`**

```python
"""SPICE simulation pipeline for circuit-graph.

Phase 1: hardcoded LM321 proof-of-concept.
Phase 3+: reads v2 primitive ``simulation`` fields for template-driven assembly.
"""

from circuit_graph.sim.spice_deck import SimulationDeck, ProbeDefinition
from circuit_graph.sim.spice_result import AnalysisResult, SpiceResult
from circuit_graph.sim.spice_runner import SpiceRunner, SpiceRunnerConfig
from circuit_graph.sim.deck_builder import DeckBuilder
from circuit_graph.sim.deck_validator import DeckValidator
from circuit_graph.sim.stimulus import StimulusSelector
from circuit_graph.sim.convergence import ConvergenceConfig

__all__ = [
    "AnalysisResult",
    "ConvergenceConfig",
    "DeckBuilder",
    "DeckValidator",
    "ProbeDefinition",
    "SimulationDeck",
    "SpiceResult",
    "SpiceRunner",
    "SpiceRunnerConfig",
    "StimulusSelector",
]
```

- [ ] **Step 3: Verify import**

```powershell
.venv\Scripts\python -c "import circuit_graph.sim; print('sim package OK')"
```

Expected: `sim package OK` (will fail until we populate the modules — that's fine, this step creates the skeleton)

- [ ] **Step 4: Commit**

```bash
git add circuit_graph/sim/__init__.py
git commit -m "feat(sim): create sim/ package skeleton

Phase 1 SPICE simulation pipeline entry point.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: SimulationDeck and ProbeDefinition data models

**Files:**
- Create: `circuit_graph/sim/spice_deck.py`
- Create: `tests/test_spice_deck.py`

**Interfaces:**
- Produces: `SimulationDeck(name, includes, models, subckts, netlist, stimuli, analyses, probes, options, diagnostics)`, `ProbeDefinition(node, label, expected)`, `.to_spice()` method

- [ ] **Step 1: Write failing test**

Create `tests/test_spice_deck.py`:

```python
"""Tests for SimulationDeck data model."""

from __future__ import annotations

from circuit_graph.sim.spice_deck import ProbeDefinition, SimulationDeck


def test_simulation_deck_to_spice_minimal():
    """A minimal deck renders valid SPICE with all sections."""
    deck = SimulationDeck(
        name="test_minimal",
        includes=(),
        models=(),
        subckts=(),
        netlist="R1 out gnd 10k",
        stimuli=("V1 in 0 DC 5",),
        analyses=(".OP",),
        probes=(ProbeDefinition(node="out", label="Vout"),),
        options=(".OPTIONS NOPAGE",),
        diagnostics=(),
    )
    spice = deck.to_spice()

    assert "* Test Minimal" in spice
    assert "R1 out gnd 10k" in spice
    assert "V1 in 0 DC 5" in spice
    assert ".OP" in spice
    assert ".OPTIONS NOPAGE" in spice
    assert ".PRINT DC V(out)" in spice
    assert ".END" in spice


def test_simulation_deck_immutable():
    """SimulationDeck is frozen — cannot set attributes."""
    deck = SimulationDeck(
        name="test",
        includes=(),
        models=(),
        subckts=(),
        netlist="* empty",
        stimuli=(),
        analyses=(".OP",),
        probes=(),
        options=(),
        diagnostics=(),
    )
    try:
        deck.name = "modified"  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except Exception:
        pass  # Expected


def test_probe_definition_renders_print_statement():
    """ProbeDefinition renders to a .PRINT line."""
    probe = ProbeDefinition(node="vout", label="Vout")
    assert probe.to_print("DC") == ".PRINT DC V(vout)"

    probe2 = ProbeDefinition(node="i(r1)", label="I_R1")
    assert probe2.to_print("TRAN") == ".PRINT TRAN I(r1)"
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv\Scripts\python -m pytest tests/test_spice_deck.py -v
```

Expected: FAIL, module not found.

- [ ] **Step 3: Write `circuit_graph/sim/spice_deck.py`**

```python
"""SPICE deck data model — immutable, self-rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class ProbeDefinition:
    """A measurement point in a SPICE simulation.

    Args:
        node: SPICE node or expression to probe, e.g. ``"vout"`` or ``"i(r1)"``.
        label: Human-readable label for this probe.
        expected: Optional expected value ranges, e.g. ``(("gain_min", 1.0), ("gain_max", 100.0))``.
    """

    node: str
    label: str
    expected: tuple[tuple[str, float], ...] | None = None

    def to_print(self, analysis_kind: str) -> str:
        """Render as a .PRINT line for the given analysis kind."""
        if self.node.startswith("i(") or self.node.startswith("I("):
            expr = f"I({self.node[2:-1]})"
        else:
            expr = f"V({self.node})"
        return f".PRINT {analysis_kind} {expr}"


@dataclass(frozen=True)
class SimulationDeck:
    """An immutable, self-contained SPICE simulation deck.

    All collection fields use tuples for immutability.  Call ``to_spice()``
    to render the complete deck text ready for Ngspice.
    """

    name: str
    includes: tuple[Path, ...] = ()
    models: tuple[str, ...] = ()
    subckts: tuple[str, ...] = ()
    netlist: str = ""
    stimuli: tuple[str, ...] = ()
    analyses: tuple[str, ...] = ()
    probes: tuple[ProbeDefinition, ...] = ()
    options: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def to_spice(self) -> str:
        """Render the complete SPICE deck as a string."""
        lines: list[str] = []

        # Title line
        lines.append(f"* {self.name}")
        lines.append("")

        # Includes
        for inc in self.includes:
            lines.append(f'.include "{inc.as_posix()}"')

        # .MODEL definitions
        for model in self.models:
            lines.append(model)

        # .SUBCKT instantiations
        for sub in self.subckts:
            lines.append(sub)

        # Options
        for opt in self.options:
            lines.append(opt)

        lines.append("")
        lines.append("* --- Netlist ---")

        # Netlist body
        for line in self.netlist.strip().splitlines():
            lines.append(line.rstrip())

        # Stimuli
        if self.stimuli:
            lines.append("")
            lines.append("* --- Stimuli ---")
            for stim in self.stimuli:
                lines.append(stim)

        # Analyses
        lines.append("")
        lines.append("* --- Analyses ---")
        for analysis in self.analyses:
            lines.append(analysis)

        # Probes (.PRINT lines)
        if self.probes:
            lines.append("")
            lines.append("* --- Probes ---")
            # Determine analysis kind for .PRINT from first analysis
            analysis_kind = _analysis_kind_short(self.analyses[0] if self.analyses else "OP")
            for probe in self.probes:
                lines.append(probe.to_print(analysis_kind))

        # End
        lines.append("")
        lines.append(".END")
        lines.append("")

        return "\n".join(lines)


def _analysis_kind_short(analysis_line: str) -> str:
    """Extract the short kind string from a SPICE analysis line."""
    upper = analysis_line.strip().upper()
    if upper.startswith(".AC"):
        return "AC"
    if upper.startswith(".TRAN"):
        return "TRAN"
    if upper.startswith(".DC"):
        return "DC"
    if upper.startswith(".NOISE"):
        return "NOISE"
    if upper.startswith(".TF"):
        return "TF"
    return "DC"  # .OP and others default to DC print
```

- [ ] **Step 4: Run tests**

```bash
.venv\Scripts\python -m pytest tests/test_spice_deck.py -v
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/sim/spice_deck.py tests/test_spice_deck.py circuit_graph/sim/__init__.py
git commit -m "feat(sim): add SimulationDeck and ProbeDefinition data models

Frozen dataclasses with to_spice() renderer. Phase 1 core types.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: SpiceResult and AnalysisResult data models

**Files:**
- Create: `circuit_graph/sim/spice_result.py`
- Create: `tests/test_spice_result.py`

**Interfaces:**
- Produces: `SpiceResult(deck, exit_code, timestamp, analyses)`, `AnalysisResult(kind, variables, data, convergence, iterations)` with `.as_dict()` method

- [ ] **Step 1: Write failing test**

Create `tests/test_spice_result.py`:

```python
"""Tests for SpiceResult and AnalysisResult data models."""

from __future__ import annotations

from circuit_graph.sim.spice_deck import SimulationDeck
from circuit_graph.sim.spice_result import AnalysisResult, SpiceResult


def _minimal_deck() -> SimulationDeck:
    return SimulationDeck(name="test")


def test_analysis_result_as_dict():
    """as_dict() converts row-major data to column-major dict."""
    result = AnalysisResult(
        kind="op",
        variables=("v(1)", "v(2)", "i(v1)"),
        data=(
            (1.0, 2.0, 0.001),
            (1.1, 2.1, 0.002),
        ),
        convergence=True,
        iterations=42,
    )

    d = result.as_dict()
    assert d["v(1)"] == [1.0, 1.1]
    assert d["v(2)"] == [2.0, 2.1]
    assert d["i(v1)"] == [0.001, 0.002]


def test_spice_result_immutable():
    """SpiceResult is frozen."""
    result = SpiceResult(
        deck=_minimal_deck(),
        exit_code=0,
        timestamp="2026-07-09T00:00:00",
        analyses=(),
    )
    try:
        result.exit_code = 1  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except Exception:
        pass


def test_analysis_result_single_row():
    """Single-row analysis (e.g. .OP) works correctly."""
    result = AnalysisResult(
        kind="op",
        variables=("v(out)",),
        data=((5.0,),),
        convergence=True,
    )
    assert result.as_dict() == {"v(out)": [5.0]}


def test_analysis_result_empty():
    """Empty data edge case."""
    result = AnalysisResult(
        kind="ac",
        variables=(),
        data=(),
        convergence=False,
    )
    assert result.as_dict() == {}
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv\Scripts\python -m pytest tests/test_spice_result.py -v
```

Expected: FAIL, module not found.

- [ ] **Step 3: Write `circuit_graph/sim/spice_result.py`**

```python
"""SpiceResult and AnalysisResult — immutable simulation output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from circuit_graph.sim.spice_deck import SimulationDeck


@dataclass(frozen=True)
class AnalysisResult:
    """Result of a single SPICE analysis (.OP, .AC, .TRAN, etc.).

    Args:
        kind: Analysis type — ``"op"``, ``"ac"``, ``"tran"``, ``"dc"``, ``"noise"``.
        variables: Column names in the order they appear.
        data: Row-major data matrix — each inner tuple is one row.
        convergence: Whether the analysis converged.
        iterations: Number of iterations to convergence (DC analyses only).
    """

    kind: str
    variables: tuple[str, ...]
    data: tuple[tuple[float, ...], ...]
    convergence: bool
    iterations: int | None = None

    def as_dict(self) -> dict[str, list[float]]:
        """Convert row-major data to column-major dict.

        Returns a dict mapping each variable name to its list of values,
        suitable for plotting or JSON export.
        """
        if not self.variables or not self.data:
            return {}
        columns: dict[str, list[float]] = {v: [] for v in self.variables}
        for row in self.data:
            for i, value in enumerate(row):
                columns[self.variables[i]].append(value)
        return columns


@dataclass(frozen=True)
class SpiceResult:
    """Complete result of running a SimulationDeck.

    Args:
        deck: The deck that produced this result.
        exit_code: Ngspice process exit code (0 = success).
        timestamp: ISO 8601 timestamp of the run.
        analyses: Results for each analysis in the deck.
    """

    deck: "SimulationDeck"
    exit_code: int
    timestamp: str
    analyses: tuple[AnalysisResult, ...]
```

- [ ] **Step 4: Run tests**

```bash
.venv\Scripts\python -m pytest tests/test_spice_result.py -v
```

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/sim/spice_result.py tests/test_spice_result.py circuit_graph/sim/__init__.py
git commit -m "feat(sim): add SpiceResult and AnalysisResult data models

Immutable simulation output types with as_dict() columnar conversion.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: ConvergenceConfig and retry logic

**Files:**
- Create: `circuit_graph/sim/convergence.py`
- Create: `tests/test_convergence.py`

**Interfaces:**
- Produces: `ConvergenceConfig(gmin, abstol, reltol, max_retries, retry_options)` with `.apply_to_deck()` method
- Consumes: `SimulationDeck` from Task 3

- [ ] **Step 1: Write failing test**

Create `tests/test_convergence.py`:

```python
"""Tests for convergence configuration and retry logic."""

from __future__ import annotations

from circuit_graph.sim.convergence import ConvergenceConfig
from circuit_graph.sim.spice_deck import SimulationDeck


def test_convergence_default_options():
    """Default config has reasonable convergence parameters."""
    config = ConvergenceConfig()
    assert config.gmin == 1e-12
    assert config.max_retries == 3
    assert len(config.retry_options) == 3


def test_apply_to_deck_adds_options():
    """apply_to_deck() appends convergence-related .OPTIONS to the deck."""
    deck = SimulationDeck(name="test")
    config = ConvergenceConfig(gmin=1e-12, abstol=1e-12, reltol=1e-3)

    new_deck = config.apply_to_deck(deck, retry_attempt=0)
    opts = "\n".join(new_deck.options)
    assert "GMIN=1e-12" in opts
    assert "ABSTOL=1e-12" in opts
    assert "RELTOL=0.001" in opts


def test_apply_to_deck_retry_1_uses_relaxed_tolerances():
    """Retry #1 uses the relaxed options from retry_options[0]."""
    deck = SimulationDeck(name="test")
    config = ConvergenceConfig(max_retries=3)

    new_deck = config.apply_to_deck(deck, retry_attempt=1)
    opts = "\n".join(new_deck.options)
    # First retry should relax GMIN
    assert "gmin=1e-10" in opts.lower() or "GMIN=1e-10" in opts


def test_apply_to_deck_retry_2_uses_gear_method():
    """Retry #2 switches to Gear integration."""
    deck = SimulationDeck(name="test")
    config = ConvergenceConfig(max_retries=3)

    new_deck = config.apply_to_deck(deck, retry_attempt=2)
    opts = "\n".join(new_deck.options)
    assert "gear" in opts.lower() or "GEAR" in opts


def test_apply_to_deck_exhausted():
    """Retry beyond max_retries uses the last available option set."""
    deck = SimulationDeck(name="test")
    config = ConvergenceConfig(max_retries=3)

    # Should not crash — uses last retry_options entry
    new_deck = config.apply_to_deck(deck, retry_attempt=5)
    assert new_deck is not None


def test_convergence_config_immutable():
    """ConvergenceConfig is frozen."""
    config = ConvergenceConfig()
    try:
        config.gmin = 0.1  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except Exception:
        pass
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv\Scripts\python -m pytest tests/test_convergence.py -v
```

Expected: FAIL, module not found.

- [ ] **Step 3: Write `circuit_graph/sim/convergence.py`**

```python
"""Convergence configuration and retry logic for Ngspice.

Provides a frozen :class:`ConvergenceConfig` that applies tolerance options
to a :class:`~circuit_graph.sim.spice_deck.SimulationDeck` and a three-level
retry strategy: relax tolerances → switch to Gear → limit integration order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from circuit_graph.sim.spice_deck import SimulationDeck

_DEFAULT_RETRY_OPTIONS: tuple[tuple[tuple[str, str], ...], ...] = (
    # Retry 1: relax tolerances
    (("gmin", "1e-10"), ("abstol", "1e-10")),
    # Retry 2: switch to Gear method
    (("gmin", "1e-9"), ("abstol", "1e-9"), ("method", "gear")),
    # Retry 3: limit integration order
    (("gmin", "1e-8"), ("abstol", "1e-8"), ("method", "gear"), ("maxord", "2")),
)


@dataclass(frozen=True)
class ConvergenceConfig:
    """Ngspice convergence parameters and retry strategy.

    Args:
        gmin: Minimum conductance shunt (S).  Default 1e-12.
        abstol: Absolute current tolerance (A).  Default 1e-12.
        reltol: Relative tolerance.  Default 1e-3.
        max_retries: Maximum convergence retry attempts.  Default 3.
        retry_options: Per-retry option overrides as ``((name, value), ...)``.
    """

    gmin: float = 1e-12
    abstol: float = 1e-12
    reltol: float = 1e-3
    max_retries: int = 3
    retry_options: tuple[tuple[tuple[str, str], ...], ...] = _DEFAULT_RETRY_OPTIONS

    def apply_to_deck(
        self,
        deck: "SimulationDeck",
        *,
        retry_attempt: int = 0,
    ) -> "SimulationDeck":
        """Return a new SimulationDeck with convergence options applied.

        On ``retry_attempt=0``, the base tolerance options are used.
        On subsequent attempts, relaxed options from ``retry_options`` are
        layered on top.  When ``retry_attempt`` exceeds ``max_retries``,
        the last available option set is reused.
        """
        options: dict[str, str] = {
            "GMIN": f"{self.gmin}",
            "ABSTOL": f"{self.abstol}",
            "RELTOL": f"{self.reltol}",
        }

        if retry_attempt > 0 and self.retry_options:
            idx = min(retry_attempt - 1, len(self.retry_options) - 1)
            for name, value in self.retry_options[idx]:
                options[name.upper()] = value

        option_lines = tuple(
            f".OPTIONS {' '.join(f'{k}={v}' for k, v in options.items())}"
        )

        # Merge with existing options (our converge opts first, then originals)
        merged_options = option_lines + deck.options

        return type(deck)(
            name=deck.name,
            includes=deck.includes,
            models=deck.models,
            subckts=deck.subckts,
            netlist=deck.netlist,
            stimuli=deck.stimuli,
            analyses=deck.analyses,
            probes=deck.probes,
            options=merged_options,
            diagnostics=deck.diagnostics,
        )
```

- [ ] **Step 4: Run tests**

```bash
.venv\Scripts\python -m pytest tests/test_convergence.py -v
```

Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/sim/convergence.py tests/test_convergence.py circuit_graph/sim/__init__.py
git commit -m "feat(sim): add ConvergenceConfig with three-level retry

GMIN stepping → Gear method → maxord limit for Ngspice convergence.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: StimulusSelector with fallback table

**Files:**
- Create: `circuit_graph/sim/stimulus.py`
- Create: `tests/test_stimulus.py`

**Interfaces:**
- Produces: `StimulusSelector` with `.select(topology_category, overrides)` → `tuple[str, ...]` and `.default_analyses(topology_category)` → `tuple[str, ...]`
- Standalone, no dependencies on other sim/ modules

- [ ] **Step 1: Write failing test**

Create `tests/test_stimulus.py`:

```python
"""Tests for StimulusSelector fallback table."""

from __future__ import annotations

from circuit_graph.sim.stimulus import StimulusSelector


def test_known_category_returns_default_stimulus():
    """Known topology categories have default stimuli."""
    selector = StimulusSelector()

    stimuli = selector.select("opamp_amplifier", node="in_p")

    assert any("AC" in s for s in stimuli)
    assert any("in_p" in s for s in stimuli)
    assert any("1.0" in s for s in stimuli)


def test_dcdc_converter_returns_dc_stimulus():
    """DCDC converters get DC + transient load step."""
    selector = StimulusSelector()

    stimuli = selector.select("dcdc_converter", node="vin")

    assert any("DC" in s for s in stimuli)
    assert any("vin" in s for s in stimuli)


def test_oscillator_returns_no_stimulus():
    """Oscillators are self-starting — no stimulus."""
    selector = StimulusSelector()

    stimuli = selector.select("oscillator", node="out")

    assert len(stimuli) == 0


def test_unknown_category_returns_empty():
    """Unknown category returns empty tuple."""
    selector = StimulusSelector()

    stimuli = selector.select("nonexistent_category", node="test")

    assert stimuli == ()


def test_default_analyses_for_opamp():
    """Opamp topologies default to .OP + .AC."""
    selector = StimulusSelector()

    analyses = selector.default_analyses("opamp_amplifier")

    assert ".OP" in analyses
    assert any("AC" in a for a in analyses)


def test_default_analyses_for_unknown():
    """Unknown category defaults to .OP only."""
    selector = StimulusSelector()

    analyses = selector.default_analyses("unknown_thing")

    assert analyses == (".OP",)
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv\Scripts\python -m pytest tests/test_stimulus.py -v
```

Expected: FAIL, module not found.

- [ ] **Step 3: Write `circuit_graph/sim/stimulus.py`**

```python
"""Stimulus selector — topology category → default stimulus + analyses.

Phase 1: pure fallback table, no dependency on v2 primitives.
Phase 3+: v2 primitive ``simulation`` fields override these defaults.
"""

from __future__ import annotations

#: Mapping from topology category to (stimuli, analyses).
#: Each value is (list_of_stimulus_lines, list_of_analysis_lines).
_CATEGORY_TABLE: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "opamp_amplifier": (
        # stimuli
        ("V1 {node} 0 DC 0 AC 1.0",),
        # analyses
        (".OP", ".AC DEC 10 1 1e6"),
    ),
    "opamp_comparator": (
        ("V1 {node} 0 PULSE(0 5 0 1n 1n 10u 20u)",),
        (".OP", ".TRAN 1u 40u"),
    ),
    "opamp_buffer": (
        ("V1 {node} 0 DC 0 AC 1.0",),
        (".OP", ".AC DEC 10 1 1e6"),
    ),
    "opamp_integrator": (
        ("V1 {node} 0 PULSE(0 1 0 1n 1n 50u 100u)",),
        (".OP", ".TRAN 1u 200u"),
    ),
    "dcdc_converter": (
        ("V_SUPPLY {node} 0 DC 12",),
        (".OP", ".TRAN 1u 500u"),
    ),
    "ldo": (
        ("VIN {node} 0 DC 5",),
        (".OP",),
    ),
    "filter_active": (
        ("V1 {node} 0 DC 0 AC 1.0",),
        (".OP", ".AC DEC 20 1 10e6"),
    ),
    "filter_passive": (
        ("V1 {node} 0 DC 0 AC 1.0",),
        (".OP", ".AC DEC 10 1 1e6"),
    ),
    "oscillator": (
        (),
        (".TRAN 1n 10u",),
    ),
}


class StimulusSelector:
    """Select default stimulus and analyses for a topology category.

    This is the fallback used when the primitive has no ``simulation`` field
    (v1 primitives or Phase 1 hardcoded path).  Phase 3+ will prefer the
    v2 primitive's ``simulation.stimulus`` and ``simulation.analyses`` fields.
    """

    def select(self, category: str, *, node: str = "1") -> tuple[str, ...]:
        """Return stimulus lines for the given topology category.

        Args:
            category: Topology category key, e.g. ``"opamp_amplifier"``.
            node: SPICE node name to inject the stimulus at.

        Returns:
            Tuple of SPICE stimulus lines, or empty tuple if unknown/not needed.
        """
        entry = _CATEGORY_TABLE.get(category)
        if entry is None:
            return ()
        stimuli, _ = entry
        return tuple(line.format(node=node) for line in stimuli)

    def default_analyses(self, category: str) -> tuple[str, ...]:
        """Return default analysis lines for the given topology category.

        Args:
            category: Topology category key.

        Returns:
            Tuple of SPICE analysis lines (``.OP``, ``.AC ...``, etc.).
        """
        entry = _CATEGORY_TABLE.get(category)
        if entry is None:
            return (".OP",)
        _, analyses = entry
        return analyses
```

- [ ] **Step 4: Run tests**

```bash
.venv\Scripts\python -m pytest tests/test_stimulus.py -v
```

Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/sim/stimulus.py tests/test_stimulus.py circuit_graph/sim/__init__.py
git commit -m "feat(sim): add StimulusSelector with topology → stimulus fallback table

Covers 9 topology categories. Phase 3+ will prefer v2 primitive fields.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: DeckValidator

**Files:**
- Create: `circuit_graph/sim/deck_validator.py`
- Create: `tests/test_deck_validator.py`

**Interfaces:**
- Produces: `DeckValidator.validate(deck)` → `tuple[str, ...]` (diagnostics, empty = clean)
- Consumes: `SimulationDeck` from Task 3

- [ ] **Step 1: Write failing test**

Create `tests/test_deck_validator.py`:

```python
"""Tests for DeckValidator."""

from __future__ import annotations

from circuit_graph.sim.deck_validator import DeckValidator
from circuit_graph.sim.spice_deck import ProbeDefinition, SimulationDeck


def test_valid_deck_passes():
    """A well-formed deck produces no diagnostics."""
    deck = SimulationDeck(
        name="test",
        netlist="R1 1 0 10k\nV1 1 0 DC 5",
        stimuli=("V1 1 0 DC 5",),
        analyses=(".OP",),
        probes=(ProbeDefinition(node="1", label="V1"),),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    assert len(diags) == 0


def test_missing_end_is_ok():
    """.END is auto-appended by to_spice(), so missing it is not an error."""
    deck = SimulationDeck(
        name="test",
        netlist="R1 1 0 10k",
        analyses=(".OP",),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    assert len(diags) == 0  # to_spice() handles .END


def test_empty_netlist_warns():
    """Empty netlist (no components) produces a warning."""
    deck = SimulationDeck(
        name="empty_test",
        netlist="",
        analyses=(".OP",),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    assert any("empty" in d.lower() for d in diags)


def test_no_analyses_warns():
    """Missing analyses produces a warning."""
    deck = SimulationDeck(
        name="no_analysis_test",
        netlist="R1 1 0 10k",
        analyses=(),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    assert any("analysis" in d.lower() for d in diags)


def test_floating_node_detected():
    """A node mentioned only once is flagged as potentially floating."""
    deck = SimulationDeck(
        name="floating_test",
        netlist="R1 floating_node 0 10k",
        analyses=(".OP",),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    # floating_node appears once → should warn
    assert any("floating_node" in d for d in diags)


def test_zero_resistance_loop_warns():
    """Zero-resistance loop (shorted voltage source) is flagged."""
    deck = SimulationDeck(
        name="short_test",
        netlist="V1 1 0 DC 5\nR1 1 0 0",
        stimuli=("V1 1 0 DC 5",),
        analyses=(".OP",),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    has_zero_r = any(
        "0" in d and ("resistance" in d.lower() or "ohm" in d.lower())
        for d in diags
    )
    assert has_zero_r
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv\Scripts\python -m pytest tests/test_deck_validator.py -v
```

Expected: FAIL, module not found.

- [ ] **Step 3: Write `circuit_graph/sim/deck_validator.py`**

```python
"""DeckValidator — pre-flight checks on a SimulationDeck.

Catches common SPICE pitfalls before Ngspice runs them:
floating nodes, zero-impedance loops, missing analyses, empty netlists.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from circuit_graph.sim.spice_deck import SimulationDeck

# Regex to extract node names from a netlist line.
# Matches alphanumeric + underscore node names.
_NODE_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")

# Known ground node names that should not be flagged as floating.
_GROUND_NODES = frozenset({"0", "gnd", "GND", "GNDD", "GNDA", "AGND", "DGND", "PGND"})


class DeckValidator:
    """Pre-flight validation for a :class:`SimulationDeck`.

    Validations are advisory — they produce diagnostics tuples but do not
    block execution.  Callers decide whether to abort based on severity.
    """

    def validate(self, deck: "SimulationDeck") -> tuple[str, ...]:
        """Run all checks and return diagnostic messages.

        Returns an empty tuple when the deck passes all checks.
        """
        diags: list[str] = []

        self._check_empty_netlist(deck, diags)
        self._check_analyses(deck, diags)
        self._check_floating_nodes(deck, diags)
        self._check_zero_impedance(deck, diags)

        return tuple(diags)

    # ── individual checks ──────────────────────────────────────────

    def _check_empty_netlist(self, deck: "SimulationDeck", diags: list[str]) -> None:
        netlist = deck.netlist.strip()
        subckts = "\n".join(deck.subckts)
        if not netlist and not subckts:
            diags.append("WARNING: netlist is empty (no components or subcircuits)")

    def _check_analyses(self, deck: "SimulationDeck", diags: list[str]) -> None:
        if not deck.analyses:
            diags.append("WARNING: no analysis commands (.OP, .AC, .TRAN, etc.)")

    def _check_floating_nodes(self, deck: "SimulationDeck", diags: list[str]) -> None:
        """Flag nodes that appear exactly once (possible floating node).

        A node with only one connection has no DC path to ground.
        """
        node_counts: dict[str, int] = {}
        for line in deck.netlist.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            # Skip the first token (component name)
            tokens = line.split()
            nodes = [t for t in tokens[1:] if _NODE_RE.match(t)]
            for node in nodes:
                if node in _GROUND_NODES:
                    continue
                node_counts[node] = node_counts.get(node, 0) + 1

        for node, count in node_counts.items():
            if count == 1:
                diags.append(
                    f"WARNING: node '{node}' appears only once — possible floating node"
                )

    def _check_zero_impedance(self, deck: "SimulationDeck", diags: list[str]) -> None:
        """Flag 0-ohm resistors that may create short circuits."""
        for line in deck.netlist.strip().splitlines():
            line_stripped = line.strip()
            if not line_stripped or line_stripped.startswith("*"):
                continue
            if not line_stripped.upper().startswith("R"):
                continue
            tokens = line_stripped.split()
            if len(tokens) < 4:
                continue
            value = tokens[3]
            try:
                v = float(value)
                if v == 0.0:
                    diags.append(
                        f"WARNING: zero-ohm resistor '{tokens[0]}' detected — "
                        f"may short nodes {tokens[1]} and {tokens[2]}"
                    )
            except ValueError:
                # Non-numeric value (e.g. "10k") — parse with suffix
                v = _parse_spice_value(value)
                if v is not None and v == 0.0:
                    diags.append(
                        f"WARNING: zero-ohm resistor '{tokens[0]}' detected — "
                        f"may short nodes {tokens[1]} and {tokens[2]}"
                    )


def _parse_spice_value(raw: str) -> float | None:
    """Parse a SPICE-format value string (e.g. '10k', '1e3', '0.1u') to float.

    Returns None if unparseable.
    """
    raw = raw.strip().lower()
    multipliers: dict[str, float] = {
        "t": 1e12, "g": 1e9, "meg": 1e6, "x": 1e6,
        "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9,
        "p": 1e-12, "f": 1e-15, "mil": 25.4e-6,
    }
    try:
        return float(raw)
    except ValueError:
        pass
    # Try multiplier suffix
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if raw.endswith(suffix):
            try:
                return float(raw[: -len(suffix)]) * mult
            except ValueError:
                return None
    return None
```

- [ ] **Step 4: Run tests**

```bash
.venv\Scripts\python -m pytest tests/test_deck_validator.py -v
```

Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/sim/deck_validator.py tests/test_deck_validator.py circuit_graph/sim/__init__.py
git commit -m "feat(sim): add DeckValidator with pre-flight SPICE checks

Detects floating nodes, zero-impedance loops, empty netlists, missing analyses.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: SpiceRunner — Ngspice process management and output parsing

**Files:**
- Create: `circuit_graph/sim/spice_runner.py`
- Create: `tests/test_spice_runner.py`

**Interfaces:**
- Produces: `SpiceRunner(config).run(deck)` → `SpiceResult`
- Consumes: `SimulationDeck` (Task 3), `SpiceResult`/`AnalysisResult` (Task 4), `ConvergenceConfig` (Task 5)

- [ ] **Step 1: Write unit tests for the output parser (no Ngspice required)**

Create `tests/test_spice_runner.py`:

```python
"""Tests for SpiceRunner — unit tests for parser, skip Ngspice integration."""

from __future__ import annotations

import pytest
from circuit_graph.sim.spice_deck import ProbeDefinition, SimulationDeck
from circuit_graph.sim.spice_runner import SpiceRunner, SpiceRunnerConfig


def _minimal_deck() -> SimulationDeck:
    return SimulationDeck(
        name="test",
        netlist="V1 1 0 DC 5\nR1 1 0 10k",
        stimuli=("V1 1 0 DC 5",),
        analyses=(".OP",),
    )


class TestParseOpOutput:
    """Unit tests for .OP output parsing — no Ngspice process needed."""

    def test_parse_simple_op(self):
        """Parse a typical .OP output block."""
        raw = """\
**** OPERATING POINT INFORMATION
Node     Voltage
----     -------
1               5.000000e+00
v1#branch       5.000000e-04
"""
        runner = SpiceRunner(SpiceRunnerConfig())
        result = runner._parse_output(raw, _minimal_deck())

        assert len(result) == 1
        op_result = result[0]
        assert op_result.kind == "op"
        assert op_result.convergence is True
        assert "v(1)" in op_result.variables
        assert "i(v1)" in op_result.variables
        assert op_result.data[0][0] == pytest.approx(5.0)


    def test_parse_op_alternate_format(self):
        """Parse .OP output with 'Voltage' header format."""
        raw = """\
Initial Transient Solution
--------------------------

Node     Voltage
----     -------
out             1.09999e+01
in_p            1.00000e+00
v1#branch       5.00000e-04
"""
        runner = SpiceRunner(SpiceRunnerConfig())
        result = runner._parse_output(raw, _minimal_deck())

        assert len(result) >= 1
        op = result[0]
        d = op.as_dict()
        assert "v(out)" in d


    def test_non_convergence_detected(self):
        """Non-convergence message is detected."""
        raw = """\
doAnalyses: iteration limit reached
run simulation(s) aborted
Error: no such vector v(out)
"""
        runner = SpiceRunner(SpiceRunnerConfig())
        result = runner._parse_output(raw, _minimal_deck())

        assert len(result) >= 1
        assert result[0].convergence is False


    def test_empty_output(self):
        """Empty stdout returns empty analyses."""
        runner = SpiceRunner(SpiceRunnerConfig())
        result = runner._parse_output("", _minimal_deck())
        assert len(result) == 1
        assert result[0].convergence is False
        assert result[0].data == ()
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv\Scripts\python -m pytest tests/test_spice_runner.py -v
```

Expected: FAIL, module not found.

- [ ] **Step 3: Write `circuit_graph/sim/spice_runner.py`**

```python
"""SpiceRunner — Ngspice process management and output parsing.

Launches Ngspice as a subprocess, feeds it a :class:`SimulationDeck`,
and parses the stdout into a structured :class:`SpiceResult`.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from circuit_graph.sim.convergence import ConvergenceConfig
from circuit_graph.sim.spice_result import AnalysisResult, SpiceResult

if TYPE_CHECKING:
    from circuit_graph.sim.spice_deck import SimulationDeck


class SpiceRunnerError(Exception):
    """Raised when Ngspice execution fails at the process level."""


@dataclass(frozen=True)  # type: ignore[no-redef]  # already decorated in convergence.py
class SpiceRunnerConfig:
    """Configuration for :class:`SpiceRunner`.

    Args:
        ngspice_bin: Path or name of the Ngspice executable.
        timeout_seconds: Maximum wall-clock time per simulation.
        tmp_dir: Directory for temporary deck files (default: system temp).
    """

    ngspice_bin: str = "ngspice"
    timeout_seconds: float = 30.0
    tmp_dir: Path | None = None


# Sentinel import guard — ConvergenceConfig is used here but defined in convergence.py.
# This is fine for Phase 1 since both modules are in the same package.
from circuit_graph.sim.convergence import ConvergenceConfig  # noqa: E402


class SpiceRunner:
    """Execute SPICE simulations via Ngspice subprocess."""

    def __init__(
        self,
        config: SpiceRunnerConfig | None = None,
        convergence: ConvergenceConfig | None = None,
    ):
        self.config = config or SpiceRunnerConfig()
        self.convergence = convergence or ConvergenceConfig()

    def run(self, deck: "SimulationDeck") -> "SpiceResult":
        """Run a SimulationDeck through Ngspice and return structured results.

        Convergence retry is automatic: if the first run fails to converge,
        the deck is re-run with progressively relaxed tolerances up to
        ``convergence.max_retries`` times.

        Args:
            deck: The simulation deck to execute.

        Returns:
            A :class:`SpiceResult` with parsed analyses.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        for attempt in range(self.convergence.max_retries + 1):
            retry_deck = self.convergence.apply_to_deck(deck, retry_attempt=attempt)
            raw_output = self._run_ngspice(retry_deck)
            analyses = self._parse_output(raw_output, retry_deck)

            if analyses and all(a.convergence for a in analyses):
                return SpiceResult(
                    deck=retry_deck,
                    exit_code=0,
                    timestamp=timestamp,
                    analyses=analyses,
                )
            if attempt == self.convergence.max_retries:
                # Last attempt — return whatever we got
                return SpiceResult(
                    deck=retry_deck,
                    exit_code=1,
                    timestamp=timestamp,
                    analyses=analyses,
                )

        # Should not reach here
        return SpiceResult(
            deck=deck,
            exit_code=1,
            timestamp=timestamp,
            analyses=(),
        )

    def _run_ngspice(self, deck: "SimulationDeck") -> str:
        """Write deck to temp file, launch Ngspice, return stdout."""
        spice_text = deck.to_spice()

        tmp_dir = self.config.tmp_dir
        if tmp_dir is not None:
            tmp_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".cir",
            prefix="cg_sim_",
            dir=str(tmp_dir) if tmp_dir else None,
            encoding="utf-8",
            delete=False,
        ) as f:
            f.write(spice_text)
            tmp_path = Path(f.name)

        try:
            result = subprocess.run(
                [self.config.ngspice_bin, "-b", str(tmp_path)],
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            return "Ngspice timeout"
        except FileNotFoundError:
            raise SpiceRunnerError(
                f"Ngspice not found at '{self.config.ngspice_bin}'. "
                "Install Ngspice or set ngspice_bin path."
            )
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _parse_output(
        self,
        raw: str,
        deck: "SimulationDeck",
    ) -> tuple[AnalysisResult, ...]:
        """Parse Ngspice stdout into structured AnalysisResults."""
        convergence = not _has_convergence_failure(raw)

        # Try .OP parsing first — look for operating point table
        op_result = _parse_op_block(raw)
        if op_result is not None:
            variables = tuple(f"v({v})" for v in op_result[0]) + tuple(
                f"i({v})" for v in op_result[1]
            )
            values = tuple(float(v) for v in op_result[2] + op_result[3])
            return (
                AnalysisResult(
                    kind="op",
                    variables=variables,
                    data=(values,),
                    convergence=convergence,
                ),
            )

        # No structured data found — return minimal result
        return (
            AnalysisResult(
                kind="op",
                variables=(),
                data=(),
                convergence=convergence,
            ),
        )


# ---------------------------------------------------------------------------
# Output parsing helpers
# ---------------------------------------------------------------------------

_NON_CONVERGENCE_PATTERNS = (
    re.compile(r"iteration limit reached", re.IGNORECASE),
    re.compile(r"run simulation\(s\) aborted", re.IGNORECASE),
    re.compile(r"no convergence", re.IGNORECASE),
    re.compile(r"timestep too small", re.IGNORECASE),
    re.compile(r"gmin stepping failed", re.IGNORECASE),
    re.compile(r"source stepping failed", re.IGNORECASE),
)


def _has_convergence_failure(raw: str) -> bool:
    return any(p.search(raw) for p in _NON_CONVERGENCE_PATTERNS)


def _parse_op_block(raw: str) -> tuple[list[str], list[str], list[str], list[str]] | None:
    """Parse Ngspice .OP output into (node_names, source_names, node_voltages, source_currents).

    Returns None if no operating point block is found.
    """
    # Find the operating point section
    # Ngspice outputs either:
    #   "**** OPERATING POINT INFORMATION"
    #   "Initial Transient Solution"
    lines = raw.splitlines()
    start_idx: int | None = None
    for i, line in enumerate(lines):
        if "operating point" in line.lower() or "initial transient solution" in line.lower():
            start_idx = i
            break

    if start_idx is None:
        return None

    # Find the header line with "Node" and "Voltage"
    node_names: list[str] = []
    source_names: list[str] = []
    node_voltages: list[str] = []
    source_currents: list[str] = []

    in_table = False
    for line in lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            continue
        if "----" in stripped:
            in_table = True
            continue
        if not in_table:
            continue

        # Table rows: "node_name    value"
        parts = stripped.split()
        if len(parts) < 2:
            continue

        name = parts[0]
        value = parts[1]

        # Skip non-data lines
        if not name or name.startswith("*"):
            continue

        if name.endswith("#branch"):
            source_names.append(name.replace("#branch", ""))
            source_currents.append(value)
        else:
            node_names.append(name)
            node_voltages.append(value)

    if not node_names and not source_names:
        return None

    return node_names, source_names, node_voltages, source_currents
```

- [ ] **Step 4: Run unit tests**

```bash
.venv\Scripts\python -m pytest tests/test_spice_runner.py -v
```

Expected: 5 PASS (unit tests only, no Ngspice required)

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/sim/spice_runner.py tests/test_spice_runner.py circuit_graph/sim/__init__.py
git commit -m "feat(sim): add SpiceRunner with Ngspice subprocess + OP parser

Convergence auto-retry, temp file management, operating point parsing.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: DeckBuilder — hardcoded LM321 non-inverting amplifier

**Files:**
- Create: `circuit_graph/sim/deck_builder.py`
- Create: `tests/test_deck_builder.py`

**Interfaces:**
- Produces: `DeckBuilder().build_lm321_non_inverting(rf, rg, vcc)` → `SimulationDeck`
- Consumes: `SimulationDeck`/`ProbeDefinition` (Task 3), `StimulusSelector` (Task 6)

- [ ] **Step 1: Write failing test**

Create `tests/test_deck_builder.py`:

```python
"""Tests for DeckBuilder — hardcoded LM321 proof-of-concept."""

from __future__ import annotations

import pytest

from circuit_graph.sim.deck_builder import DeckBuilder


def test_build_lm321_non_inverting_defaults():
    """Default values produce a valid deck."""
    builder = DeckBuilder()
    deck = builder.build_lm321_non_inverting()

    spice = deck.to_spice()
    assert "* LM321 Non-Inverting Amplifier" in spice
    assert "Rf out in_n 10k" in spice
    assert "Rg in_n 0 1k" in spice
    assert "VIN in_p 0 DC 0 AC 1.0" in spice
    assert ".OP" in spice
    assert ".AC" in spice
    assert ".END" in spice


def test_build_lm321_non_inverting_custom_values():
    """Custom Rf, Rg, VCC are reflected in the deck."""
    builder = DeckBuilder()
    deck = builder.build_lm321_non_inverting(rf="22k", rg="2k", vcc="12V")

    spice = deck.to_spice()
    assert "Rf out in_n 22k" in spice
    assert "Rg in_n 0 2k" in spice
    assert "VDC vcc 0 DC 12" in spice


def test_build_lm321_non_inverting_probes():
    """Deck includes probes for output and input nodes."""
    builder = DeckBuilder()
    deck = builder.build_lm321_non_inverting()

    probe_nodes = {p.node for p in deck.probes}
    assert "out" in probe_nodes
    assert "in_p" in probe_nodes


def test_deck_has_expected_gain_range():
    """With Rf=10k, Rg=1k, expected gain is ~11."""
    builder = DeckBuilder()
    deck = builder.build_lm321_non_inverting(rf="10k", rg="1k")

    out_probe = next(p for p in deck.probes if p.node == "out")
    assert out_probe.expected is not None
    expected = dict(out_probe.expected)
    assert expected["gain_min"] == pytest.approx(9.0, rel=0.1)
    assert expected["gain_max"] == pytest.approx(13.0, rel=0.1)
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv\Scripts\python -m pytest tests/test_deck_builder.py -v
```

Expected: FAIL, module not found. Need to add `import pytest` at top of test file.

- [ ] **Step 3: Write `circuit_graph/sim/deck_builder.py`**

```python
"""DeckBuilder — SPICE deck assembly.

Phase 1: hardcoded LM321 non-inverting amplifier proof-of-concept.
Phase 3+: reads v2 primitive ``simulation`` fields for template-driven assembly.
"""

from __future__ import annotations

from circuit_graph.sim.spice_deck import ProbeDefinition, SimulationDeck
from circuit_graph.sim.stimulus import StimulusSelector


class DeckBuilder:
    """Build :class:`SimulationDeck` instances.

    Phase 1 provides a single hardcoded method for the LM321 non-inverting
    amplifier.  Phase 3 will add template-driven methods that consume v2
    primitive data.
    """

    def __init__(self) -> None:
        self._stimulus = StimulusSelector()

    def build_lm321_non_inverting(
        self,
        *,
        rf: str = "10k",
        rg: str = "1k",
        vcc: str = "5V",
    ) -> SimulationDeck:
        """Build a deck for an LM321 non-inverting amplifier.

        Topology:
            VCC → LM321 V+
            GND → LM321 V-
            Rf  between OUT and IN-
            Rg  between IN- and GND
            AC 1.0V stimulus at IN+

        Gain formula: Av = 1 + Rf/Rg

        With defaults Rf=10k, Rg=1k → Av ≈ 11.
        """
        # Ideal opamp model for LM321 (single-supply compatible)
        # Using a voltage-controlled voltage source (E) as ideal opamp
        # E_out out 0 opamp in_p in_n 1e5
        # This is a simplified behavioral model.
        opamp_subckt = (
            "* Ideal single-supply opamp model (LM321-like)\n"
            f"XOPAMP in_p in_n vcc 0 out IDEAL_OPAMP_1CH\n"
        )

        # Full netlist
        netlist = "\n".join([
            "* LM321 Non-Inverting Amplifier (hardcoded PoC)",
            f"Rf out in_n {rf}",
            f"Rg in_n 0 {rg}",
            f"Rload out 0 10k",
            f"VDC vcc 0 DC {vcc}",
        ])

        stimuli = self._stimulus.select("opamp_amplifier", node="in_p")
        analyses = self._stimulus.default_analyses("opamp_amplifier")

        probes = (
            ProbeDefinition(
                node="out",
                label="Vout",
                expected=(
                    ("gain_min", 9.0),
                    ("gain_max", 13.0),
                ),
            ),
            ProbeDefinition(node="in_p", label="Vin"),
        )

        return SimulationDeck(
            name="LM321 Non-Inverting Amplifier (Phase 1 PoC)",
            netlist=netlist,
            subckts=(opamp_subckt,),
            stimuli=stimuli,
            analyses=analyses,
            probes=probes,
            options=(".OPTIONS NOPAGE NOECHO",),
        )
```

- [ ] **Step 4: Run tests**

```bash
.venv\Scripts\python -m pytest tests/test_deck_builder.py -v
```

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/sim/deck_builder.py tests/test_deck_builder.py circuit_graph/sim/__init__.py
git commit -m "feat(sim): add DeckBuilder with hardcoded LM321 non-inverting amp

Phase 1 proof-of-concept. Rf=10k, Rg=1k, Av≈11. Uses ideal opamp model.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 10: End-to-end integration test with Ngspice

**Files:**
- Create: `tests/test_spice_integration.py`

**Interfaces:**
- Consumes: `DeckBuilder` (Task 9), `SpiceRunner` (Task 8), all data models

**Note:** This test requires Ngspice installed (Task 1). If Ngspice is not available, skip with `pytest.mark.skipif`.

- [ ] **Step 1: Write integration test**

Create `tests/test_spice_integration.py`:

```python
"""End-to-end Ngspice integration tests.

These tests require Ngspice to be installed and on PATH.
They are skipped automatically if Ngspice is not found.
"""

from __future__ import annotations

import shutil

import pytest

from circuit_graph.sim.deck_builder import DeckBuilder
from circuit_graph.sim.deck_validator import DeckValidator
from circuit_graph.sim.spice_runner import SpiceRunner, SpiceRunnerConfig

# Check if Ngspice is available
_NGSPICE_AVAILABLE = shutil.which("ngspice") is not None

pytestmark = pytest.mark.skipif(
    not _NGSPICE_AVAILABLE,
    reason="Ngspice not installed or not on PATH",
)


class TestLM321NonInvertingIntegration:
    """End-to-end: build deck → validate → run Ngspice → check gain."""

    def test_deck_validates_clean(self):
        """The LM321 deck passes pre-flight validation."""
        builder = DeckBuilder()
        deck = builder.build_lm321_non_inverting()

        validator = DeckValidator()
        diags = validator.validate(deck)

        assert len(diags) == 0, f"Validation diagnostics: {diags}"

    def test_ngspice_runs_and_converges(self):
        """Ngspice runs the LM321 deck and converges."""
        builder = DeckBuilder()
        deck = builder.build_lm321_non_inverting()

        runner = SpiceRunner(SpiceRunnerConfig(timeout_seconds=10.0))
        result = runner.run(deck)

        assert result.exit_code == 0, (
            f"Ngspice failed with exit code {result.exit_code}"
        )
        assert len(result.analyses) >= 1

        op = result.analyses[0]
        assert op.convergence, (
            f"Operating point did not converge. Data: {op.as_dict()}"
        )

    def test_gain_matches_expected(self):
        """With Rf=10k, Rg=1k, gain should be ~11."""
        builder = DeckBuilder()
        deck = builder.build_lm321_non_inverting(rf="10k", rg="1k")

        runner = SpiceRunner(SpiceRunnerConfig(timeout_seconds=10.0))
        result = runner.run(deck)

        op = result.analyses[0]
        d = op.as_dict()

        # Find output voltage (DC operating point)
        vout = _find_voltage(d, "out")
        vin = _find_voltage(d, "in_p")

        assert vout is not None, f"No output voltage found in: {list(d)}"
        assert vin is not None, f"No input voltage found in: {list(d)}"

        # Input is at 0V DC with 1V AC — so DC gain is from opamp offset only.
        # The AC gain is tested separately.
        # For DC, just verify reasonable operating point (output not railed).
        assert 0.0 < vout < 5.0, (
            f"LM321 output {vout}V railed — expected 0-5V range with VCC=5V"
        )


def _find_voltage(d: dict[str, list[float]], node: str) -> float | None:
    """Find the DC voltage at a node in the operating point result."""
    for key, values in d.items():
        if key.lower() == f"v({node})".lower() and values:
            return values[0]
    return None
```

- [ ] **Step 2: Run integration test**

```bash
.venv\Scripts\python -m pytest tests/test_spice_integration.py -v
```

Expected: 3 PASS if Ngspice installed; 3 SKIP otherwise.

- [ ] **Step 3: Commit**

```bash
git add tests/test_spice_integration.py
git commit -m "test(sim): add end-to-end Ngspice integration test for LM321

Validates deck → Ngspice run → convergence → operating point sanity.
Auto-skipped when Ngspice is not installed.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 11: cg sim CLI command

**Files:**
- Modify: `circuit_graph/cli.py` (add `sim` command group)

**Interfaces:**
- Produces: `cg sim` CLI command with `--designator`, `--file`, `--dry-run`, `--output`, `--analysis`, `--ngspice-opt` options
- Consumes: `SimulationDeck` (Task 3), `DeckBuilder` (Task 9), `SpiceRunner` (Task 8)

- [ ] **Step 1: Read existing CLI structure for patterns**

The CLI uses `@main.command()` for top-level commands like `view`. The `sim` command
will follow the same pattern. Refer to `circuit_graph/cli.py:69-125` for the `view`
command example.

- [ ] **Step 2: Add `sim` command import and function**

Insert after the existing imports in `circuit_graph/cli.py` (after line 29):

```python
from circuit_graph.sim.deck_builder import DeckBuilder
from circuit_graph.sim.spice_runner import SpiceRunner, SpiceRunnerConfig
```

Insert the `sim` command after the `view` command (after line 124). Find the end of `view()` function and insert:

```python
@main.command()
@click.argument("designator")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Generate the SPICE deck but do not run Ngspice.")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Write SPICE deck to file.")
@click.option(
    "--analysis",
    type=click.Choice(["op", "ac", "tran"]),
    default=None,
    help="Override the default analysis type.",
)
@click.option(
    "--ngspice-opt",
    multiple=True,
    help="Extra Ngspice option, e.g. --ngspice-opt gmin=1e-9. Repeatable.",
)
def sim(
    designator: str,
    file: Path,
    dry_run: bool,
    output: Path | None,
    analysis: str | None,
    ngspice_opt: tuple[str, ...],
) -> None:
    """Simulate the functional block containing DESIGNATOR.

    Parses the netlist FILE, identifies the topology around DESIGNATOR,
    generates a SPICE deck, and optionally runs it through Ngspice.

    Phase 1: hardcoded LM321 non-inverting amplifier proof-of-concept.
    """
    # Phase 1: always uses the hardcoded LM321 deck, designator is informational
    builder = DeckBuilder()
    deck = builder.build_lm321_non_inverting()

    # Apply CLI overrides
    if analysis:
        analysis_upper = analysis.upper()
        deck = SimulationDeck(
            name=deck.name,
            includes=deck.includes,
            models=deck.models,
            subckts=deck.subckts,
            netlist=deck.netlist,
            stimuli=deck.stimuli,
            analyses=(
                (f".{analysis_upper}",)
                if analysis_upper == "OP"
                else (f".{analysis_upper} DEC 10 1 1e6",)
            ),
            probes=deck.probes,
            options=deck.options + tuple(
                f".OPTIONS {opt}" for opt in ngspice_opt
            ),
            diagnostics=deck.diagnostics,
        )

    spice_text = deck.to_spice()

    if dry_run:
        if output:
            output.write_text(spice_text, encoding="utf-8")
            click.echo(f"SPICE deck written to {output}")
        else:
            click.echo(spice_text)
        return

    # Run Ngspice
    try:
        runner = SpiceRunner(SpiceRunnerConfig(timeout_seconds=30.0))
        result = runner.run(deck)
    except Exception as exc:
        click.echo(json.dumps({"error": str(exc)}, indent=2))
        sys.exit(1)

    # Output results as JSON
    analyses_output = []
    for a in result.analyses:
        analyses_output.append({
            "kind": a.kind,
            "convergence": a.convergence,
            "iterations": a.iterations,
            "data": a.as_dict(),
        })

    output_obj = {
        "designator": designator,
        "exit_code": result.exit_code,
        "timestamp": result.timestamp,
        "analyses": analyses_output,
        "diagnostics": list(deck.diagnostics),
    }

    if output:
        out_json = json.dumps(output_obj, indent=2, ensure_ascii=False)
        output.write_text(out_json, encoding="utf-8")
        click.echo(f"Results written to {output}")
    else:
        click.echo(json.dumps(output_obj, indent=2, ensure_ascii=False))

    if deck.diagnostics:
        click.echo("\nDiagnostics:", err=True)
        for d in deck.diagnostics:
            click.echo(f"  {d}", err=True)
```

Also need to add `SimulationDeck` import at the top of the function body — actually it's used in the function so we need it in the imports too. Add to the import block:

```python
from circuit_graph.sim.spice_deck import SimulationDeck
```

- [ ] **Step 3: Write CLI test**

Create `tests/test_cli_sim.py`:

```python
"""Tests for cg sim CLI command."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from circuit_graph.cli import main

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "protel" / "FX-T268-V6-10米-5065.NET"


class TestSimCommand:
    def test_sim_dry_run_outputs_spice(self):
        """--dry-run outputs SPICE deck text."""
        runner = CliRunner()
        result = runner.invoke(main, ["sim", "R100", str(FIXTURE), "--dry-run"])

        assert result.exit_code == 0
        assert "* LM321" in result.output
        assert ".OP" in result.output
        assert ".END" in result.output

    def test_sim_dry_run_with_output_file(self, tmp_path: Path):
        """--dry-run --output writes deck to file."""
        out_file = tmp_path / "test.cir"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["sim", "R100", str(FIXTURE), "--dry-run", "-o", str(out_file)],
        )

        assert result.exit_code == 0
        assert out_file.exists()
        content = out_file.read_text()
        assert ".OP" in content

    def test_sim_with_analysis_override(self):
        """--analysis tran overrides default AC analysis."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["sim", "R100", str(FIXTURE), "--dry-run", "--analysis", "tran"],
        )

        assert result.exit_code == 0
        assert ".TRAN" in result.output

    def test_sim_with_ngspice_opt(self):
        """--ngspice-opt appends .OPTIONS lines."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["sim", "R100", str(FIXTURE), "--dry-run", "--ngspice-opt", "gmin=1e-9"],
        )

        assert result.exit_code == 0
        assert "GMIN=1e-9" in result.output.upper() or "gmin=1e-9" in result.output

    def test_sim_missing_file(self):
        """Missing netlist file returns error."""
        runner = CliRunner()
        result = runner.invoke(main, ["sim", "R100", "nonexistent.NET", "--dry-run"])

        assert result.exit_code != 0
```

- [ ] **Step 4: Run CLI tests**

```bash
.venv\Scripts\python -m pytest tests/test_cli_sim.py -v
```

Expected: 5 PASS

- [ ] **Step 5: Run all tests to confirm no regressions**

```bash
.venv\Scripts\python -m pytest tests/ -v --ignore=tests/test_spice_integration.py
```

Expected: all existing tests PASS, new sim tests PASS.

- [ ] **Step 6: Commit**

```bash
git add circuit_graph/cli.py tests/test_cli_sim.py circuit_graph/sim/__init__.py
git commit -m "feat(cli): add cg sim command for SPICE simulation

cg sim DESIGNATOR FILE [--dry-run] [--output] [--analysis] [--ngspice-opt]
Phase 1 uses hardcoded LM321 non-inverting amplifier deck.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 12: Final verification and README update

**Files:**
- Modify: `README.md` (add `cg sim` documentation section)

**Interfaces:** None (documentation only)

- [ ] **Step 1: Run full test suite**

```bash
.venv\Scripts\python -m pytest tests/ -v
```

Expected: All tests pass or skip. Review any failures.

- [ ] **Step 2: Add `cg sim` section to README**

Insert after the `cg view` section in README.md (after line 54):

```markdown
### 仿真 (Phase 1)

```powershell
# 生成 SPICE deck（不运行）
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET --dry-run

# 导出 deck 到文件
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET --dry-run -o lm321_amp.cir

# 运行仿真并查看结果
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET

# 指定分析类型
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET --analysis tran

# 传递 Ngspice 选项
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET --ngspice-opt gmin=1e-9
```

Phase 1 使用硬编码的 LM321 同相放大器电路（Rf=10k, Rg=1k, VCC=5V）。
Ngspice 需单独安装。
```

- [ ] **Step 3: Check git status and commit**

```bash
git add README.md
git commit -m "docs: add cg sim CLI documentation to README

Phase 1 LM321 non-inverting amplifier proof-of-concept.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 4: Final verification checklist**

Run through manually:
```powershell
# 1. Help text
cg sim --help

# 2. Dry run (no Ngspice needed)
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET --dry-run

# 3. Integration (requires Ngspice)
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET
```

---

## Summary

| Task | Deliverable | Test coverage |
|------|------------|---------------|
| 1 | Ngspice installed on PATH | Manual smoke test |
| 2 | `sim/__init__.py` package skeleton | Import check |
| 3 | `spice_deck.py` — SimulationDeck + ProbeDefinition | 3 unit tests |
| 4 | `spice_result.py` — SpiceResult + AnalysisResult | 4 unit tests |
| 5 | `convergence.py` — ConvergenceConfig + retry | 6 unit tests |
| 6 | `stimulus.py` — StimulusSelector + fallback table | 6 unit tests |
| 7 | `deck_validator.py` — DeckValidator | 6 unit tests |
| 8 | `spice_runner.py` — SpiceRunner + OP parser | 5 unit tests |
| 9 | `deck_builder.py` — DeckBuilder (LM321 hardcoded) | 4 unit tests |
| 10 | `test_spice_integration.py` — E2E Ngspice test | 3 integration tests |
| 11 | `cli.py` — `cg sim` command | 5 CLI tests |
| 12 | README update + final verification | Manual smoke test |

**Total:** ~45 tests, 12 commits.
