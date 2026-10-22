"""cg view output-truncation tests (todolist P2-8)."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import main

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "protel"
FIXTURE = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"

# Baseline measured 2026-10-22: `cg view Q5 --with-blocks` = 28774 bytes.
_SIZE_LIMIT = 11_500


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_view_q5_size_under_limit(runner: CliRunner) -> None:
    result = runner.invoke(main, ["view", "Q5", str(FIXTURE), "--with-blocks"])
    assert result.exit_code == 0, result.output
    # PowerShell acceptance: (& cg view ... | Out-String).Length → chars.
    assert len(result.stdout) < _SIZE_LIMIT, (
        f"view output {len(result.stdout)} chars >= {_SIZE_LIMIT}"
    )


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_view_q5_focal_nets_and_members_intact(runner: CliRunner) -> None:
    result = runner.invoke(main, ["view", "Q5", str(FIXTURE)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    q5 = data["components"]["Q5"]
    pin_nets = {p["net"] for p in q5["pins"]}
    # Every pin net the focal physically touches is present in the nets block.
    assert pin_nets and pin_nets <= set(data["nets"])
    # Full pin detail for the focal itself.
    assert len(q5["pins"]) >= 12


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_view_q5_gnd_folded(runner: CliRunner) -> None:
    result = runner.invoke(main, ["view", "Q5", str(FIXTURE)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    gnd = data["nets"]["GND"]
    assert gnd["members"][-1].startswith("(+")  # "(+N more)" fold marker
    assert gnd["members"][-1].endswith(" more)")
    # degree reports the TRUE fanout, not the folded list length.
    assert gnd["degree"] > len(gnd["members"])


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_view_max_members_adjustable(runner: CliRunner) -> None:
    base = runner.invoke(main, ["view", "Q5", str(FIXTURE)])
    data = json.loads(base.stdout)
    # Default (20): SW1's five members are listed in full.
    assert not str(data["nets"]["SW1"]["members"][-1]).startswith("(+")

    tight = runner.invoke(
        main, ["view", "Q5", str(FIXTURE), "--max-members", "3"]
    )
    data2 = json.loads(tight.stdout)
    sw1 = data2["nets"]["SW1"]
    assert sw1["members"][-1].startswith("(+"), (
        "SW1 (5 members) must fold with --max-members 3"
    )
    assert sw1["degree"] == data["nets"]["SW1"]["degree"]


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_view_stubs_for_non_focal_reached(runner: CliRunner) -> None:
    """Components reached via rails/multi-hop are id-only stubs."""
    result = runner.invoke(main, ["view", "Q5", str(FIXTURE)])
    data = json.loads(result.stdout)
    stubs = [cid for cid, e in data["components"].items() if "pins" not in e]
    full = [cid for cid, e in data["components"].items() if "pins" in e]
    assert stubs and full
    assert "Q5" in full
    for cid in stubs:
        assert data["components"][cid] == {"id": cid}
