# 电子工具 Skill 集

一套用于**嵌入式 / 单片机 / 原理图 / PCB** 日常开发的 Claude Code Skill 集合,
全部本地运行,与 Claude Code 一起解决从芯片 datasheet 解析到固件烧录的常见痛点。

每个 skill 是一个独立子目录,有自己的 `SKILL.md` 描述和 `scripts/` 实现。
本目录的根 `SKILL.md` 是**路由表**,Claude Code 会根据你的请求关键词自动选择对应 skill。

## 快速导航

| 我想做的事 | 用哪个 skill | 一句话说明 |
|---|---|---|
| 串口看打印 | `serial-monitor` | pyserial + CH340, 自动识别端口 |
| 抓波形 / 解码 UART | `atk-logic-capture` | ATK-Logic 逻辑分析仪,带协议解码 |
| 抓波形 (高阶) | `atk-logic-capture-pro` | 完整 12 脚本工具集, 多协议分析 |
| 初始化 Keil 项目 | `keil-init` | Keil 裸机/RTOS 项目脚手架 + 钩子 |
| 生成 Keil 编译脚本 | `keil-batch-gen` | 只生成 `__build.ps1` / `__download.ps1` |
| MM32 编译烧录 | `mm32-armgcc-build` | arm-gcc 编译 + JLink 烧录 MM32G0001 |
| 对比两块板子 | `hardware-diff-by-codes` | 固件反推 → 判断是否同一 PCB |
| 提取 datasheet 引脚 | `pdf-pin-extract` | PDF → 引脚 JSON (+ 可选 markdown) |
| 提取 datasheet 任意表格 | `pdf-table-reader` | PDF → 全部表格 → 单个 md (跨页合并) |
| JLCPCB / KiCad → JSON | `pin2json` | 元件型号/C-id/`.kicad_sym` → 引脚 JSON |
| 网表 → 引脚信号表 | `schmd-from-netlist` | `.NET` 网表 → 板上信号名 + 拓扑推断 |
| 通用网表信号映射 | `netlist-signalmap` | 上面的轻量版, 仅做信号映射不做拓扑 |

## 按工作流分类

### 1. 上手 (入门)
- **`keil-init`** — 拿到新项目, 一键生成 Keil 编译/下载脚本、CLAUDE.md、pre-commit 钩子
- **`serial-monitor`** — 板子接上 CH340 立刻能看打印, 不需要写串口工具

### 2. 硬件资料解析 (芯片 datasheet 痛点)
- **`pdf-pin-extract`** — 拿到新芯片 PDF, 5 分钟拿到引脚定义 + 复用表 JSON
- **`pdf-table-reader`** — 任意 PDF (不只引脚表) 提取所有表格, 跨页自动合并
- **`pin2json`** — 已有 KiCad 符号 / JLCPCB 料号, 转成统一 JSON
- **`schmd-from-netlist`** — 原理图导出的 `.NET` 网表 → 每个引脚的真实板上信号名
- **`netlist-signalmap`** — 同上, 轻量版, 无拓扑推断

### 3. 调试 / 抓数据
- **`serial-monitor`** — 看串口日志、发送指令
- **`atk-logic-capture`** — 抓逻辑分析仪波形, 解码 UART/SPI/I2C
- **`atk-logic-capture-pro`** — 上面那个的完整版, 12 个脚本

### 4. 编译 / 烧录
- **`keil-init`** — 初始化 (含编译脚本)
- **`keil-batch-gen`** — 只生成 `__build.ps1` / `__download.ps1`
- **`mm32-armgcc-build`** — MM32 系列专门的 arm-gcc + JLink 烧录流

### 5. 硬件对比 / 固件验证
- **`hardware-diff-by-codes`** — 对比两份固件, 判断是否同一块板子 (GPIO 寄存器级别)
- **`schmd-from-netlist`** — 含拓扑推断, 给引脚信号名加问号时反推电路

## 怎么用

Claude Code 启动后, 直接说自然语言就行, 例如:
- "帮我看下 COM7 的串口输出"
- "把这份 STM32 datasheet 提取引脚表"
- "对比这两个项目的固件是否同一块板子"

Claude Code 读本目录的 `SKILL.md` 路由表 → 自动选子 skill → 读子 skill 的 SKILL.md → 执行。

如果你知道 skill 名, 可以直接说:
- `/keil-init` `/pdf-pin-extract` `/schmd-from-netlist` 等

## 子 skill 目录结构

每个子 skill 都是:
```
<skill-name>/
├── SKILL.md          # 触发词、用法、输出、局限
└── scripts/          # 实现脚本
```

主 SKILL.md 路由表见 [SKILL.md](./SKILL.md) (本目录根)。

## 设计原则

- **单职责**: 每个 skill 只做一件事, 不跨领域
- **本地优先**: 所有处理在本机, 不调用云 API (除可选 LLM 辅助)
- **结构化输出**: 全部输出结构化 markdown / JSON, 便于二次处理
- **可串联**: 多个 skill 可流水线配合 (e.g. `pdf-pin-extract` → `pin2json` → `schmd-from-netlist`)
- **依赖最小**: 多数 skill 只用 stdlib, 需要 PDF 库 / JLink 的明确写在 SKILL.md

## 维护

新增 skill 时:
1. 在本目录建 `新skill名/SKILL.md` + `scripts/`
2. 在 [SKILL.md](./SKILL.md) 路由表加一行 + 在目录树加一行
3. 在本 README 加一行
4. 跑一次实测, 确认触发词命中

详细路由规则见 [SKILL.md](./SKILL.md)。
