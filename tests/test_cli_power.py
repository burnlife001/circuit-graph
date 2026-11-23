"""CLI tests for the power-context slice of ``cg view --with-power``.

The legacy ``cg power`` top-level command was retired in favour of the
``--with-power`` flag on ``cg view``.  This file therefore exercises the
new code path and keeps an opt-in smoke test for the legacy module API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import main

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "protel"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_view_with_power_text_context_on_t268(runner: CliRunner) -> None:
    """``cg view --with-power`` returns a JSON object with power_context."""
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"fixture not found: {fixture}")

    # U2 is the main MCU on the T268 — it sits on the 5V2 / GND rails.
    result = runner.invoke(main, ["view", "U2", str(fixture), "--with-power"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "power_context" in data
    ctx = data["power_context"]
    # T268 has no annotated regulator part numbers, so source tracing may
    # surface ``unknown`` while still reporting the rail identity.
    assert ctx["rail"] in {"5V2", "5V", "3.3V"}
    # confidence maps the resolution level: deep path=high, direct source=medium,
    # rail-only fallback=low; T268 (no annotated regulators) lands on low.
    assert ctx["confidence"] in {"high", "medium", "low"}
    if ctx["source_kind"] == "unknown":
        assert ctx["confidence"] == "low"
    assert isinstance(ctx["upstream_regulators"], list)


def test_view_with_power_missing_file_fails(runner: CliRunner) -> None:
    """``cg view --with-power /no/such/file.NET`` exits non-zero."""
    result = runner.invoke(main, ["view", "U2", "/definitely/not/a/file.NET", "--with-power"])
    assert result.exit_code != 0


def test_view_without_with_power_flag_omits_context(runner: CliRunner) -> None:
    """Default ``cg view`` output must not carry power_context."""
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"fixture not found: {fixture}")

    result = runner.invoke(main, ["view", "U2", str(fixture)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "power_context" not in data


def test_view_with_power_component_not_on_rail(runner: CliRunner) -> None:
    """A signal-only component has no power_context (flag is opt-in)."""
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"fixture not found: {fixture}")

    # R70 sits on PGND + CC2 — neither is a POWER-typed net, so
    # power_context is omitted from the output.
    result = runner.invoke(main, ["view", "R70", str(fixture), "--with-power"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "power_context" not in data
