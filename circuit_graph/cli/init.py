"""cg initskill / cg init — project bootstrap commands (extracted from app.py)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import click

from circuit_graph.cli.app import main

# ---------------------------------------------------------------------------
# cg initskill — bootstrap another project with all bundled skills
# ---------------------------------------------------------------------------

# Skills ship as data directories under the Python package. Each immediate
# subdirectory that contains a SKILL.md is treated as one skill. Top-level
# files (router docs, comparisons) are copied alongside the skills.
# (cli/app.py is one level deeper than the historical cli.py — hence parent.parent.)
_BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


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


