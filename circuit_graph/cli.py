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
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from circuit_graph.primitives import load_primitives
from circuit_graph.sim.deck_builder import DeckBuilder
from circuit_graph.sim.spice_deck import SimulationDeck
from circuit_graph.sim.spice_runner import SpiceRunner, SpiceRunnerConfig
from circuit_graph.erc import ErcReport, ElectricalRulesChecker, KicadCliRunner

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
    db = ComponentDatabase.default()
    result = view_component(ir, designator, hops=hops, db=db)
    if result is None:
        click.echo(json.dumps({"error": f"Component {designator} not found"}, indent=2))
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

    # Run Ngspice
    try:
        runner = SpiceRunner(SpiceRunnerConfig(timeout_seconds=30.0))
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


def _parse_and_enrich(file: Path) -> CircuitIR:
    """Parse the netlist and run the standard enricher pipeline.

    This is the single source of truth for "fresh IR from a file" — every
    CLI command routes through it so that downstream consumers always see
    normalised values, classified nets and recognised topology blocks.
    """
    parser = ProtelNetParser()
    ir = parser.parse(ParserInput(source_id=str(file), file_path=str(file)))
    ir = run_enrichers(
        ir,
        [
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
            }
        )
    return blocks


# ---------------------------------------------------------------------------
# cg kb — knowledge base sub-commands
# ---------------------------------------------------------------------------


@main.group()
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    default=DEFAULT_KB_PATH,
    show_default=True,
    help="SQLite database file (default: <pkg>/knowledge/circuit_graph.db).",
)
@click.pass_context
def kb(ctx: click.Context, db_path: Path) -> None:
    """Query and update the component knowledge base (SQLite)."""
    ctx.ensure_object(dict)
    ctx.obj["kb_path"] = db_path


def _open_kb(ctx: click.Context) -> ComponentDatabase:
    db_path: Path = ctx.obj["kb_path"]
    return ComponentDatabase.open(db_path)


@kb.command(name="init")
@click.pass_context
def kb_init(ctx: click.Context) -> None:
    """Create / upgrade the KB schema and migrate vendor_db.json."""
    db = _open_kb(ctx)
    click.echo(f"Database: {db.db_path}")
    click.echo(json.dumps(db.stats(), indent=2))
    vendor = _PROJECT_ROOT / "circuit_graph" / "knowledge" / "vendor_db.json"
    if vendor.exists():
        n = db.migrate_from_vendor_db(vendor)
        click.echo(f"Migrated {n} prefixes from vendor_db.json")


@kb.command(name="lookup")
@click.argument("part_number")
@click.pass_context
def kb_lookup(ctx: click.Context, part_number: str) -> None:
    """Show a single component + pinout + properties."""
    db = _open_kb(ctx)
    rec = db.get_component(part_number)
    if rec is None:
        click.echo(json.dumps({"error": f"not found: {part_number}"}))
        sys.exit(1)
    payload: dict[str, object] = {
        "part_number": rec.part_number,
        "manufacturer": rec.manufacturer,
        "category": rec.category,
        "subcategory": rec.subcategory,
        "package": rec.package,
        "pin_count": rec.pin_count,
        "description": rec.description,
        "is_credible": rec.is_credible,
        "pin_source": rec.pin_source,
    }
    pinout = db.get_pinout(rec.part_number)
    if pinout:
        payload["pinout"] = pinout
    props = db.get_properties(rec.part_number)
    if props:
        payload["properties"] = props
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@kb.command(name="search")
@click.argument("keyword")
@click.pass_context
def kb_search(ctx: click.Context, keyword: str) -> None:
    """Full-text search across part_number / description / category."""
    db = _open_kb(ctx)
    rows = db.query(any=keyword)
    payload = [
        {
            "part_number": r.part_number,
            "manufacturer": r.manufacturer,
            "category": r.category,
            "package": r.package,
        }
        for r in rows
    ]
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    click.echo(f"# {len(payload)} match(es)", err=True)


@kb.command(name="pinout")
@click.argument("part_number")
@click.pass_context
def kb_pinout(ctx: click.Context, part_number: str) -> None:
    """Show only the pin map for ``part_number``."""
    db = _open_kb(ctx)
    pinout = db.get_pinout(part_number)
    if pinout is None:
        click.echo(json.dumps({"error": f"no pinout for {part_number}"}))
        sys.exit(1)
    click.echo(json.dumps(pinout, indent=2, ensure_ascii=False))


# NOTE: Earlier revisions also defined `@kb.group(name="import") -> kb_import_group`
# plus bare-bones `protel` / `kicad` subcommands (no --symbol-dir). Click silently
# overrides that group with the later `kb_import` definition further down, so the
# earlier block was unreachable dead code. It has been removed in 2026-07; if you
# re-introduce it, register on the canonical `kb_import` group below.


@kb.command(name="extract-pins")
@click.option(
    "--pdf",
    "pdf_path",
    type=click.Path(exists=True, path_type=Path),
    help="Extract pins from a specific PDF file.",
)
@click.option(
    "--part",
    "part_number",
    help="Auto-locate datasheet by part number (searches DATASHEET_DIR).",
)
@click.option(
    "--pages",
    help="Manual page range override (e.g. '32-35').",
)
@click.option(
    "--search",
    is_flag=True,
    help="Only detect pin-table pages, skip extraction.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Save result to JSON file.",
)
@click.pass_context
def kb_extract_pins(
    ctx: click.Context,
    pdf_path: Path | None,
    part_number: str | None,
    pages: str | None,
    search: bool,
    output: Path | None,
) -> None:
    """Extract pin names from a PDF datasheet via Camelot.

    Supports two modes:

    \b
    1. --pdf DS_MM32F0140_EN.pdf   Extract from a specific PDF file.
    2. --part MM32F0140            Auto-locate the datasheet under DATASHEET_DIR.

    \b
    Use --pages to override the auto-detected page range.
    Use --search to only detect pages without extracting.
    """
    from circuit_graph.config import load as _load_config
    from circuit_graph.knowledge.pdf_pin_extract import (
        search_pin_pages,
        extract_pin_table,
        extract_from_datasheet,
    )

    _load_config()

    # Resolve PDF path.
    if pdf_path is not None:
        target_pdf = pdf_path
    elif part_number is not None:
        pin_map = extract_from_datasheet(part_number)
        if pin_map is None:
            click.echo(json.dumps({"error": f"no pin table found for {part_number}"}))
            sys.exit(1)
        _emit_pin_result(pin_map, output)
        return
    else:
        click.echo(
            json.dumps({"error": "either --pdf or --part is required"}),
            err=True,
        )
        sys.exit(1)

    # Search mode.
    if search:
        detected = search_pin_pages(target_pdf)
        click.echo(json.dumps({"pdf": str(target_pdf), "pin_pages": detected}, indent=2))
        return

    # Extract mode.
    if pages is not None:
        page_list = _parse_page_range(pages)
        if not page_list:
            click.echo(json.dumps({"error": f"invalid --pages range: {pages!r}"}))
            sys.exit(1)
    else:
        page_list = search_pin_pages(target_pdf)
        if not page_list:
            click.echo(json.dumps({"error": "no pin-table pages detected; use --pages to specify manually"}))
            sys.exit(1)

    pin_map = extract_pin_table(target_pdf, page_list)
    if pin_map is None:
        click.echo(json.dumps({"error": "failed to extract pin table"}))
        sys.exit(1)

    _emit_pin_result(pin_map, output)


def _emit_pin_result(pin_map: dict[str, str], output: Path | None) -> None:
    """Print pin map to stdout or write to file."""
    result = {
        "pin_count": len(pin_map),
        "pins": {num: name for num, name in sorted(
            pin_map.items(),
            key=lambda x: (int(x[0]) if x[0].isdigit() else 9999, x[0]),
        )},
    }
    if output is not None:
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        click.echo(f"Wrote {len(pin_map)} pins to {output}")
    else:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))


def _parse_page_range(s: str) -> list[int] | None:
    """Parse a page range string like '32-35' or '32,33,34' into a list of ints."""
    try:
        if "-" in s:
            parts = s.split("-", 1)
            start, end = int(parts[0].strip()), int(parts[1].strip())
            if start < 1 or end < start:
                return None
            return list(range(start, end + 1))
        else:
            return [int(p.strip()) for p in s.split(",") if p.strip()]
    except (ValueError, IndexError):
        return None


@kb.group(name="import")
def kb_import() -> None:
    """Bulk import a KiCad netlist, Protel netlist, or chip_convert JSON file."""


@kb_import.command(name="kicad")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--symbol-dir",
    "symbol_dirs",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Directories to search for .kicad_sym files (before JLCPCB).",
)
@click.pass_context
def kb_import_kicad(ctx: click.Context, path: Path, symbol_dirs: tuple[Path, ...]) -> None:
    """Parse a KiCad S-expression netlist and upsert components + pins."""
    db = _open_kb(ctx)
    n = db.import_kicad_netlist(path, symbol_dirs=list(symbol_dirs) or None)
    click.echo(f"Imported {n} component(s) from {path}")


@kb_import.command(name="protel")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--symbol-dir",
    "symbol_dirs",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Directories to search for .kicad_sym files (before JLCPCB).",
)
@click.pass_context
def kb_import_protel(ctx: click.Context, path: Path, symbol_dirs: tuple[Path, ...]) -> None:
    """Parse Protel .NET netlist(s) and upsert components + pins."""
    db = _open_kb(ctx)
    n = db.import_protel_netlist(path, symbol_dirs=list(symbol_dirs) or None)
    click.echo(f"Imported {n} component(s) from {path}")


@kb.command(name="resolve")
@click.argument("netlist", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--symbol-dir",
    "symbol_dirs",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Extra directories to search for .kicad_sym files.",
)
@click.option("--dry-run", is_flag=True, help="Only report, do not invoke pin2json.")
@click.pass_context
def kb_resolve(
    ctx: click.Context,
    netlist: Path,
    symbol_dirs: tuple[Path, ...],
    dry_run: bool,
) -> None:
    """Scan ``netlist`` and resolve unnamed pins via KB + pin2json."""
    db = _open_kb(ctx)
    parser = ProtelNetParser()
    try:
        ir = parser.parse(
            ParserInput(source_id=str(netlist), file_path=str(netlist))
        )
    except Exception as exc:  # noqa: BLE001
        click.echo(json.dumps({"error": f"parse failed: {exc}"}))
        sys.exit(1)

    non_passive = [
        c
        for c in ir.components
        if c.part_number and any(p.name is None for p in c.pins)
    ]
    if dry_run:
        report = {
            "netlist": str(netlist),
            "dry_run": True,
            "components_with_unnamed_pins": [
                {"id": c.id, "part_number": c.part_number, "pins": len(c.pins)}
                for c in non_passive
            ],
        }
        click.echo(json.dumps(report, indent=2, ensure_ascii=False))
        return

    resolved = db.resolve_pins(non_passive, list(symbol_dirs))
    click.echo(
        json.dumps(
            {
                "netlist": str(netlist),
                "resolved_pins": resolved,
                "warnings": db.pin_resolver_warnings,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@kb.command(name="stats")
@click.pass_context
def kb_stats(ctx: click.Context) -> None:
    """Print aggregate counts in the knowledge base."""
    db = _open_kb(ctx)
    click.echo(json.dumps(db.stats(), indent=2))


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


# ---------------------------------------------------------------------------
# cg initskill — bootstrap another project with all bundled skills
# ---------------------------------------------------------------------------

# Skills ship as data directories under the Python package. Each immediate
# subdirectory that contains a SKILL.md is treated as one skill. Top-level
# files (router docs, comparisons) are copied alongside the skills.
_BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _discover_bundled_skills() -> list[Path]:
    """Return absolute paths of every bundled skill directory.

    A skill is any immediate subdirectory of ``_BUNDLED_SKILLS_DIR`` that
    contains a ``SKILL.md``. Returns them sorted by name for stable output.
    """
    if not _BUNDLED_SKILLS_DIR.is_dir():
        return []
    skills: list[Path] = []
    for entry in sorted(_BUNDLED_SKILLS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / "SKILL.md").is_file():
            skills.append(entry)
    return skills


def _discover_bundled_docs() -> list[Path]:
    """Return absolute paths of top-level files in the bundled skills tree.

    Files directly under ``_BUNDLED_SKILLS_DIR`` (e.g. the README routing
    table) are not skills themselves but are copied to the target so the
    consumer of ``cg initskill`` gets the full directory snapshot.
    """
    if not _BUNDLED_SKILLS_DIR.is_dir():
        return []
    return sorted(
        p for p in _BUNDLED_SKILLS_DIR.iterdir() if p.is_file()
    )


@main.command(name="initskill")
@click.argument(
    "target",
    type=click.Path(file_okay=False, path_type=Path),
    default=".",
    metavar="[TARGET]",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing skill files at <target>/.claude/skills/<name>/.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be copied without writing any files.",
)
def init_cmd(target: Path, force: bool, dry_run: bool) -> None:
    """Copy every bundled skill into <target>/.claude/skills/<name>/.

    Run this in OTHER projects to bootstrap them with the full circuit-graph
    skill set (cg circuit analysis, atk-logic-capture, keil-init, …). Skills
    ship inside the circuit-graph Python package, so no network access is
    required.

    TARGET defaults to the current directory. Each skill is installed under
    <TARGET>/.claude/skills/<skill-name>/. Already-installed skills are
    skipped unless --force is passed.
    """
    skills = _discover_bundled_skills()
    if not skills:
        click.echo(
            json.dumps(
                {
                    "error": (
                        f"no bundled skills found under {_BUNDLED_SKILLS_DIR}. "
                        "Reinstall circuit-graph with `uv pip install -e .` "
                        "to refresh package data."
                    )
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        sys.exit(2)

    target_root = target.resolve()
    target_base = target_root / ".claude" / "skills"

    results: list[dict[str, object]] = []
    installed = 0
    skipped = 0
    failed = 0

    for skill_src in skills:
        name = skill_src.name
        target_dir = target_base / name
        files = sorted(p for p in skill_src.rglob("*") if p.is_file())
        exists = target_dir.exists()

        entry: dict[str, object] = {
            "name": name,
            "source": str(skill_src),
            "target": str(target_dir),
            "file_count": len(files),
            "existed": exists,
        }

        if dry_run:
            entry["status"] = "would_overwrite" if exists else "would_install"
            results.append(entry)
            continue

        if exists and not force:
            entry["status"] = "skipped"
            skipped += 1
            results.append(entry)
            continue

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            copied = 0
            for src in files:
                rel = src.relative_to(skill_src)
                dest = target_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                copied += 1
            entry["status"] = "installed"
            entry["copied"] = copied
            installed += 1
        except OSError as exc:  # noqa: BLE001
            entry["status"] = "failed"
            entry["error"] = str(exc)
            failed += 1

        results.append(entry)

    summary = {
        "status": "ok" if failed == 0 else "partial",
        "target": str(target_base),
        "installed": installed,
        "skipped": skipped,
        "failed": failed,
        "dry_run": dry_run,
        "skills": results,
    }

    # Copy top-level bundled files (e.g. the SKILL router README) so the
    # target receives the full skills/ directory snapshot, not just the
    # per-skill subdirectories.
    bundled_docs = _discover_bundled_docs()
    docs: list[dict[str, object]] = []
    for doc_src in bundled_docs:
        dest = target_base / doc_src.name
        exists = dest.exists()
        entry: dict[str, object] = {
            "name": doc_src.name,
            "source": str(doc_src),
            "target": str(dest),
            "existed": exists,
        }
        if dry_run:
            entry["status"] = "would_overwrite" if exists else "would_install"
            docs.append(entry)
            continue
        if exists and not force:
            entry["status"] = "skipped"
            docs.append(entry)
            continue
        try:
            target_base.mkdir(parents=True, exist_ok=True)
            shutil.copy2(doc_src, dest)
            entry["status"] = "installed"
        except OSError as exc:  # noqa: BLE001
            entry["status"] = "failed"
            entry["error"] = str(exc)
        docs.append(entry)

    summary["docs"] = docs
    summary["docs_installed"] = sum(
        1 for d in docs if d["status"] == "installed"
    )
    summary["docs_skipped"] = sum(1 for d in docs if d["status"] == "skipped")
    summary["docs_failed"] = sum(1 for d in docs if d["status"] == "failed")
    summary["failed"] = summary["failed"] + summary["docs_failed"]
    if summary["docs_failed"]:
        summary["status"] = "partial"

    click.echo(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary["failed"]:
        sys.exit(1)


# ---------------------------------------------------------------------------
# cg init — project bootstrap orchestrator
# ---------------------------------------------------------------------------

@main.command(name="init")
@click.argument("target", type=click.Path(file_okay=False, path_type=Path),
                default=".", metavar="[TARGET]")
@click.option("--skip-config", is_flag=True, help="Don't write .cg.toml.")
@click.option("--skip-kb",     is_flag=True, help="Don't call `cg kb init`.")
@click.option("--skip-skill",  is_flag=True, help="Don't call `cg initskill`.")
@click.option("-y", "--yes",   is_flag=True, help="Auto-accept proposed config; skip interactive confirm.")
@click.option("--force",       is_flag=True, help="Overwrite existing .cg.toml; re-run kb/skill sub-commands.")
@click.option("--dry-run",     is_flag=True, help="Print full plan (detection, config diff, sub-cmd invocations); no writes.")
def init_orchestrator_cmd(target: Path, skip_config: bool, skip_kb: bool, skip_skill: bool,
                          yes: bool, force: bool, dry_run: bool) -> None:
    """Bootstrap a project for circuit-graph.

    By default writes .cg.toml, runs `cg kb init`, and runs `cg initskill`.
    Use --skip-* to opt out. Re-running is idempotent; pass --force to overwrite.
    """
    from circuit_graph import bootstrap
    report = bootstrap.init_orchestrator(
        target=Path(target),
        skip_config=skip_config, skip_kb=skip_kb, skip_skill=skip_skill,
        yes=yes, force=force, dry_run=dry_run,
    )
    click.echo(report)
    parsed = json.loads(report)
    if parsed.get("status") == "failed":
        sys.exit(2 if parsed.get("error", "").startswith("target") else 1)
    if parsed.get("status") == "partial":
        sys.exit(1)


if __name__ == "__main__":
    main()
