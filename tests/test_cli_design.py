import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import main


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_cli_design_writes_netlist(runner: CliRunner, tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"inputs": {"vin": 12, "vout": 5}}), encoding="utf-8")
    out = tmp_path / "out.NET"
    result = runner.invoke(
        main, ["design", "voltage-divider", "--spec", str(spec), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert "R1" in text and "R2" in text
    assert "14k" in text


def test_cli_design_json_output(runner: CliRunner, tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"inputs": {"vin": 12, "vout": 5}}), encoding="utf-8")
    out = tmp_path / "out.json"
    result = runner.invoke(
        main, ["design", "voltage-divider", "--spec", str(spec), "-o", str(out), "--json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {c["id"] for c in data["components"]} == {"R1", "R2"}


def test_cli_design_unknown_primitive(runner: CliRunner, tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text("{}", encoding="utf-8")
    result = runner.invoke(main, ["design", "no-such-primitive", "--spec", str(spec)])
    assert result.exit_code == 1
    assert "no-such-primitive" in result.output