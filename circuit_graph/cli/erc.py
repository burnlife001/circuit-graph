"""cg erc — electrical rules check commands (extracted from app.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from circuit_graph.cli.app import main
from circuit_graph.erc import ErcReport, ElectricalRulesChecker, KicadCliRunner



# ---------------------------------------------------------------------------
# cg erc — electrical rules check (Level 1 kicad-cli + Level 2 native)
# ---------------------------------------------------------------------------

# ANSI colors for the human-friendly --pretty output. Disabled automatically
# when stdout is not a TTY so piped output stays plain.
_RESET = "\033[0m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BOLD = "\033[1m"


def _color(text: str, code: str, enabled: bool) -> str:
    return f"{code}{text}{_RESET}" if enabled else text


def _format_violations_text(
    report: ErcReport,
    *,
    severity_filter: str,
    color_enabled: bool,
) -> str:
    """Render an ErcReport as a human-readable table."""
    sev_rank = {"error": 0, "warning": 1, "info": 2}
    rows: list[tuple[str, str, str, str, str, str]] = []
    for v in report.violations:
        if severity_filter == "error" and v.severity != "error":
            continue
        if severity_filter == "warning" and v.severity != "warning":
            continue
        rows.append((
            v.severity.upper(),
            v.code,
            v.type,
            ", ".join(v.component_refs) or "-",
            ", ".join(v.pin_numbers) or "-",
            v.net_name or "-",
        ))
    rows.sort(key=lambda r: (sev_rank.get(r[0].lower(), 9), r[1]))

    header = ("SEVERITY", "CODE", "TYPE", "COMPONENTS", "PINS", "NET")
    widths = [len(h) for h in header]
    for row in rows:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row)]

    def _line(cells: tuple[str, ...], color_codes: list[str] | None = None) -> str:
        parts: list[str] = []
        for i, cell in enumerate(cells):
            rendered = cell
            if color_codes and color_codes[i]:
                rendered = _color(cell.ljust(widths[i]), color_codes[i], color_enabled)
            else:
                rendered = cell.ljust(widths[i])
            parts.append(rendered)
        return "  ".join(parts)

    sep = "  ".join("-" * w for w in widths)
    lines: list[str] = []
    title = _color(
        f"{_BOLD}ERC report: {report.engine} engine, "
        f"{report.error_count} error(s), {report.warning_count} warning(s){_RESET}",
        _BOLD,
        color_enabled,
    )
    lines.append(title)
    lines.append(_line(header))
    lines.append(sep)
    for row in rows:
        codes = ["", "", "", "", "", ""]
        if row[0] == "ERROR":
            codes[0] = _RED
        elif row[0] == "WARNING":
            codes[0] = _YELLOW
        lines.append(_line(row, codes))
    if not rows:
        lines.append(_color("(no violations matching filter)", _YELLOW, color_enabled))
    lines.append("")
    lines.append(
        f"duration_ms={report.duration_ms:.2f}"
        + (f"  kicad_version={report.kicad_version}" if report.kicad_version else "")
    )
    return "\n".join(lines)


def _run_native_erc(sch_file: Path) -> ErcReport:
    """Run the pure-Python native engine on a .kicad_sch file."""
    from circuit_graph.erc.extractor import NetlistExtractor

    extractor = NetlistExtractor()
    sch = extractor.extract(sch_file)
    return ElectricalRulesChecker().run(sch)


def _run_kicad_erc(
    sch_file: Path,
    severity_filter: str,
) -> ErcReport:
    """Run kicad-cli ERC wrapper on a .kicad_sch file."""
    severities = None if severity_filter == "all" else [severity_filter]
    return KicadCliRunner().run_erc(sch_file, severities=severities)


def _format_report(
    report: ErcReport,
    *,
    fmt: str,
    severity_filter: str,
    pretty: bool,
) -> str:
    if fmt == "text":
        return _format_violations_text(
            report,
            severity_filter=severity_filter,
            color_enabled=pretty and sys.stdout.isatty(),
        )
    # JSON mode: filter then serialize.
    filtered = report.violations
    if severity_filter == "error":
        filtered = [v for v in filtered if v.severity == "error"]
    elif severity_filter == "warning":
        filtered = [v for v in filtered if v.severity == "warning"]
    payload = report.to_dict()
    payload["violations"] = [v.to_dict() for v in filtered]
    payload["violation_count"] = len(filtered)
    if pretty:
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return json.dumps(payload, ensure_ascii=False)


@main.group()
def erc() -> None:
    """Electrical Rules Check — verify schematic for connection issues."""


@erc.command(name="check")
@click.argument("schematic", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--engine",
    type=click.Choice(["native", "kicad"]),
    default="native",
    show_default=True,
    help="Engine: pure-Python (native) or wrap kicad-cli (kicad).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "text"]),
    default="json",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--severity",
    type=click.Choice(["error", "warning", "all"]),
    default="all",
    show_default=True,
    help="Severity filter (only honoured by kicad engine for now).",
)
@click.option(
    "--pretty",
    is_flag=True,
    help="Pretty-print: indented JSON or colorised table when --format text.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Write to file instead of stdout.",
)
def erc_check(
    schematic: Path,
    engine: str,
    fmt: str,
    severity: str,
    pretty: bool,
    output: Path | None,
) -> None:
    """Run ERC on a .kicad_sch file.

    Reports pin-type conflicts, undriven power nets, duplicate references,
    and unconnected inputs. Exit code is 0 when there are no error-severity
    violations, 1 otherwise.
    """
    if schematic.suffix.lower() != ".kicad_sch":
        click.echo(
            json.dumps(
                {
                    "error": (
                        f"unsupported schematic format: {schematic.suffix!r}. "
                        "cg erc only accepts .kicad_sch files. "
                        f"Got: {schematic}"
                    )
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        sys.exit(2)

    try:
        if engine == "kicad":
            report = _run_kicad_erc(schematic, severity)
        else:
            report = _run_native_erc(schematic)
    except Exception as exc:  # noqa: BLE001
        click.echo(
            json.dumps({"error": f"ERC failed: {exc}"}, ensure_ascii=False),
            err=True,
        )
        sys.exit(1)

    rendered = _format_report(
        report, fmt=fmt, severity_filter=severity, pretty=pretty
    )
    if output is not None:
        output.write_text(rendered + "\n", encoding="utf-8")
        click.echo(f"Wrote ERC report to {output}", err=True)
    else:
        click.echo(rendered)
        if not rendered.endswith("\n"):
            click.echo("")

    sys.exit(1 if report.error_count > 0 else 0)


