# circuit-graph (Python)

Python 迁移版电路理解平台，保持原有 TypeScript IR 语义，以 cg-graph 电路 JSON 为核心输出。

## 功能

- 解析 Protel .NET 网表（`[...]` 元件块 / `(...)` 网络块）
- 构建组件 / 引脚 / 网络 / 功能块 异构图（NetworkX）
- 数值标准化、网络分类、引脚名补全、拓扑识别增强器（按 priority 串行 + `requires` 依赖）
- 元件追踪、网络追踪、邻居查询、BFS/DFS 遍历、引脚间最短路径
- 拓扑识别：**基元库 + VF2 子图匹配 + 四源融合评分**（structural / naming / parametric / type）；新增拓扑 = 追加 `primitives/*.json`
- 元件型号知识库：**SQLite 数据库**（`circuit_graph/knowledge/circuit_graph.db`）维护 part-number → 类别 / 引脚定义 / 属性
- PDF 数据手册引脚提取：Camelot lattice 模式自动搜索引脚表页面，提取 `{引脚号: 引脚名}` 映射
- 供电上下文（`cg view --with-power`）：从 USB / 电池 / 排针 → 稳压器 → 负载
- 通信上下文（`cg view --with-comm`）：检测 I2C / UART / SPI / PWM 总线
- 生成 cg-graph JSON（v2.1）：含 `part_number`、`circuit.rails`、每条 net 带 `degree`
- 生成单个元件的 ego-subgraph 视图（默认 2 hops，可调；高扇出轨线不下钻）
- 导出 yEd 兼容 GraphML
- ERC 引擎：`cg erc check <schematic>` 调原生检查器（4 个 validator：PinType / Connectivity / Component / Power），或 `--engine kicad` 调 `KicadCliRunner`（封装 `kicad-cli sch erc --format json`）

## 安装

```powershell
uv venv
uv pip install -e ".[dev]"
```

安装后 `.venv\Scripts\cg.exe` 会加入 PATH（新 PowerShell 窗口生效）。

## 快速开始

默认输出目录为项目根目录的 `output/`，与当前工作目录无关。

```powershell
# 生成 cg-graph JSON（默认 compact，--pretty 可人工阅读）
# 输出：output/circuit.graph.json
cg export graph fixtures/protel/FX-T268-V6-10米-5065.NET

# 导出 GraphML
# 输出：output/circuit.graphml
cg export graphml fixtures/protel/FX-T268-V6-10米-5065.NET

# 生成单个元件的 ego-subgraph 视图（cg-graph 格式，--hops 可调，默认 2）
cg view R100 fixtures/protel/FX-T268-V6-10米-5065.NET --pretty

# 在 view 输出中附加供电上下文
# 会显示 focal 元件所在 rail、上游 source、稳压器链
cg view U2 fixtures/protel/FX-T268-V6-10米-5065.NET --with-power

# 在 view 输出中附加通信总线上下文
cg view U2 fixtures/protel/FX-T268-V6-10米-5065.NET --with-comm

# 同时附加供电 + 通信上下文
cg view U2 fixtures/protel/FX-T268-V6-10米-5065.NET --with-all
```

> **`--with-power` fallback 行为**：当 focal 元件所在 rail 不是 `power_tree_recognizer` 已登记的输入源（USB / BAT / VIN / 排针等）或下游稳压器时，`power_context` 给出明确 fallback：
> ```json
> {
>   "rail": "5V2",
>   "source_net_id": null,
>   "source_net": null,
>   "source_kind": "unknown",
>   "upstream_regulators": []
> }
> ```
> `source_kind: "unknown"` 表示 **power_tree 上溯失败**（不是 bug，也不是元件没接电）；`source_net_id: null` 表明该 rail 内部生成或来自外部注入。如果要扩展识别范围（覆盖 5V2 / 3V3 / VBAK 等内部 rail 与稳定器），改 `_resolve_power_path` / `power_tree_recognizer` 的源 net 集合。

> **`cg view` 输出 pins[].name 行为差异**：
> - **IC 元件**（U 类）：`pins[]` 自动补 KB 中的引脚名（`+` / `GND` / `-` / `OUT` / `V+` 等）。例如 U7 (LM321) 输出 `{"number": 1, "name": "IN+", ...}`。
> - **被动元件**（R / C / L / D）：`pins[]` 只含 `number` + `net`，**没有 `name` 字段**，因为 KB 不为这类元件维护 pin name（例如 R101 输出 `{"number": 1, "net": "NetR100_1"}`，没有 `"name"`）。
> 这意味着做交叉对比时不要把 IC 的 `name` 与 passive 的 `number` 直接映射；如果要按引脚功能过滤，只在 IC 上用 `name`。
# ERC（Electrical Rules Check）— 原生检查器（默认 engine=native，无需 KiCad 安装）
# 检查 4 类违规：引脚类型冲突、未驱动电源、重复 Reference、未连接输入
cg erc check path/to/schematic.kicad_sch

# JSON 输出（默认），写入文件
cg erc check path/to/schematic.kicad_sch --format json -o output/erc.json

# 文本表格 + 颜色（ERROR 红色 / WARNING 黄色，非 TTY 自动禁用颜色）
cg erc check path/to/schematic.kicad_sch --format text --pretty

# 只看 error，过滤掉 warning
cg erc check path/to/schematic.kicad_sch --severity error

# 切换到 KiCad 官方 ERC 引擎（需 kicad-cli 可用）
cg erc check path/to/schematic.kicad_sch --engine kicad
```

> **输入格式**：仅支持 KiCad 9 S-expression `.kicad_sch`，**不支持 Protel `.NET`**。
> 把 Protel 网表喂给 `cg erc check` 会直接退出码 2 + 提示 `unsupported schematic format: '.NET'`。
> Protel 网表请走知识库管线：`cg kb import protel fixtures/protel/FX-T268-V6-10米-5065.NET`。

`KicadCliRunner` 按 `KICAD_CLI_PATH` → `D:/Programs/KiCad/*/bin/kicad-cli.exe` → PATH 中 `kicad-cli` 的顺序定位 KiCad CLI。

退出码：有 error 时 `exit(1)`，否则 `exit(0)`，可直接接 CI。

错误代码（稳定标识，可作过滤器）：

| 代码 | 含义 | 来源 |
|------|------|------|
| `E001` | `pin_conflict` — 引脚类型冲突 | PinTypeValidator |
| `E002` | `duplicate_reference` — 重复 Reference | ComponentValidator |
| `E003` | `power_pin_not_driven` — POWER_IN 未被 POWER_OUT 驱动 | PowerValidator |
| `W001` | `pin_conflict_warning` — 引脚类型警告 | PinTypeValidator |
| `W002` | `single_pin_net` — 单 pin net | ConnectivityValidator |
| `W003` | `unconnected_input` — INPUT 引脚未连接 | ConnectivityValidator |
| `W004` | `missing_value` — 元件缺 Value | ComponentValidator |
| `I001` | `no_power_flag` — 电源 net 缺 PWR_FLAG | PowerValidator（预留） |

`view` 直接解析 `.NET` 网表并构建内部图，不依赖任何预生成的 JSON。

## 仿真 (Phase 1)

```powershell
# 运行仿真（.OP 直流工作点）
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET

# 生成 SPICE deck 预览（不运行 Ngspice）
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET --dry-run

# 导出 deck 到文件
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET --dry-run -o lm321_amp.cir

# 指定分析类型
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET --analysis tran

# 传递 Ngspice 收敛选项
cg sim R100 fixtures/protel/FX-T268-V6-10米-5065.NET --ngspice-opt gmin=1e-9
```

> **⚠️ Phase 1 PoC 警告**：`cg sim` **忽略输入的 designator**，始终跑硬编码的 LM321 deck。
> 上面的 `R100` / `Rf=10k / Rg=1k / VCC=5V` 都是占位用 deck 的固定参数，**与 `R100` 实际所在的电路无关**。
> 真正的网表驱动仿真（含按 topology 选 deck）会在 Phase 3 落地。
> 现在把 `cg sim` 当作 LM321 deck 自检工具，而不是对所传 designator 的行为验证。

Phase 1 使用硬编码的 LM321 同相放大器电路（Rf=10k, Rg=1k, VCC=5V）。
直流工作点验证：`V(in_p)=0.5V, V(in_n)≈0.5V（虚地）, V(out)≈5.5V`，增益 ≈ 11.0（理论值 `1+Rf/Rg=11`）。

Ngspice 安装：从 [SourceForge](https://sourceforge.net/projects/ngspice/files/ng-spice-rework/46/ngspice-46_64.7z/download) 下载，解压后 `Spice64\bin\ngspice_con.exe` 加入 PATH（控制台版，无 GUI 弹窗）。

## 知识库 CLI

```powershell
# 初始化 / 升级 SQLite 数据库，并迁移 vendor_db.json
cg kb init

# 查询单个元件（含引脚、属性、pin_source）
cg kb lookup JW5065

# 全文搜索
cg kb search Holtek

# 只看引脚映射
cg kb pinout HT7533

# 从 Protel .NET 网表导入型号 + 引脚
cg kb import protel fixtures/protel/FX-T268-V6-10米-5065.NET

# 从 KiCad 网表导入型号 + 引脚功能
cg kb import kicad fixtures/kicad/FX-T268-V6-10米-5065-kicad.net

# 导入时自动解析所有 IC 引脚（调用 chip_resolver），标记 pin_source
# 也可事后手动执行：
cg kb resolve fixtures/protel/FX-T268-V6-10米-5065.NET

# 数据库统计
cg kb stats
```

> **⚠️ Import 网络副作用**：`cg kb import {protel,kicad}` 会在每个识别为 IC 的元件上自动调用 **chip_resolver**：
> 1. 查 `jlc search --json`（**对外网 JLCPCB 服务**）；
> 2. 命中后用 JLC2KiCadLib 生成 KiCad 符号 / 直接调用 `pin2json`（**本地**）。
> 运行期间会看到形如 `[pin2json] no symbol match for LCSC id C107412 (part CON_2P_1.2MM)` 的 warning —— 这是**正常**未匹配项，不是错误。
> 离线环境预案：
> - `cg kb resolve --dry-run` 只解析、**不调用 pin2json**（仍会查 KB + JLCPCB）；
> - `cg kb import --symbol-dir <path>` 优先匹配本地 `.kicad_sym` 减少 JLCPCB 调用；
> - 完全离线模式尚未提供；若网络超时，目前行为是 warning 然后跳过（不会破坏 import）。

### 从 JLCPCB 补全 description

`components.description` 在导入网表时不会自动填入。`scripts/fill_descriptions_from_jlc.py`
会遍历所有 `description` 为空的行，对每个 `part_number` 调用 `jlc search … --json`，
取首个命中结果的 `description` 字段回写。

- 幂等：已有 `description` 的行不会改写
- 自动跳过乱码 / 过短的不可搜索型号
- `--dry-run` 只打印，不入库
- `--limit N` 限制处理条数（冒烟测试）
- 默认 0.4s 节流，76 行约 30–60s

```powershell
# 干跑预览（不改库）
python scripts/fill_descriptions_from_jlc.py --dry-run --limit 5

# 全量补全
python scripts/fill_descriptions_from_jlc.py

# 自定义节流
python scripts/fill_descriptions_from_jlc.py --sleep 1.0

# 单条重取（修复某行的乱码 / 过时数据）
python scripts/fill_descriptions_from_jlc.py --id 32

# 强制覆盖已有 description（谨慎使用）
python scripts/fill_descriptions_from_jlc.py --force
```

### 扫描 description 字段质量

`scripts/audit_descriptions.py` 分类所有 88 行 `components.description`，
输出 6 类：mojibake / placeholder / jlc / vendor_label / empty / suspicious。

```powershell
# 默认：打印所有可疑分类的明细
python scripts/audit_descriptions.py

# 机器可读
python scripts/audit_descriptions.py --json
```

分类规则：

| 类别 | 含义 |
|------|------|
| `mojibake` | 含 `??` 且夹杂非 ASCII 字节的乱码（解码失败留下的 `?`） |
| `placeholder` | 纯 `?` 或 50%+ `?` 的字符串（如 `USB???`、`?????`） |
| `jlc` | JLC 标准描述（≥30 字 + 单位/ROHS/Independent 关键词） |
| `vendor_label` | `vendor_db.json` 里的短分类标签（`synchronous_buck` 等） |
| `empty` | NULL 或空串（`fill_descriptions_from_jlc.py` 已尝试 76 行） |
| `suspicious` | 上述都不满足 — 含 JLC 的极简返回、手写通用词（`Connector`）、混合 `?` 的损坏字段 |

依赖：`npm install -g @jlcpcb/cli`（`jlc search --json`）。

### chip_resolver — IC 引脚自动解析

导入网表时，对识别为 IC 的元件自动调用 **chip_resolver** 引擎：

1. 搜索 JLCPCB 获取 LCSC 料号
2. 通过 JLC2KiCadLib 生成 KiCad 符号
3. 提取引脚定义 + 语义分类入库

解析成功的数据标记 ``pin_source=jlcpcb``，表示来自 JLCPCB 官方符号库。
已有 credible 数据的部件跳过重复解析，避免不必要的网络请求。

`pin2json` CLI 可独立使用（`-p` 指定封装，`--lcsc` 直传 C-id）：

```powershell
# 搜索 + 解析
pin2json LM321 -p SOT-23-5

# 已知 C-id 直接解析
pin2json C9405

# 解析已有 .kicad_sym 文件
pin2json path/to/symbol.kicad_sym

# 保存到文件
pin2json LM321 -p SOT-23-5 output.json
```

### PDF 数据手册引脚提取

当 JLCPCB 批量解析失败时，`pdf_pin_extract` 模块直接从 MCU 数据手册 PDF 中提取引脚表。

**依赖**：Camelot 外部 venv（`CAMELOT_VENV`），与项目 venv 分离，通过 `subprocess` 通信。

**配置**（`.env`）：
```
DATASHEET_DIR=E:\__electric\source\datasheet_um
CAMELOT_VENV=E:\__electric\tools\camelot\.venv
```

**编程接口**：

```python
# ... (同上)
```

**CLI 用法**：

```powershell
# 从 PDF 直接提取
cg kb extract-pins --pdf "E:\__electric\source\datasheet_um\mcus\MM32F0140\DS_MM32F0140_EN.pdf"

# 按型号自动查找数据手册
cg kb extract-pins --part MM32F0140

# 仅检测引脚表页面
cg kb extract-pins --pdf DS_MM32F0140_EN.pdf --search

# 保存到 JSON 文件
cg kb extract-pins --pdf DS_MM32F0140_EN.pdf -o mm32_pins.json
```

**处理流程**：
1. `search_pin_pages` — pypdfium2 关键词匹配（"pin assignment", "引脚定义" 等），结合引脚名正则（`P[A-H]\d+`）区分表格页与引脚图
2. `extract_pin_table` — Camelot `lattice` 模式提取表格，多页自动合并（按列签名分组），列名匹配 + 启发式回退
3. 返回 `dict[str, str]`（引脚号 → 引脚名）

**提取示例**（MM32F0140 LQFP48，48 pins）：

| Pin | Name          | Pin | Name       |
|-----|---------------|-----|------------|
| 1   | NC            | 25  | PB12       |
| 2   | PC13          | 26  | PB13       |
| 8   | VSS           | 31  | PA10       |
| 9   | VDDA          | 32  | PA11       |
| 10  | PA0 WKUP      | 44  | PD5 BOOT0  |
| 24  | VDD           | 48  | VDD        |

## 原理图布局

`circuit_graph.layout` 模块调用 `kicad-sch-api` 自动布局 KiCad 原理图。从 `CircuitIR` + per-unit JSON 配置出发，自动完成信号流向分析的元件放置、正交布线、无重叠标签和 T 节点添加。

```python
from circuit_graph.layout import layout_schematic
import kicad_sch_api as ksa

ir = ...  # 构建或加载 CircuitIR
sch = ksa.create_schematic("my_design")
layout_schematic(ir, sch, unit_configs={"main": "path/to/unit.json"})
sch.save("my_design.kicad_sch")
```

Per-unit JSON 配置文件放在 `circuit_graph/layout/configs/`。复制 `default.json` 或 `lm321_ccs.json` 作为起点。

### LM321 CCS 示例

`lm321_ccs/convert_to_kicad.py` 默认使用 `circuit_graph.layout` 自动生成 `1A_CCS_LM321_BJT.kicad_sch`，基于 `ccs_v5_bjt.cir`：

```powershell
# 自动布局（默认）
python lm321_ccs/convert_to_kicad.py

# 指定输出路径
python lm321_ccs/convert_to_kicad.py output/my_ccs.kicad_sch

# 手工布局版本（已重新整理为左控制电路 / 右功率通路，正交布线，位号/数值 0°）
python lm321_ccs/convert_to_kicad.py --manual
```

自动布局流程：

1. `_ir_from_spice` 把 `ccs_v5_bjt.cir` 解析为 `CircuitIR`（过滤测量源、合并 0V 电流表节点）。
2. `layout_schematic` 结合 `lm321_ccs.json` 完成元件放置、正交布线、参考/值标签和 T 节点。
3. `_wire_power_and_inputs` 在布局后补全 `+12V` / `GND` 电源符号和 `N_REF` 等被过滤源的标签/连线。

`--layout` 仍可作为兼容参数传入，但已不再需要。

### 模块架构

```
circuit_graph/layout/
├── __init__.py          # layout_schematic 入口
├── config.py            # LayoutConfig 数据类和 JSON 加载器
├── signal_graph.py      # 信号流有向图构建和列分配
├── placer.py            # 元件 (x, y, rotation) 分配
├── wire_placer.py       # 正交线布线（贪心直线斯坦纳树）
├── label_placer.py      # 无重叠 0° 参考/值标签
├── geometry.py          # 引脚绝对位置（旋转+平移）
├── unit_layout.py       # 单 unit 编排：图→放置→布线→标签
├── decomposer.py        # CircuitIR → LayoutUnit 分解（v1: 单 unit）
├── page_layout.py       # 页面上放置 unit（v1: 透传）
├── kicad_writer.py      # 将结果应用到 ksa.Schematic
└── configs/
    ├── default.json      # 默认布局参数
    └── lm321_ccs.json    # LM321 CCS per-unit 覆盖
```

## 人性化观察

`output/pretty.ps1` 提供菜单，把 `output/` 里的 `.json` 转成带缩进的 `<filename>_human.json`，方便直接阅读。

```powershell
cd output
.\pretty.ps1
```

## 测试

```bash
pytest
```

或显式使用虚拟环境：

```powershell
.venv\Scripts\python -m pytest
```

## 项目结构

```
circuit_graph/
├── ir.py                       # 核心 IR 数据模型（唯一权威）
├── graph.py                    # NetworkX 异构图
├── query.py                    # trace / neighbors / bfs / dfs
├── cli.py                      # 命令行入口（cg ...）
├── primitives.py               # 基元库加载 + 二部图编码
├── vf2_matcher.py              # VF2 子图匹配（带超时）
├── scoring.py                  # 四源融合评分
├── power_tree_recognizer.py    # 供电上下文识别
├── comm_topology.py            # 通信总线识别（I2C/UART/SPI/PWM）
├── visualization.py            # 供电树 SVG 渲染
├── parsers/protel.py           # Protel .NET 解析器
├── enrichers/                  # value_normalizer / net_classifier / pin_resolver / topology_recognizer
├── generators/cg_graph.py       # cg-graph JSON 生成器（v2.1）
├── exporters/graphml.py        # yEd 兼容 GraphML
├── layout/                     # 原理图自动布局引擎
│   ├── __init__.py              # layout_schematic 公共 API
│   ├── config.py                # LayoutConfig + JSON 加载器
│   ├── signal_graph.py          # 信号流有向图 + 列分配
│   ├── placer.py                # 元件 (x, y, rotation) 放置
│   ├── wire_placer.py           # 正交布线
│   ├── label_placer.py          # 无重叠标签
│   ├── geometry.py              # 引脚绝对位置
│   ├── unit_layout.py           # single-unit 编排
│   ├── decomposer.py            # CircuitIR → LayoutUnit
│   ├── page_layout.py           # unit 页面排布
│   ├── kicad_writer.py          # 应用到 ksa.Schematic
│   └── configs/                 # per-unit JSON 配置
├── knowledge/                  # SQLite 知识库 + 迁移/导入/种子工具
│   ├── db.py                   # ComponentDatabase（核心 API）
│   ├── chip_resolver.py         # chip_resolver 引脚解析引擎（JLCPCB + KiCad 符号）
│   ├── pdf_pin_extract.py       # PDF 数据手册引脚提取（Camelot lattice）
│   └── vendor_db.json          # 遗留 JSON，首次 `cg kb init` 时迁移入 SQLite
└── models/power_tree.py        # 供电树数据模型

primitives/                     # 拓扑基元模板 JSON（新增拓扑 = 追加 *.json）
tests/                          # pytest 测试
output/pretty.ps1               # JSON 美化菜单脚本
```

CLI 命令速查：

| 命令 | 说明 |
|------|------|
| `cg export graph <file>` | 生成 cg-graph JSON（`output/circuit.graph.json`） |
| `cg export graphml <file>` | 生成 yEd GraphML（`output/circuit.graphml`） |
| `cg view <id> <file> [--hops N] [--pretty]` | 元件 ego-subgraph（默认 2 hops） |
| `cg view <id> <file> --with-power` | 附加供电上下文 |
| `cg view <id> <file> --with-comm` | 附加通信总线上下文 |
| `cg view <id> <file> --with-all` | 同时附加供电 + 通信上下文 |
| `cg erc check <schematic> [--engine native\|kicad]` | ERC 检查（默认原生引擎，4 个 validator） |
| `cg kb init` | 初始化 / 升级 SQLite 知识库 |
| `cg kb lookup <part_number>` | 查询元件 + 引脚 + 属性（含 `pin_source`） |
| `cg kb search <keyword>` | 全文搜索 |
| `cg kb pinout <part_number>` | 只看引脚映射 |
| `cg kb import protel <path>` | 从 Protel .NET 网表导入（自动解析 IC 引脚） |
| `cg kb import kicad <path>` | 从 KiCad 网表导入（自动解析 IC 引脚） |
| `cg kb resolve <netlist> [--symbol-dir ...]` | 批量解析未命名引脚并入库 |
| `cg kb stats` | 数据库统计 |
| `cg kb extract-pins --pdf <path>` | 从 PDF 数据手册提取引脚名（Camelot） |
| `cg kb extract-pins --part <型号>` | 按型号自动定位数据手册并提取引脚 |
| `cg kb extract-pins --pdf <path> --search` | 仅检测引脚表所在页面 |
| `cg kb extract-pins --pdf <path> -o pins.json` | 提取并保存到文件 |
| `pin2json <query> -p <package>` | 独立 pin2json CLI（JLCPCB 搜索 + 引脚提取） |
| `pin2json --lcsc <C-id>` | 从 C-id 直接解析 |
| `python scripts/fill_descriptions_from_jlc.py` | 从 JLCPCB 批量补全 `components.description` 空字段 |
