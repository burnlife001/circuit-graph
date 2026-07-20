"""Level 1 ERC: wrap ``kicad-cli sch erc`` as a subprocess.

kicad-cli outputs a JSON report file alongside the input schematic. We parse
that file and normalize it into the same :class:`ErcReport` shape the native
engine produces, so callers can compare results engine-agnostically.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from circuit_graph.erc.models import ErcReport, ErcViolation, Severity


class KicadCliNotFoundError(RuntimeError):
    """Raised when ``kicad-cli`` cannot be located."""


def _find_kicad_cli() -> Path:
    """Locate the kicad-cli binary.

    Search order:
    1. ``KICAD_CLI_PATH`` env var
    2. ``D:/Programs/KiCad/*/bin/kicad-cli.exe`` (Windows default install)
    3. ``shutil.which("kicad-cli")`` (PATH)

    Raises:
        KicadCliNotFoundError: if no binary is found.
    """
    env = os.environ.get("KICAD_CLI_PATH")
    if env:
        env_path = Path(env)
        if env_path.exists():
            return env_path

    win_root = Path("D:/Programs/KiCad")
    if win_root.exists():
        for child in sorted(win_root.iterdir(), reverse=True):
            candidate = child / "bin" / "kicad-cli.exe"
            if candidate.exists():
                return candidate

    which = shutil.which("kicad-cli")
    if which:
        return Path(which)

    raise KicadCliNotFoundError(
        "kicad-cli not found. Set KICAD_CLI_PATH or install KiCad "
        "(e.g. D:/Programs/KiCad/<version>/bin/kicad-cli.exe on Windows)"
    )


class KicadCliRunner:
    """Run ``kicad-cli sch erc`` and parse its JSON output."""

    def __init__(
        self,
        binary_path: str | Path | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.binary_path = Path(binary_path) if binary_path is not None else _find_kicad_cli()
        self.timeout = timeout

    def run_erc(
        self,
        sch_file: Path,
        severities: list[str] | None = None,
    ) -> ErcReport:
        """Run ERC on ``sch_file`` and return the normalized report."""
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "erc-report.json"
            cmd = [
                str(self.binary_path),
                "sch",
                "erc",
                "--format",
                "json",
                "--output",
                str(report_path),
                *self._severity_args(severities),
                str(sch_file),
            ]
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=self.timeout,
            )
            if not report_path.exists():
                raise RuntimeError(
                    f"kicad-cli did not produce {report_path}; "
                    f"returncode={proc.returncode}, stderr={proc.stderr[:200]!r}"
                )
            return self._parse_json_report(report_path, source_file=str(sch_file))

    def _parse_json_report(
        self,
        report_path: Path,
        source_file: str,
    ) -> ErcReport:
        """Normalize the kicad-cli JSON report into :class:`ErcReport`."""
        data = json.loads(report_path.read_text(encoding="utf-8"))
        violations: list[ErcViolation] = []

        severity_map: dict[str, Severity] = {
            "error": "error",
            "warning": "warning",
            "exclusion": "info",
        }

        for sheet in data.get("sheets", []):
            sheet_path = sheet.get("path", "/")
            for raw in sheet.get("violations", []):
                item_details = self._parse_items(raw.get("items", []))
                violation_type = raw.get("type", "unknown")
                violations.append(
                    ErcViolation(
                        severity=severity_map.get(raw.get("severity", "warning"), "warning"),
                        type=violation_type,
                        code=f"kicad-cli:{violation_type}",
                        message=raw.get("description", ""),
                        net_name=None,
                        component_refs=item_details["refs"],
                        pin_numbers=item_details["pins"],
                        position=item_details["position"],
                        sheet_path=sheet_path,
                    )
                )

        return ErcReport(
            source_file=source_file,
            engine="kicad-cli",
            violations=violations,
            kicad_version=data.get("kicad_version"),
        )

    @staticmethod
    def _severity_args(severities: list[str] | None) -> list[str]:
        if severities is None:
            return ["--severity-all"]

        flag_by_severity = {
            "all": "--severity-all",
            "error": "--severity-error",
            "warning": "--severity-warning",
            "exclusion": "--severity-exclusions",
            "exclusions": "--severity-exclusions",
        }
        valid = sorted(flag_by_severity.keys())
        result: list[str] = []
        for severity in severities:
            flag = flag_by_severity.get(severity)
            if flag is None:
                raise ValueError(
                    f"Unknown severity: {severity!r}. Valid options: {valid}"
                )
            result.append(flag)
        return result

    @classmethod
    def _parse_items(cls, items: list[dict]) -> dict[str, list[str] | tuple[float, float] | None]:
        positions: list[tuple[float, float]] = []
        refs: set[str] = set()
        pins: set[str] = set()

        for item in items:
            pos = item.get("pos")
            if pos is not None:
                positions.append((float(pos["x"]), float(pos["y"])))
            description = item.get("description", "")
            refs.update(cls._extract_refs(description))
            pins.update(cls._extract_pins(description))

        return {
            "refs": sorted(refs),
            "pins": sorted(pins),
            "position": positions[0] if positions else None,
        }

    @staticmethod
    def _extract_refs(text: str) -> list[str]:
        """Pull component refs such as R100 or U1 from a kicad-cli item string."""
        return re.findall(r"\b([A-Z][A-Z0-9_]*\d+)\b", text)

    @staticmethod
    def _extract_pins(text: str) -> list[str]:
        """Pull pin numbers from a kicad-cli item string."""
        return re.findall(r"\bpin\s+(\d+)\b", text)
