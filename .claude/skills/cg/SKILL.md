# cg — 电路查询（唯一入口）

`cg` 全局可用，所有电路信息只能经 `cg` 取得。

## 两档模式（默认 1 档）

| 档 | 触发 | 做法 |
|---|---|---|
| **1 档（默认）** | 任何「X 的作用 / X 是什么」 | 只跑一条 `cg role X $NET`，按结论直接回答。不补查拓扑，不输出查证过程 |
| **2 档（12345 全流程）** | 用户说「12345」「跑流程」「全流程」「深查」「看拓扑」；或 role 返回 fallback/low 且用户需要依据 | 一条命令跑脚本，脚本自动完成 1→5 步 |

规则：

- 1 档**只准**调用 `cg role`，命中规则就直接回答。
- 升级条件仅两种：用户明确要流程；或 role=fallback/low 且答案不成立。
- 2 档不逐条手敲 cg，统一走 `cg_flow.py`。脚本的 `[5]` 行给出 cbm 查询线索，agent 再执行一次 `cbm search_code` 完成闭环。

## 2 档脚本

```bash
python .claude/skills/cg/cg_flow.py R101
python .claude/skills/cg/cg_flow.py U7 --expand 5 --neighbors 10
python .claude/skills/cg/cg_flow.py R101 --net-file "__docs/xxx.NET"
```

- 不给 `--net-file` 时自动取当前项目第一个 `*.NET`
- `--expand N` 展开几个关键网（默认 3）；`--neighbors N` 查几个同网元件（默认 8）
- 输出顺序：`[1]` role 结论 → `[2]` 引脚→网 → `[3]` 对端元件另一端 → `[4]` KB 引脚 → `[5]` cbm 查询线索

## 命令（脚本不可用时手工兜底）

| 命令 | 用途 |
|---|---|
| `cg role X $NET` | **X 是干什么的**——规则引擎定性，答案前置 |
| `cg query $NET --conn X` | X 的引脚、所在网、同网元件（带参数值） |
| `cg query $NET --net N` | 某个网的全部成员 |
| `cg query $NET --comp X` | X 的引脚→网络（IC 带引脚名）；多个就重复 `--comp` |
| `cg query $NET --find STR` | 按型号/值/位号模糊找 |
| `cg query $NET --all --type mosfet` | 列出某类元件 |
| `cg query $NET --conn X --format json` | 结构化输出 |
| `cg kb lookup <part>` | IC 引脚名与功能（唯一来源） |

## 硬规则

- 🚫 **不读 `.NET`**：grep、编辑器、自写解析器都不行。网表解析只归 `cg`。
- 🚫 不用 `scripts/cgq.py`（`cg query` 已覆盖，同源更快）。
- 🚫 不用 `cg view --hops 3`（刷屏）。
- 🚫 不用 `cg sim <DESIGNATOR>`：Phase 1 PoC，deck 是硬编码的（Rf=10k / Rg=1k / VCC=5V），与本设计无关，输出不构成任何验证。
- 🚫 不跑 `cg --help` / `cg kb --help`：本表即完整命令表（仅当命令报错时才看）。
- 🚫 不靠「行业惯例」猜 IC 引脚功能（必须 `cg kb lookup`），也不靠惯例猜元件作用（必须看拓扑）。
- 网表硬数据优先：既有文档/笔记与 `cg query` 结果冲突时，以网表为准，并回头纠正那份文档。
- ⏱ 单条命令预算 5 s：超时就换 `--conn/--net`，或直接开 2 档脚本。
- `.SchDoc` cg 不支持；先用 `cg query` 出结论，别去啃它。
