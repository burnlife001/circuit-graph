---
name: cg
description: Use when analyzing electronic circuits — Protel .NET netlists, KiCad .kicad_sch, ERC checks, component knowledge base, power/communication context, or chip pinouts. Triggers on "R100 是什么", "C9 的作用", "这个元件干嘛用的", "为什么要有这个电阻", "电路分析", "netlist", ".NET file", "kicad_sch", "ERC", "稳压器链", "I2C 总线", "chip pinout", "元件数据库".
---

# cg — 电路查询（唯一入口）

`cg` 全局可用（`C:\Users\yg\.local\bin\cg.exe`）。**所有**电路信息只能经 `cg` 取得。

## 默认最小路径（通常 1 次调用）

```powershell
$NET = "<project>\__docs\FX-T268-V6-10米-5065.NET"
cg role R100 $NET     # ~300 ms：一条命令出功能定性，直接照此回答
```

`cg role` 自动完成「--conn → 追另一端 → kb pinout → 拓扑推理」并答案前置。
**跑到这里就够了 —— 默认不再查别的。** 直接引用 role 的结论回答用户。

## 只有两种情况才继续调查

1. role 输出 **fallback**（未命中规则 / KB 无该 IC pinout）；
2. 结论可疑、或用户明确要求看证据 / 追更深。

| 步 | 命令 | 拿到什么 |
|---|---|---|
| 1 | `cg role X $NET` | 功能定性（反馈电阻/上下拉/分压/RC 滤波/去耦/耦合/shunt…，含增益、fc、分压比） |
| 2 | role fallback 或结论可疑时：`cg query $NET --conn X`（IC 用 `--comp X`） | 引脚 → 所在网 → 同网元件（带值） |
| 3 | `cg query $NET --net <网>` 或 `--comp A --comp B` | 同网其余成员的**另一端**接在哪 |
| 4 | `cg kb lookup <IC型号>` | IC 引脚功能（唯一来源） |
| 5 | 可选：`cbm search_code` 找固件里这个信号的用法 | 闭环验证（`|` 需显式 `regex=true`） |

- 步 3 里多个元件**一条命令查完**：`--comp A --comp B --comp C`，不要一个一个来。
- 互不依赖的 cg 调用**同一条消息并发发**，不要串行等。
- 判「作用」靠拓扑：先认全该网所有连接点的角色（电源 / 地 / 信号 / 开关节点 / 回流），再定性。

## 命令（每条 200–300 ms）

| 命令 | 用途 |
|---|---|
| `cg role X $NET` | **X 是干什么的**——规则引擎自动定性，答案前置 — 首选 |
| `cg query $NET --conn X` | X 的引脚、所在网、同网元件（带参数值）— role 兜底 |
| `cg query $NET --net N` | 某个网的全部成员 |
| `cg query $NET --comp X` | X 的引脚→网络（IC 带引脚名）；多个就重复 `--comp` |
| `cg query $NET --find STR` | 按型号/值/位号模糊找 |
| `cg query $NET --all --type mosfet` | 列出某类元件 |
| `cg query $NET --conn X --format json` | 需要结构化输出时 |
| `cg kb lookup <part>` | IC 引脚名与功能（唯一来源） |
| `cg view X $NET --pretty` | 仅当需要 2 跳子图结构 |

## 硬规则

- 🚫 **不读 `.NET`**：grep、编辑器、自写解析器都不行。网表解析只归 `cg`。
- 🚫 不用 `scripts/cgq.py`（`cg query` 已覆盖，同源更快）。
- 🚫 不用 `cg view --hops 3`（刷屏）。
- 🚫 不用 `cg sim <DESIGNATOR>`：Phase 1 PoC，deck 是硬编码的（Rf=10k / Rg=1k / VCC=5V），与本设计无关，输出不构成任何验证。
- 🚫 不跑 `cg --help` / `cg kb --help`：本表即完整命令表（仅当命令报错时才看）。
- 🚫 不靠「行业惯例」猜 IC 引脚功能（必须 `cg kb lookup`），也不靠惯例猜元件作用（必须看拓扑）。
- 网表硬数据优先：既有文档/笔记与 `cg query` 结果冲突时，以网表为准，并回头纠正那份文档。
- ⏱ 5 秒预算：命令超 5 s 就是选错了，换 `--conn/--net`。
- `.SchDoc` cg 不支持；但先用 `cg query` 出结论，别去啃它。
