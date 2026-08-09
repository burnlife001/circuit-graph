# `cg init` Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `cg init` as a one-stop project bootstrap orchestrator that writes `.cg.toml`, calls `cg kb init`, and calls `cg initskill` in one command, with all sub-steps returning JSON strings for debuggability.

**Architecture:** New `circuit_graph/bootstrap.py` module with five functions (`detect_project`, `propose_config`, `write_config`, `delegate`, `init_orchestrator`) — each returns a JSON string. New `@main.command(name="init")` Click subcommand in `circuit_graph/cli.py` that calls `init_orchestrator` and prints its result. Tests in `tests/test_cli_init_cg.py` use `monkeypatch` to mock `delegate` and `click.confirm`.

**Tech Stack:** Python 3.10+, Click 8+, pytest 8+, monkeypatch (stdlib), `subprocess` (stdlib), `json` (stdlib), `tomli_w` (TOML writer — needs to be added to pyproject.toml).

## Global Constraints

- Python ≥ 3.10 (per `pyproject.toml`).
- Line endings: LF only (per `CLAUDE.md`).
- All cross-function returns are JSON strings (no dataclasses across boundaries).
- Subprocess invocation: `sys.executable -m circuit_graph.cli <subcommand> [args]` from `delegate`.
- Error strings: one-sentence, include suggested action.
- Module path: `circuit_graph/bootstrap.py` (NOT `init.py` — avoid name collision with `__init__.py`).
- Test file: `tests/test_cli_init_cg.py` (NOT `test_cli_init.py` — that file already covers `cg initskill`).
- Idempotent re-runs: `init_orchestrator` must NOT re-write `.cg.toml` or re-call `cg kb init` / `cg initskill` unless `--force` is passed.
- Exit codes: 0 (ok/skipped), 1 (at least one sub-step failed), 2 (entry-param error).

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `circuit_graph/bootstrap.py` | Create | Five public functions: `detect_project`, `propose_config`, `write_config`, `delegate`, `init_orchestrator` |
| `circuit_graph/cli.py` | Modify (insert after `init_cmd` at line 1225) | Register `@main.command(name="init")` that calls `init_orchestrator` and `click.echo` the result |
| `tests/test_cli_init_cg.py` | Create | ~26 tests across 5 groups (detect / propose / write / delegate / orchestrate) |
| `pyproject.toml` | Modify | Add `tomli_w>=1.0` dependency (TOML writer) |
| `circuit_graph/skills/cg/SKILL.md` | Modify | Add `cg init` to routing table |

---

### Task 1: `detect_project` — basic happy path

**Files:**
- Create: `circuit_graph/bootstrap.py`
- Create: `tests/test_cli_init_cg.py`
- Modify: `pyproject.toml:7-14` (add `tomli_w>=1.0` to dependencies)

**Interfaces:**
- Produces: `detect_project(target: Path) -> str` (JSON string)

- [ ] **Step 1: Add `tomli_w` dependency**

Edit `pyproject.toml`, find the `dependencies` list and add `"tomli_w>=1.0"` after `"python-dotenv>=1.2.2"`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_cli_init_cg.py
"""Tests for `cg init` orchestrator."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from circuit_graph import bootstrap


def test_detect_project_finds_protel_nets(tmp_path: Path) -> None:
    (tmp_path / "board1.NET").write_text("(NET1 R1-1 R2-1)", encoding="utf-8")
    (tmp_path / "board2.net").write_text("(NET1 R1-1 R2-1)", encoding="utf-8")
    result = json.loads(bootstrap.detect_project(tmp_path))
    nets = [Path(p).name for p in result["protel_nets"]]
    assert "board1.NET" in nets
    assert "board2.net" in nets
    assert result["target"] == str(tmp_path.resolve())
    assert result["existing_cg_toml"] is None
    assert result["existing_kb_db"] is None
    assert result["existing_skills_dir"] is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py::test_detect_project_finds_protel_nets -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'circuit_graph.bootstrap'`

- [ ] **Step 4: Implement minimal `detect_project`**

```python
# circuit_graph/bootstrap.py
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py::test_detect_project_finds_protel_nets -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml circuit_graph/bootstrap.py tests/test_cli_init_cg.py
git commit -m "feat(bootstrap): detect_project scans target for schematic + cg artifacts

Foundation for the cg init orchestrator. Returns a JSON string (per the
2026-08-09 design spec) listing detected protel .NET / kicad .kicad_sch
files and any pre-existing .cg.toml, KB database, or .claude/skills/
directory in the target.

Also adds tomli_w to dependencies in preparation for write_config."
```

---

### Task 2: `detect_project` — kicad + entry-param failures

**Files:**
- Modify: `tests/test_cli_init_cg.py`

**Interfaces:**
- Consumes: `detect_project` (from Task 1)
- Produces: 4 new tests

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_cli_init_cg.py

def test_detect_project_finds_kicad_schematics(tmp_path: Path) -> None:
    (tmp_path / "main.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    (tmp_path / "aux.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    (tmp_path / "project.kicad_pro").write_text("(kicad_pro)", encoding="utf-8")
    result = json.loads(bootstrap.detect_project(tmp_path))
    sch_names = [Path(p).name for p in result["kicad_schematics"]]
    assert "main.kicad_sch" in sch_names
    assert "aux.kicad_sch" in sch_names
    assert any(p.endswith("project.kicad_pro") for p in result["kicad_projects"])


def test_detect_project_finds_existing_cg_toml(tmp_path: Path) -> None:
    (tmp_path / ".cg.toml").write_text("project_name = 'x'", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "skills").mkdir()
    (tmp_path / "circuit_graph" / "knowledge").mkdir(parents=True)
    (tmp_path / "circuit_graph" / "knowledge" / "circuit_graph.db").write_text("", encoding="utf-8")
    result = json.loads(bootstrap.detect_project(tmp_path))
    assert result["existing_cg_toml"] is not None
    assert result["existing_skills_dir"] is not None
    assert result["existing_kb_db"] is not None


def test_detect_project_missing_target_returns_error(tmp_path: Path) -> None:
    ghost = tmp_path / "does-not-exist"
    result = json.loads(bootstrap.detect_project(ghost))
    assert result["status"] == "failed"
    assert "does not exist" in result["error"]


def test_detect_project_target_is_file(tmp_path: Path) -> None:
    f = tmp_path / "not-a-dir"
    f.write_text("x", encoding="utf-8")
    result = json.loads(bootstrap.detect_project(f))
    assert result["status"] == "failed"
    assert "not a directory" in result["error"]
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v -k "detect_project"`
Expected: 5 passed (Task 1 + Task 2)

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli_init_cg.py
git commit -m "test(bootstrap): cover kicad detection + entry-param failures for detect_project"
```

---

### Task 3: `propose_config`

**Files:**
- Modify: `circuit_graph/bootstrap.py` (add `propose_config`)
- Modify: `tests/test_cli_init_cg.py`

**Interfaces:**
- Produces: `propose_config(json_ctx: str) -> str` (JSON string)

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_cli_init_cg.py

def test_propose_config_picks_up_detected_schematics() -> None:
    ctx = json.dumps({
        "protel_nets": ["/abs/board1.NET", "/abs/board2.net"],
        "kicad_schematics": ["/abs/main.kicad_sch"],
        "kicad_projects": [],
    })
    result = json.loads(bootstrap.propose_config(ctx))
    assert "board1.NET" in result["schematics"]
    assert "board2.net" in result["schematics"]
    assert "main.kicad_sch" in result["schematics"]
    assert result["project_name"]  # non-empty


def test_propose_config_uses_defaults_when_empty() -> None:
    ctx = json.dumps({"protel_nets": [], "kicad_schematics": [], "kicad_projects": []})
    result = json.loads(bootstrap.propose_config(ctx))
    assert result["schematics"] == []
    assert result["default_graph_file"] == "output/circuit.graph.json"
    assert result["default_kb_path"] == "circuit_graph/knowledge/circuit_graph.db"


def test_propose_config_returns_json_string() -> None:
    ctx = json.dumps({"protel_nets": [], "kicad_schematics": [], "kicad_projects": []})
    out = bootstrap.propose_config(ctx)
    assert isinstance(out, str)
    json.loads(out)  # must parse without error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v -k "propose_config"`
Expected: FAIL with `AttributeError: module 'circuit_graph.bootstrap' has no attribute 'propose_config'`

- [ ] **Step 3: Implement `propose_config`**

```python
# Append to circuit_graph/bootstrap.py

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
    schematics: list[str] = []
    for p in ctx.get("protel_nets", []) + ctx.get("kicad_schematics", []):
        try:
            rel = str(Path(p).relative_to(target))
        except ValueError:
            rel = p
        schematics.append(rel)
    cfg = {
        "project_name": target.name,
        "default_graph_file": "output/circuit.graph.json",
        "default_graphml_file": "output/circuit.graphml",
        "default_kb_path": "circuit_graph/knowledge/circuit_graph.db",
        "schematics": sorted(schematics),
        "skills": [],
    }
    return json.dumps(cfg, ensure_ascii=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v -k "propose_config"`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/bootstrap.py tests/test_cli_init_cg.py
git commit -m "feat(bootstrap): propose_config turns detect JSON into CgConfig

Maps every detected .NET / .kicad_sch path to a project-relative entry
in the proposed config. Empty context falls back to all defaults. All
output is a JSON string per the design spec."
```

---

### Task 4: `write_config`

**Files:**
- Modify: `circuit_graph/bootstrap.py` (add `write_config`)
- Modify: `tests/test_cli_init_cg.py`

**Interfaces:**
- Produces: `write_config(json_cfg: str, target: Path, *, force: bool) -> str` (JSON string)

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_cli_init_cg.py

def test_write_config_creates_cg_toml(tmp_path: Path) -> None:
    cfg = json.dumps({"project_name": "demo", "default_graph_file": "output/x.json",
                      "default_graphml_file": "output/x.graphml",
                      "default_kb_path": "kb.db", "schematics": [], "skills": []})
    result = json.loads(bootstrap.write_config(cfg, tmp_path))
    assert result["written"] is True
    assert result["was_overwritten"] is False
    assert result["reason"] == "ok"
    out = (tmp_path / ".cg.toml").read_text(encoding="utf-8")
    assert "project_name" in out
    assert "demo" in out


def test_write_config_refuses_overwrite_without_force(tmp_path: Path) -> None:
    (tmp_path / ".cg.toml").write_text('project_name = "old"\n', encoding="utf-8")
    cfg = json.dumps({"project_name": "new", "default_graph_file": "",
                      "default_graphml_file": "", "default_kb_path": "",
                      "schematics": [], "skills": []})
    result = json.loads(bootstrap.write_config(cfg, tmp_path))
    assert result["written"] is False
    assert result["was_overwritten"] is False
    assert "--force" in result["reason"]
    assert (tmp_path / ".cg.toml").read_text(encoding="utf-8") == 'project_name = "old"\n'


def test_write_config_force_overwrites(tmp_path: Path) -> None:
    (tmp_path / ".cg.toml").write_text('project_name = "old"\n', encoding="utf-8")
    cfg = json.dumps({"project_name": "new", "default_graph_file": "",
                      "default_graphml_file": "", "default_kb_path": "",
                      "schematics": [], "skills": []})
    result = json.loads(bootstrap.write_config(cfg, tmp_path, force=True))
    assert result["written"] is True
    assert result["was_overwritten"] is True
    out = (tmp_path / ".cg.toml").read_text(encoding="utf-8")
    assert "new" in out
    assert "old" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v -k "write_config"`
Expected: FAIL with `AttributeError: module 'circuit_graph.bootstrap' has no attribute 'write_config'`

- [ ] **Step 3: Implement `write_config`**

```python
# Append to circuit_graph/bootstrap.py

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v -k "write_config"`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/bootstrap.py tests/test_cli_init_cg.py
git commit -m "feat(bootstrap): write_config persists CgConfig as .cg.toml

Refuses to overwrite an existing .cg.toml unless force=True. Returns a
JSON status with the absolute path and overwrite state for downstream
orchestration."
```

---

### Task 5: `delegate` — subprocess wrapper

**Files:**
- Modify: `circuit_graph/bootstrap.py` (add `delegate`)
- Modify: `tests/test_cli_init_cg.py`

**Interfaces:**
- Produces: `delegate(cmd: list[str], cwd: Path | None = None) -> str` (JSON string)

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_cli_init_cg.py

def test_delegate_kb_init_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "{\"status\": \"ok\"}"
        stderr = ""
    def fake_run(cmd, **kwargs):
        return FakeCompleted()
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    out = bootstrap.delegate(["cg", "kb", "init"])
    result = json.loads(out)
    assert result["exit_code"] == 0
    assert result["status"] == "ok"
    assert "kb" in result["cmd"]


def test_delegate_kb_init_captures_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "no bundled skills found"
    def fake_run(cmd, **kwargs):
        return FakeCompleted()
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    out = bootstrap.delegate(["cg", "kb", "init"])
    result = json.loads(out)
    assert result["exit_code"] == 1
    assert result["status"] == "failed"
    assert "no bundled skills" in result["stderr"]


def test_delegate_initskill_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "{\"status\": \"ok\"}"
        stderr = ""
    def fake_run(cmd, **kwargs):
        return FakeCompleted()
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    out = bootstrap.delegate(["cg", "initskill", "/tmp/target"])
    result = json.loads(out)
    assert result["exit_code"] == 0
    assert "initskill" in result["cmd"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v -k "delegate"`
Expected: FAIL with `AttributeError: module 'circuit_graph.bootstrap' has no attribute 'delegate'`

- [ ] **Step 3: Implement `delegate`**

```python
# Append to circuit_graph/bootstrap.py

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v -k "delegate"`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/bootstrap.py tests/test_cli_init_cg.py
git commit -m "feat(bootstrap): delegate wraps subprocess with JSON status

Captures exit_code, stdout, stderr, and duration. The exit-code-to-status
mapping is the single source of truth for what delegates 'failed' looks
like; the orchestrator can use status without re-checking exit_code."
```

---

### Task 6: `init_orchestrator` — happy path + skip flags

**Files:**
- Modify: `circuit_graph/bootstrap.py` (add `init_orchestrator`)
- Modify: `tests/test_cli_init_cg.py`

**Interfaces:**
- Produces: `init_orchestrator(target: Path, *, skip_config=False, skip_kb=False, skip_skill=False, yes=False, force=False, dry_run=False) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_cli_init_cg.py

@pytest.fixture
def mock_delegate(monkeypatch: pytest.MonkeyPatch):
    """Replace bootstrap.delegate with a recorder. Returns a list that
    each fake invocation appends its cmd to, so tests can assert what
    was called."""
    calls: list[list[str]] = []
    def fake(cmd, cwd=None):
        calls.append(list(cmd))
        return json.dumps({
            "cmd": list(cmd),
            "exit_code": 0,
            "status": "ok",
            "stdout": "{\"status\":\"ok\"}",
            "stderr": "",
            "duration_s": 0.0,
        })
    monkeypatch.setattr(bootstrap, "delegate", fake)
    return calls


def test_init_writes_cg_toml_default_path(tmp_path: Path, mock_delegate) -> None:
    out = bootstrap.init_orchestrator(tmp_path, yes=True)
    report = json.loads(out)
    assert report["status"] == "ok"
    assert report["config"]["written"] is True
    assert (tmp_path / ".cg.toml").is_file()


def test_init_runs_all_three_steps_by_default(tmp_path: Path, mock_delegate) -> None:
    bootstrap.init_orchestrator(tmp_path, yes=True)
    cmds_str = " ".join(" ".join(c) for c in mock_delegate)
    assert "kb init" in cmds_str
    assert "initskill" in cmds_str


def test_init_skip_config_only_runs_kb_and_skill(tmp_path: Path, mock_delegate) -> None:
    bootstrap.init_orchestrator(tmp_path, yes=True, skip_config=True)
    assert not (tmp_path / ".cg.toml").exists()
    cmds_str = " ".join(" ".join(c) for c in mock_delegate)
    assert "kb init" in cmds_str
    assert "initskill" in cmds_str


def test_init_skip_kb_only_runs_config_and_skill(tmp_path: Path, mock_delegate) -> None:
    bootstrap.init_orchestrator(tmp_path, yes=True, skip_kb=True)
    assert (tmp_path / ".cg.toml").is_file()
    cmds_str = " ".join(" ".join(c) for c in mock_delegate)
    assert "kb init" not in cmds_str
    assert "initskill" in cmds_str


def test_init_skip_skill_only_runs_config_and_kb(tmp_path: Path, mock_delegate) -> None:
    bootstrap.init_orchestrator(tmp_path, yes=True, skip_skill=True)
    assert (tmp_path / ".cg.toml").is_file()
    cmds_str = " ".join(" ".join(c) for c in mock_delegate)
    assert "kb init" in cmds_str
    assert "initskill" not in cmds_str
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v -k "init_writes or init_runs or init_skip"`
Expected: FAIL with `AttributeError: module 'circuit_graph.bootstrap' has no attribute 'init_orchestrator'`

- [ ] **Step 3: Implement `init_orchestrator`**

```python
# Append to circuit_graph/bootstrap.py

def init_orchestrator(
    target: Path,
    *,
    skip_config: bool = False,
    skip_kb: bool = False,
    skip_skill: bool = False,
    yes: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """Top-level orchestrator. Returns a JSON InitReport string.

    Flow: detect -> propose -> [confirm] -> write_config -> delegate kb init
    -> delegate initskill. Honors skip_config/skip_kb/skip_skill, force,
    yes, dry_run.
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
            "skill": None,
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
                "skill": None,
                "duration_s": round(time.time() - start, 3),
            }, ensure_ascii=False)

    config_report = None
    if dry_run:
        config_report = {"written": False, "path": None, "was_overwritten": False, "dry_run": True}
    elif skip_config:
        config_report = {"written": False, "path": None, "was_overwritten": False, "skipped": True}
    else:
        config_report = json.loads(write_config(cfg_str, target, force=force))

    kb_report = None
    skill_report = None
    sub_statuses: list[str] = []

    if not dry_run:
        if skip_kb:
            kb_report = {"status": "skipped"}
        else:
            kb_report = json.loads(delegate([sys.executable, "-m", "circuit_graph.cli", "kb", "init"], cwd=target))
            sub_statuses.append(kb_report["status"])

        if skip_skill:
            skill_report = {"status": "skipped"}
        else:
            skill_report = json.loads(delegate([sys.executable, "-m", "circuit_graph.cli", "initskill", str(target)], cwd=target))
            sub_statuses.append(skill_report["status"])

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
        "skill": skill_report,
        "duration_s": round(time.time() - start, 3),
    }, ensure_ascii=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v -k "init_writes or init_runs or init_skip"`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/bootstrap.py tests/test_cli_init_cg.py
git commit -m "feat(bootstrap): init_orchestrator wires detect/propose/write/delegate

Implements the happy path: detect target, propose .cg.toml contents,
confirm (skipped with --yes), write .cg.toml, delegate cg kb init and
cg initskill as subprocesses. --skip-config/--skip-kb/--skip-skill gate
each sub-step independently."
```

---

### Task 7: `init_orchestrator` — force, dry-run, idempotency

**Files:**
- Modify: `tests/test_cli_init_cg.py`

**Interfaces:**
- Consumes: `init_orchestrator` (from Task 6)

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_cli_init_cg.py

def test_init_force_overwrites_existing_config(tmp_path: Path, mock_delegate) -> None:
    (tmp_path / ".cg.toml").write_text('project_name = "old"\n', encoding="utf-8")
    bootstrap.init_orchestrator(tmp_path, yes=True, force=True)
    out = (tmp_path / ".cg.toml").read_text(encoding="utf-8")
    assert "old" not in out


def test_init_dry_run_prints_plan_no_writes(tmp_path: Path, mock_delegate) -> None:
    out = bootstrap.init_orchestrator(tmp_path, yes=True, dry_run=True)
    report = json.loads(out)
    assert report["status"] == "dry_run"
    assert report["dry_run"] is True
    assert not (tmp_path / ".cg.toml").exists()
    assert mock_delegate == []  # no subprocess calls in dry-run


def test_init_idempotent_second_run_no_changes(tmp_path: Path, mock_delegate) -> None:
    bootstrap.init_orchestrator(tmp_path, yes=True)
    # Second run: config should NOT be re-written, kb/skill should NOT re-run
    mock_delegate.clear()
    out = bootstrap.init_orchestrator(tmp_path, yes=True)
    report = json.loads(out)
    assert report["status"] == "ok"
    # Without --force, on second run, we should detect existing config and skip
    # the writes; kb and skill are also skipped (idempotent by default).
    assert report["config"]["written"] is False
    assert report["config"].get("skipped") is True
    assert mock_delegate == []  # no delegate calls on idempotent re-run


def test_init_yes_flag_skips_interactive_confirm(tmp_path: Path, monkeypatch, mock_delegate) -> None:
    import click
    confirm_calls: list[str] = []
    def fake_confirm(text, default=True):
        confirm_calls.append(text)
        return True
    monkeypatch.setattr(click, "confirm", fake_confirm)
    bootstrap.init_orchestrator(tmp_path, yes=True)
    # yes=True means we never call click.confirm
    assert confirm_calls == []


def test_init_returns_json_string_at_top_level(tmp_path: Path, mock_delegate) -> None:
    out = bootstrap.init_orchestrator(tmp_path, yes=True)
    assert isinstance(out, str)
    parsed = json.loads(out)
    assert "status" in parsed
    assert "target" in parsed
    assert "config" in parsed
    assert "kb" in parsed
    assert "skill" in parsed
```

- [ ] **Step 2: Implement idempotency in `init_orchestrator`**

Edit `init_orchestrator` in `circuit_graph/bootstrap.py`. The current implementation always calls `write_config` + `delegate` — modify to short-circuit when an existing `.cg.toml` is present and `force=False`, and skip the `kb init` / `initskill` sub-commands when their respective state files already exist (KB db at `<target>/circuit_graph/knowledge/circuit_graph.db`; skills at `<target>/.claude/skills/`).

Replace the section starting at `if not dry_run:` through the end of the `if` block with:

```python
    if not dry_run:
        cg_toml = target / ".cg.toml"
        kb_db = target / "circuit_graph" / "knowledge" / "circuit_graph.db"
        skills_dir = target / ".claude" / "skills"

        if skip_kb:
            kb_report = {"status": "skipped"}
        elif kb_db.is_file() and not force:
            kb_report = {"status": "skipped", "reason": "KB already exists; pass --force to re-run"}
        else:
            kb_report = json.loads(delegate([sys.executable, "-m", "circuit_graph.cli", "kb", "init"], cwd=target))
            sub_statuses.append(kb_report["status"])

        if skip_skill:
            skill_report = {"status": "skipped"}
        elif skills_dir.is_dir() and not force:
            skill_report = {"status": "skipped", "reason": "skills already installed; pass --force to re-run"}
        else:
            skill_report = json.loads(delegate([sys.executable, "-m", "circuit_graph.cli", "initskill", str(target)], cwd=target))
            sub_statuses.append(skill_report["status"])
```

Also, in the `elif skip_config:` branch, change to also detect existing config and skip without re-writing. Replace:

```python
    elif skip_config:
        config_report = {"written": False, "path": None, "was_overwritten": False, "skipped": True}
```

with:

```python
    elif skip_config or (target / ".cg.toml").is_file() and not force:
        config_report = {
            "written": False,
            "path": str(target / ".cg.toml"),
            "was_overwritten": False,
            "skipped": True,
        }
```

(Read the current state of `init_orchestrator` first to know exactly which lines to replace. Match the existing `elif` shape.)

- [ ] **Step 3: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v`
Expected: 16 passed (5 detect + 3 propose + 3 write + 3 delegate + 5 happy-path/skip from Task 6 + 5 from this task minus the duplicate `test_init_skip_skill_only_runs_config_and_kb` already counted = 16 distinct)

If any fail, fix `init_orchestrator` and re-run.

- [ ] **Step 4: Commit**

```bash
git add circuit_graph/bootstrap.py tests/test_cli_init_cg.py
git commit -m "feat(bootstrap): idempotent re-runs; --force to overwrite; --dry-run no-op

Default re-runs now detect existing .cg.toml, KB db, and skills dir
and skip the corresponding sub-step. --force overrides every check.
--dry-run prints the plan and runs zero subprocesses."
```

---

### Task 8: Register `cg init` Click subcommand

**Files:**
- Modify: `circuit_graph/cli.py:1225-1230` (insert new command before `if __name__ == "__main__":`)

- [ ] **Step 1: Write the failing CLI test**

Add a new test to `tests/test_cli_init_cg.py`:

```python
# Append to tests/test_cli_init_cg.py

from click.testing import CliRunner  # noqa: E402

from circuit_graph.cli import main  # noqa: E402


def test_cli_init_listed_in_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.stdout


def test_cli_init_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--help"])
    assert result.exit_code == 0
    assert "Bootstrap" in result.stdout
    assert "--skip-config" in result.stdout
    assert "--skip-kb" in result.stdout
    assert "--skip-skill" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v -k "cli_init"`
Expected: FAIL (init command not registered yet)

- [ ] **Step 3: Register `cg init` in cli.py**

In `circuit_graph/cli.py`, find the line `# Exit non-zero if any skill failed to copy.` and `if failed: sys.exit(1)` (around line 1223-1225). Immediately after the `if failed: sys.exit(1)` block, add:

```python
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
def init_cmd(target: Path, skip_config: bool, skip_kb: bool, skip_skill: bool,
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
```

Note: the function is named `init_orchestrator_cmd` to avoid colliding with the existing `init_cmd` (the `cg initskill` handler at line 1131).
```

Use the function name `init_orchestrator_cmd` (NOT `init_cmd` — that name is already taken by the `cg initskill` Click handler in this same file at line 1131; the rename commit 59c86cd changed only the @main.command(name=…) and left the Python function name untouched).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py -v`
Expected: 18 passed (all)

- [ ] **Step 5: Smoke-test the live CLI**

Run: `.venv\Scripts\cg.exe init --help`
Expected: Help text listing all flags (--skip-config, --skip-kb, --skip-skill, -y, --force, --dry-run).

Run: `cd $env:TEMP; New-Item -ItemType Directory -Force cg-init-smoke | Out-Null; cd cg-init-smoke; .venv\Scripts\cg.exe init . --yes --dry-run`
Expected: JSON report with `"status": "dry_run"`, no `.cg.toml` written.

- [ ] **Step 6: Commit**

```bash
git add circuit_graph/cli.py tests/test_cli_init_cg.py
git commit -m "feat(cli): register \`cg init\` orchestrator subcommand

Wires \`@main.command(name='init')\` to the bootstrap.init_orchestrator
function. Exits with code 0 on ok/skipped/dry_run, 1 on partial
failure, 2 on entry-param error (matching the spec exit-code table)."
```

---

### Task 9: End-to-end test + update SKILL routing

**Files:**
- Modify: `tests/test_cli_init_cg.py` (add e2e test)
- Modify: `circuit_graph/skills/cg/SKILL.md` (add `cg init` to routing)

- [ ] **Step 1: Add end-to-end CLI test**

```python
# Append to tests/test_cli_init_cg.py

def test_cli_init_end_to_end_dry_run(tmp_path: Path) -> None:
    (tmp_path / "board.NET").write_text("(NET1 R1-1 R2-1)", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["init", str(tmp_path), "--dry-run", "--yes"])
    assert result.exit_code == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "dry_run"
    assert "board.NET" in report["config"].get("path", "") or report["config"]["written"] is False
    assert not (tmp_path / ".cg.toml").exists()
```

- [ ] **Step 2: Run new test, ensure it passes**

Run: `.venv\Scripts\python -m pytest tests/test_cli_init_cg.py::test_cli_init_end_to_end_dry_run -v`
Expected: PASS

- [ ] **Step 3: Update cg SKILL.md routing table**

In `circuit_graph/skills/cg/SKILL.md`, find the routing dot-graph block (around lines 16-46). Add a new node and edge for `cg init` after the existing "Only part number?" branch:

Find the line `"Only part number?" -> "cg kb lookup|search|pinout" [label="yes"];` and add right after it:

```
    "First-time setup?" [shape=diamond];
    "cg init [TARGET]" [shape=box, style=filled, fillcolor=lightyellow];
    "First-time setup?" -> "cg init [TARGET]" [label="yes"];
```

(The lightyellow fill marks `cg init` as a setup step, distinct from the analysis steps.)

Also add a line to the "Core Commands" section (find the existing `## Core Commands (Protel .NET — the common case)` header, around line 48) before its first code block:

```markdown
## First-time setup

```powershell
# Bootstrap a fresh project: write .cg.toml + init KB + install skills
cg init .
cg init . --force           # overwrite existing .cg.toml; re-run sub-steps
cg init . --skip-skill      # only write config + init KB, skip skills
```
```

- [ ] **Step 4: Run the full test suite**

Run: `.venv\Scripts\python -m pytest -q 2>&1 | Select-Object -Last 5`
Expected: 355 (existing) + ~19 (new) = ~374 passed, 3 skipped

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli_init_cg.py circuit_graph/skills/cg/SKILL.md
git commit -m "test+docs(cli): end-to-end cg init dry-run + skill routing entry

Adds an end-to-end test that exercises the registered cg init subcommand
via CliRunner (covers both the Click wiring and the bootstrap module
together), and routes 'first-time setup' questions to \`cg init\`
in the cg skill's routing graph."
```

---

## Self-Review

**1. Spec coverage:**
- §1 背景 → README + spec already cover it
- §2 设计决策 → reflected in Tasks 1-9
- §3 架构 → Tasks 6-7 implement the exact flow
- §4 组件签名 → Tasks 1, 3, 4, 5, 6 produce each signature
- §5 CLI 表面 → Task 8 registers the exact command
- §6 错误处理 → Task 7's "partial" / "failed" statuses, Task 8's exit codes
- §7 Flag 行为矩阵 → Task 6 + 7 + 8 cover all 6 flags
- §8 测试 → 19 tests across all 5 groups + e2e
- §9 实施检查清单 → Tasks 1-9 cover items 1-5

**2. Placeholder scan:** No "TBD", "TODO", "fill in", or "implement later" in the plan. Every code block is complete.

**3. Type consistency:** `detect_project`, `propose_config`, `write_config`, `delegate`, `init_orchestrator` all consistently return `str` (JSON). `init_orchestrator` parameters consistent across Tasks 6, 7, 8.
