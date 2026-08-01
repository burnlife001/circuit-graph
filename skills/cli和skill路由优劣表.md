# CLI vs Skill 路由 — eetool 架构评估 (cbmm 实测)

> 使用 codebase-memory MCP (`explore`, `get_architecture`, `get_code_snippet`, `search_graph`) 对两套路由方案进行代码级对比。
> 评估日期: 2026-07-05

## 两个方案

| 方案 | 路径 | 路由机制 | cbmm 索引 |
|------|------|----------|-----------|
| **A: CLI 路由** | `E:\__electric\__workspace\stacking\eetool` | `eetool <domain> <action>` → argparse subparser → `commands/*.py` | ✅ 已索引 (4027 节点, 12665 边) |
| **B: Skill 路由** | `C:\Users\yg\.claude\skills\eetool` | SKILL.md → `Read(sub-skill/SKILL.md)` → 执行子技能工作流 | ✅ 已索引 (3696 节点, 11578 边) |

> 注: 原路径 `E:\__work\BaseTools\eetool` 已不存在，项目已迁移至 `E:\__electric\__workspace\stacking\eetool`。

---

## 一、架构对比 (cbmm 实测)

### A: CLI 路由 — 代码图数据

```
src/eetool/
├── cli.py (55 行)               ← 入口: build_parser() + main()
├── commands/ (7 模块)
│   ├── serial.py (~357 行)      ← add_subparser() + run()
│   ├── pin2json.py (~140 行)    ← add_subparser() + run()
│   ├── pin_extract.py (~921 行) ← add_subparser() + run()  [最大]
│   ├── schmd_from_netlist.py (~470 行)
│   ├── keil.py (~586 行)        ← add_subparser() + run() + setup_project()
│   ├── capture.py (~122 行)     ← subprocess 委托模式
│   └── doctor.py (~143 行)      ← add_subparser() + run()
├── core/ (3 共享模块)
│   ├── locks.py (32 行)         ← ProcessLock (filelock 封装)
│   ├── kicad_parser.py          ← .kicad_sym S-expression 解析器
│   └── netlist_parser.py        ← Protel/DXP 网表解析器
├── capture/ (子包, 独立进程)
│   ├── atk_cli.py               ← 主 CLI 入口
│   ├── lib/ (lib_reader, lib_proto, lib_decoder, lib_config_sync)
│   ├── analyze/ (atk_classify, atk_pwm)
│   ├── capture/ (atk_preflight, uia_save)
│   └── runtime/decoders/ (~50+ 协议解码器)
└── data/keil/                   ← PowerShell 模板 (包数据)
```

### B: Skill 路由 — 目录结构

```
~/.claude/skills/eetool/
├── SKILL.md (路由表, ~120 行)
├── serial-monitor/SKILL.md      ← 独立 venv (E:/__electric/MM32/tools/pyserial/.venv)
├── atk-logic-capture/SKILL.md   ← 12 脚本 + 独立依赖
├── keil-init/SKILL.md           ← 8 步 SOP，调用 keil-batch-gen
├── keil-batch-gen/SKILL.md      ← 3 步模板替换
├── pdf-pin-extract/SKILL.md     ← 独立 venv (E:/__electric/tools/camelot/.venv)
├── pin2json/SKILL.md            ← 依赖系统级 JLC2KiCadLib
├── schmd-from-netlist/SKILL.md  ← 独立脚本
└── hardware-diff-by-codes/SKILL.md ← 纯 SOP，无脚本
```

### 结构对比

| 维度 | A: CLI 路由 | B: Skill 路由 |
|------|-------------|---------------|
| **入口** | `eetool.exe` (pip entry point) | 单一 `SKILL.md` (Claude Code 自动发现) |
| **子命令分发** | argparse `subparsers` → `command_dispatch` dict | 路由表匹配意图 → `Read` 工具加载子技能 |
| **子模块定义** | Python 模块，统一 `add_subparser() + run()` 契约 | 独立目录 + SKILL.md + scripts/ + 独立 venv |
| **参数传递** | argparse 类型检查 + Namespace → `run(args)` | 用户原始请求 verbatim → 子技能自行解析 |
| **代码共享** | `core/` 3 个共享模块，类型安全导入 | 无共享 — 每个子技能独立维护 |
| **依赖管理** | 单一 `pyproject.toml` + `.venv` (11 个依赖) | 至少 3 个独立 `.venv` |
| **发现机制** | `eetool --help` 自文档化 | Skill scanner 仅扫描一级深度 |

---

## 二、路由机制深入 (cbmm trace)

### A: CLI 两级路由

**Level 1 — `cli.py:build_parser()` → `main()`:**

```python
# 7 个命令模块均暴露 add_subparser(subparsers) + run(args) → int
subparsers = parser.add_subparsers(dest="command")
serial.add_subparser(subparsers)         # → "serial"
pin2json.add_subparser(subparsers)       # → "pin2json"
pin_extract.add_subparser(subparsers)    # → "pin"      (注意: dest 缩写)
schmd_from_netlist.add_subparser(subparsers)  # → "schmd-from-netlist"
keil.add_subparser(subparsers)           # → "keil"
capture.add_subparser(subparsers)        # → "capture"
doctor.add_subparser(subparsers)         # → "doctor"

# dispatch dict — 字符串键 → run 函数
command_dispatch = {
    "serial": serial.run, "pin2json": pin2json.run, "pin": pin_extract.run,
    "schmd-from-netlist": schmd_from_netlist.run, "keil": keil.run,
    "capture": capture.run, "doctor": doctor.run,
}
```

**Level 2 — 各命令内部 dispatch:**

| 命令 | Level 2 dest | Action 路由 |
|------|-------------|-------------|
| `serial` | `serial_command` | `if/elif` on `args.serial_command` |
| `keil` | `keil_command` | `if "init" → init_project()`, `if "setup" → setup_project()`, `if in TEMPLATES → render_template()` |
| `pin_extract` | `pin_command` | `if "search" → cmd_search()`, `if "extract" → cmd_extract()`, `if "verify" → cmd_verify()` |
| `schmd_from_netlist` | `schmd_command` | `if "map" → cmd_map()`, `if "infer" → cmd_infer()` |
| `capture` | (REMAINDER 透传) | subprocess 委托 — 见下文 |
| `pin2json` | (无 Level 2) | 单 action |
| `doctor` | (无 Level 2) | 单 action |

### B: Capture 的 subprocess 委托 — 特殊路由

`capture.py` 是所有命令中唯一**不直接 import** 业务逻辑的模块：

```python
# capture.py 常量
CLI_MODULE = "eetool.capture.atk_cli"
ALLOWED_SUBCOMMANDS = {
    "info", "export", "decode", "list-decoders",  # atk_cli 顶层 verb
    "start", "stop", "show", "set",               # atk_cli 二层 verb
    "classify", "pwm", "preflight",               # 独立入口模块
}
CAPTURE_VERB_PARENT = {  # 二层 verb → 父 verb 映射
    "start": "capture", "stop": "capture",
    "show": "config",    "set": "config",
}
ALT_MODULES = {  # 非 atk_cli 的入口模块
    "classify": "eetool.capture.analyze.atk_classify",
    "pwm": "eetool.capture.analyze.atk_pwm",
    "preflight": "eetool.capture.capture.atk_preflight",
}

# run() 核心逻辑:
# 1. 验证 subcommand ∈ ALLOWED_SUBCOMMANDS
# 2. 查 ALT_MODULES, 查 CAPTURE_VERB_PARENT, 构造 forwarded_verb
# 3. ProcessLock("capture", timeout=0) → subprocess.call([python, -m, module, *verbs, *extra_args])
```

**为什么用 subprocess?** DLL 代理 (Qt5Network.dll)、UIA 自动化、TCP socket IPC、GUI 崩溃隔离 — 进程级隔离最干净。

### C: Skill 路由 — Read 工具迂回

```
用户请求 → eetool SKILL.md 匹配路由表 → Read(sub-skill/SKILL.md) → Agent 解析步骤 → 执行 shell 命令
```

**两级嵌套问题**: Claude Code skill scanner 只扫描 `skills/*/SKILL.md` (一级深度)。`eetool/atk-logic-capture/SKILL.md` 是两级深 — `Skill(skill="atk-logic-capture")` → `Unknown skill`。

**实际后果**: 每次路由需 2 次 tool call (Read router + Read sub-skill)，Agent 需解析 Markdown 工作流。

---

## 三、代码质量指标 (cbmm 实测)

### 规模

| 指标 | A: CLI | B: Skill |
|------|--------|----------|
| cbmm 节点数 | 4027 | 3696 |
| cbmm 边数 | 12665 | 11578 |
| Python 文件数 | 332 | ~45 |
| 核心路由代码 | ~120 行 (cli.py + commands/__init__.py) | ~120 行 (SKILL.md 路由表) |
| 最大单文件 | pin_extract.py (921 行) | atk-logic-capture/SKILL.md (~265 行) |
| 测试文件 | 12 个 (tests/) | 0 |

### 复杂度 (cbmm 热点分析)

| 热点函数 | 入度 | 说明 |
|----------|------|------|
| `capture.run` | 23 | dispatch 逻辑被广泛测试 |
| `atk_classify.main` | 入口点 | 独立 CLI 入口 |
| `atk_pwm.main` | 入口点 | 独立 CLI 入口 |
| `keil.setup_project` | 内部 | 8 步编排核心 (~120 行) |

### 测试覆盖 (cbmm 发现的测试文件)

```
tests/
├── test_cli.py                       ← CLI 顶层测试
├── test_doctor.py                    ← doctor 命令测试
├── test_integration.py               ← 集成烟雾测试:
│   ├── test_all_top_level_commands_in_help
│   ├── test_help_does_not_omit_legacy_flags
│   └── test_each_command_module_imports_clean  ← 验证 add_subparser + run 契约
├── commands/
│   ├── test_capture.py
│   ├── test_keil.py
│   ├── test_pin2json.py
│   ├── test_pin_extract.py
│   ├── test_schmd_from_netlist.py
│   └── test_serial.py
└── core/
    ├── test_config_sync.py
    ├── test_kicad_parser.py
    ├── test_locks.py
    └── test_netlist_parser.py
```

**Skill 路由侧: 0 个自动化测试** — 所有验证需手动执行。

### cbmm 聚类分析

CLI 项目的 12 个聚类中：
- Cluster 79 (`src`, 157 成员, 凝聚度 0.78): 核心命令 + schmd/netlist 逻辑
- Cluster 98 (`src`, 78 成员, 凝聚度 0.89): capture 子系统 (atk_cli, decode, cmd_capture)
- Cluster 405 (54 成员, 凝聚度 1.0): libsigrokdecode 协议解码器 (高内聚)
- 其余 9 个聚类: 各协议解码器独立群组 (I2C, SPI, UART, Modbus, etc.)

**结论**: CLI 项目的模块边界清晰，capture 子系统和协议解码器各自高内聚。

---

## 四、功能对比 (CLI 有而 Skill 无的特性)

| 特性 | CLI 路由 | Skill 路由 | 实现位置 |
|------|---------|-----------|----------|
| **跨进程锁** | ✅ ProcessLock (filelock) | ❌ 无并发保护 | `core/locks.py` |
| **串口 INI 持久化** | ✅ 自动保存/恢复配置到 `~/.local/share/eetool/serial.ini` | ❌ 每次手动指定参数 | `serial.py:_apply_ini_defaults()` |
| **环境诊断** | ✅ `eetool doctor` (Python 版本 + 11 依赖 + 串口) | ❌ 无 | `doctor.py:collect_checks()` |
| **参数类型校验** | ✅ argparse type + choices | ❌ 运行时手动校验 | 各 `add_subparser()` |
| **管道友好输出** | ✅ pin2json stdout 纯 JSON | ✅ 同 (因调用同一脚本) | `pin2json.py:run()` |
| **Keil setup 全流程** | ✅ `eetool keil setup` 一键完成 8 步 | ⚠️ 8 步手动按 SOP 执行 | `keil.py:setup_project()` |
| **集成测试** | ✅ `test_integration.py` | ❌ | |

---

## 五、Skill 路由优于 CLI 的场景

| 场景 | 原因 |
|------|------|
| **NL 意图匹配** | "帮我看下 COM7 的串口输出" → `serial-monitor` — 无需精确子命令 |
| **歧义消解** | 路由表 disambiguation rules + AskUserQuestion fallback |
| **keil-init 编排** | 8 步流程需要 Agent 判断 (分析项目结构 → 确定禁区 → 编写 CLAUDE.md) — CLI 的 `setup` 虽已实现，但 Agent 需理解上下文 |
| **hardware-diff** | 纯 SOP — 读代码 → 模拟寄存器 → 分类差异 → 出报告，无法 CLI 化 |
| **atk-logic-capture 复杂诊断** | GUI 挂死、TCP 异常、UIA 失败等场景需要 Agent 理解上下文做决策 |

---

## 六、关键洞察

### 1. CLI 已基本完成 Skill 路由的功能迁移

通过 cbmm 代码分析确认：**7 个命令模块全部实现了 `add_subparser() + run()` 契约**，`test_integration.py` 验证了契约一致性。所有设计文档 (docs/design.md) 中的 CLI 命令均已落地。

### 2. Skill 子技能的实质是"未编译的 CLI 文档"

以 `serial-monitor/SKILL.md` 为例：
```
source E:/__electric/MM32/tools/pyserial/.venv/Scripts/activate
python $HOME/.claude/skills/serial-monitor/scripts/serial_monitor.py --port COM7
```
等价于 CLI 的：
```
eetool serial listen --port COM7
```
区别是：CLI 版有类型检查、INI 持久化、ProcessLock、`doctor` 诊断 — Skill 版只有硬编码路径。

### 3. 两级嵌套是 Skill 路由的结构性缺陷

`Skill(skill="atk-logic-capture")` → `Unknown skill`。每次路由多 1 次 Read tool call + Agent 需解析 Markdown。这个问题**不是暂时限制**，而是 Claude Code 技能发现机制的设计约束。

### 4. CLI 已具备"医生"能力 — Skill 没有

`eetool doctor` 检查 Python 版本、11 个依赖、串口可用性 — 这是 CLI 独有的运维能力。Skill 路由的错误排查完全依赖 Agent 试错。

### 5. 真正无法 CLI 化的只有 2 个

- **hardware-diff**: 纯 Agent 分析，需要阅读代码、模拟寄存器状态、分类差异
- **keil-init 的"理解项目"部分**: `setup_project()` 虽已实现自动化，但 Agent 仍需验证结果、调整禁区

---

## 七、推荐: 混合路由 (更新版)

```
~/.claude/skills/eetool/SKILL.md   ← Skill 路由 (薄层，仅意图匹配)
    │
    ├── 匹配到简单操作 → 直接调用 eetool CLI
    │   eetool serial listen --port COM7
    │   eetool capture start --ch 2,3
    │   eetool pin2json C9405
    │   eetool pin extract datasheet.pdf
    │   eetool schmd-from-netlist map board.NET
    │   eetool keil setup . --framework FreeRTOS
    │   eetool doctor
    │
    └── 匹配到 Agent 编排 → 子技能 SKILL.md (纯工作流，调用 CLI 辅助)
        hardware-diff-by-codes/SKILL.md   ← 纯 SOP，无 CLI
        atk-logic-capture/SKILL.md        ← 复杂诊断场景保留
```

### 决策矩阵

| 子技能 | 路由方式 | CLI 状态 | 理由 |
|--------|---------|---------|------|
| serial-monitor | **CLI** | ✅ 已实现 | INI 持久化、CH340 自动检测、ProcessLock |
| pin2json | **CLI** | ✅ 已实现 | stdout JSON、管道友好 |
| pdf-pin-extract | **CLI** | ✅ 已实现 | search/extract/verify 三子命令 |
| schmd-from-netlist | **CLI** | ✅ 已实现 | map/infer 双命令 |
| keil-batch-gen | **CLI** | ✅ 已实现 (keil.py gen-*) | 模板渲染 |
| keil-init | **CLI (`keil setup`) + Skill 验证** | ✅ 已实现 setup_project() | 全自动 8 步，Agent 验证结果 |
| atk-logic-capture | **CLI (常规) + Skill (诊断)** | ✅ 已实现基础命令 | 常规采集用 CLI，异常诊断用 Skill |
| hardware-diff | **Skill** | N/A | 纯 Agent 分析 SOP |

### 迁移建议

**当前状态**: CLI 项目在 `E:\__electric\__workspace\stacking\eetool` 已实现设计文档中的全部功能。`

**建议动作**:
1. **更新 Skill 路由的 SKILL.md** — 将简单操作的入口从"Read sub-skill SKILL.md"改为"直接调用 eetool CLI"
2. **归档旧 Skill 子目录** — serial-monitor, pin2json, pdf-pin-extract, schmd-from-netlist, keil-batch-gen 的独立目录可删除或替换为 stub (指向 CLI)
3. **保留 2 个纯 Skill 子目录** — hardware-diff-by-codes (纯 SOP) 和 atk-logic-capture (复杂诊断场景)
4. **keil-init 可降级为 stub** — `eetool keil setup .` 已覆盖其 8 步流程，Skill 仅保留验证角色

---

## 八、结论

| 问题 | 答案 |
|------|------|
| CLI 和 Skill 哪个更好？ | **取决于操作类型** — 确定性的用 CLI，需 Agent 判断的用 Skill |
| CLI 是否已覆盖 Skill 功能？ | **已覆盖 6/8** — serial, pin2json, pin-extract, schmd, keil, capture 基础功能 |
| 不可覆盖的是哪些？ | **hardware-diff (纯分析)** 和 **capture 异常诊断 (上下文敏感)** |
| 两级嵌套问题能解决吗？ | **不能** — 是 Claude Code 技能发现的设计约束，不是 bug |
| 当前优先做什么？ | 更新 Skill 路由表，将已 CLI 化的子技能指向 `eetool` 命令而非 Read sub-skill |
