---
name: eetool
description: Use when the user requests an electronics / embedded / EE workflow (serial port, logic analyzer, Keil build, PDF pin extraction, KiCad pin JSON, netlist signal map, hardware diff, project init) and you must pick the correct sub-skill. Triggers on 串口, 逻辑分析仪, ATK-Logic, Keil, pin assignment, AF table, pin2json, JLCPCB, 网表, 硬件对比, 电路板对比, 初始化 Keil, 引脚复用, 数据手册, MCU 引脚, hardware diff, same board.
---

# eetool — Electronics / Embedded Skill Router

`eetool` is a directory of independent sub-skills for the EE workflow. It does **no work itself** — its only job is to **route** the user's request to exactly one sub-skill via the `Skill` tool.

## Routing Table

Pick **one** row whose intent matches the user's request, then invoke the named sub-skill with the original args. Do not combine sub-skills.

| User intent / 触发词 | Sub-skill to invoke | What it does |
|---|---|---|
| 串口 / COM / CH340 / UART / USART / 抓取串口数据 / 发送指令 | `serial-monitor` | pyserial CH340 monitor, send/listen/list |
| 捕获波形 / 逻辑分析仪 / ATK-Logic / atkdl / 解码 UART / 测频率 / PWM 分析 | `atk-logic-capture` | ATK-Logic capture + analyze .atkdl |
| 初始化 Keil / 建项目 / 生成 CLAUDE.md / 禁区 / pre-commit 钩子 | `keil-init` | Full Keil project bootstrap |
| Keil 编译脚本 / __build.ps1 / __download.ps1 / 烧录脚本 | `keil-batch-gen` | Generate build/flash scripts only |
| 提取引脚 / pin assignment / AF table / 引脚复用 / 数据手册 PDF / 管脚分配 | `pdf-pin-extract` | PDF → pin JSON (+ optional Markdown) |
| pin2json / JLCPCB 料号 / KiCad 符号 → JSON / C9405 → 引脚名 | `pin2json` | KiCad .kicad_sym / LCSC id → JSON |
| 网表 / .NET / 板上信号名 / 引脚信号表 / 拓扑推断 | `schmd-from-netlist` | Protel/DXP netlist → pin signal map |
| 硬件对比 / 电路板对比 / same board / 固件是否同一 PCB / GPIO 寄存器差异 | `hardware-diff-by-codes` | Two firmwares → same/different PCB verdict |

## Invocation Pattern

**Sub-skills live one level below `eetool/` and are NOT auto-discovered by the `Skill` tool.** Claude Code's skill scanner only walks one level deep under `~/.claude/skills/`, so `Skill(skill="atk-logic-capture")` returns `Unknown skill`. The namespaced form `Skill(skill="eetool:atk-logic-capture")` also does not work for two-level nesting.

**Correct routing procedure** (one sub-skill per request):

1. Match the user's intent to exactly one row in the Routing Table above.
2. **Read the sub-skill's `SKILL.md` directly** with the `Read` tool — pass the original user request as `args` (forward verbatim, do not translate).
3. Follow the workflow inside that sub-skill's `SKILL.md` to completion.

```
Read tool:
  file_path = ~/.claude/skills/eetool/<sub-skill-name>/SKILL.md
  (Read it, then execute its workflow with the original user args)
```

Use the absolute path on Windows:

```
Read tool:
  file_path = C:/Users/yg/.claude/skills/eetool/<sub-skill-name>/SKILL.md
```

**Forward the user's request verbatim** — the sub-skill owns its own argument parsing and prompts. Do not pre-interpret or translate.

### Example routing

| User says | Router action |
|---|---|
| "帮我看下 COM7 的串口输出" | `Read(C:/Users/yg/.claude/skills/eetool/serial-monitor/SKILL.md)`, args="帮我看下 COM7 的串口输出" |
| "capture a 3 second waveform on CH2" | `Read(C:/Users/yg/.claude/skills/eetool/atk-logic-capture/SKILL.md)`, args="capture a 3 second waveform on CH2" |
| "把这个 MM32 项目初始化一下" | `Read(C:/Users/yg/.claude/skills/eetool/keil-init/SKILL.md)`, args="把这个 MM32 项目初始化一下" |
| "DS_MM32F0140_EN.pdf 提取引脚定义和复用表" | `Read(C:/Users/yg/.claude/skills/eetool/pdf-pin-extract/SKILL.md)`, args="DS_MM32F0140_EN.pdf 提取引脚定义和复用表" |
| "C9405 转成引脚 JSON" | `Read(C:/Users/yg/.claude/skills/eetool/pin2json/SKILL.md)`, args="C9405 转成引脚 JSON" |
| "对比这两个项目硬件是否相同" | `Read(C:/Users/yg/.claude/skills/eetool/hardware-diff-by-codes/SKILL.md)`, args="对比这两个项目硬件是否相同" |

### Fallback: Skill tool

If a future Claude Code version starts auto-discovering nested sub-skills, agents may try `Skill(skill="<name>")` first. If that returns `Unknown skill`, fall back to the `Read` tool pattern above. Do **not** retry the `Skill` call more than once.

## Disambiguation Rules

If the user's request matches more than one row:

1. **Netlist extraction + source code presence** → `schmd-from-netlist` (it auto-calls pin2json-style logic only when source pins are involved; do not pre-call `pdf-pin-extract`).
2. **Keil project bootstrap (everything)** → `keil-init`. Do not call `keil-batch-gen` separately — `keil-init` invokes it internally.
3. **"Generate build script only" (no CLAUDE.md, no hooks)** → `keil-batch-gen`.
4. **PDF + JSON question** → `pdf-pin-extract` (outputs JSON; Markdown only if `--package` is given).

If intent is still ambiguous after these rules, **ask the user** with a single `AskUserQuestion` listing the 2–3 plausible sub-skills — do not guess.

## When NOT to Use This Router

- The user already named the sub-skill (`/serial-monitor`, `/keil-init`, etc.) → call it directly, skip the router.
- The request is non-EE (git sync, code review, paper writing, v2rayn, etc.) → another skill handles it; `eetool` is silent.
- The request is "build / run" of an EE project that already has a working `__build.ps1` → just run the script directly; no skill needed.

## Sub-skill Locations

Each sub-skill lives as a sibling directory under `eetool/`:

```
~/.claude/skills/eetool/
├── SKILL.md                    ← this file (router, auto-discovered as "eetool")
├── serial-monitor/
│   └── SKILL.md                ← Read tool target, NOT auto-discovered
├── atk-logic-capture/
│   └── SKILL.md                ← Read tool target, NOT auto-discovered
├── keil-init/
│   └── SKILL.md                ← Read tool target, NOT auto-discovered
├── keil-batch-gen/
│   └── SKILL.md                ← Read tool target, NOT auto-discovered
├── pdf-pin-extract/
│   └── SKILL.md                ← Read tool target, NOT auto-discovered
├── pin2json/
│   └── SKILL.md                ← Read tool target, NOT auto-discovered
├── schmd-from-netlist/
│   └── SKILL.md                ← Read tool target, NOT auto-discovered
└── hardware-diff-by-codes/
    └── SKILL.md                ← Read tool target, NOT auto-discovered
```

The router does not read sub-skill contents — the **Read** tool loads them on demand. **Never duplicate sub-skill content into this router file.**

## Common Mistakes

| Mistake | Why it fails | Fix |
|---|---|---|
| Calling `Skill(skill="atk-logic-capture", ...)` directly | Sub-skill is nested two levels deep; Skill scanner only walks one level → `Unknown skill` | Use `Read` on `C:/Users/yg/.claude/skills/eetool/<sub-skill>/SKILL.md` instead |
| Manually running `python serial_monitor.py` instead of reading the sub-skill `SKILL.md` first | Skips preflight (venv activation, dependency checks, working dir) documented in the sub-skill | Always read the sub-skill `SKILL.md` and follow its workflow |
| Reading the sub-skill's `SKILL.md` but then re-running its steps yourself | Duplicates logic, drifts from canonical workflow, often misreads shell quoting on Windows | Trust the sub-skill `SKILL.md` — execute the exact commands it lists |
| Calling `pdf-pin-extract` then `pin2json` for a pin lookup | `pin2json` alone handles KiCad `.kicad_sym` and LCSC ids; PDF extraction is a separate, heavy task | Use `pin2json` unless the user explicitly asks for PDF extraction |
| Calling `keil-batch-gen` then `keil-init` for a fresh project | `keil-init` already calls `keil-batch-gen` internally | Call `keil-init` once |
| Inventing a path like `~/.claude/skills/eetool/serial-monitor.py` | Sub-skill scripts are not on PATH; they live under the sub-skill's own `scripts/` | Let the sub-skill `SKILL.md` tell you the exact script path to run |
| Routing a non-EE request through `eetool` because the keywords vaguely match | Wastes a tool call; the real handler is a different skill | Re-read the request; if it's not EE, do not invoke any sub-skill |

## Verifying the Route

After invoking the sub-skill, confirm it accepted the task:

- Sub-skill ran without an early "no matching intent" return → route was correct.
- Sub-skill returned a clarification question → relay it to the user verbatim; do not answer on the user's behalf.
- Sub-skill errored out → surface the error to the user; do **not** silently try another sub-skill.