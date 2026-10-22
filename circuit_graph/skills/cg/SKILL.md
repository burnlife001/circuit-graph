---
name: cg
description: Use when analyzing electronic circuits — Protel .NET netlists, KiCad .kicad_sch, ERC checks, component knowledge base, power/communication context, or chip pinouts. Triggers on "R100 是什么", "C9 的作用", "这个元件干嘛用的", "为什么要有这个电阻", "电路分析", "netlist", ".NET file", "kicad_sch", "ERC", "稳压器链", "I2C 总线", "chip pinout", "元件数据库".
---

# cg — 电路查询（唯一入口）

`cg` 全局可用（`C:\Users\yg\.local\bin\cg.exe`）。**所有**电路信息只能经 `cg` 取得。

## 标准流程（目标 < 2 s）

```powershell
$NET = "<project>\__docs\FX-T268-V6-10米-5065.NET"
cg query $NET --conn R100     # ~220 ms：引脚 → 所在网 → 同网元件（带值）
cg query $NET --net SW2       # 需要时再展开一层（~220 ms）
```

拿到「引脚→网络→同网元件」就出结论：**类型/值 → 接在哪个网 → 同网还有什么 → 由此判断作用**（IC 引脚功能用 `cg kb lookup`）。

## 命令（每条 200–250 ms）

| 命令 | 用途 |
|---|---|
| `cg query $NET --conn X` | X 的引脚、所在网、同网元件（带参数值）— 首选 |
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
- ⏱ 2 秒预算：命令超 2 s 就是选错了，换 `--conn/--net`。
- `.SchDoc` cg 不支持；但先用 `cg query` 出结论，别去啃它。

## 板级快查（T268-V6）

- 网表：`E:\__electric\__workspace\coding\T268-V6\__docs\FX-T268-V6-10米-5065.NET`
- SW5001 (Q5/Q7) 开关节点吸收 2Ω+4.7nF 成对（SW1=R25+C7、SW2=R26+C9、SW3=R39+C31、SW4=R40+C33），拆一个即失效。
- 三路通道同一模板：开关节点→电容组→二极管→1.5k→MCU 采样（A:VDMOA / B:VDMOB / C:VDMO）。
