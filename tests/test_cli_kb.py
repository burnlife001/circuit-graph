"""KB reverse-lookup CLI tests (todolist P2-9)."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import main

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "protel"
FIXTURE = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_kb_lookup_usage_in(runner: CliRunner) -> None:
    result = runner.invoke(
        main, ["kb", "lookup", "SW5001", "--usage-in", str(FIXTURE)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    usage = data["usage"]
    designators = sorted(i["designator"] for i in usage["instances"])
    assert designators == ["Q5", "Q7"]
    q5 = next(i for i in usage["instances"] if i["designator"] == "Q5")
    assert {"SW1", "SW2"} <= set(q5["nets"])
    q7 = next(i for i in usage["instances"] if i["designator"] == "Q7")
    assert {"SW3", "SW4"} <= set(q7["nets"])


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_kb_search_by_pin(runner: CliRunner) -> None:
    result = runner.invoke(main, ["kb", "search", "--pin", "SW2"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    part_numbers = {m["part_number"] for m in data["matches"]}
    assert "SW5001" in part_numbers


def test_kb_search_pin_requires_arg_or_keyword(runner: CliRunner) -> None:
    result = runner.invoke(main, ["kb", "search"])
    assert result.exit_code != 0
