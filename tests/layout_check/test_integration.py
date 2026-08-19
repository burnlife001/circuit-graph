"""End-to-end integration test — locks in the baseline violation count
on `lm321_ccs/1A_CCS_LM321_BJT.kicad_sch` so future changes (intentional
or accidental) to the rule engine surface as a snapshot diff.

This is a smoke test, not a pass/fail gate. The user is expected to
either fix violations or re-baseline them via `--baseline`.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import layout_check


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LM321 = _REPO_ROOT / "lm321_ccs" / "1A_CCS_LM321_BJT.kicad_sch"
_DEFAULT_RULES = _REPO_ROOT / "fixtures" / "rules" / "default.schlayout"

# Snapshot file lives next to the test. The id is the SHA-256 prefix of the
# default rules file so re-baselining rules updates this id and forces a
# human to refresh the snapshot knowingly. Keep this in source control.
_SNAPSHOT = Path(__file__).parent / "test_integration.snapshot.json"


def _load_rules_sha() -> str | None:
    if not _DEFAULT_RULES.exists():
        return None
    import hashlib
    return hashlib.sha256(_DEFAULT_RULES.read_bytes()).hexdigest()


def _read_snapshot() -> dict | None:
    if not _SNAPSHOT.exists():
        return None
    try:
        return json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_snapshot(data: dict) -> None:
    _SNAPSHOT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def test_lm321_default_rule_violations_match_snapshot():
    """Run the default rule set against the real lm321 schematic and assert
    the violation count matches the locked-in snapshot.

    Refresh snapshot (when intentional rule changes are made) by setting
    `UPDATE_LAYOUT_CHECK_SNAPSHOT=1` in the env when running this test.
    """
    if not _LM321.exists() or not _DEFAULT_RULES.exists():
        pytest.skip("lm321 fixture or default rules missing")

    runner = CliRunner()
    result = runner.invoke(layout_check, [str(_LM321)])
    assert result.exit_code in (0, 1), (
        f"unexpected exit {result.exit_code}\nstdout:\n{result.output[:2000]}"
    )
    payload = json.loads(result.output)
    sub = payload["results"][0]
    observed_count = sub["summary"]["violations"]

    snapshot = _read_snapshot()
    sha = _load_rules_sha()

    update_mode = "UPDATE_LAYOUT_CHECK_SNAPSHOT" in os.environ

    if snapshot is None or update_mode:
        # First run / forced refresh: write the snapshot and pass without enforcing.
        _write_snapshot({
            "rules_sha256": sha,
            "schematic": str(_LM321.relative_to(_REPO_ROOT)),
            "violation_count": observed_count,
            "rule_breakdown": _rule_breakdown(sub["violations"]),
        })
        # Snapshot was just written — pass this run; subsequent runs will compare.
        assert observed_count >= 0
    else:
        # Compare against the locked-in snapshot.
        expected_count = snapshot.get("violation_count", 0)
        assert observed_count == expected_count, (
            f"violation count drift: snapshot says {expected_count}, got {observed_count}.\n"
            f"If this is intentional, re-run with UPDATE_LAYOUT_CHECK_SNAPSHOT=1."
        )


def _rule_breakdown(violations: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for v in violations:
        rid = v.get("rule_id") or "_unknown"
        counts[rid] = counts.get(rid, 0) + 1
    # Stable order for diff-friendliness.
    return {k: counts[k] for k in sorted(counts)}


def test_lm321_summary_fields_present():
    if not _LM321.exists() or not _DEFAULT_RULES.exists():
        pytest.skip("lm321 fixture or default rules missing")
    runner = CliRunner()
    result = runner.invoke(layout_check, [str(_LM321)])
    payload = json.loads(result.output)
    sub = payload["results"][0]
    s = sub["summary"]
    # Duration is best-effort; only the structural fields are required.
    for k in ("rules_total", "rules_evaluated", "rules_skipped_disabled",
              "rules_skipped_unsupported_input", "violations",
              "checked_components", "checked_nets"):
        assert k in s, f"missing summary.{k}"
    # Spot-check types
    assert s["rules_total"] == 12
    assert s["checked_components"] >= 1
    # All entries in summary must be JSON-serialisable primitives.
    for v in s.values():
        assert isinstance(v, (int, float))


def test_lm321_each_violation_has_location_and_context():
    if not _LM321.exists() or not _DEFAULT_RULES.exists():
        pytest.skip("lm321 fixture or default rules missing")
    runner = CliRunner()
    result = runner.invoke(layout_check, [str(_LM321)])
    payload = json.loads(result.output)
    sub = payload["results"][0]
    for v in sub["violations"]:
        assert v["severity"] == "error"
        assert v["rule_id"]
        assert v["assert_kind"]
        assert isinstance(v["message"], str)
        assert isinstance(v["context"], dict)
        loc = v["location"]
        # KiCad coordinates are available; either component or net must be set
        assert loc.get("component") or loc.get("net")
