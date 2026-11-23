"""cg init — project bootstrap orchestrator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from circuit_graph.cli.app import main


@main.command(name="init")
@click.argument(
    "target",
    type=click.Path(file_okay=False, path_type=Path),
    default=".",
    metavar="[TARGET]",
)
@click.option("--skip-config", is_flag=True, help="Don't write .cg.toml.")
@click.option("--skip-kb", is_flag=True, help="Don't call `cg kb init`.")
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Auto-accept proposed config; skip interactive confirm.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing .cg.toml; re-run `cg kb init` when needed.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print full plan (detection, config diff); no writes.",
)
def init_orchestrator_cmd(
    target: Path,
    skip_config: bool,
    skip_kb: bool,
    yes: bool,
    force: bool,
    dry_run: bool,
) -> None:
    """Bootstrap a project for circuit-graph.

    By default writes .cg.toml and runs `cg kb init`.
    """
    from circuit_graph import bootstrap

    report = bootstrap.init_orchestrator(
        target=Path(target),
        skip_config=skip_config,
        skip_kb=skip_kb,
        yes=yes,
        force=force,
        dry_run=dry_run,
    )
    click.echo(report)
    parsed = json.loads(report)
    if parsed.get("status") == "failed":
        sys.exit(2 if parsed.get("error", "").startswith("target") else 1)

