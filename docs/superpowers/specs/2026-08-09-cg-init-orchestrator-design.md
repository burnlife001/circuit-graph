# `cg init` 编排器设计

**日期**: 2026-08-09
**状态**: 设计稿，待审
**作者**: brainstorm 输出
**关联**: 紧接 `cg init` → `cg initskill` 重命名（commit 59c86cd），目的是把空出来的 `init` 槽位用于项目级引导。

## 1. 背景与动机

`cg init` 在 commit 59c86cd 之前承担 "复制 bundled skill 到目标 .claude/skills/" 的职责。重命名为 `cg initskill` 后，`init` 名字空出。

当前 `cg` CLI 现状：

| 子命令 | 职责 |
|---|---|
| `cg initskill` | 复制 bundled skills（无项目状态概念） |
| `cg kb init` | 初始化/升级 SQLite KB、迁移 vendor_db.json |
| `cg export / view / sim / erc` | 使用 cg |
| **空缺** | **项目级引导：写 `.cg.toml`、决定 KB/skills 装在哪儿、首次接入项目** |

**问题**：用户首次在新项目里使用 `cg`，需要手动：
1. 写 `.cg.toml`（目前还没这文件，但 `cg view` 等命令的默认路径都基于 cwd，需要先确立配置）
2. 跑 `cg kb init`
3. 跑 `cg initskill`

三步分散、易遗漏、CI 集成时无人写 `.cg.toml`。

**目标**：用 `cg init` 一次性完成上述三步，并在新项目里自动生成 `.cg.toml`。

## 2. 设计决策

| 维度 | 选择 | 理由 |
|---|---|---|
| 定位 | 一站式编排器 | 用户确认 |
| 默认范围 | 全部默认开 + `--skip-*` 反向跳过 | 用户确认 |
| 配置内容来源 | 检测现成文件 + 提议值 | 用户确认 |
| 重跑语义 | 幂等（刷新检测、跳过已完成） | 用户确认 |
| 模块名 | `circuit_graph/bootstrap.py` | 用户确认 |
| 跨函数返回值 | **JSON 字符串** | 用户确认（方便调试：print 即看） |
| 错误信息 | 简洁一句话 + 建议动作 | 用户确认 |
| 子命令调用 | `subprocess.run` 调 `cg kb init` / `cg initskill` | 进程隔离、行为可预测、零代码重复 |
| 原子性 | 失败不回滚（已写 `.cg.toml` 保留） | 简单；用户可 `--force` 修复 |

## 3. 架构

```
cg init [TARGET] [flags]
  │
  ├─── detect_project(target)            → str (JSON ProjectContext)
  │
  ├─── propose_config(json_ctx_str)      → str (JSON CgConfig)
  │
  ├─── [interactive] confirm?            # 打 proposed .cg.toml diff, 问 y/n
  │      └ (--yes 跳过)
  │
  ├─── write_config(json_cfg_str, force) → str (JSON {written, path, was_overwritten})
  │
  ├─── delegate kb_init()                → str (JSON {exit_code, status, stdout, stderr})
  │
  └─── delegate initskill(target)        → str (JSON {exit_code, status, stdout, stderr})
         │
         └→ InitReport (JSON str, 顶层 dict: target/status/dry_run/config/kb/skill/duration_s)
```

## 4. 组件签名

`circuit_graph/bootstrap.py`:

```python
def detect_project(target: Path) -> str:
    """返回 JSON 字符串:
    {
      "target": "<abs path>",
      "protel_nets": ["<abs>", ...],
      "kicad_schematics": ["<abs>", ...],
      "kicad_projects": ["<abs>", ...],
      "existing_cg_toml": "<abs>" | null,
      "existing_kb_db": "<abs>" | null,
      "existing_skills_dir": "<abs>" | null
    }
    失败时: {"status": "failed", "error": "<一句话>"}
    """

def propose_config(json_ctx: str) -> str:
    """输入 detect_project 的 JSON; 返回 JSON:
    {
      "project_name": "<target.name>",
      "default_graph_file": "output/circuit.graph.json",
      "default_graphml_file": "output/circuit.graphml",
      "default_kb_path": "circuit_graph/knowledge/circuit_graph.db",
      "schematics": ["..."],
      "skills": ["..."]
    }
    """

def write_config(json_cfg: str, target: Path, *, force: bool) -> str:
    """返回 JSON:
    {"written": true|false, "path": "<abs>", "was_overwritten": true|false,
     "reason": "ok" | "exists; pass --force to overwrite" | "no-write"}
    """

def delegate(cmd: list[str], cwd: Path | None = None) -> str:
    """subprocess 调子命令; 返回 JSON:
    {"cmd": [...], "exit_code": int, "status": "ok"|"failed",
     "stdout": "...", "stderr": "...", "duration_s": float}
    """

def init_orchestrator(
    target: Path, *,
    skip_config: bool = False,
    skip_kb: bool = False,
    skip_skill: bool = False,
    yes: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """全流程编排. 返回 JSON 顶层 dict:
    {
      "target": "<abs>",
      "status": "ok" | "partial" | "failed" | "dry_run",
      "dry_run": bool,
      "config": {"path", "written", "was_overwritten"} | null,
      "kb":     {"exit_code", "status", "stdout"} | null,
      "skill":  {"exit_code", "status", "stdout"} | null,
      "duration_s": float
    }
    """
```

## 5. CLI 表面

`@main.command(name="init")` 紧邻 `initskill` 之后注册：

```python
@main.command(name="init")
@click.argument("target", type=click.Path(file_okay=False, path_type=Path),
                default=".", metavar="[TARGET]")
@click.option("--skip-config", is_flag=True)
@click.option("--skip-kb",     is_flag=True)
@click.option("--skip-skill",  is_flag=True)
@click.option("-y", "--yes",   is_flag=True, help="Auto-accept proposed config.")
@click.option("--force",       is_flag=True, help="Overwrite .cg.toml; re-run sub-commands.")
@click.option("--dry-run",     is_flag=True, help="Print plan, no writes.")
def init_cmd(target, skip_config, skip_kb, skip_skill, yes, force, dry_run) -> None:
    """Bootstrap a project for circuit-graph.

    By default writes .cg.toml, runs `cg kb init`, and runs `cg initskill`.
    Use --skip-* to opt out. Re-running is idempotent; pass --force to overwrite.
    """
```

## 6. 错误处理

| 步骤 | 触发 | 错误字符串 |
|---|---|---|
| `detect_project` | target 不存在 | `"target '<x>' does not exist"` |
| `detect_project` | target 是文件 | `"target '<x>' is not a directory"` |
| `propose_config` | 无任何探测项 | `"warning: no schematic files detected; using defaults"`（非错误） |
| `write_config` | 已有 `.cg.toml` 无 `--force` | `"exists; pass --force to overwrite"` |
| `delegate *` | 子命令非 0 退出 | `"<子命令名> failed: <stderr 最后一行>"` |
| `init_orchestrator` | 任一步 failed | 顶层 `status: "partial"`，对应子步 `status: "failed"`，附 `error` 字段 |

**原则**：
- 错误字符串都包含「触发条件 + 建议动作」
- 不抛跨函数异常；orchestrator 内部 try/except 转 JSON 字段
- 不做事务回滚；失败后 `.cg.toml` 保留，用户可手动改 / 重跑 `--force`

**退出码**：
- `0` — 全 OK 或全 skip
- `1` — 至少一步 failed（partial 状态）
- `2` — 入口参数错误

## 7. Flag 行为矩阵

| 标志 | 默认 | 作用 |
|---|---|---|
| `--skip-config` | 关 | 不写 `.cg.toml`；kb/skill 仍跑 |
| `--skip-kb` | 关 | 不调 `cg kb init` |
| `--skip-skill` | 关 | 不调 `cg initskill` |
| `-y` / `--yes` | 关 | 跳过交互式 confirm，直接落盘 |
| `--force` | 关 | 覆盖已有 `.cg.toml`；强制重跑 kb/skill |
| `--dry-run` | 关 | 跑 detect + 打印 diff + 打印子命令计划，零写入 |

## 8. 测试

`tests/test_cli_init_cg.py`（独立文件，避免与 `test_cli_init.py` 命名混淆）：

**检测 (5)**
- `test_detect_project_finds_protel_nets`
- `test_detect_project_finds_kicad_schematics`
- `test_detect_project_finds_existing_cg_toml`
- `test_detect_project_missing_target_returns_error`
- `test_detect_project_returns_json_string`

**提议 (3)**
- `test_propose_config_picks_up_detected_schematics`
- `test_propose_config_uses_defaults_when_empty`
- `test_propose_config_returns_json_string`

**写入 (3)**
- `test_write_config_creates_cg_toml`
- `test_write_config_refuses_overwrite_without_force`
- `test_write_config_force_overwrites`

**委派 (3)** — monkeypatch mock subprocess
- `test_delegate_kb_init_succeeds`
- `test_delegate_kb_init_captures_stderr`
- `test_delegate_initskill_succeeds`

**编排 (12)**
- `test_init_writes_cg_toml_default_path`
- `test_init_runs_all_three_steps_by_default`
- `test_init_skip_config_only_runs_kb_and_skill`
- `test_init_skip_kb_only_runs_config_and_skill`
- `test_init_skip_skill_only_runs_config_and_kb`
- `test_init_force_overwrites_existing_config`
- `test_init_dry_run_prints_plan_no_writes`
- `test_init_idempotent_second_run_no_changes`
- `test_init_yes_flag_skips_interactive_confirm`
- `test_init_returns_json_string_at_top_level`
- `test_init_exit_code_1_when_substep_fails`
- `test_init_exit_code_2_when_target_missing`

总计 ~26 个测试。

## 9. 实施检查清单

1. 新建 `circuit_graph/bootstrap.py` 含 5 个公开函数
2. `circuit_graph/cli.py` 注册 `@main.command(name="init")` 调用 `init_orchestrator` 并 `click.echo(report_str)`
3. 新建 `tests/test_cli_init_cg.py` 含上述 ~26 测试
4. `README.md` 加 "项目引导 (cg init)" 章节，列出 flags 与示例
5. `circuit_graph/skills/cg/SKILL.md` 同步新增 `cg init` 入口（路由表）
6. 全量 `pytest` 通过；新增 `cg init --help`、`cg init . --dry-run` 烟雾测试

## 10. 范围外（v1 不做）

- `.cg.toml` 校验（`cg config validate` 子命令）
- `.cg.toml` 读取消费（`cg view` 等命令暂时仍基于 cwd + 默认路径）
- 跨多个 target 批量 init
- 交互式 TUI（v1 用 `click.confirm` 简单 y/n）
- 配置文件 schema 版本号
