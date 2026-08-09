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