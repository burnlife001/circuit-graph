# Signal-name fallback for primitive templates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every shipped primitive template match Protel auto-named nets (e.g. `NetR97_1`, `NetC82_1`, `USB_5V`) through a built-in fallback pattern table — without editing any `.json` template.

**Architecture:** Extend `signal_name_matches` with an optional `signal_id` kwarg. When the user-supplied regex misses, OR-check against an in-module `_DEFAULT_SIGNAL_PATTERNS[signal_id]` list. The vf2_matcher passes `signal_id=prim_data.id` for net matches; everywhere else, the function's existing two-arg signature stays unchanged so all pre-existing tests stay green.

**Tech Stack:** Python 3.x, `pytest`, `re`, `networkx` (already vendored). No new deps.

## Global Constraints

From the spec (`docs/superpowers/specs/2026-08-17-signal-name-fallback-design.md`):

- **No `primitives/*.json` may be edited.** Fallback is matcher-side.
- **Two-arg `signal_name_matches(pattern, name)` must keep its current strict semantics** (returns True only if user's regex matches).
- **All 21 existing primitive tests must pass unchanged** after this work.
- **`scoring` weights and thresholds are out of scope** — do not touch.
- **Per `CLAUDE.md`:** commit per logical unit; `Readme.md` update on completion; `git push` afterwards. No merge/squash unless explicitly asked.
- **Line endings LF only** (UTF-8, no CRLF).

## File Structure

| File | Role | Change |
| --- | --- | --- |
| `circuit_graph/primitives.py` | Hosts `signal_name_matches` and the new fallback table | extend function + add `_DEFAULT_SIGNAL_PATTERNS` dict |
| `circuit_graph/vf2_matcher.py` | Calls `signal_name_matches` during net match | pass `signal_id=prim_data.id` (1 line) |
| `tests/test_primitives.py` | Houses primitive-related unit tests | add 1 new function `test_signal_name_matches_falls_back_to_default_for_protel_nets` |
| `tests/test_vf2_matcher.py` | Houses integration-ish tests | add `test_non_inverting_amplifier_matches_protel_auto_named_nets` |
| `Readme.md` | Project overview doc | one-line note about the fallback behavior |
| `docs/superpowers/specs/2026-08-17-signal-name-fallback-design.md` | Source-of-truth design doc | committed as-is, no further edits |

---

### Task 1: Commit the spec doc

**Files:**
- Create (already created): `docs/superpowers/specs/2026-08-17-signal-name-fallback-design.md`

- [ ] **Step 1: Verify the spec file exists**

Run:
```bash
ls -la "E:/__electric/__workspace/stacking/circuit-graph/docs/superpowers/specs/2026-08-17-signal-name-fallback-design.md"
```
Expected: file present, size > 1 KB.

- [ ] **Step 2: Commit**

Run:
```bash
cd "E:/__electric/__workspace/stacking/circuit-graph"
git add docs/superpowers/specs/2026-08-17-signal-name-fallback-design.md
git commit -m "docs(spec): signal-name fallback for primitive templates

Spec for one-shot matcher-side fix that lets all 21 primitive templates
match Protel auto-named nets (NetR97_1, USB_5V, ...) without editing any
.json. Fallback table lives in primitives.py and is keyed by signal.id."
```

---

### Task 2: TDD — signal_name_matches fallback (failing test → impl → pass → commit)

**Files:**
- Modify: `circuit_graph/primitives.py:320-322` (signal_name_matches) and ~line 80+ (add `_DEFAULT_SIGNAL_PATTERNS` near the top)
- Modify: `tests/test_primitives.py` (add 1 test)

**Interfaces:**
- Consumes: signal name (str), user regex (str), optional `signal_id` (str).
- Produces: `bool` — True iff user regex matches OR any default fallback for that id matches.

- [ ] **Step 1: Write the failing test**

Open `tests/test_primitives.py` and append at the end:

```python
def test_signal_name_matches_falls_back_to_default_for_protel_nets():
    """signal_name_matches must OR in _DEFAULT_SIGNAL_PATTERNS[sigid]
    when the user pattern misses, so all 21 templates match Protel nets.
    """
    from circuit_graph.primitives import signal_name_matches

    # User pattern miss + id-keyed default hit ⇒ True
    assert signal_name_matches(r"^OUT$", "NetR100_1", signal_id="out") is True
    assert signal_name_matches(r"^VIN\+$", "NetC82_1", signal_id="in_p") is True
    assert signal_name_matches(r"^VIN\-$", "NetR97_1", signal_id="in_n") is True
    # Protel-style power rail
    assert signal_name_matches(r"^VCC$", "USB_5V", signal_id="vcc") is True

    # Two-arg form MUST remain strict (no fallback)
    assert signal_name_matches(r"^OUT$", "NetR100_1") is False
    assert signal_name_matches(r"^VIN\-$", "NetR97_1") is False

    # User hit short-circuits before fallback
    assert signal_name_matches(r"^GND$", "GND") is True
    assert signal_name_matches(r"^GND$", "GND", signal_id="gnd") is True

    # Unknown signal_id with no defaults ⇒ False
    assert signal_name_matches(r"^X$", "anything", signal_id="__no_such_id__") is False
```

- [ ] **Step 2: Run test to verify it FAILS**

Run:
```bash
cd "E:/__electric/__workspace/stacking/circuit-graph"
./.venv/Scripts/python.exe -m pytest tests/test_primitives.py::test_signal_name_matches_falls_back_to_default_for_protel_nets -v
```
Expected: FAIL. The 4 `signal_id=` calls raise `TypeError: signal_name_matches() got an unexpected keyword argument 'signal_id'`.

- [ ] **Step 3: Add `_DEFAULT_SIGNAL_PATTERNS` and extend `signal_name_matches`**

Edit `circuit_graph/primitives.py`. Add this dict **above** `signal_name_matches` (after line 318, just before line 320):

```python
_DEFAULT_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    # ----- Power rails -----
    "vcc": (
        r".*VDD.*", r".*VCC.*", r"^V[+A-Z0-9]+$", r".*V$",
        r"^[+]?\d+(\.\d+)?V(_.*)?$", r"^USB_5V$",
        r"^PVCC$", r"^AVCC$", r"^AVDD$",
    ),
    "gnd": (
        r"^GND\d*$", r".*GND$", r"^PGND$", r"^AGND$", r"^DGND$",
        r"^CGND$", r".*VSS$", r".*VEE$", r".*_GND$", r"^GND_[A-Z0-9]+$",
    ),
    "vcc_low":  (r"^Net.*$", r".*VDD.*LOW.*", r".*_LOW$"),
    "vcc_high": (r"^Net.*$", r".*VDD.*HIGH.*", r".*_HIGH$"),

    # ----- Inputs (semantic + Protel auto-named) -----
    "in_p":      (r"^Net.*_1$", r"^AINP.*$", r"^INP.*$", r"^V\+IN.*$", r"^VIP$", r"^POS$"),
    "in_n":      (r"^Net.*_1$", r"^AINN.*$", r"^INN.*$", r"^V\-IN.*$", r"^VIN$", r"^NEG$"),
    "vin":       (r"^Net.*_1$", r"^VIN.*$", r"^V[Ii]n.*$", r"^V[_]?[Ii]n.*$"),

    # ----- Outputs -----
    "out":       (r"^Net.*_1$", r"^V?OUT.*$", r"^VO$", r"^AMP_OUT.*$",
                  r"^OA_OUT.*$", r"^OP_OUT.*$", r"^VOUT.*$", r"^AOUT.*$"),
    "vout":      (r"^Net.*_1$", r"^V?OUT.*$", r"^VOUT.*$", r"^AOUT.*$"),

    # ----- Generic signals -----
    "signal":    (r"^Net.*$", r"^[A-Z]+_\d+V$", r"^SIG.*$", r"^A(IO|IN|OUT)\d+$"),
    "sig_in":    (r"^Net.*$", r"^SIG.*$", r"^VIN.*$", r"^AIN.*$"),
    "sig_p":     (r"^Net.*$", r"^SIG.*P$", r"^.+_P$"),
    "sig_n":     (r"^Net.*$", r"^SIG.*N$", r"^.+_N$"),
    "sig_low":   (r"^Net.*$", r".*_LOW$"),
    "sig_high":  (r"^Net.*$", r".*_HIGH$"),
    "load_net":  (r"^Net.*$", r"^LOAD.*$", r"^.+_LOAD$"),

    # ----- Differential / specific -----
    "ac1":       (r"^Net.*_1$", r"^AC\d?$", r"^A$"),
    "ac2":       (r"^Net.*_2$", r"^AC\d?$", r"^B$"),
    "dc_plus":   (r"^Net.*$", r"^D\+$", r"^DC\+$", r"^V\+IN$"),
    "dc_minus":  (r"^Net.*$", r"^D\-$", r"^DC\-$", r"^V\-IN$"),
    "xtal_p":    (r"^Net.*$", r"^XT.*P$", r"^XI$", r"^XIN$"),
    "xtal_n":    (r"^Net.*$", r"^XT.*N$", r"^XO$", r"^XOUT$"),

    # ----- Switches / drivers -----
    "ctrl":      (r"^Net.*$", r"^CTRL.*$", r".*_CTRL$", r"^GATE\d?$"),
    "high_side": (r"^Net.*$", r".*HS$", r".*_HIGH$"),
    "low_side":  (r"^Net.*$", r".*LS$", r".*_LOW$"),
    "pwr_in":    (r"^Net.*$", r"^PWR.*IN.*$", r".+_IN$"),
    "pwr_out":   (r"^Net.*$", r"^PWR.*OUT.*$", r".+_OUT$"),

    # ----- Pull-ups / CC / protection -----
    "pull_up":   (r"^Net.*$", r".*PULLUP.*", r".*PU$"),
    "cc":        (r"^CC[12]?$", r".*CC\d?$"),
    "protected": (r"^Net.*$", r"^PROT.*$"),
}
```

Replace the existing function (currently 2 lines):

```python
def signal_name_matches(pattern: str, name: str) -> bool:
    """Check whether ``name`` matches ``pattern`` (case-insensitive regex)."""
    return bool(re.search(pattern, name, re.IGNORECASE))
```

with:

```python
def signal_name_matches(
    pattern: str,
    name: str,
    *,
    signal_id: str | None = None,
) -> bool:
    """Check whether ``name`` matches ``pattern`` (case-insensitive regex).

    With the keyword-only ``signal_id``, a built-in default fallback table
    is consulted *after* the user pattern misses — this lets the matcher
    match Protel auto-named nets (``NetR97_1``, ``USB_5V`` ...) without
    editing any primitive template. The two-arg form is unchanged and
    triggers no fallback, so all pre-existing tests stay green.
    """
    if re.search(pattern, name, re.IGNORECASE):
        return True
    if signal_id is None:
        return False
    for fallback in _DEFAULT_SIGNAL_PATTERNS.get(signal_id, ()):
        if re.search(fallback, name, re.IGNORECASE):
            return True
    return False
```

- [ ] **Step 4: Run test, verify it PASSES**

Run:
```bash
cd "E:/__electric/__workspace/stacking/circuit-graph"
./.venv/Scripts/python.exe -m pytest tests/test_primitives.py::test_signal_name_matches_falls_back_to_default_for_protel_nets -v
```
Expected: PASS, all 7 assertions inside the test succeed.

- [ ] **Step 5: Run full primitive suite to confirm no regression**

Run:
```bash
cd "E:/__electric/__workspace/stacking/circuit-graph"
./.venv/Scripts/python.exe -m pytest tests/test_primitives.py -v
```
Expected: every previous test still passes. New test `test_signal_name_matches_falls_back_to_default_for_protel_nets` also passes.

- [ ] **Step 6: Commit**

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph"
git add circuit_graph/primitives.py tests/test_primitives.py
git commit -m "feat(primitives): default signal-name fallback for Protel auto-nets

signal_name_matches gains a keyword-only signal_id arg that OR-checks a
DEFAULT_SIGNAL_PATTERNS table when the user pattern misses. Two-arg form
is unchanged so all pre-existing primitive tests stay green. R97's
neighborhood (NetR97_1 / NetC82_1 / NetR100_1 / USB_5V) now becomes a
candidate for non-inverting-amplifier.json — see spec
docs/superpowers/specs/2026-08-17-signal-name-fallback-design.md."
```

---

### Task 3: Wire signal_id through vf2_matcher + add integration test

**Files:**
- Modify: `circuit_graph/vf2_matcher.py` in `_node_match_circuit_to_primitive` (1 line)
- Modify: `tests/test_vf2_matcher.py` — add 1 test
- Read-only: `fixtures/protel/FX-T268-V6-10米-5065.NET`

**Interfaces:**
- Consumes: existing `prim_data: PrimitiveSignal` (has `.id`, `.name_pattern`).
- Produces: net-side match returns True for Protel-named nets iff user pattern or fallback matches.

- [ ] **Step 1: Write the integration test (will fail until Step 3 wires it up)**

Open `tests/test_vf2_matcher.py`. Append at end:

```python
def test_non_inverting_amplifier_matches_protel_auto_named_nets(tmp_path):
    """End-to-end: a Protel-exported netlist whose intermediate nets use
    the default 'Net<COMP>_<PIN>' naming should still produce a
    non-inverting-amplifier FunctionalBlock containing R97.
    """
    from pathlib import Path

    from circuit_graph.enrichers import run_enrichers
    from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
    from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

    netlist = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "protel"
        / "FX-T268-V6-10米-5065.NET"
    )
    ir = ProtelNetParser().parse(ParserInput(file_path=str(netlist)))
    ir2 = run_enrichers(ir, [TopologyRecognizer()]).ir

    blocks = [b for b in ir2.blocks if b.id.startswith("non-inverting-amplifier-")]
    assert any("R97" in b.component_ids for b in blocks), (
        f"Expected a non-inverting-amplifier-* block containing R97; got: "
        f"{[(b.id, b.component_ids) for b in blocks]}"
    )
```

- [ ] **Step 2: Run, verify FAIL**

Run:
```bash
cd "E:/__electric/__workspace/stacking/circuit-graph"
./.venv/Scripts/python.exe -m pytest tests/test_vf2_matcher.py::test_non_inverting_amplifier_matches_protel_auto_named_nets -v
```
Expected: FAIL — even after fallback fires, **the matcher has not been told to pass `signal_id` yet**, so the test will report no matching block.

- [ ] **Step 3: Wire signal_id in `_node_match_circuit_to_primitive`**

Open `circuit_graph/vf2_matcher.py`. Find the net-match branch:

```python
        if circ_kind == "net":
            if not isinstance(circ_data, Net):
                return False
            return signal_name_matches(prim_data.name_pattern, circ_data.name)
```

Replace with:

```python
        if circ_kind == "net":
            if not isinstance(circ_data, Net):
                return False
            return signal_name_matches(
                prim_data.name_pattern, circ_data.name, signal_id=prim_data.id
            )
```

- [ ] **Step 4: Run test, verify PASS**

Run:
```bash
cd "E:/__electric/__workspace/stacking/circuit-graph"
./.venv/Scripts/python.exe -m pytest tests/test_vf2_matcher.py::test_non_inverting_amplifier_matches_protel_auto_named_nets -v
```
Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run:
```bash
cd "E:/__electric/__workspace/stacking/circuit-graph"
./.venv/Scripts/python.exe -m pytest tests/ -v
```
Expected: every previous test passes; the two new tests pass; total tests count increased by 2.

- [ ] **Step 6: Commit**

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph"
git add circuit_graph/vf2_matcher.py tests/test_vf2_matcher.py
git commit -m "test(vf2): assert R97 matches non-inverting-amplifier on Protel netlist

Passes signal_id from the primitive signal down to signal_name_matches,
activating the default fallback for Protel auto-named nets. R97's
neighborhood (U7=LM321 with R97=1k, R100=100k, R101=3k) now resolves to
a non-inverting-amplifier-u7 FunctionalBlock."
```

---

### Task 4: Update Readme.md + push

**Files:**
- Modify: `Readme.md` — append a short note about the new matcher behavior.

- [ ] **Step 1: Read the current Readme to choose an insertion point**

Run:
```bash
ls "E:/__electric/__workspace/stacking/circuit-graph/Readme.md"
```
Use your Read tool on `E:/__electric/__workspace/stacking/circuit-graph/Readme.md`.

Pick a sensible section — likely "Features" or "How it works".

- [ ] **Step 2: Add one bullet / paragraph**

Insert (under whatever section fits best; in the absence of a section titled "Primitives" or "Matching", find the closest):

```markdown
### Primitives & matching

Templates in `primitives/*.json` declare signal `name_pattern` regexes. The
matcher additionally consults a built-in **default fallback table** keyed by
`signal.id`, so Protel/Altium auto-nets such as `NetR97_1`, `USB_5V`,
`NetC82_1` match without editing any `.json`. User patterns stay first-class:
when they hit, the fallback is bypassed.
```

If there is no obvious section for it, append at the bottom before any
"License" / "Author" footer and add an `###` heading as shown.

- [ ] **Step 3: Commit**

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph"
git add Readme.md
git commit -m "docs(readme): note primitive signal-name fallback for Protel nets"
```

- [ ] **Step 4: Push**

```bash
cd "E:/__electric/__workspace/stacking/circuit-graph"
git push
```
Expected: all 4 commits appear on origin/master (or whichever branch).

---

## Self-Review (run before execution)

1. **Spec coverage** (skim the spec):
   - "Approach (recommended)" §3 — covered by Task 2 ✓
   - "_DEFAULT_SIGNAL_PATTERNS" §3.2 — copied into Task 2 ✓
   - "API surface" §3.4 — covered by Task 2 ✓
   - "Validation strategy" §4 — TDD step in Task 2 + integration test in Task 3 ✓
   - "Out-of-scope" §5 — explicitly NOT touched (no scoring, no .json edits) ✓
   - "Risks" §7 — addressed by full-suite regressions in Tasks 2 & 3 ✓
   - "Commit plan" §8 — mirrored 1:1 in task sequence ✓

2. **Placeholder scan:** none. Every code block is fully spelled out.

3. **Type consistency:**
   - `signal_name_matches(pattern: str, name: str) -> bool` (existing)
   - `signal_name_matches(pattern: str, name: str, *, signal_id: str | None = None) -> bool` (new) — kwarg-only, so positional callers keep working.
   - `_DEFAULT_SIGNAL_PATTERNS: dict[str, tuple[str, ...]]` — task declares type, code in Task 2 matches.
   - vf2_matcher call changed to keyword form: `signal_name_matches(prim_data.name_pattern, circ_data.name, signal_id=prim_data.id)` ✓

4. **No `.json` edits**: Task 2 Step 3 modifies `primitives.py` and `tests/test_primitives.py` only. Task 3 modifies `vf2_matcher.py` and `tests/test_vf2_matcher.py` only. Task 4 modifies `Readme.md`. Spec doc created in Task 1. ✓
