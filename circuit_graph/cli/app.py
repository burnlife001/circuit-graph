"""Circuit understanding commands and the shared parse/enrich pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from circuit_graph.ir import CircuitIR
from circuit_graph.parsers.protel import ParserInput
from circuit_graph.parsers.registry import parse_netlist

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_GRAPH_FILE = _PROJECT_ROOT / "output" / "circuit.graph.json"
DEFAULT_GRAPHML_FILE = _PROJECT_ROOT / "output" / "circuit.graphml"
DEFAULT_KB_PATH = _PROJECT_ROOT / "circuit_graph" / "knowledge" / "circuit_graph.db"


@click.group(
    epilog="Use `cg role DESIGNATOR FILE` for one component or "
    "`cg role --all FILE` for every component in a netlist.",
)
@click.version_option(version="0.2.0")
def main() -> None:
    """Explain what a circuit does, one command at a time."""


@main.group()
def export() -> None:
    """Export circuit analysis graphs."""


def _cache_options(f: Any) -> Any:
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
    if warnings:
        click.echo(f"warnings: {len(warnings)} — {'; '.join(warnings)}", err=True)


def build_pin_category_provider(kb_path: Path | None = None):
    try:
        from circuit_graph.knowledge.chip_resolver import classify_pin_types
        from circuit_graph.knowledge.db import ComponentDatabase
        db = ComponentDatabase.open(kb_path if kb_path is not None else DEFAULT_KB_PATH)
    except Exception:  # noqa: BLE001
        return None
    _cache: dict[str, dict[str, str]] = {}

    def provider(comp):
        part = comp.part_number or comp.raw_value
        if not part:
            return None
        if part in _cache:
            return _cache[part]
        rec = db.get_component(part)
        if rec is None:
            _cache[part] = {}
            return None
        pinout = db.get_pinout(rec.part_number) or {}
        cats = classify_pin_types(pinout)
        _cache[part] = cats
        return cats

    return provider


def _parse_and_enrich(
    file: Path,
    *,
    warnings_out: list[str] | None = None,
    topology_timeout: float | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    pin_category_provider: Any | None = None,
) -> CircuitIR:
    # Cache hits must not pay the enricher import cost.
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
            TopologyRecognizer(
                timeout_seconds=topology_timeout,
                pin_category_provider=(
                    build_pin_category_provider()
                    if pin_category_provider is None
                    else pin_category_provider
                ),
            ),
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


from circuit_graph.cli import init as _init  # noqa: E402,F401
from circuit_graph.cli import kb as _kb  # noqa: E402,F401
from circuit_graph.cli import primitives as _primitives  # noqa: E402,F401
from circuit_graph.cli import query as _query  # noqa: E402,F401
from circuit_graph.cli import role as _role  # noqa: E402,F401
from circuit_graph.cli import view as _view  # noqa: E402,F401

if __name__ == "__main__":
    main()
