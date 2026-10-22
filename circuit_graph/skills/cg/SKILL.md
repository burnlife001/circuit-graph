---
name: cg
description: Use when analyzing electronic circuits — Protel .NET netlists, KiCad .kicad_sch, ERC checks, component knowledge base, power/communication context, or chip pinouts. Triggers on "R100 是什么", "C9 的作用", "这个元件干嘛用的", "为什么要有这个电阻", "电路分析", "netlist", ".NET file", "kicad_sch", "ERC", "稳压器链", "I2C 总线", "chip pinout", "元件数据库".
---

# cg (circuit-graph analysis)

Use the `cg` CLI from the **circuit-graph** project (`E:\__electric\__workspace\stacking\circuit-graph`) to analyze circuits instead of hand-parsing netlists. The CLI preserves 100% of the original IR — no information loss, no re-implementation of parsing.

## 0. Resolve the CLI FIRST (one probe per session)

`cg` is **often not on PATH**. Probing once saves a wasted round trip:

```powershell
$CG = (Get-Command cg -ErrorAction SilentlyContinue).Source
if (-not $CG) { $CG = "E:\__electric\__workspace\stacking\circuit-graph\.venv\Scripts\cg.exe" }
& $CG --help
```

Never call bare `cg` before this probe — on this machine it fails with `术语 'cg' 不会被识别`.

## 1. Fast path (default): one export, many questions

`cg view` prints the whole ego-subgraph as JSON (several KB per call). For exploration use the bundled query helper instead — one export per netlist, cached, then one short command per question.

The helper lives next to this file: `scripts/cgq.py` (skill dir = `<project>/.claude/skills/cg/`, canonical source = `E:\__electric\__workspace\stacking\circuit-graph\circuit_graph\skills\cg\scripts\cgq.py`).

```powershell
# resolve the skill dir (installed copy wins, else the repo copy)
$SK = "<project>\.claude\skills\cg"
if (-not (Test-Path "$SK\scripts\cgq.py")) {
    $SK = "E:\__electric\__workspace\stacking\circuit-graph\circuit_graph\skills\cg"
}

# first call: exports + caches the graph (~1 s), then answers
python $SK\scripts\cgq.py --netlist <file.NET> --conn C9

# later calls: cache hit, ~140 ms
python $SK\scripts\cgq.py --netlist <file.NET> --comp C9 R26 --net SW2
```

Cache lands in `d:/tmp/cg/` (never under `~/.claude`), keyed by netlist mtime+size, so it auto-invalidates when the netlist changes.

## 2. cgq.py cheat sheet

| Flag | Answers | Example |
|------|---------|---------|
| `--conn X` | X 的每个引脚接在哪些网、网上还有谁（带参数值） | `--conn C9` |
| `--comp X Y` | 位号 / 类型 / 阻容值 / 型号 / 引脚→网络(含 IC 引脚名) | `--comp C9 Q5` |
| `--net N` | 网络成员清单（带参数值） | `--net SW2` |
| `--find STR` | 按 型号/参数/位号 模糊找元件 | `--find SW5001` |
| `--all --type mosfet` | 列出全部某类元件 | `--all --type mosfet` |
| `--stats` | 元件/网络计数 | `--stats` |
| `--json` | 结构化输出（给脚本用） | 任意组合加 `--json` |

Worked example — "C9 的作用":
1. `--conn C9` → C9 接 GND 与 NetC9_2，NetC9_2 上只有 R26 → 得知 R26→SW2
2. `--net SW2` → SW2 上还有 Q5(SW5001) 的自举电容 C28、1nF(C12)、A 路输出电容组 → 得知 SW2 是开关节点
3. `--find 4.7nf` / `--comp C7 R25` → 发现 SW1/SW3/SW4 有完全对称的 R+C 吸收 → 判定为 RC 吸收网络

## 3. `cg view` — still the right tool for structure

```powershell
cg view R100 <file.NET> --pretty          # 2-hop ego subgraph (default)
cg view U2  <file.NET> --with-power       # 电源链 / 上游稳压器
cg view U2  <file.NET> --with-comm        # I2C / UART / SPI / PWM 总线
cg view U2  <file.NET> --with-all         # both + functional blocks
```

Use it when you need the *subgraph* or the *power/comm context layer*, not for listing parts. `--hops` > 2 floods the output with unrelated parts — don't.

## 4. Knowledge Base

```powershell
cg kb lookup <part_number>   # details + pin map + pin_source (IC pin NAMES live here)
cg kb search <keyword>       # full-text
cg kb pinout <part_number>   # pin map only
cg kb import protel <file.NET>   # ingest netlist, auto-resolves IC pins
cg kb import kicad <file.net>   # KiCad netlist ingestion
cg kb stats
```

KB lookup is the way to turn a net into a pin function (`SW2` → `SW5001 pin15 = SW2`).

## 5. ERC (KiCad `.kicad_sch` ONLY)

```powershell
cg erc check <file.kicad_sch>                       # native engine (4 validators)
cg erc check <file.kicad_sch> --engine kicad        # kicad-cli wrapper
cg erc check ... --format text --pretty             # human readable
cg erc check ... --severity error                   # filter
```

Exit codes: `1` if any ERROR, `0` otherwise — CI-friendly.

## 6. Setup in a fresh project (`cg init`)

```powershell
cg init                       # writes .cg.toml + KB init + installs bundled skills
cg init . --yes               # skip interactive confirm (CI / scripts)
cg init . --force             # overwrite existing .cg.toml + re-run sub-steps
cg init . --dry-run           # print the plan, no writes
cg init . --skip-skill        # only config + KB
```

`cg initskill <path>` installs every bundled skill under `<target>/.claude/skills/<name>/` and copies the bundle `README.md` routing table to `<target>/.claude/skills/README.md`. Re-runs are skipped without `--force`.

## 7. Performance rules (validated on T268-V6, 2026-10-22)

| Operation | Verdict | Why |
|-----------|---------|-----|
| `cg export graph` + query the JSON | **most effective** | one export serves every question; 140 ms/query |
| `cg kb lookup <part>` | effective | only way to get IC pin names |
| `cg view X --pretty` (2 hops) | effective but verbose | keep to 1–2 calls, for structure |
| `cg view X --hops 3` | ineffective | floods output with unrelated parts |
| bare `cg` without a PATH probe | ineffective | command-not-found round trip |
| regex-grepping the raw `.NET` | ineffective | the file has no designer comments; use the graph |
| one `cg view` per question | ineffective | re-parses the netlist every time |

## 8. Red Flags — Stop and Re-Route

- **Opening the .NET in a text editor** to figure out what a part does. Use `cgq.py --conn/--comp/--net`; the raw file is slow and error-prone.
- **Running `cg erc` on a Protel `.NET` file** — unsupported format, exits 2 with `unsupported schematic format: '.NET'`. Use `cg erc` only on `.kicad_sch`; for Protel, ingest via `cg kb import protel`.
- **Treating `cg sim` as if the designator matters** — Phase 1 PoC hardcodes an LM321 deck; the part-number you pass is **ignored**. Use it as an LM321 deck self-test, NOT as a simulation of the designator you passed.
- **Comparing IC `pins[].name` with passive `pins[].number` directly** — the KB only maintains pin names for ICs. Cross-reference IC pin **names → numbers**, never against passive numbers.
- **Power-context `--with-power` fallback means "not a bug"** — `source_kind: "unknown"` with `source_net_id: null` means the rail is internally generated or injected, NOT a power-tree tracing failure.
- **A capacitor on a switch node is not "optional decoupling"** — if it sits behind a series resistor (2Ω + 4.7nF), the pair is a snubber; the resistor is marked `2R/NC`, so if the resistor is not fitted the capacitor is left floating and the network is dead. Never drop one half of the pair.

## 9. Board facts

- **Default fixture**: `E:\__electric\__workspace\stacking\circuit-graph\fixtures\protel\FX-T268-V6-10米-5065.NET`
- **T268-V6 project netlist**: `E:\__electric\__workspace\coding\T268-V6\__docs\FX-T268-V6-10米-5065.NET` (identical to the fixture, md5 `3c83ed05…`)
- **SW5001 (Q5/Q7) switch-node snubbers** — every SW node carries a 2Ω + 4.7nF series RC to ground: SW1=R25+C7, SW2=R26+C9, SW3=R39+C31, SW4=R40+C33, plus a 1nF direct to ground (C10/C12/C34/C35) and a 100nF bootstrap (C27/C28/C51/C52).
