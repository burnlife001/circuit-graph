---
name: schmd-from-netlist
description: 从 DXP/Altium 导出的网表(.NET)提取 MCU 各引脚的真实板上信号名,并用 __shared_docs 下匹配芯片的引脚功能 JSON 补全电源/模拟引脚;若网表所在项目存在源码(.c/.h),会自动扫描 GPIO 寄存器位宏并在输出中增加“源码标识”列,形成闭环。触发词:网表提取信号名, 提取板上信号名, 网表引脚映射, netlist signal name, 网表信号, .NET 提取, 引脚信号表, MCU 引脚信号, pin signal map, 板上信号名.
---

# schmd-from-netlist

从 Protel/DXP/Altium 导出的网表(`.NET`)中提取某颗 MCU 每个引脚的**真实板上信号名**,
对网表无法命名的电源/模拟引脚,自动寻找上级目录 `__shared_docs` 下匹配芯片的引脚功能 JSON 补全,形成闭环。

此外,如果网表所在目录附近存在项目源码(`.c`/`.h`),脚本会自动扫描其中形如 `#define CC1IO GPIOC->ODR_F.P13` 的 GPIO 寄存器位宏,在输出表中增加**源码标识**列,把原理图引脚直接关联到固件里的命名。

## 跑之前原封不动先念给用户
```plaintext
- 鉴于很多人SCH-PCB设计不同步,强制要求从原理图生成网表文件
- 除NC脚外,mcu的每一个引脚都放上你的网络标号,否则无法识别
- 禁止多个网络标号连接在一起
```

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

## 命名优先级(每个引脚逐条降级)

1. **网表显式网络标签**(非 `NetXxx_n`)→ 直接采用,这是设计者手工命名,最权威
2. **`NetXxx_n` 但网络里挂带名测试点**(封装以 `TP` 开头,值字段是信号名形式)→ 用测试点值字段
   - 例:`NetT10_1` 挂着 T10,T10 值字段=`TXD` → 板上信号名 = TXD
3. **`NetXxx_n` 且 JSON 判定为电源引脚**(`type` == `"S"`)→ 用 JSON 主功能名(VDDA/VSS/VDD…)← **闭环补全**
   - 例:`NetC15_1`(只挂滤波 C/R)JSON 查得 pin9=VDDA → 板上信号名 = VDDA
4. **`NetXxx_n` 但固件源码有明确 GPIO 宏名**(扫描到 `#define NAME GPIOx->REG.Pn` 命中本脚)→ 直接采用源码标识宏名 ← **源码闭环**
   - 例:`NetR27_2`(PC13,自动命名)固件里 `#define CC1IO GPIOC->ODR_F.P13` → 板上信号名 = CC1IO,来源标注 `源码标识(NetR27_2)`
5. **引脚未出现在任何网络** → 直接标记为 `NC`(板上悬空/未连接),无需用户确认
6. **其余无法判定** → 保留 `NetXxx_n` 并标注 `⚠ 必须拓扑推断`(下一步必须运行 `netlist_pin_infer.py`)

### 源码异名提示(ⓘ,非 ⚠)

当引脚走**优先级 1**(有原理图网络标签)、同时固件里又有 GPIO 宏命中本脚,但**两者不是同一信号**时,
在「来源」列追加 ` · ⓘ源码作 <宏名>` 的温和提示,**不升级为 `⚠`、不触发拓扑推断**。
典型是“SWD 调试线被复用为快充协议线”这类设计漂移(`SWDAT`↔`UNDATA`、`SWLCK`↔`UPDN`、`RXD`↔`CSCP`)。

「同一信号」判定(任一满足即视为一致、**不提示**):
- 归一化后互为子串:`SDA` ↔ `SDA_OUT`、`TX` ↔ `TXD`
- 归一化后最长公共前缀 ≥ 3:`T1PWMN` ↔ `T1PWM1N`

原理图网络名始终优先作为板上信号名,`ⓘ` 仅供人工最终确认,不改变取名结果。

## 关键认知:为什么需要 JSON

纯网表能做到的极限:有人工网络标签 / 带名测试点的引脚 100% 可恢复;
但**纯电源/模拟引脚(VDDA/VSSA…)的名字属于芯片封装定义,网表里没有任何文字线索**,
必须靠芯片引脚功能表(JSON)补全。两者合并才是完整闭环。

## __shared_docs 与芯片 JSON

- 脚本从网表所在目录**逐级上溯**,找到第一个 `__shared_docs/` 目录(无需硬编码层级)
- JSON 由 `pdf-pin-extract` 技能从数据手册生成,关键字段:
  `chip_name`、`packages`、`pins[].name`、`pins[].type`(`S`=电源)、`pins[].packages.<封装>`=引脚号
- 芯片匹配:用 MCU 值字段与各 JSON `chip_name` 的最长公共前缀(`MM32F014X` ↔ `MM32F0140`)
- 封装匹配:用 MCU 封装/值字段归一化后匹配 JSON `packages`(`LQFP-48`/`QFP48` ↔ `LQFP48`)

## 用法

```bash
# 全自动:当前目录下的 .NET,自动识别 MCU/封装,自动上溯 __shared_docs 与源码目录
# 输出 .md 与 .NET 放在同一目录
python netlist_signalmap.py

# 指定参数(含源码目录)
python netlist_signalmap.py path/to/board.NET --designator U2 --package LQFP48 \
       --shared-docs /path/to/__shared_docs \
       --source-dir /path/to/project_root \
       --out path/to/SCH-MM32F0140.md
```

Windows PowerShell 下若控制台中文乱码,设 `$env:PYTHONIOENCODING="utf-8"`(不影响输出文件,文件恒为 UTF-8/LF)。

## 输出

`SCH-<mcuname>.md`,与输入 `.NET` 同目录,只含一张表:
- **原理图**:`引脚号 | 芯片功能 | 板上信号名 | 来源` —— 便于审计每个名字怎么来的
- 若检测到项目源码,则增加 **`源码标识`** 列,形如 `CC1IO (GPIO/gpio.h:12)`,用于把芯片引脚直接对应到固件命名

未指定 `--out` 时,文件生成在 `.NET` 所在目录;指定 `--out` 时按指定路径。

## 源码标识自动扫描

- 脚本从网表目录**逐级上溯**,找到第一个包含 `.c`/`.h` 子目录的目录作为项目源码根
- 扫描源码中 `#define <NAME> GPIOx->REG.Pn` 形式的宏定义(覆盖 `GPIOA`~`GPIOD`, `P0`~`P15`)
- 若芯片引脚名如 `PC13` 与某宏的 `GPIOx.Pn` 匹配,则在该行填入 `NAME (relative/path:line)`
- 无源码或未匹配时,不添加该列,保持原有四列表格

## 待定脚功能推断(必须, netlist_pin_infer.py)

对没贴标号但**接了电路**的脚,网表的拓扑足以反推功能,这是闭环的强制下一步。两段式:脚本导拓扑,LLM 解读。

```bash
python netlist_pin_infer.py board.NET --out path/to/U2_pin_infer.md
```

- 输出每个待定脚的 **2 跳 BFS 拓扑指纹**(L0 本网→L1 邻件去向→L2 再下一级),
  电源/地、大网络自动截断。2 跳才能看穿"串电阻→MOSFET 漏极→CC 线"这类两级结构。
- 悬空脚(0 连接)已在主脚本中直接标记为 `NC`,**不再询问确认**;本脚本跳过悬空脚,不输出候选复用功能。
- 拿到拓扑后由 LLM 给出"疑似功能 + 依据 + 置信度"。实测能逐字还原设计者命名
- 不要在输出中包含任何无关信息,只输出疑似功能和推理依据

## 局限

- 仅识别 Protel/DXP 方括号+圆括号格式的网表;其他 EDA 网表格式需另写解析
- 优先级 6 的 `⚠ 必须拓扑推断` 引脚:既无网络标签、无测试点、非电源、固件里也无对应 GPIO 宏名,**必须运行 `netlist_pin_infer.py` 进行拓扑推断**,而非人工猜测
- JSON 缺失对应封装或芯片时,电源引脚无法补全,会退回 `NetXxx_n`
