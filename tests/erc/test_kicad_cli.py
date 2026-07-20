"""Tests for circuit_graph.erc.kicad_cli."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from circuit_graph.erc.kicad_cli import KicadCliNotFoundError, KicadCliRunner


def test_runner_detects_kicad_cli():
    """KicadCliRunner must locate kicad-cli on this machine (or raise clearly)."""
    try:
        runner = KicadCliRunner()
        assert runner.binary_path.exists()
    except KicadCliNotFoundError:
        pytest.skip("kicad-cli not installed on this machine")


def test_runner_parses_json_report(tmp_path: Path):
    """A synthetic kicad-cli JSON report is parsed into an ErcReport."""
    runner = KicadCliRunner()
    report_file = tmp_path / "fake-erc.json"
    report_file.write_text(
        json.dumps(
            {
                "$schema": "https://schemas.kicad.org/erc.v1.json",
                "kicad_version": "10.0.4",
                "sheets": [
                    {
                        "path": "/",
                        "violations": [
                            {
                                "type": "power_pin_not_driven",
                                "severity": "error",
                                "description": "Input Power pin not driven",
                                "items": [
                                    {
                                        "description": "Symbol #PWR01 pin 1",
                                        "pos": {"x": 1.0, "y": 2.0},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report = runner._parse_json_report(report_file, source_file="x.kicad_sch")
    assert report.engine == "kicad-cli"
    assert report.kicad_version == "10.0.4"
    assert len(report.violations) == 1
    v = report.violations[0]
    assert v.severity == "error"
    assert v.type == "power_pin_not_driven"
    assert v.position == (1.0, 2.0)


def test_severity_args_unknown_raises_value_error():
    """Unknown severity strings raise ValueError (not KeyError) with options."""
    with pytest.raises(ValueError, match="Unknown severity"):
        KicadCliRunner._severity_args(["fatal"])
    with pytest.raises(ValueError, match="error"):
        KicadCliRunner._severity_args(["unknown"])


def test_severity_args_known_values():
    """Known severity values map to the correct kicad-cli flags."""
    assert KicadCliRunner._severity_args(None) == ["--severity-all"]
    assert KicadCliRunner._severity_args(["all"]) == ["--severity-all"]
    assert KicadCliRunner._severity_args(["error"]) == ["--severity-error"]
    assert KicadCliRunner._severity_args(["warning"]) == ["--severity-warning"]
    assert KicadCliRunner._severity_args(["exclusion"]) == ["--severity-exclusions"]
    assert KicadCliRunner._severity_args(["exclusions"]) == ["--severity-exclusions"]
