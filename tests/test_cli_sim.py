"""Tests for cg sim CLI command."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from circuit_graph.cli import main

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "protel" / "FX-T268-V6-10米-5065.NET"


class TestSimCommand:
    def test_sim_dry_run_outputs_spice(self):
        """--dry-run outputs SPICE deck text."""
        runner = CliRunner()
        result = runner.invoke(main, ["sim", "R100", str(FIXTURE), "--dry-run"])

        assert result.exit_code == 0
        assert "* LM321" in result.output
        assert ".OP" in result.output
        assert ".END" in result.output

    def test_sim_dry_run_with_output_file(self, tmp_path: Path):
        """--dry-run --output writes deck to file."""
        out_file = tmp_path / "test.cir"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["sim", "R100", str(FIXTURE), "--dry-run", "-o", str(out_file)],
        )

        assert result.exit_code == 0
        assert out_file.exists()
        content = out_file.read_text()
        assert ".OP" in content

    def test_sim_with_analysis_override(self):
        """--analysis tran overrides default AC analysis."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["sim", "R100", str(FIXTURE), "--dry-run", "--analysis", "tran"],
        )

        assert result.exit_code == 0
        assert ".TRAN" in result.output

    def test_sim_with_ngspice_opt(self):
        """--ngspice-opt appends .OPTIONS lines."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["sim", "R100", str(FIXTURE), "--dry-run", "--ngspice-opt", "gmin=1e-9"],
        )

        assert result.exit_code == 0
        assert "GMIN=1e-9" in result.output.upper() or "gmin=1e-9" in result.output

    def test_sim_missing_file(self):
        """Missing netlist file returns error."""
        runner = CliRunner()
        result = runner.invoke(main, ["sim", "R100", "nonexistent.NET", "--dry-run"])

        assert result.exit_code != 0

    def test_sim_preflight_fails_clearly_without_ngspice(self, monkeypatch):
        """Non-dry-run without a SPICE executable: clear error, non-zero exit."""
        import json

        import circuit_graph.cli.app as cli_module

        monkeypatch.setattr(cli_module.shutil, "which", lambda _bin: None)
        runner = CliRunner()
        result = runner.invoke(main, ["sim", "R100", str(FIXTURE)])

        assert result.exit_code != 0
        # result.output mixes stderr (the PoC warning); stdout carries the JSON
        payload = json.loads(result.stdout)
        assert "SPICE executable not found" in payload["error"]
        assert "--dry-run" in payload["error"]
