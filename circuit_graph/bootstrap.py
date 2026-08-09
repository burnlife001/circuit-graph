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
      "existing_kb_db": "<abs>" | null,
      "existing_skills_dir": "<abs>" | null
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
    existing_skills_dir = target_resolved / ".claude" / "skills"

    result: dict[str, Any] = {
        "target": str(target_resolved),
        "protel_nets": protel_nets,
        "kicad_schematics": kicad_schematics,
        "kicad_projects": kicad_projects,
        "existing_cg_toml": str(existing_cg_toml) if existing_cg_toml.is_file() else None,
        "existing_kb_db": str(existing_kb_db) if existing_kb_db.is_file() else None,
        "existing_skills_dir": str(existing_skills_dir) if existing_skills_dir.is_dir() else None,
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
      "skills": []
    }
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