"""Command line interface for circuit-graph.

`cg view` is the single entry-point for circuit understanding: it returns a
scoped ego-subgraph for a focal component, optionally annotated with the
component's power-supply context (--with-power), communication-bus
context (--with-comm) and/or the functional blocks touching the ego
subgraph (--with-blocks).  The legacy `cg power` / `cg comm` top-level
commands were removed in favour of these flags — the underlying modules
remain available programmatically.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import click

from circuit_graph.ir import CircuitIR, ir_to_json
from circuit_graph.parsers.protel import ParserInput
from circuit_graph.parsers.registry import parse_netlist

# Heavy module chains (enrichers → matcher → networkx, sim, erc, design)
# are imported lazily inside the commands that need them so that warm-cache
# CLI paths (cg view on an unchanged board) start in ~0.2 s instead of ~0.7 s.

# app.py lives one level deeper than the historical cli.py (cli/ package),
# so the project root is three parents up: cli/app.py -> cli -> circuit_graph -> root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_GRAPH_FILE = _PROJECT_ROOT / "output" / "circuit.graph.json"
DEFAULT_GRAPHML_FILE = _PROJECT_ROOT / "output" / "circuit.graphml"
DEFAULT_KB_PATH = _PROJECT_ROOT / "circuit_graph" / "knowledge" / "circuit_graph.db"
DEFAULT_LAYOUT_RULES_FILE = _PROJECT_ROOT / "fixtures" / "rules" / "default.schlayout"
DEFAULT_DESIGN_OUTPUT = _PROJECT_ROOT / "output" / "design.NET"
DEFAULT_DESIGN_JSON = _PROJECT_ROOT / "output" / "design.json"


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """circuit-graph CLI."""


@main.group()
def export() -> None:
    """Export circuit data."""


def _cache_options(f: Any) -> Any:
    """Shared --cache-dir / --no-cache options for parse-backed commands."""
    f = click.option(
        "--cache-dir",
        type=click.Path(path_type=Path),
        default=None,
        help="IR cache directory (default: <repo>/.cg-cache).",
    )(f)
    f = click.option("--no-cache", is_flag=True, help="Bypass the IR parse/enrich cache.")(f)
    return f


@export.command(name="graphml")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=DEFAULT_GRAPHML_FILE)
@click.option(
    "--topology-timeout",
    type=float,
    default=None,
    help="Per-template topology match timeout in seconds (default: recognizer built-in).",
)
@_cache_options
def export_graphml(
    file: Path,
    output: Path,
    topology_timeout: float | None,
    cache_dir: Path | None,
    no_cache: bool,
) -> None:
    """Export the circuit graph to GraphML."""
    warnings: list[str] = []
    ir = _parse_and_enrich(
        file,
        warnings_out=warnings,
        topology_timeout=topology_timeout,
        cache_dir=cache_dir,
        use_cache=not no_cache,
    )
    _emit_warnings_summary(warnings)
    from circuit_graph.exporters.graphml import write_graphml

    write_graphml(ir, output)
    click.echo(f"Wrote GraphML to {output}")


@export.command(name="graph")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=DEFAULT_GRAPH_FILE)
@click.option("--pretty", is_flag=True, help="Indent the JSON for humans (~2x larger).")
@click.option(
    "--topology-timeout",
    type=float,
    default=None,
    help="Per-template topology match timeout in seconds (default: recognizer built-in).",
)
@_cache_options
def export_graph(
    file: Path,
    output: Path,
    pretty: bool,
    topology_timeout: float | None,
    cache_dir: Path | None,
    no_cache: bool,
) -> None:
    """Generate the cg-graph JSON view of the circuit."""
    warnings: list[str] = []
    ir = _parse_and_enrich(
        file,
        warnings_out=warnings,
        topology_timeout=topology_timeout,
        cache_dir=cache_dir,
        use_cache=not no_cache,
    )
    _emit_warnings_summary(warnings)
    from circuit_graph.generators.cg_graph import write_cg_graph

    write_cg_graph(ir, output, pretty=pretty)
    click.echo(f"Wrote cg-graph JSON to {output}")


def _emit_warnings_summary(warnings: list[str]) -> None:
    """Print a one-line warnings summary to stderr (silent when clean)."""
    if warnings:
        click.echo(f"warnings: {len(warnings)} — {'; '.join(warnings)}", err=True)




@main.command(name="layout-check")
@click.argument(
    "schematics",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--rules",
    "rules_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_LAYOUT_RULES_FILE,
    show_default=True,
    help="Path to .schlayout rules file (default: <repo>/fixtures/rules/default.schlayout)",
)
@click.option(
    "--compact",
    is_flag=True,
    help="Emit JSON on a single line (default: pretty multi-line).",
)
@click.option(
    "--baseline",
    type=click.Path(path_type=Path),
    help="Write the violation baseline snapshot to PATH.",
)
@click.option(
    "--diff-baseline",
    type=click.Path(exists=True, path_type=Path),
    help="Fail only on violations NOT present in the baseline at PATH.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Print per-rule PASS/SKIP summary to stderr.",
)
def layout_check(
    schematics: tuple[Path, ...],
    rules_path: Path,
    compact: bool,
    baseline: Path | None,
    diff_baseline: Path | None,
    verbose: bool,
) -> None:
    """Run declarative layout rules against one or more schematic files.

    SCHEMATICS may be ``.NET`` (Protel logical netlist) or ``.kicad_sch``
    (KiCad geometry). Each input format supports a different rule subset;
    spatial rules (alignment, decoupling, power/ground placement, flow,
    routing) auto-skip on ``.NET`` and are counted in
    ``summary.rules_skipped_unsupported_input``.

    Exit codes:
        0  all rules passed (or only baselined violations remained)
        1  ≥1 violation remains
        2  configuration / parse error

    stdout: JSON report; stderr: human progress (only with ``-v``).
    """
    # Imported lazily so the cli module stays importable even before the
    # layout_check package is fully wired (e.g. on a fresh checkout mid-batch).
    from circuit_graph.layout_check.checker import LayoutChecker
    from circuit_graph.layout_check.loader import LayoutCheckError

    # Pre-flight: rules file existence is checked explicitly so the
    # error code is 2 (not 1) and reports `rules_file_not_found`.
    if not rules_path.exists():
        sys.stdout.buffer.write(
            json.dumps(
                {
                    "$schema": "cg-layout-check-v1",
                    "tool": {"name": "cg-layout-check", "version": "0.1.0"},
                    "error": {
                        "kind": "rules_file_not_found",
                        "message": f"rules file not found: {rules_path}",
                        "location": str(rules_path),
                        "cause_path": "$",
                    },
                },
                indent=None if compact else 2,
                ensure_ascii=False,
            ).encode("utf-8")
        )
        sys.stdout.buffer.write(b"\n")
        sys.exit(2)

    results: list[dict] = []
    final_exit = 0

    for sch in schematics:
        if verbose:
            click.echo(f"checking: {sch}", file=sys.stderr)

        checker = LayoutChecker(rules_path=rules_path, schematic_path=sch)
        try:
            checker.load_rules()
            checker.parse_schematic()
        except LayoutCheckError as e:
            results.append(
                {
                    "$schema": "cg-layout-check-v1",
                    "tool": {"name": "cg-layout-check", "version": "0.1.0"},
                    "input": {"schematic": str(sch), "rules_file": str(rules_path)},
                    "error": {
                        "kind": e.kind,
                        "message": e.message,
                        "location": e.location,
                        "cause_path": e.cause_path,
                    },
                }
            )
            final_exit = max(final_exit, 2)
            continue

        if verbose:
            n_rules = len(checker.rules or [])
            cn = len(checker.schematic.components)
            nn = len(checker.schematic.nets)
            wn = len(checker.schematic.wires)
            click.echo(
                f"  loaded {n_rules} rule(s); "
                f"schematic has {cn} components / {nn} nets / {wn} wires",
                file=sys.stderr,
            )

        checker.evaluate()

        if verbose:
            s = checker.summary
            click.echo(
                f"  evaluated: {s.get('rules_evaluated', 0)}/{s.get('rules_total', 0)} rules, "
                f"{s.get('violations', 0)} violation(s); "
                f"skipped (disabled={s.get('rules_skipped_disabled', 0)}, "
                f"unsupported-input={s.get('rules_skipped_unsupported_input', 0)})",
                file=sys.stderr,
            )

        # Optional: write baseline snapshot of current violation sigs.
        if baseline:
            checker.write_baseline(baseline)
            if verbose:
                click.echo(f"  wrote baseline to {baseline}", file=sys.stderr)

        report = checker.report()

        # Optional: filter to violations NOT in baseline.
        if diff_baseline:
            filtered = checker.diff_against_baseline(diff_baseline)
            from dataclasses import asdict  # local import keeps top-level clean
            report["violations"] = [asdict(v) for v in filtered]
            report["summary"]["violations"] = len(filtered)
            report["summary"]["baselined_violations"] = (
                len(checker._violations) - len(filtered)  # noqa: SLF001 (orchestrator-private)
            )

        results.append(report)

        if report.get("violations"):
            final_exit = max(final_exit, 1)

    output_doc = {
        "$schema": "cg-layout-check-v1",
        "tool": {"name": "cg-layout-check", "version": "0.1.0"},
        "results": results,
        "summary": {"schematics": len(results)},
    }
    sys.stdout.buffer.write(
        json.dumps(
            output_doc,
            indent=None if compact else 2,
            ensure_ascii=False,
        ).encode("utf-8")
    )
    sys.stdout.buffer.write(b"\n")
    sys.exit(final_exit)


@main.command(name="design")
@click.argument("primitive_id")
@click.option(
    "--spec",
    "spec_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="JSON design spec: {inputs, nets, designators, start_index}.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output .NET netlist (default: <repo>/output/design.NET).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit CircuitIR JSON instead of a .NET netlist.",
)
def design(primitive_id: str, spec_path: Path, output: Path | None, as_json: bool) -> None:
    """Instantiate a primitive template into a concrete netlist."""
    from circuit_graph.primitives import load_primitives

    templates = load_primitives()
    template = next((t for t in templates if t.id == primitive_id), None)
    if template is None:
        click.echo(f"Unknown primitive: {primitive_id}", err=True)
        sys.exit(1)

    from circuit_graph.design import DesignError, DesignSpec, instantiate, parse_number

    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    spec = DesignSpec(
        inputs={k: parse_number(v) for k, v in raw.get("inputs", {}).items()},
        nets=raw.get("nets", {}),
        designators=raw.get("designators", {}),
        start_index=int(raw.get("start_index", 1)),
    )
    try:
        result = instantiate(template, spec)
    except DesignError as exc:
        click.echo(f"Design error: {exc}", err=True)
        sys.exit(1)
    for warning in result.warnings:
        click.echo(f"warning: {warning}", err=True)

    out = output or (DEFAULT_DESIGN_JSON if as_json else DEFAULT_DESIGN_OUTPUT)
    out.parent.mkdir(parents=True, exist_ok=True)
    if as_json:
        out.write_text(ir_to_json(result.ir), encoding="utf-8")
    else:
        from circuit_graph.exporters.protel_netlist import write_protel_netlist

        write_protel_netlist(result.ir, out)
    click.echo(f"Wrote {len(result.ir.components)} components to {out}")


@main.command()
@click.argument("designator")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Generate the SPICE deck but do not run Ngspice.")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Write SPICE deck to file.")
@click.option(
    "--analysis",
    type=click.Choice(["op", "ac", "tran"]),
    default=None,
    help="Override the default analysis type.",
)
@click.option(
    "--ngspice-opt",
    multiple=True,
    help="Extra Ngspice option, e.g. --ngspice-opt gmin=1e-9. Repeatable.",
)
def sim(
    designator: str,
    file: Path,
    dry_run: bool,
    output: Path | None,
    analysis: str | None,
    ngspice_opt: tuple[str, ...],
) -> None:
    """Simulate the functional block containing DESIGNATOR.

    Parses the netlist FILE, identifies the topology around DESIGNATOR,
    generates a SPICE deck, and optionally runs it through Ngspice.

    Phase 1: hardcoded LM321 non-inverting amplifier proof-of-concept.
    """
    # Phase 1: designator is currently ignored — we always run the hardcoded LM321 deck.
    click.echo(
        "WARNING: 'cg sim' is Phase 1 PoC — the DESIGNATOR argument is ignored. "
        "A hardcoded DeckBuilder.build_lm321_non_inverting() deck (Rf=10k, Rg=1k, "
        "VCC=5V) is always simulated. Do NOT treat results as a verification of the "
        "part you passed in; results only prove the LM321 deck self-checks.",
        err=True,
    )
    from circuit_graph.sim.deck_builder import DeckBuilder

    builder = DeckBuilder()
    deck = builder.build_lm321_non_inverting()

    # Apply CLI overrides
    if analysis:
        analysis_upper = analysis.upper()
        if analysis_upper == "OP":
            analyses = (".OP",)
        elif analysis_upper == "TRAN":
            analyses = (".TRAN 1u 100u",)
        else:
            analyses = (f".{analysis_upper} DEC 10 1 1e6",)
    else:
        analyses = deck.analyses

    ngspice_opts = tuple(f".OPTIONS {opt}" for opt in ngspice_opt)

    from circuit_graph.sim.spice_deck import SimulationDeck

    deck = SimulationDeck(
        name=deck.name,
        includes=deck.includes,
        models=deck.models,
        subckts=deck.subckts,
        netlist=deck.netlist,
        stimuli=deck.stimuli,
        analyses=analyses,
        probes=deck.probes,
        options=deck.options + ngspice_opts,
        diagnostics=deck.diagnostics,
    )

    # Run pre-flight validation
    from circuit_graph.sim.deck_validator import DeckValidator
    validator = DeckValidator()
    diags = validator.validate(deck)
    if diags:
        deck = SimulationDeck(
            name=deck.name,
            includes=deck.includes,
            models=deck.models,
            subckts=deck.subckts,
            netlist=deck.netlist,
            stimuli=deck.stimuli,
            analyses=deck.analyses,
            probes=deck.probes,
            options=deck.options,
            diagnostics=diags,
        )

    spice_text = deck.to_spice()

    if dry_run:
        if output:
            output.write_text(spice_text, encoding="utf-8")
            click.echo(f"SPICE deck written to {output}")
        else:
            click.echo(spice_text)
        return

    # Pre-flight: fail fast with a clear message when Ngspice is absent
    # instead of dying inside the subprocess call.
    from circuit_graph.sim.spice_runner import SpiceRunner, SpiceRunnerConfig

    config = SpiceRunnerConfig(timeout_seconds=30.0)
    if shutil.which(config.ngspice_bin) is None:
        click.echo(
            json.dumps(
                {
                    "error": (
                        f"SPICE executable not found: {config.ngspice_bin!r}. "
                        "Install ngspice (see the Readme 仿真 section) "
                        "or re-run with --dry-run to only generate the deck."
                    )
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    # Run Ngspice
    try:
        runner = SpiceRunner(config)
        result = runner.run(deck)
    except Exception as exc:
        click.echo(json.dumps({"error": str(exc)}, indent=2))
        sys.exit(1)

    # Output results as JSON
    analyses_output = []
    for a in result.analyses:
        analyses_output.append({
            "kind": a.kind,
            "convergence": a.convergence,
            "iterations": a.iterations,
            "data": a.as_dict(),
        })

    output_obj = {
        "designator": designator,
        "exit_code": result.exit_code,
        "timestamp": result.timestamp,
        "analyses": analyses_output,
        "diagnostics": list(deck.diagnostics),
    }

    if output:
        out_json = json.dumps(output_obj, indent=2, ensure_ascii=False)
        output.write_text(out_json, encoding="utf-8")
        click.echo(f"Results written to {output}")
    else:
        click.echo(json.dumps(output_obj, indent=2, ensure_ascii=False))

    if deck.diagnostics:
        click.echo("\nDiagnostics:", err=True)
        for d in deck.diagnostics:
            click.echo(f"  {d}", err=True)


# ---------------------------------------------------------------------------
# Shared helpers


def _parse_and_enrich(
    file: Path,
    *,
    warnings_out: list[str] | None = None,
    topology_timeout: float | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
) -> CircuitIR:
    """Parse the netlist and run the standard enricher pipeline.

    This is the single source of truth for "fresh IR from a file" — every
    CLI command routes through it so that downstream consumers always see
    normalised values, classified nets and recognised topology blocks.

    When ``warnings_out`` is given, the pipeline's warnings (dependency
    misses, skipped enrichers, and enricher-level warnings such as
    ``TOPOLOGY_MATCH_ABORTED``) are appended to it.

    Results are cached in ``cache_dir`` (default ``<repo>/.cg-cache``) keyed
    by netlist content + enricher version + primitive library hash; pass
    ``use_cache=False`` to bypass.  Cache hits restore the warnings too, so
    diagnostics stay visible on warm runs.
    """
    # Cache lookup first: a warm hit must not pay the enricher import chain
    # (enrichers → matcher → networkx is ~0.3 s).
    from circuit_graph.cache import cache_key, default_cache_dir, load_cached

    cache_root = cache_dir if cache_dir is not None else default_cache_dir()
    key: str | None = None
    if use_cache:
        try:
            key = cache_key(file)
        except OSError:
            key = None
        if key is not None:
            hit = load_cached(cache_root, key)
            if hit is not None:
                ir, cached_warnings = hit
                if warnings_out is not None:
                    warnings_out.extend(cached_warnings)
                return ir

    from circuit_graph.cache import save_cached
    from circuit_graph.enrichers import run_enrichers
    from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher
    from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
    from circuit_graph.enrichers.net_classifier import NetClassifier
    from circuit_graph.enrichers.pin_resolver import PinNameResolver
    from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
    from circuit_graph.enrichers.value_normalizer import ValueNormalizer

    ir = parse_netlist(ParserInput(source_id=str(file), file_path=str(file)))
    collected: list[str] = []
    result = run_enrichers(
        ir,
        [
            MultiUnitSplitter(),
            ValueNormalizer(),
            NetClassifier(),
            PinNameResolver(),
            EdgeSemanticEnricher(),
            TopologyRecognizer(timeout_seconds=topology_timeout),
        ],
    )
    collected.extend(result.warnings)
    if warnings_out is not None:
        warnings_out.extend(collected)
    if key is not None:
        try:
            save_cached(cache_root, key, result.ir, collected)
        except OSError:
            pass  # cache write failure must not break the command
    return result.ir




if __name__ == "__main__":
    main()


# Register extracted command groups (imported for side effects).
from circuit_graph.cli import erc as _erc  # noqa: E402,F401
from circuit_graph.cli import init as _init  # noqa: E402,F401
from circuit_graph.cli import kb as _kb  # noqa: E402,F401
from circuit_graph.cli import query as _query  # noqa: E402,F401
from circuit_graph.cli import view as _view  # noqa: E402,F401
