"""Project bootstrap orchestrator for `cg init`.

All public functions return JSON strings for debuggability. Subprocess
calls go through `delegate`, which captures exit code, stdout, stderr,
and duration.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def detect_project(target: Path) -> str:
    """Scan `target` for schematic files and existing cg artifacts.

    Returns a JSON string:
    {
      "target": "<abs path>",
      "protel_nets": ["<abs>", ...],
      "kicad_schematics": ["<abs>", ...],
      "kicad_projects": ["<abs>", ...],
      "existing_cg_toml": "<abs>" | null,
      "existing_kb_db": "<abs>" | null
    }
    On entry-param failure: {"status": "failed", "error": "<msg>"}
    """
    target_resolved = target.resolve()
    if not target_resolved.exists():
        return json.dumps({"status": "failed", "error": f"target '{target}' does not exist"})
    if not target_resolved.is_dir():
        return json.dumps({"status": "failed", "error": f"target '{target}' is not a directory"})

    protel_nets = sorted(str(p) for p in target_resolved.glob("*.NET")) + \
                  sorted(str(p) for p in target_resolved.glob("*.net"))
    kicad_schematics = sorted(str(p) for p in target_resolved.glob("*.kicad_sch"))
    kicad_projects = sorted(str(p) for p in target_resolved.glob("*.kicad_pro"))

    existing_cg_toml = target_resolved / ".cg.toml"
    existing_kb_db = target_resolved / "circuit_graph" / "knowledge" / "circuit_graph.db"
    result: dict[str, Any] = {
        "target": str(target_resolved),
        "protel_nets": protel_nets,
        "kicad_schematics": kicad_schematics,
        "kicad_projects": kicad_projects,
        "existing_cg_toml": str(existing_cg_toml) if existing_cg_toml.is_file() else None,
        "existing_kb_db": str(existing_kb_db) if existing_kb_db.is_file() else None,
    }
    return json.dumps(result, ensure_ascii=False)


def propose_config(json_ctx: str) -> str:
    """Turn a detect_project JSON into a proposed CgConfig (JSON string).

    Returns:
    {
      "project_name": "<target.basename>",
      "default_graph_file": "output/circuit.graph.json",
      "default_graphml_file": "output/circuit.graphml",
      "default_kb_path": "circuit_graph/knowledge/circuit_graph.db",
      "schematics": ["<relative>", ...],
      "skills": [],
      "e2e_spec": ""
    }

    ``e2e_spec`` is the E2E acceptance spec (the requirement document the
    hardware test compares against).  It lives here — not in the project's
    ``.claude/agents/settings.ini`` — so a shared, linked agent script never
    has to guess which requirement version a board is on.  Empty means "not
    set"; the capture script refuses to run (exit 2) until it is filled in.
    """
    ctx = json.loads(json_ctx)
    target = Path(ctx.get("target", "."))
    project_name = target.name or Path.cwd().name or "untitled"
    schematics: list[str] = []
    for p in ctx.get("protel_nets", []) + ctx.get("kicad_schematics", []):
        p_path = Path(p)
        try:
            rel = str(p_path.relative_to(target))
        except ValueError:
            # path is absolute and outside target, or relative — fall back to filename
            rel = p_path.name
        schematics.append(rel)
    cfg = {
        "project_name": project_name,
        "default_graph_file": "output/circuit.graph.json",
        "default_graphml_file": "output/circuit.graphml",
        "default_kb_path": "circuit_graph/knowledge/circuit_graph.db",
        "schematics": sorted(schematics),
        "skills": [],
        "e2e_spec": "",
    }
    return json.dumps(cfg, ensure_ascii=False)


def write_config(json_cfg: str, target: Path, *, force: bool = False) -> str:
    """Write a CgConfig JSON to `<target>/.cg.toml` (TOML format).

    Returns JSON:
    {"written": bool, "path": "<abs>", "was_overwritten": bool, "reason": str}
    """
    cfg = json.loads(json_cfg)
    cfg_path = (target.resolve() / ".cg.toml")
    existed = cfg_path.is_file()
    if existed and not force:
        return json.dumps({
            "written": False,
            "path": str(cfg_path),
            "was_overwritten": False,
            "reason": "exists; pass --force to overwrite",
        })
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import tomli_w
        with cfg_path.open("wb") as f:
            tomli_w.dump(cfg, f)
    except (OSError, ImportError) as exc:
        return json.dumps({
            "written": False,
            "path": str(cfg_path),
            "was_overwritten": False,
            "reason": f"write failed: {exc}",
        })
    return json.dumps({
        "written": True,
        "path": str(cfg_path),
        "was_overwritten": existed,
        "reason": "ok",
    }, ensure_ascii=False)


def delegate(cmd: list[str], cwd: Path | None = None) -> str:
    """Run a subprocess, return a JSON result.

    JSON shape:
    {"cmd": [...], "exit_code": int, "status": "ok"|"failed",
     "stdout": "...", "stderr": "...", "duration_s": float}
    """
    start = time.time()
    try:
        completed = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                                   capture_output=True, text=True, check=False)
    except (OSError, FileNotFoundError) as exc:
        return json.dumps({
            "cmd": cmd,
            "exit_code": -1,
            "status": "failed",
            "stdout": "",
            "stderr": str(exc),
            "duration_s": round(time.time() - start, 3),
        })
    duration = round(time.time() - start, 3)
    return json.dumps({
        "cmd": list(cmd),
        "exit_code": completed.returncode,
        "status": "ok" if completed.returncode == 0 else "failed",
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "duration_s": duration,
    }, ensure_ascii=False)


def init_orchestrator(
    target: Path,
    *,
    skip_config: bool = False,
    skip_kb: bool = False,
    yes: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """Top-level orchestrator. Returns a JSON InitReport string.

    Flow: detect -> propose -> [confirm] -> write_config -> delegate kb init.
    Honors skip_config/skip_kb, force, yes, dry_run.
    """
    start = time.time()
    target = target.resolve()
    ctx_str = detect_project(target)
    ctx = json.loads(ctx_str)
    if ctx.get("status") == "failed":
        return json.dumps({
            "target": str(target),
            "status": "failed",
            "dry_run": dry_run,
            "config": None,
            "kb": None,
            "error": ctx["error"],
            "duration_s": round(time.time() - start, 3),
        }, ensure_ascii=False)

    cfg_str = propose_config(ctx_str)

    if not dry_run and not skip_config and not yes:
        import click
        if not click.confirm(f"Write proposed .cg.toml to {target}?", default=True):
            return json.dumps({
                "target": str(target),
                "status": "aborted",
                "dry_run": False,
                "config": None,
                "kb": None,
                "duration_s": round(time.time() - start, 3),
            }, ensure_ascii=False)

    config_report = None
    cg_toml = target / ".cg.toml"
    if dry_run:
        config_report = {"written": False, "path": None, "was_overwritten": False, "dry_run": True}
    elif skip_config:
        config_report = {"written": False, "path": None, "was_overwritten": False, "skipped": True}
    elif cg_toml.is_file() and not force:
        # Idempotent: existing .cg.toml, no --force -> skip
        config_report = {
            "written": False,
            "path": str(cg_toml),
            "was_overwritten": False,
            "skipped": True,
        }
    else:
        config_report = json.loads(write_config(cfg_str, target, force=force))

    kb_report = None
    sub_statuses: list[str] = []

    kb_db = target / "circuit_graph" / "knowledge" / "circuit_graph.db"

    if dry_run:
        kb_report = {"status": "skipped", "reason": "dry-run"}
    elif skip_kb:
        kb_report = {"status": "skipped"}
    elif kb_db.is_file() and not force:
        kb_report = {"status": "skipped", "reason": "KB already exists; pass --force to re-run"}
    else:
        kb_report = json.loads(
            delegate([sys.executable, "-m", "circuit_graph.cli", "kb", "init"], cwd=target)
        )
        sub_statuses.append(kb_report["status"])

    if dry_run:
        overall = "dry_run"
    elif any(s == "failed" for s in sub_statuses):
        overall = "partial"
    else:
        overall = "ok"

    return json.dumps({
        "target": str(target),
        "status": overall,
        "dry_run": dry_run,
        "config": config_report,
        "kb": kb_report,
        "duration_s": round(time.time() - start, 3),
    }, ensure_ascii=False)
