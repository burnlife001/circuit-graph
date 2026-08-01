---
name: netlist-signalmap
description: 从 DXP/Altium 导出的网表(.NET)提取 MCU 各引脚的真实板上信号名,并用 __shared_docs 下匹配芯片的引脚功能 JSON 补全电源/模拟引脚,形成闭环。触发词:网表提取信号名, 提取板上信号名, 网表引脚映射, netlist signal name, 网表信号, .NET 提取, 引脚信号表, MCU 引脚信号, pin signal map, 板上信号名.
---

# netlist-signalmap

从 Protel/DXP/Altium 导出的网表(`.NET`)中提取某颗 MCU 每个引脚的**真实板上信号名**。
网表无法直接命名的引脚,工具**自动执行后继分析**(测试点 → 芯片JSON电源 → 串接追溯),
而不是甩给人工。仅当所有自动规则都失效时,才在依据列列出该网络的完整连接点供最终判断。

## 何时使用

- 用户给一个 `.NET` 网表,想得到 MCU 引脚号 → 板上信号名的对照表
- 想验证原理图引脚信号命名是否完整、是否有遗漏网络标签
- 没有人工整理的参考表时,纯靠网表 + 芯片数据手册 JSON 自动生成引脚信号表

## 网表格式约定(Protel/DXP 导出)

```
[                 (
U2                 VOL
LQFP-48            C6-1
MM32F014X-QFP48    R8-2
                   U2-10        ← <位号>-<引脚号> 连接点
]                  )
元件块:位号/封装/值   网络块:第一行=网络名, 其后=连接点
```

某引脚的板上信号名 = 含该 `<位号>-<引脚号>` 连接点的网络的网络名。

## 命名优先级(逐条降级,工具全自动完成,无需人工介入)

1. **网表显式网络标签**(非 `NetXxx_n`)→ 直接采用,设计者手工命名,最权威
2. **`NetXxx_n` 挂带名测试点**(封装 `TP*`,值字段是信号名)→ 用测试点值字段
   - 例:`NetT10_1` 挂 T10,T10 值=`TXD` → 信号名 = TXD
3. **`NetXxx_n` 且 JSON 判为电源引脚**(`type=="S"`)→ 用 JSON 主功能名(VDDA/VSS…)
   - 例:`NetC15_1`(只挂滤波 C/R),JSON 查得 pin9=VDDA → 信号名 = VDDA
4. **`NetXxx_n` 经串联 R/L 追溯到相邻具名网络** → 用相邻网络名,标注经哪个器件
   - 例:`NetU2_30 = {U2-30, R5-1}`,R5-2 在 `RS485_TX` → 信号名 = RS485_TX(经 R5 推断)
5. **引脚未出现在任何网络** → 留空(悬空 / NC),依据列注芯片功能
6. **以上全失效** → 保留 `NetXxx_n`,依据列以 `⚠` 打头**列出该网络全部连接点**供人工/AI判断

## 为什么需要 JSON

纯网表的极限:有人工网络标签 / 带名测试点 / 可串接追溯的引脚都能恢复;
但**纯电源/模拟引脚(VDDA/VSSA…)的名字属于芯片封装定义,网表无任何文字线索**,
必须靠芯片引脚功能 JSON 补全。两者合并才是完整闭环。

## __shared_docs 与芯片 JSON

- 脚本从网表所在目录**逐级上溯**,找到第一个 `__shared_docs/` 目录(不硬编码层级)
- JSON 由 `pdf-pin-extract` 技能从数据手册生成,关键字段:
  `chip_name`、`packages`、`pins[].name`、`pins[].type`(`S`=电源)、`pins[].packages.<封装>`=引脚号
- 芯片匹配:MCU 值字段与各 JSON `chip_name` 最长公共前缀(`MM32F014X` ↔ `MM32F0140`)
- 封装匹配:MCU 封装/值字段归一化后匹配 JSON `packages`(`LQFP-48`/`QFP48` ↔ `LQFP48`)

## 用法

```bash
# 全自动:当前目录 .NET,自动识别 MCU/封装,自动上溯 __shared_docs
python netlist_signalmap.py

# 指定参数
python netlist_signalmap.py path/to/board.NET --designator U2 --package LQFP48 \
       --shared-docs /path/to/__shared_docs --out U2_signalmap.md
```

Windows PowerShell 控制台中文乱码时设 `$env:PYTHONIOENCODING="utf-8"`(不影响输出文件,文件恒 UTF-8/LF)。

## 输出

`<designator>_signalmap.md` —— **单张复杂表**,每脚一行:

| 引脚号 | 芯片功能 | 类型 | 网表网络 | 板上信号名 | 依据/分析 |

末行汇总"仍需人工确认 N 脚";N=0 表示工具已全自动判定完成。
`依据/分析` 列对每个名字给出出处,`⚠` 行直接附带该网络连接点,审计/复核一目了然。

## 局限

- 仅识别 Protel/DXP 方括号+圆括号格式网表;其他 EDA 格式需另写解析
- 串接追溯只穿过两端 R/L,不穿 C(C 多为对地去耦);多级串接只追一跳
- JSON 缺对应封装/芯片时,电源引脚无法补全,退回 `NetXxx_n` 并进入 `⚠` 行
