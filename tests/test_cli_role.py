"""cg role CLI 测试。"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import main

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "protel" / "FX-T268-V6-10米-5065.NET"
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_role_r100_answer_first(runner: CliRunner) -> None:
    result = runner.invoke(main, ["role", "R100", str(FIXTURE)])
    assert result.exit_code == 0, result.output
    first_line = result.stdout.splitlines()[0]
    # 答案前置：第 1 行必须同时含位号与功能定性
    assert "R100" in first_line
    assert "反馈电阻" in first_line
    assert "LM321" in first_line
    assert "101" in result.stdout


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_role_json_shape(runner: CliRunner) -> None:
    result = runner.invoke(main, ["role", "R100", str(FIXTURE), "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["designator"] == "R100"
    assert data["rule"] == "opamp_feedback"
    assert data["data"]["gain"] == pytest.approx(101.0)
    assert data["evidence"]


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_role_unknown_designator_exit_1(runner: CliRunner) -> None:
    result = runner.invoke(main, ["role", "NOPE1", str(FIXTURE)])
    assert result.exit_code == 1
    assert "not found" in result.stderr
