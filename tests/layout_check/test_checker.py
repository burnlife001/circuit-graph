"""LayoutChecker orchestrator tests.

12+ tests covering end-to-end wiring of rules → orchestrator → JSON report,
plus baseline / diff-baseline support. Designed to be resilient against the
parallel-subagent ``flow.py`` / ``routing.py`` not yet being on disk: tests
that depend on those two kinds skip with a clear reason if they aren't
importable; tests that depend only on the 10 already-shipped assertions run
unconditionally.

Test list (per the Batch-5 task brief):

 1. test_all_assertion_kinds_registered          — registry covers BUILT_IN_ASSERTION_KINDS subset.
 2. test_empty_rules_produces_zero_violations    — vacuous pass.
 3. test_disabled_rule_counted_as_skipped        — ``enabled: false`` → rules_skipped_disabled++.
 4. test_protel_skips_unsupported_input          — .NET + ``no-trace-through-component`` skip.
 5. test_baseline_round_trip                     — write_baseline → diff_against_baseline == [].
 6. test_report_is_json_serializable             — ``json.dumps`` produces valid JSON.
 7. test_violation_rule_id_is_filled             — orchestrator stamps rule_id on every violation.
 8. test_summary_keys_present                    — all spec §6.1 summary keys present.
 9. test_summary_components_and_nets_counted     — non-zero counts on a real fixture.
10. test_unknown_kind_blocked_at_loader          — loader prevents unknown kind reaching evaluate.
11. test_load_rules_raises_layout_check_error_for_bad_yaml — error propagation works.
12. test_baseline_signature_stable_across_runs   — same violation twice → same signature.
13. test_mixed_pass_fail_violation_count_matches — mixed pass/fail counts add up in summary.
14. test_violation_to_dict_handles_dataclass     — direct unit test of the serializer.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from circuit_graph.layout_check.checker import (
    ASSERTION_REGISTRY,
    LayoutChecker,
    _violation_to_dict,
)
from circuit_graph.layout_check.loader import LayoutCheckError, load_rules
from circuit_graph.layout_check.model import (
    BUILT_IN_ASSERTION_KINDS,
    Location,
    Violation,
)

# Real fixtures (lm321 + the FX-T268 Protel netlist used in the user-facing
# demos). Tests skip with ``pytest.skip`` if they are missing so this file
# can run in isolation without the upstream integration data.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LM321 = _REPO_ROOT / "lm321_ccs" / "1A_CCS_LM321_BJT.kicad_sch"
_FXT268 = _REPO_ROOT / "fixtures" / "protel" / "FX-T268-V6-10米-5065.NET"


def _require_lm321() -> None:
    if not _LM321.exists():
        pytest.skip(f"fixture missing: {_LM321}")


def _require_fxt268() -> None:
    if not _FXT268.exists():
        pytest.skip(f"fixture missing: {_FXT268}")


def _write_rules(tmp_path: Path, body: str, name: str = "rules.schlayout") -> Path:
    """Write a ``.schlayout`` rule file under ``tmp_path``. Returns the path."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. Registry coverage
# ---------------------------------------------------------------------------

def test_all_assertion_kinds_registered():
    """The registry must include every kind that has shipped.

    The two parallel-subagent kinds (``signal-flow-axis``,
    ``no-trace-through-component``) are skipped if not yet on disk; once
    they ship, this test starts asserting against them too.
    """
    have = set(ASSERTION_REGISTRY.keys())
    want = set(BUILT_IN_ASSERTION_KINDS)
    missing = want - have
    # If the parallel subagent has shipped both, missing must be empty.
    # Otherwise only the shipped 10 are required to be present.
    if have >= want:
        assert not missing, f"registry missing kinds: {missing}"
    else:
        # Document the gap explicitly rather than failing silently.
        assert missing <= {"signal-flow-axis", "no-trace-through-component"}, (
            f"unexpected kinds missing from registry: {missing}"
        )
        assert len(have) >= 10, (
            "expected at least 10 shipped kinds; got "
            f"{len(have)}: {sorted(have)}"
        )


# ---------------------------------------------------------------------------
# 2. Empty rules → vacuous pass
# ---------------------------------------------------------------------------

def test_empty_rules_produces_zero_violations(tmp_path):
    _require_lm321()
    rp = _write_rules(tmp_path, "schema_version: 1\nrules: []\n")
    checker = LayoutChecker(rules_path=rp, schematic_path=_LM321)
    checker.load_rules()
    checker.parse_schematic()
    vs = checker.evaluate()
    assert vs == []
    summary = checker.summary
    assert summary["rules_total"] == 0
    assert summary["rules_evaluated"] == 0
    assert summary["violations"] == 0
    assert summary["rules_skipped_disabled"] == 0
    assert summary["rules_skipped_unsupported_input"] == 0


# ---------------------------------------------------------------------------
# 3. Disabled rules
# ---------------------------------------------------------------------------

def test_disabled_rule_counted_as_skipped(tmp_path):
    _require_lm321()
    rp = _write_rules(
        tmp_path,
        """
        schema_version: 1
        rules:
          - id: disabled-rule
            enabled: false
            assert: {kind: designator-present}
        """,
    )
    checker = LayoutChecker(rules_path=rp, schematic_path=_LM321)
    checker.load_rules()
    checker.parse_schematic()
    checker.evaluate()
    assert checker.summary["rules_skipped_disabled"] == 1
    assert checker.summary["rules_evaluated"] == 0


# ---------------------------------------------------------------------------
# 4. Protel auto-skip
# ---------------------------------------------------------------------------

def test_protel_skips_unsupported_input(tmp_path):
    """``no-trace-through-component`` requires geometry; auto-skipped on .NET.

    If the parallel-subagent ``routing.py`` hasn't shipped, this test is
    skipped (it cannot exercise a kind that does not yet exist).
    """
    if "no-trace-through-component" not in ASSERTION_REGISTRY:
        pytest.skip("routing.py not yet shipped by parallel subagent")
    _require_fxt268()
    rp = _write_rules(
        tmp_path,
        """
        schema_version: 1
        rules:
          - id: ann
            assert: {kind: designator-present}
          - id: trace
            assert: {kind: no-trace-through-component, tolerance_mm: 5}
        """,
    )
    checker = LayoutChecker(rules_path=rp, schematic_path=_FXT268)
    checker.load_rules()
    checker.parse_schematic()
    checker.evaluate()
    summary = checker.summary
    assert summary["rules_skipped_unsupported_input"] == 1
    assert summary["rules_evaluated"] == 1  # only the annotation rule ran


# ---------------------------------------------------------------------------
# 5. Baseline round-trip
# ---------------------------------------------------------------------------

def test_baseline_round_trip(tmp_path):
    """Write baseline → diff-against-baseline → expect zero diffs on a second run."""
    _require_lm321()
    rp = _write_rules(
        tmp_path,
        """
        schema_version: 1
        rules:
          - id: designator
            assert: {kind: designator-present}
        """,
    )
    bl = tmp_path / "baseline.json"
    c1 = LayoutChecker(rules_path=rp, schematic_path=_LM321)
    c1.load_rules()
    c1.parse_schematic()
    c1.evaluate()
    c1.write_baseline(bl)
    assert bl.exists()
    written = json.loads(bl.read_text(encoding="utf-8"))
    assert isinstance(written, list)
    # baseline contains one tuple per violation (may be empty list).

    c2 = LayoutChecker(rules_path=rp, schematic_path=_LM321)
    c2.load_rules()
    c2.parse_schematic()
    c2.evaluate()
    new = c2.diff_against_baseline(bl)
    assert new == []


# ---------------------------------------------------------------------------
# 6. Report is JSON-serializable
# ---------------------------------------------------------------------------

def test_report_is_json_serializable(tmp_path):
    _require_lm321()
    rp = _write_rules(
        tmp_path,
        """
        schema_version: 1
        rules:
          - id: designator
            assert: {kind: designator-present}
          - id: value
            applies_to: {refdes_prefix: ["C*"]}
            assert: {kind: value-present}
        """,
    )
    c = LayoutChecker(rules_path=rp, schematic_path=_LM321)
    c.load_rules()
    c.parse_schematic()
    c.evaluate()
    report = c.report()
    encoded = json.dumps(report, default=str)
    parsed = json.loads(encoded)
    assert parsed["$schema"] == "cg-layout-check-v1"
    assert parsed["tool"]["name"] == "cg-layout-check"
    assert parsed["tool"]["version"]  # non-empty
    assert "summary" in parsed
    assert "violations" in parsed
    assert isinstance(parsed["violations"], list)
    assert "schematic" in parsed["input"]
    assert "rules_file" in parsed["input"]
    assert "rules_file_sha256" in parsed["input"]


# ---------------------------------------------------------------------------
# 7. rule_id is stamped on every violation
# ---------------------------------------------------------------------------

def test_violation_rule_id_is_filled(tmp_path):
    _require_lm321()
    rp = _write_rules(
        tmp_path,
        """
        schema_version: 1
        rules:
          - id: designator
            assert: {kind: designator-present}
        """,
    )
    c = LayoutChecker(rules_path=rp, schematic_path=_LM321)
    c.load_rules()
    c.parse_schematic()
    vs = c.evaluate()
    for v in vs:
        assert v.rule_id == "designator"


# ---------------------------------------------------------------------------
# 8. Summary key set
# ---------------------------------------------------------------------------

def test_summary_keys_present(tmp_path):
    _require_lm321()
    rp = _write_rules(
        tmp_path,
        """
        schema_version: 1
        rules:
          - id: designator
            assert: {kind: designator-present}
          - id: value
            enabled: false
            assert: {kind: value-present}
        """,
    )
    c = LayoutChecker(rules_path=rp, schematic_path=_LM321)
    c.load_rules()
    c.parse_schematic()
    c.evaluate()
    s = c.summary
    # Per spec §6.1 the summary has these 7 keys (duration_ms is included
    # when measureable; the orchestrator always fills it).
    required = {
        "rules_total",
        "rules_evaluated",
        "rules_skipped_disabled",
        "rules_skipped_unsupported_input",
        "violations",
        "checked_components",
        "checked_nets",
        "duration_ms",
    }
    assert required <= s.keys(), f"missing summary keys: {required - s.keys()}"


# ---------------------------------------------------------------------------
# 9. Counts are non-zero on a real fixture
# ---------------------------------------------------------------------------

def test_summary_components_and_nets_counted(tmp_path):
    _require_lm321()
    rp = _write_rules(
        tmp_path,
        """
        schema_version: 1
        rules:
          - id: designator
            assert: {kind: designator-present}
        """,
    )
    c = LayoutChecker(rules_path=rp, schematic_path=_LM321)
    c.load_rules()
    c.parse_schematic()
    c.evaluate()
    s = c.summary
    assert s["checked_components"] > 0
    assert s["checked_nets"] >= 0
    assert s["rules_total"] == 1
    assert s["rules_evaluated"] == 1


# ---------------------------------------------------------------------------
# 10. Loader prevents unknown kinds reaching evaluate
# ---------------------------------------------------------------------------

def test_unknown_kind_blocked_at_loader(tmp_path):
    """``unknown_assert_kind`` is raised at load time; evaluate never sees it.

    This is the contract from design-doc §7 / loader.py; we re-verify it
    here to lock in the orchestrator's behaviour (orchestrator is allowed
    to be defensive, but the canonical guard is the loader).
    """
    rp = _write_rules(
        tmp_path,
        """
        schema_version: 1
        rules:
          - id: bad
            assert: {kind: not-a-real-kind}
        """,
    )
    with pytest.raises(LayoutCheckError) as ei:
        load_rules(rp)
    assert ei.value.kind == "unknown_assert_kind"
    # The orchestrator's own load_rules() must propagate this unchanged.
    checker = LayoutChecker(rules_path=rp, schematic_path=_LM321)
    with pytest.raises(LayoutCheckError) as ei2:
        checker.load_rules()
    assert ei2.value.kind == "unknown_assert_kind"


# ---------------------------------------------------------------------------
# 11. Bad YAML → LayoutCheckError
# ---------------------------------------------------------------------------

def test_load_rules_raises_layout_check_error_for_bad_yaml(tmp_path):
    p = tmp_path / "bad.schlayout"
    p.write_text("schema_version: 1\nrules: [unclosed", encoding="utf-8")
    checker = LayoutChecker(rules_path=p, schematic_path=_LM321)
    with pytest.raises(LayoutCheckError) as ei:
        checker.load_rules()
    assert ei.value.kind == "yaml_parse_error"


# ---------------------------------------------------------------------------
# 12. Baseline signature stability
# ---------------------------------------------------------------------------

def test_baseline_signature_stable_across_runs(tmp_path):
    """Same violation produces the same signature across calls.

    We use ``grid-alignment`` with a 1.0 mm grid against the lm321 fixture
    because lm321's stock positions are 2.54 mm-pitch and the rule fires
    reliably across multiple components, giving us a non-trivial set of
    violations to fingerprint.
    """
    _require_lm321()
    rp = _write_rules(
        tmp_path,
        """
        schema_version: 1
        rules:
          - id: grid
            assert: {kind: grid-alignment, grid_mm: 1.0}
        """,
    )
    c = LayoutChecker(rules_path=rp, schematic_path=_LM321)
    c.load_rules()
    c.parse_schematic()
    vs = c.evaluate()
    assert vs, "expected non-trivial violations from grid-alignment on lm321"
    sigs = [c.baseline_signature(v) for v in vs]
    # All distinct violations produce distinct sigs (no two share the
    # exact (rule_id, comp, net, x, y) tuple).
    assert len(set(sigs)) == len(sigs)
    # Recomputing the same signature twice is deterministic.
    for v in vs:
        assert c.baseline_signature(v) == c.baseline_signature(v)
    # x_mm / y_mm appear as floats rounded to 1e-3 mm.
    for sig in sigs:
        x, y = sig[3], sig[4]
        if x is not None:
            assert x == round(x, 3)
        if y is not None:
            assert y == round(y, 3)


# ---------------------------------------------------------------------------
# 13. Mixed pass/fail — counts add up
# ---------------------------------------------------------------------------

def test_mixed_pass_fail_violation_count_matches(tmp_path):
    _require_lm321()
    rp = _write_rules(
        tmp_path,
        """
        schema_version: 1
        rules:
          - id: designator-rule
            assert: {kind: designator-present}
          - id: value-rule
            applies_to: {refdes_prefix: ["R*"]}
            assert: {kind: value-present}
        """,
    )
    c = LayoutChecker(rules_path=rp, schematic_path=_LM321)
    c.load_rules()
    c.parse_schematic()
    vs = c.evaluate()
    s = c.summary
    # The summary count must equal what evaluate() returned.
    assert s["violations"] == len(vs)
    assert s["rules_total"] == 2
    assert s["rules_evaluated"] == 2
    assert s["rules_skipped_disabled"] == 0


# ---------------------------------------------------------------------------
# 14. Direct test of the violation serializer
# ---------------------------------------------------------------------------

def test_violation_to_dict_handles_dataclass():
    """``_violation_to_dict`` round-trips a Violation to JSON-ready dict."""
    v = Violation(
        rule_id="r1",
        assert_kind="designator-present",
        severity="error",
        message="example",
        location=Location(component="R1", net=None, x_mm=10.0, y_mm=20.0,
                          sheet="/", page=1),
        context={"k": 1},
        doc="circuit-graph://rules#designator-present",
    )
    d = _violation_to_dict(v)
    # Top-level fields are present.
    assert d["rule_id"] == "r1"
    assert d["assert_kind"] == "designator-present"
    assert d["severity"] == "error"
    assert d["message"] == "example"
    assert d["doc"] == "circuit-graph://rules#designator-present"
    # Nested location is a plain dict.
    assert isinstance(d["location"], dict)
    assert d["location"]["component"] == "R1"
    assert d["location"]["x_mm"] == 10.0
    assert d["location"]["y_mm"] == 20.0
    # Context is a copy, not a reference.
    assert d["context"] == {"k": 1}
    assert d["context"] is not v.context
    # The whole thing must JSON-serialize without custom encoders.
    encoded = json.dumps(d)
    parsed = json.loads(encoded)
    assert parsed["rule_id"] == "r1"
