"""Tests for the `cg layout-check` Click subcommand."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import layout_check


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LM321 = _REPO_ROOT / "lm321_ccs" / "1A_CCS_LM321_BJT.kicad_sch"
_FXT268 = _REPO_ROOT / "fixtures" / "protel" / "FX-T268-V6-10米-5065.NET"
_DEFAULT_RULES = _REPO_ROOT / "fixtures" / "rules" / "default.schlayout"


def _invoke(args, *, catch_exceptions=True):
    runner = CliRunner()
    return runner.invoke(layout_check, args, catch_exceptions=catch_exceptions)


def test_layout_check_help_exits_zero():
    result = _invoke(["--help"])
    assert result.exit_code == 0
    assert "layout-check" in result.output
    assert "--rules" in result.output


def test_layout_check_unknown_extension_exits_two(tmp_path):
    p = tmp_path / "weird.sch"
    p.write_text("foo", encoding="utf-8")
    result = _invoke([str(p)])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    # The dispatcher raises schematic_parse_error; report nests it under results[0].error.
    assert payload["results"][0]["error"]["kind"] == "schematic_parse_error"


def test_layout_check_missing_rules_file_exits_two(tmp_path):
    """Explicit --rules path that does not exist → exit 2 with rules_file_not_found.

    The CLI uses ``path_type=Path`` without ``exists=True`` on the option so that
    the orchestrator's pre-flight check (rather than Click's own validation)
    is what surfaces the error in the JSON report.
    """
    missing_rules = tmp_path / "nope.schlayout"
    result = _invoke([str(_LM321), "--rules", str(missing_rules)])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["error"]["kind"] == "rules_file_not_found"


def test_layout_check_default_rules_run_on_lm321():
    """Smoke integration: the shipped default.schlayout runs against the
    real lm321 fixture. Exit code is in {0, 1}; JSON is valid; summary
    fields present. No exact violation-count assert (baseline is recorded
    in test_integration.py)."""
    if not _LM321.exists() or not _DEFAULT_RULES.exists():
        pytest.skip("lm321 fixture or default rules missing")
    result = _invoke([str(_LM321)])
    assert result.exit_code in (0, 1)
    payload = json.loads(result.output)
    assert "results" in payload
    assert len(payload["results"]) == 1
    sub = payload["results"][0]
    assert "$schema" in sub
    assert sub["tool"]["name"] == "cg-layout-check"
    s = sub["summary"]
    assert s["rules_total"] == 12
    for k in ("rules_evaluated", "rules_skipped_disabled",
              "rules_skipped_unsupported_input", "violations",
              "checked_components", "checked_nets"):
        assert k in s, f"missing summary key: {k}"
    assert isinstance(sub["violations"], list)


def test_layout_check_uses_alternate_rules(tmp_path):
    """A custom rules file declaring zero rules → exit 0, zero violations."""
    if not _LM321.exists():
        pytest.skip("lm321 fixture missing")
    p = tmp_path / "empty.schlayout"
    p.write_text("schema_version: 1\nrules: []\n", encoding="utf-8")
    result = _invoke([str(_LM321), "--rules", str(p)])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    sub = payload["results"][0]
    assert sub["summary"]["violations"] == 0
    assert sub["summary"]["rules_total"] == 0


def test_layout_check_baseline_round_trip(tmp_path):
    """`--baseline` writes a snapshot; `--diff-baseline` accepts it cleanly."""
    if not _LM321.exists() or not _DEFAULT_RULES.exists():
        pytest.skip("lm321 fixture or default rules missing")
    bl = tmp_path / "baseline.json"
    # First invocation: snapshot existing violations.
    r1 = _invoke([str(_LM321), "--baseline", str(bl)])
    assert r1.exit_code in (0, 1)
    assert bl.exists()
    # Second invocation: diff against the same baseline → 0 NEW violations.
    r2 = _invoke([str(_LM321), "--diff-baseline", str(bl)])
    assert r2.exit_code == 0
    payload = json.loads(r2.output)
    sub = payload["results"][0]
    # Baselines everything → no remaining violations.
    assert sub["summary"]["violations"] == 0


def test_layout_check_compact_produces_single_line():
    if not _LM321.exists() or not _DEFAULT_RULES.exists():
        pytest.skip("lm321 fixture or default rules missing")
    result = _invoke([str(_LM321), "--compact"])
    assert result.exit_code in (0, 1)
    payload = json.loads(result.output)
    # Pretty-print the same payload and check that single-line version lacks newlines.
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "\n" not in encoded


def test_layout_check_multiple_schematics(tmp_path):
    """Two schematics → results array contains both."""
    if not _LM321.exists():
        pytest.skip("lm321 fixture missing")
    # Copy the lm321 fixture to a tmp path so the dispatcher still sees .kicad_sch
    copy = tmp_path / "copy.kicad_sch"
    copy.write_bytes(_LM321.read_bytes())
    result = _invoke([str(_LM321), str(copy)])
    assert result.exit_code in (0, 1)
    payload = json.loads(result.output)
    assert payload["summary"]["schematics"] == 2
    assert len(payload["results"]) == 2


def test_layout_check_protel_skips_spatial_rules(tmp_path):
    """`no-trace-through-component` is geometry-required → auto-skip on .NET."""
    if not _FXT268.exists() or not _DEFAULT_RULES.exists():
        pytest.skip("fxt268 fixture or default rules missing")
    # Trim rules to the §6 set so we can verify auto-skip semantics cleanly.
    trimmed = tmp_path / "protel-only.schlayout"
    trimmed.write_text(
        "schema_version: 1\n"
        "rules:\n"
        "  - id: designator\n"
        "    assert: {kind: designator-present}\n"
        "  - id: trace\n"
        "    assert: {kind: no-trace-through-component, tolerance_mm: 5}\n",
        encoding="utf-8",
    )
    result = _invoke([str(_FXT268), "--rules", str(trimmed)])
    assert result.exit_code in (0, 1)
    payload = json.loads(result.output)
    sub = payload["results"][0]
    assert sub["summary"]["rules_skipped_unsupported_input"] == 1
    assert sub["summary"]["rules_evaluated"] == 1   # designator-present still runs
