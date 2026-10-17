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

import click

from circuit_graph.design import DesignError, DesignSpec, instantiate, parse_number
from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.enrichers.net_classifier import NetClassifier
from circuit_graph.enrichers.pin_resolver import PinNameResolver
from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
from circuit_graph.enrichers.value_normalizer import ValueNormalizer
from circuit_graph.exporters.graphml import write_graphml
from circuit_graph.exporters.protel_netlist import write_protel_netlist
from circuit_graph.generators.cg_graph import view_component, write_cg_graph
from circuit_graph.graph import create_graph
from circuit_graph.ir import CircuitIR
from circuit_graph.ir import ir_to_json
from circuit_graph.knowledge import ComponentDatabase
from circuit_graph.parsers.protel import ParserInput
from circuit_graph.parsers.registry import parse_netlist
from circuit_graph.primitives import load_primitives
from circuit_graph.sim.deck_builder import DeckBuilder
from circuit_graph.sim.spice_deck import SimulationDeck
from circuit_graph.sim.spice_runner import SpiceRunner, SpiceRunnerConfig
from circuit_graph.erc import ErcReport, ElectricalRulesChecker, KicadCliRunner

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


@export.command(name="graphml")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=DEFAULT_GRAPHML_FILE)
def export_graphml(file: Path, output: Path) -> None:
    """Export the circuit graph to GraphML."""
    ir = _parse_and_enrich(file)
    write_graphml(ir, output)
    click.echo(f"Wrote GraphML to {output}")


@export.command(name="graph")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=DEFAULT_GRAPH_FILE)
@click.option("--pretty", is_flag=True, help="Indent the JSON for humans (~2x larger).")
def export_graph(file: Path, output: Path, pretty: bool) -> None:
    """Generate the cg-graph JSON view of the circuit."""
    ir = _parse_and_enrich(file)
    write_cg_graph(ir, output, pretty=pretty)
    click.echo(f"Wrote cg-graph JSON to {output}")


@main.command()
@click.argument("designator")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--hops", type=int, default=2, show_default=True)
@click.option("--pretty", is_flag=True, help="Indent the JSON for humans (~2x larger).")
@click.option(
    "--with-power",
    is_flag=True,
    help="Include `power_context` describing the focal component's power-supply path.",
)
@click.option(
    "--with-comm",
    is_flag=True,
    help="Include `comm_context` describing the focal component's communication buses.",
)
@click.option(
    "--with-blocks",
    is_flag=True,
    help="Include `blocks` listing functional blocks that touch the ego subgraph.",
)
@click.option(
    "--with-all",
    is_flag=True,
    help="Shorthand for --with-power --with-comm --with-blocks.",
)
def view(
    designator: str,
    file: Path,
    hops: int,
    pretty: bool,
    with_power: bool,
    with_comm: bool,
    with_blocks: bool,
    with_all: bool,
) -> None:
    """Output a scoped ego-subgraph for a single component.

    Pass --with-power, --with-comm and/or --with-blocks to enrich the output
    JSON with the focal component's power-supply path, communication-bus
    participation and recognised functional blocks.
    """
    if with_all:
        with_power = True
        with_comm = True
        with_blocks = True

    ir = _parse_and_enrich(file)
    designator = _canonical_designator(ir, designator)
    db = ComponentDatabase.default()
    result = view_component(ir, designator, hops=hops, db=db)
    if result is None:
        payload = {"error": _not_found_message(ir, designator)}
        sys.stdout.buffer.write(
            json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        )
        sys.stdout.buffer.write(b"\n")
        sys.exit(1)

    if with_power:
        ctx = _build_power_context(ir, designator)
        if ctx is not None:
            result["power_context"] = ctx
    if with_comm:
        ctx = _build_comm_context(ir, designator)
        if ctx is not None:
            result["comm_context"] = ctx
    if with_blocks:
        result["blocks"] = _build_blocks_context(ir, result)

    output = json.dumps(result, indent=2 if pretty else None, ensure_ascii=False)
    sys.stdout.buffer.write(output.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")


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

        violations = checker.evaluate()

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
    templates = load_primitives()
    template = next((t for t in templates if t.id == primitive_id), None)
    if template is None:
        click.echo(f"Unknown primitive: {primitive_id}", err=True)
        sys.exit(1)

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


def _canonical_designator(ir: CircuitIR, designator: str) -> str:
    """Resolve ``designator`` to the exact component id, case-insensitively.

    Protel designators are uppercase by convention but users type ``u2b``;
    an exact match always wins, otherwise the unique case-insensitive match
    is used.  Returns the input unchanged when nothing matches.
    """
    ids = {c.id for c in ir.components}
    if designator in ids:
        return designator
    lowered = designator.lower()
    return next((i for i in sorted(ids) if i.lower() == lowered), designator)


def _not_found_message(ir: CircuitIR, designator: str) -> str:
    """Build the "component not found" error message for ``cg view``.

    When ``designator`` names a multi-unit device that MultiUnitSplitter has
    split into per-unit virtual components (e.g. LM358 U2 -> U2A/U2B/U2C),
    the bare designator no longer exists in the IR.  The message then lists
    the split units — annotating the power unit — and suggests a concrete
    signal unit to view instead.  Matching is case-insensitive so ``u2``
    still surfaces the hint for ``U2``.
    """
    lowered = designator.lower()
    units = sorted(
        (
            c
            for c in ir.components
            if str(c.metadata.get("split_from", "")).lower() == lowered
        ),
        key=lambda c: c.id,
    )
    if not units:
        return f"Component {designator} not found"
    parent = str(units[0].metadata["split_from"])
    base = f"Component {parent} not found"
    labels = [
        f"{u.id}(电源)" if u.metadata.get("unit_type") == "power" else u.id
        for u in units
    ]
    suggested = next(
        (u.id for u in units if u.metadata.get("unit_type") != "power"),
        units[0].id,
    )
    return f"{base}. 已拆分为多单元: {', '.join(labels)} — 请用 cg view {suggested} 查看"


def _parse_and_enrich(file: Path) -> CircuitIR:
    """Parse the netlist and run the standard enricher pipeline.

    This is the single source of truth for "fresh IR from a file" — every
    CLI command routes through it so that downstream consumers always see
    normalised values, classified nets and recognised topology blocks.
    """
    ir = parse_netlist(ParserInput(source_id=str(file), file_path=str(file)))
    ir = run_enrichers(
        ir,
        [
            MultiUnitSplitter(),
            ValueNormalizer(),
            NetClassifier(),
            PinNameResolver(),
            EdgeSemanticEnricher(),
            TopologyRecognizer(),
        ],
    ).ir
    return ir


def _build_power_context(ir: CircuitIR, designator: str) -> dict[str, object] | None:
    """Return a power-supply context dict for ``designator``, or ``None``.

    The context is None when the component is not on any recognised power
    path (e.g. it sits on a signal net only).  When present, the returned
    dict exposes:

    * ``rail`` — the power-rail net name the focal component sits on
    * ``source_net`` — the upstream source net name (USB_5V, VBAT, ...)
    * ``source_kind`` — the inferred source kind (``usb``, ``battery``,
      ``barrel_jack``, ``header_pin``)
    * ``upstream_regulators`` — regulators between the rail and the source,
      listed in source-to-load order

    ComponentDatabase loading is deferred so the cost is only paid when
    ``--with-power`` is requested.
    """
    from circuit_graph.ir import NetType
    from circuit_graph.power_tree_recognizer import build_power_tree

    comp_map = {c.id: c for c in ir.components}
    if designator not in comp_map:
        return None

    net_ids_for_comp: set[str] = set()
    for conn in ir.connections:
        if conn.pin_id.startswith(f"{designator}."):
            net_ids_for_comp.add(conn.net_id)
    if not net_ids_for_comp:
        return None

    graph = create_graph().load(ir).graph
    db = ComponentDatabase.default()
    tree = build_power_tree(ir, graph, db)

    net_lookup = {n.id: n for n in ir.nets}
    net_id_to_name = {n.id: n.name for n in ir.nets}

    # Restrict to nets the focal component actually touches AND that the
    # NetClassifier has tagged as POWER.  This avoids reporting context for
    # signal-only nets (e.g. CC2, RXD) and for ground nets (handled below).
    power_net_ids_for_comp = {
        net_id
        for net_id in net_ids_for_comp
        if (net_lookup.get(net_id) is not None and net_lookup[net_id].type is NetType.POWER)
    }
    if not power_net_ids_for_comp:
        return None

    # 1) Try the deep path first: regulator-rooted chains.
    best: dict[str, object] | None = None
    for net_id in sorted(power_net_ids_for_comp):
        net = net_lookup.get(net_id)
        if net is None:
            continue
        info = _resolve_power_path(tree, net_id, designator, net_id_to_name)
        if info is None:
            continue
        info["rail"] = net.name
        if best is None or (
            not best.get("upstream_regulators") and info.get("upstream_regulators")
        ):
            best = info
    if best is not None:
        best["confidence"] = "high"
        return best

    # 2) Fallback: the focal component sits directly on a source net.
    source_net_lookup: dict[str, str] = {}
    for rid in getattr(tree, "roots", []):
        meta = tree.nodes[rid].metadata or {}  # type: ignore[attr-defined]
        nid = meta.get("net_id")
        kind = meta.get("source_kind")
        if isinstance(nid, str) and isinstance(kind, str):
            source_net_lookup[nid] = kind
    for net_id in sorted(power_net_ids_for_comp):
        if net_id in source_net_lookup:
            net = net_lookup.get(net_id)
            return {
                "rail": net.name if net else net_id,
                "source_net_id": net_id,
                "source_net": net.name if net else net_id,
                "source_kind": source_net_lookup[net_id],
                "upstream_regulators": [],
                "confidence": "medium",
            }

    # 3) Last-resort fallback: the component sits on a POWER-typed net but
    # no source pattern matched (e.g. a downstream rail such as 3V3 / 5V
    # whose upstream tracing was not annotated).  Surface a minimal context
    # so the caller still knows the rail identity, but flag source_kind as
    # ``unknown`` to be honest about the missing source.
    for net_id in sorted(power_net_ids_for_comp):
        net = net_lookup.get(net_id)
        if net is None:
            continue
        return {
            "rail": net.name,
            "source_net_id": None,
            "source_net": None,
            "source_kind": "unknown",
            "upstream_regulators": [],
            "confidence": "low",
        }

    return None


def _resolve_power_path(
    tree: object,
    net_id: str,
    designator: str,
    net_id_to_name: dict[str, str],
) -> dict[str, object] | None:
    """Walk ``tree`` and find the power path leading to ``designator``.

    Returns a dict with ``source_net``, ``source_kind``, ``upstream_regulators``
    or ``None`` when the component is not on any tracked power path.
    """
    for root_id in getattr(tree, "roots", []):
        path: list[str] = []
        if not _scan_path_for_component(tree, root_id, net_id, designator, path):
            continue
        source_node = tree.nodes[path[0]]  # type: ignore[attr-defined]
        source_meta = source_node.metadata or {}
        source_net_id = source_meta.get("net_id")  # type: ignore[assignment]
        source_kind = source_meta.get("source_kind")  # type: ignore[assignment]

        upstream: list[dict[str, object]] = []
        for node_id in path[1:]:
            node = tree.nodes[node_id]  # type: ignore[attr-defined]
            if node.kind == "regulator":
                reg_meta = (node.metadata or {}).get("regulator") or {}
                upstream.append(
                    {
                        "component_id": reg_meta.get("component_id", node.label),
                        "kind": reg_meta.get("kind"),
                        "part_number": reg_meta.get("part_number"),
                        "confidence": reg_meta.get("confidence"),
                        "output_net_id": reg_meta.get("output_net_id"),
                    }
                )

        return {
            "source_net_id": source_net_id,
            "source_net": net_id_to_name.get(source_net_id or "", source_net_id),  # type: ignore[arg-type]
            "source_kind": source_kind,
            "upstream_regulators": upstream,
        }

    return None


def _scan_path_for_component(
    tree: object,
    node_id: str,
    target_net_id: str,
    designator: str,
    path: list[str],
) -> bool:
    """Depth-first scan returning True if ``node_id``'s subtree feeds the component."""
    if node_id in path:
        return False
    path.append(node_id)
    try:
        node = tree.nodes[node_id]  # type: ignore[attr-defined]
        meta = node.metadata or {}
        if meta.get("net_id") == target_net_id:
            return True
        if meta.get("component_id") == designator:
            return True
        for child_id in tree.children.get(node_id, []):  # type: ignore[attr-defined]
            if _scan_path_for_component(tree, child_id, target_net_id, designator, path):
                return True
        return False
    finally:
        path.pop()


def _build_comm_context(ir: CircuitIR, designator: str) -> dict[str, object] | None:
    """Return a comm-bus context dict for ``designator``, or ``None``.

    The returned dict has a single key ``buses`` — a list of bus records
    containing protocol, role and fellow members.  Returns ``None`` when the
    component participates in no detected bus.
    """
    from circuit_graph.comm_topology import detect_comm_topology

    comp_map = {c.id: c for c in ir.components}
    if designator not in comp_map:
        return None

    result = detect_comm_topology(ir)
    buses: list[dict[str, object]] = []
    for bus in result.buses:
        # Does the focal component appear as a member?
        member = next((m for m in bus.members if m.component_id == designator), None)
        if member is None:
            continue
        buses.append(
            {
                "id": bus.id,
                "protocol": bus.protocol,
                "role": member.role,
                "master_id": bus.master_id,
                "slave_ids": list(bus.slave_ids),
                "members": [
                    {
                        "component_id": m.component_id,
                        "role": m.role,
                    }
                    for m in bus.members
                ],
                "nets": list(bus.nets),
                "confidence": bus.confidence,
                "evidence": list(bus.evidence),
            }
        )

    if not buses:
        return None
    return {"buses": buses}


def _build_blocks_context(ir: CircuitIR, view_result: dict[str, object]) -> list[dict[str, object]]:
    """Return functional blocks that touch the ego subgraph in ``view_result``.

    A block is included when at least one of its components appears in the
    view's component set, so the focal component's block membership plus any
    blocks spilling into the neighbourhood are both surfaced.  The list is
    empty (not absent) when nothing was recognised — callers always get the
    key when ``--with-blocks`` is passed.
    """
    ego_components = set(view_result.get("components", {}))
    blocks: list[dict[str, object]] = []
    for block in ir.blocks:
        if not ego_components.intersection(block.component_ids):
            continue
        blocks.append(
            {
                "id": block.id,
                "name": block.name,
                "role": block.role.value,
                "component_ids": list(block.component_ids),
                "description": block.description,
                "source": block.source,
            }
        )
    return blocks


if __name__ == "__main__":
    main()


# Register extracted command groups (imported for side effects).
from circuit_graph.cli import kb as _kb  # noqa: E402,F401
from circuit_graph.cli import erc as _erc  # noqa: E402,F401
from circuit_graph.cli import init as _init  # noqa: E402,F401
