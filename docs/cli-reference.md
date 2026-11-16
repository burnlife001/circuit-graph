# cg CLI 参考

**一行命令说清楚电路用途。** 主入口 `cg role` 输出元件作用和依据；其他命令用于核验、补充知识和导出分析结果。

## 命令树

```text
cg
├── role [位号] <网表>       单个元件作用；--all 解释整板各元件
├── query <网表>             连接、元件、网络查询
├── view <位号> <网表>       局部图与功能上下文
├── primitives coverage     模板覆盖率与未识别元件
├── kb                      引脚与元件知识库
├── export
│   ├── graph <网表>         JSON 分析图
│   └── graphml <网表>       GraphML 分析图
├── init [TARGET]            写入项目配置、初始化知识库
```

项目不提供设计、仿真、原理图生成、布局或规则检查命令。

## 通用约定

- 主分析链输入是 Protel 格式 `.NET` / `.net`，不接受原生 `.kicad_sch` 或 `.SchDoc`。KiCad 网表的元件导入使用 `cg kb import kicad`。
- 网表编码自动按 UTF-8、GBK、Latin-1 尝试。
- `--cache-dir PATH` 指定解析缓存，`--no-cache` 绕过缓存。
- `0` 表示成功，`1` 表示业务失败，`2` 表示用法或参数错误。
- 具体可用参数以 `cg <command> --help` 为准。

## `cg role`：直接解释作用

```powershell
cg role R100 board.NET
cg role R100 board.NET --format json
cg role --all board.NET
cg role --all board.NET --format json
```

首行回答“这个元件在这块板上做什么”，随后给出连接、引脚和相关元件依据。`--all` 一次解析后解释全部元件，不等于自动判定整机产品用途。

| 参数 | 说明 |
|---|---|
| `DESIGNATOR` | 元件位号，大小写不敏感；`--all` 时省略 |
| `FILE` | 网表路径 |
| `--all` | 解释整板各元件 |
| `--format [text\|json]` | 默认 `text` |
| `--db-path PATH` | 指定 SQLite 引脚知识库 |
| `--kb-source SOURCE` | 显式补全缺失引脚；不传时离线 |
| `--cache-dir PATH` / `--no-cache` | 缓存控制 |

多单元 IC 需使用拆分后的位号；查询整件时会提示可用单元。没有足够证据时报告未知或仅给出器件类别，不猜测用途。

### 可选的引脚补全

```powershell
cg role IC7 board.NET --kb-source datasheet
cg role IC7 board.NET --kb-source pin2json
cg role --all board.NET --kb-source pin2json
```

- `datasheet`：本地数据手册目录；`pin2json`：联网查询元件库并解析引脚。
- 也可显式传入单个型号的数据手册 URL；不能与 `--all` 合用。
- 单元件查询只补目标自身，不为被动元件隐式联网查询相邻芯片。
- 补全前校验型号、引脚和封装；失败如实报告，不暗中切换来源。
- 补全成功的数据写入知识库；stderr 给出成功/失败/跳过汇总，JSON 输出含 `kb_fill`。

## `cg query`：核对连接依据

```powershell
cg query board.NET --conn R100
cg query board.NET --comp U7
cg query board.NET --net NetR100_1
cg query board.NET --find 4.7nf
cg query board.NET --all --type capacitor
```

| 参数 | 说明 |
|---|---|
| `--conn TEXT` | 指定元件的引脚、网络与同网元件 |
| `--comp TEXT` | 元件摘要，可重复 |
| `--net TEXT` | 网络成员 |
| `--find TEXT` | 按位号、数值、型号检索 |
| `--all` / `--type TEXT` | 列出全部元件并按类型过滤 |
| `--format [text\|json]` | 默认 `text` |
| `--cache-dir PATH` / `--no-cache` | 缓存控制 |

## `cg view`：查看局部上下文

```powershell
cg view R100 board.NET --pretty
cg view R100 board.NET --with-all
```

| 参数 | 说明 |
|---|---|
| `--hops INTEGER` | 邻域跳数，默认 2 |
| `--with-power` | 供电路径 |
| `--with-comm` | 通信总线 |
| `--with-blocks` | 相关功能块 |
| `--with-all` | 上述三个上下文的简写 |
| `--max-members INTEGER` | 高扇出网成员折叠阈值，默认 20 |
| `--topology-timeout FLOAT` | 单模板匹配超时秒数 |
| `--pretty` | 缩进 JSON |
| `--cache-dir PATH` / `--no-cache` | 缓存控制 |

供电来源无法上溯时 `source_kind` 为 `unknown`。IC 引脚名来自知识库；被动元件只有引脚号和网络，不保证有引脚名称。

## `cg primitives coverage`：核对识别覆盖

```powershell
cg primitives coverage board.NET
cg primitives coverage board.NET --format json
```

报告模板命中与未被模板覆盖的元件。未被模板覆盖不等于 `cg role` 无法解释：用途推理还使用器件属性和通用连接规则。

## `cg primitives draft`：自动模板（机器闸门）

```powershell
cg primitives draft board.NET --chip U3
cg primitives draft board.NET --all
cg primitives draft board.NET --all --write
```

板 + 芯片位号 → 自动模板草稿。默认只出报告（stdout 一行一颗芯片：位号 / 型号 / state / 结论 / 耗时，完整报告写到 `output/auto-draft-report.md`），`--write` 才把通过机器闸门的模板写进 `primitives/auto/<型号slug>.json` 并重建 `primitives/__index_primitives.md`。

- `--all` 处理板上所有 IC 类元件；`--chip` 可重复，指定单颗。两者都不给等同 `--all`。
- 闸门：0 手工模板已覆盖该芯片 / 1 草稿 state 必须是 ready / 2 基准板上已被手工模板认领的元件不得改块归属 / 3 基准层答案不得指向别的具体模板。不通过的芯片只记原因，不写文件。
- 单颗芯片异常或回放超时不影响其它芯片，报告里给出原因。
- `--primitives-dir` / `--fixtures-dir` 指定模板库与基准板目录（测试用临时目录）。
- `--format json` 输出完整结构化结果。

## `cg export`：导出分析结果

```powershell
cg export graph board.NET -o output/board.graph.json --pretty
cg export graphml board.NET -o output/board.graphml
```

默认输出仓库 `output/circuit.graph.json` 或 `output/circuit.graphml`。两者支持 `--topology-timeout`、`--cache-dir`、`--no-cache`；只有 JSON 支持 `--pretty`。导出的是已有电路的分析图，不生成新的电路网表。

## `cg kb`：维护元件和引脚知识

全局 `--db-path PATH` 位于 `kb` 与子命令之间，用于独立测试库或项目库。

```powershell
cg kb init
cg kb lookup LM321
cg kb lookup LM321 --usage-in board.NET
cg kb pinout LM321
cg kb search Holtek
cg kb search --pin SW2
cg kb stats
cg kb import protel board.NET
cg kb import kicad board.net
cg kb resolve board.NET --dry-run
cg kb extract-pins --pdf datasheet.pdf --pages 3-5
```

- `init`：建库、升级 schema、迁移已有器件标签。
- `lookup` / `pinout` / `search` / `stats`：查询已存元件、引脚、属性、来源。
- `import protel|kicad`：导入网表器件，可用 `--symbol-dir PATH` 指定本地符号。
- `resolve`：补全引脚；`--dry-run` 只报告、不调用外部解析器。
- `extract-pins`：从本地 PDF 提取引脚，支持 `--part`、`--search`、`--pages` 和 `-o`。

**联网边界**：`cg kb import` 可能自动调用 JLCPCB 查询；指定本地符号目录不保证完全离线。默认离线承诺针对 `cg role`，不适用于所有 KB 写入命令。

PDF 提取使用 `DATASHEET_DIR` 与 `CAMELOT_VENV` 环境配置；Camelot 运行在独立环境，不属于主分析链必装依赖。

独立引脚工具：

```powershell
pin2json LM321 -p SOT-23-5
pin2json C9405
pin2json path/to/symbol.kicad_sym
```

## `cg init`：项目接入

```powershell
cg init --dry-run
cg init
cg init -y
```

`cg init [TARGET]` 检测网表、写入 `.cg.toml` 并初始化知识库。支持 `--skip-config`、`--skip-kb`、`-y/--yes`、`--force`、`--dry-run`；失败时返回非零退出码。它只处理项目配置与知识库，不分发 Claude skills 或 agents。

## 知识库维护脚本

- `scripts/import_pin_json.py`：导入核验过的引脚 JSON。
- `scripts/fill_descriptions_from_jlc.py`：显式联网补充器件描述，支持 `--dry-run`。
- `scripts/audit_descriptions.py`：审计描述质量。
- `scripts/build_primitive_index.py`：生成或检查识别模板索引。
