# Spec — Signal-name fallback for primitive templates

**Date:** 2026-08-17
**Status:** Draft (pending user review)

## 1. Problem

`circuit_graph.primitives.signal_name_matches` (file: `circuit_graph/primitives.py`,
line 320) enforces a strict regex match between a template's `name_pattern` and
the candidate circuit net name. In a **Protel/Altium-exported netlist**, the
intermediate nets are auto-named (e.g. `NetR97_1`, `NetC82_1`, `NetR100_1`,
`USB_5V`) — these never match the "semantic-name" patterns shipped in the
JSON templates (e.g. `^VIN+$`, `^VOUT$`, `^AINP.*`).

Empirical evidence, run on `fixtures/protel/FX-T268-V6-10米-5065.NET` and
`primitives/non-inverting-amplifier.json`:

| signal id | template pattern | actual net | match? |
| --- | --- | --- | --- |
| `vcc`  | `^(VCC|VDD\|V+\|...\|+5V\|+12V\|...)$` | `USB_5V`     | no |
| `gnd`  | `^(GND\|PGND\|...\|VNEG)$`             | `GND`        | yes |
| `in_p` | `^(IN+\|VIN+\|...\|VSET).*`           | `NetC82_1`   | no |
| `in_n` | `^(IN-\|VIN-\|...\|NFB).*`            | `NetR97_1`   | no |
| `out`  | `^(V?OUT\|VO\|...\|AOUT).*`           | `NetR100_1`  | no |

4/5 fail ⇒ VF2 prunes the candidate immediately ⇒ no FunctionalBlock ever gets
generated for R97's neighborhood.

The same failure mode affects all 21 primitive templates — only the
`tests/test_vf2_matcher.py` synthetic fixtures, which use semantic names like
`VCC` / `VIN` / `VOUT`, manage to match.

## 2. Goal

Make every shipped primitive template **also match common Protel auto-named
nets and other low-info net names** — without editing any JSON template, and
without regressing any existing test.

## 3. Approach (recommended)

Add a per-`signal.id` **fallback pattern table** in `circuit_graph/primitives.py`.
`signal_name_matches` retains its current `(pattern, name) -> bool` shape and
its strictness for user patterns, but gains an optional `signal_id` argument.
When the user pattern misses, the matcher passes `signal_id` and we OR in any
hit against `_DEFAULT_SIGNAL_PATTERNS[signal_id]`.

User patterns stay first — this **preserves all 21 existing test cases**
because a matching user pattern short-circuits before the fallback is consulted.

### 3.1 Where the fallback fires

Two places consult `signal_name_matches`:

| Caller | File | Required change |
| --- | --- | --- |
| `vf2_matcher._node_match_circuit_to_primitive` | `circuit_graph/vf2_matcher.py:74` | pass `signal_id=prim_data.id` |
| existing tests of `signal_name_matches` direct | `tests/test_primitives.py`, etc. | optional — must keep working without arg |

We extend the **first** caller only. `signal_name_matches` keeps the old
two-arg signature working.

### 3.2 Default fallback patterns (initial draft)

Tuned against the **real net names** observed in
`fixtures/protel/FX-T268-V6-10米-5065.NET` and the 30 signal-ids present
across the 21 templates.

```python
_DEFAULT_SIGNAL_PATTERNS: dict[str, list[str]] = {
    # Power rails
    "vcc":       [r".*VDD.*", r".*VCC.*", r"^V[+A-Z0-9]+$",
                  r".*V$", r"^[+]?\d+(\.\d+)?V(_.*)?$", r"^USB_5V$",
                  r"^PVCC$", r"^AVCC$", r"^AVDD$"],
    "gnd":       [r"^GND\d*$", r"^.*GND$", r"^PGND$", r"^AGND$", r"^DGND$",
                  r"^CGND$", r"^.*VSS$", r"^.*VEE$", r"^.*_GND$",
                  r"^GND_[A-Z0-9]+$"],

    # Inputs / outputs (semantic + Protel auto-named)
    "in_p":      [r"^Net.*_1$", r"^AINP.*$", r"^INP.*$", r"^V\+IN.*$",
                  r"^VIP.*$", r"^POS$"],
    "in_n":      [r"^Net.*_1$", r"^AINN.*$", r"^INN.*$", r"^V\-IN.*$",
                  r"^VIN.*$", r"^NEG$"],
    "vin":       [r"^Net.*_1$", r"^VIN.*$", r"^V[Ii]n.*$", r"^V[_]?[Ii]n.*$"],
    "out":       [r"^Net.*_1$", r"^V?OUT.*$", r"^VO$", r"^AMP_OUT.*$",
                  r"^OA_OUT.*$", r"^OP_OUT.*$", r"^VOUT.*$", r"^AOUT.*$"],
    "vout":      [r"^Net.*_1$", r"^V?OUT.*$", r"^VOUT.*$", r"^AOUT.*$"],

    # Generic signals
    "signal":    [r"^Net.*$", r"^[A-Z]+_\d+V$", r"^SIG.*$", r"^A(IO|IN|OUT)\d+$"],
    "sig_in":    [r"^Net.*$", r"^SIG.*$", r"^VIN.*$", r"^AIN.*$"],
    "sig_p":     [r"^Net.*$", r"^SIG.*P$", r"^.+_P$"],
    "sig_n":     [r"^Net.*$", r"^SIG.*N$", r"^.+_N$"],
    "sig_low":   [r"^Net.*$", r".*_LOW$"],
    "sig_high":  [r"^Net.*$", r".*_HIGH$"],
    "load_net":  [r"^Net.*$", r"^LOAD.*$", r"^.+_LOAD$"],

    # Differential / generic differential pairs
    "ac1":       [r"^Net.*_1$", r"^AC\d?$", r"^A$"],
    "ac2":       [r"^Net.*_2$", r"^AC\d?$", r"^B$"],

    # Differential amplifier specifics
    "dc_plus":   [r"^Net.*$", r"^D\+$", r"^DC\+$", r"^V\+IN$"],
    "dc_minus":  [r"^Net.*$", r"^D\-$", r"^DC\-$", r"^V\-IN$"],
    "xtal_p":    [r"^Net.*$", r"^XT.*P$", r"^XI$", r"^XIN$"],
    "xtal_n":    [r"^Net.*$", r"^XT.*N$", r"^XO$", r"^XOUT$"],

    # Crystal / bias
    "vcc_low":   [r"^Net.*$", r".*VDD.*LOW.*", r".*_LOW$"],
    "vcc_high":  [r"^Net.*$", r".*VDD.*HIGH.*", r".*_HIGH$"],

    # Switch controls
    "ctrl":      [r"^Net.*$", r"^CTRL.*$", r"^.*_CTRL$", r"^GATE\d?$"],
    "high_side": [r"^Net.*$", r"^.*HS$", r"^.*_HIGH$"],
    "low_side":  [r"^Net.*$", r"^.*LS$", r"^.*_LOW$"],

    # Power entry / exit
    "pwr_in":    [r"^Net.*$", r"^PWR.*IN.*$", r"^.+_IN$"],
    "pwr_out":   [r"^Net.*$", r"^PWR.*OUT.*$", r"^.+_OUT$"],

    # Pull-ups / CC / protected pins
    "pull_up":   [r"^Net.*$", r"^.*PULLUP.*$", r"^.*PU$"],
    "cc":        [r"^CC[12]?$", r"^.*CC\d?$"],
    "protected": [r"^Net.*$", r"^PROT.*$"],
}
```

**Note**: the table is intentionally permissive — anything `Net.*` matches a
broad set of signal ids (`in_p`, `in_n`, `out`, `signal`, `ctrl`, etc.). That
permissiveness is **fine** for VF2, because VF2 only commits a candidate if
**all** signal ids in the template match simultaneously — `in_p` and `in_n`
cannot collide with each other (they require two distinct net ids from the
circuit graph). What the fallback buys us is: a single Protel auto-named net
like `NetR97_1` now becomes a candidate for any of `in_p` / `in_n` / `out` /
`signal` — VF2 still picks the role-assignment that satisfies the structural
constraints.

### 3.3 Tuning / scoring

We intentionally **do not** change scoring weights or thresholds in this
spec. Keeping it match-or-no-match prevents accidental score-regressions.

If we later want strict-name matches to score higher than fallback hits, that
is a follow-up spec (e.g. add an `naming` evidence item in
`TopologyScorer._naming_score` recording whether the match came from the user
pattern or the fallback).

### 3.4 API surface

```python
# Existing — unchanged behavior, no signal_id → no fallback.
def signal_name_matches(pattern: str, name: str) -> bool: ...

# New — used by vf2_matcher.
def signal_name_matches(pattern: str, name: str, *, signal_id: str | None = None) -> bool:
    if re.search(pattern, name, re.IGNORECASE):
        return True
    if signal_id is not None:
        for fb in _DEFAULT_SIGNAL_PATTERNS.get(signal_id, ()):
            if re.search(fb, name, re.IGNORECASE):
                return True
    return False
```

## 4. Validation strategy (TDD)

1. **New failing test** (`tests/test_primitives.py` →
   `test_signal_name_matches_uses_fallback_for_protel_nets`):
   ```python
   def test_signal_name_matches_uses_fallback_for_protel_nets():
       assert signal_name_matches(r"^OUT$", "NetR100_1", signal_id="out") is True
       assert signal_name_matches(r"^GND$", "USB_5V",     signal_id="gnd") is True
   ```
2. **Existing tests** must all pass — they call `signal_name_matches(pattern,
   name)` without `signal_id`; in that case fallback is dormant.
3. **Integration test** (`tests/test_vf2_matcher.py`):
   parse `FX-T268-V6-10米-5065.NET`, enrich with `TopologyRecognizer`,
   assert that a `non-inverting-amplifier-u7` FunctionalBlock exists with
   `R97` in its `component_ids` and `confidence` ≥ `low`.
4. **Manual `cg view R97`** confirmation of the block in the IR.

## 5. Out-of-scope

- Editing any `primitives/*.json`.
- Changing scoring weights / thresholds.
- Adapting to KiCad-style semantic nets (those already work).
- New templates for circuit shapes not currently covered.

## 6. Files touched

| File | Change |
| --- | --- |
| `circuit_graph/primitives.py` | add `_DEFAULT_SIGNAL_PATTERNS`; extend `signal_name_matches` with optional `signal_id` kwarg |
| `circuit_graph/vf2_matcher.py` | pass `signal_id=prim_data.id` in the net-side match call |
| `tests/test_primitives.py` | add fallback test |
| `tests/test_vf2_matcher.py` | add R97-protel integration test |

## 7. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Fallback patterns collide and admit wrong signals | VF2's structural constraints still must be satisfied simultaneously; net ids must be distinct |
| New patterns break an existing test | Test plan explicitly asserts all existing tests still pass before merge |
| User pattern regressions from typos | CI runs the full pre-existing 21-template test suite on every change |
| Future maintainer confusion about two-layer pattern logic | Docstring on `_DEFAULT_SIGNAL_PATTERNS` spells out the contract |

## 8. Commit / push plan

Per `CLAUDE.md`, each small unit of work commits and pushes separately:

1. Commit the failing test only → `test(primitives): add signal_name_matches fallback case`
2. Commit the implementation → `feat(primitives): add default signal-name fallback for Protel nets`
3. Commit the integration test (after it passes) → `test(vf2): assert R97 matches non-inverting-amplifier`
4. Update `Readme.md` mention of the templates count + link the new fallback note.
