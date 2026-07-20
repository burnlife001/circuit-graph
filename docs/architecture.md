# circuit-graph 架构说明

> 目标：一行命令看穿电路是干什么的，一行命令解释每个元件的作用。
> 当前实现：Python 唯一实现，基于 Protel `.NET` 网表。

---

## 一、总体架构（一句话）

```text
.NET 网表 → 解析器 → CircuitIR → 增强器 → NetworkX 图 → 查询/识别/解释/导出 → CLI 输出
```

---

## 二、数据流全景图

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                              输入层                                      │
│  Protel .NET 网表文件，例如：fixtures/protel/FX-T268-V6-10米-5065.NET    │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                              解析层                                      │
│  circuit_graph/parsers/protel.py                                        │
│  ProtelNetParser.parse() → CircuitIR                                    │
│  输出：元件列表、网络列表、连接列表                                       │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            IR 增强管道                                   │
│  circuit_graph/enrichers/                                               │
│  ├─ value_normalizer.py   把 "100K" 解析成 100000 Ω 结构化数值           │
│  ├─ net_classifier.py     把网络分类为 POWER / GND / SIGNAL / CLOCK 等   │
│  └─ topology_recognizer.py 拓扑识别（基元库 + VF2 + 多源评分）           │
│                                                                         │
│  增强器按 priority 串行执行；supports 声明依赖（requires）；              │
│  不修改原 IR；失败隔离为 warnings                                         │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       拓扑识别子系统（被 TopologyRecognizer 调用）         │
│  ├─ primitives.py        JSON 基元模板加载 + 二部图编码                  │
│  ├─ vf2_matcher.py       VF2 子图匹配（带超时/早剪）                     │
│  ├─ scoring.py           结构/命名/参数/类型四源融合评分                 │
│  └─ primitives/*.json    基元模板（如 type-c-cc-pull-down.json）        │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           图模型层（NetworkX）                            │
│  circuit_graph/graph.py                                                 │
│  CircuitGraph.load(ir) 把 IR 转成异构图                                  │
│                                                                         │
│  节点类型：component / pin / net / functional_block / sheet              │
│  边类型：  has_pin / connects_to / belongs_to / powered_by /              │
│            communicates_with                                            │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
        ┌─────────┬──────────────┼──────────────┬──────────────┐
        │         │              │              │              │
        ▼         ▼              ▼              ▼              ▼
┌──────────────┐ ┌──────────┐ ┌──────────────┐ ┌──────────┐ ┌─────────────┐
│   查询层      │ │ 供电识别  │ │ 通信识别      │ │ JSON 导出 │ │ GraphML 导出 │
│ query.py     │ │ power_   │ │ comm_topology│ │ cg_graph   │ │ graphml.py  │
│              │ │ tree_    │ │   .py        │ │   .py     │ │             │
│ trace_       │ │ recognizer│ │              │ │           │ │ yEd 兼容    │
│   component  │ │   .py    │ │ I2C/UART/    │ │ v2.1      │ │             │
│ trace_net    │ │          │ │   SPI/PWM    │ │           │ │             │
│ neighbors_of │ │ models/  │ │              │ │           │ │             │
│ bfs / dfs    │ │ power_   │ │              │ │           │ │             │
│ path_between │ │ tree.py  │ │              │ │           │ │             │
└──────┬───────┘ └────┬─────┘ └──────┬───────┘ └─────┬─────┘ └──────┬──────┘
       │              │              │              │              │
       │              ▼             ▼              │              │
       │       ┌─────────────┐ ┌──────────────┐     │              │
       │       │visualization│ │ emits        │     │              │
       │       │   .py       │ │ semantic     │     │              │
       │       │ (SVG)       │ │ edges        │     │              │
       │       └─────────────┘ └──────────────┘     │              │
       │                                            │              │
       └────────────────────┬───────────────────────┴──────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                              CLI 层                                     │
│  circuit_graph/cli.py                                                   │
│  cg export graph <file>       → 生成 cg-graph JSON（v2.1）               │
│  cg export graphml <file>     → 生成 yEd GraphML                        │
│  cg view <id> <file>         → ego-subgraph（默认 2 hops，可调）          │
│  cg power <file>             → 供电树（纯文本 + 可选 SVG）                 │
│  cg comm <file>              → 通信总线（I2C/UART/SPI/PWM）               │
└─────────────────────────────────────────────────────────────────────────┘

辅助：
  circuit_graph/knowledge/__init__.py   元件型号知识库（vendor_db.json）
                                        PowerTreeRecognizer 用它识别
                                        DCDC / LDO / MOSFET pair 等
```

---

## 三、核心数据模型：CircuitIR

位置：`circuit_graph/ir.py`

`CircuitIR` 是整个系统的**唯一权威数据表示**，所有模块都围绕它工作。

```python
@dataclass
class CircuitIR:
    meta: CircuitMeta              # 来源、格式、解析时间
    components: list[Component]    # 元件
    nets: list[Net]                # 网络
    connections: list[Connection]  # 连接
    blocks: list[FunctionalBlock]  # 功能块
    semantic_edges: list[SemanticEdge]
```

### 主要实体

| 实体 | 关键字段 | 说明 |
|------|---------|------|
| `Component` | `id`, `device_kind`, `footprint`, `value`, `raw_value`, `manufacturer`, `part_number`, `pins`, `metadata` | 元件，如 R100、U7 |
| `Pin` | `id`, `number`, `name`, `type`, `net_id` | 元件引脚，如 `R100.1` |
| `Net` | `id`, `name`, `type`, `metadata` | 网络，如 `NetR100_1` |
| `Connection` | `pin_id`, `net_id`, `provenance`, `metadata` | 引脚与网络的连接关系 |
| `FunctionalBlock` | `id`, `name`, `role`, `component_ids`, `description`, `source` | 识别出的功能块 |
| `SemanticEdge` | `source`, `target`, `kind`, `provenance`, `metadata` | 语义边，如 `powered_by` |

---

## 四、IR 增强管道详解

位置：`circuit_graph/enrichers/`

增强器把原始解析后的 IR 变得更“智能”，让后续查询和解释能基于语义而非纯字符串。

### 1. ValueNormalizer（priority=10）

把元件的原始字符串值解析成结构化数值：

| 原始值 | 解析结果 |
|--------|---------|
| `100K` | `ResistanceValue(magnitude=100000, unit=ohm)` |
| `4u7/16V` | `CapacitanceValue(magnitude=4.7e-6, unit=F, voltage_rating=16)` |
| `10uH` | `InductanceValue(magnitude=10, unit=uH)` |

### 2. NetClassifier（priority=20）

根据网络名称给网络分类：

| 类型 | 识别规则 |
|------|---------|
| `GND` | `^GND` / `^PGND` / `^AGND` / `^DGND` / `^SGND` |
| `POWER` | 含 `\d+\.?\d*V`（如 `5V`、`3.3V`）或 VBUS / VCC / VDD / VEE / VBAT / VIN |
| `SIGNAL` | `^CC1$` / `^CC2$` / `^SBU1$` / `^SBU2$` / `^DP$` / `^DM$` / `^D\+$` / `^D-$` |
| `CLOCK` | 含 `CLK` / `OSC` |
| `ANALOG` | 含 `AIN` / `ADC` / `DAC` / `ANALOG` |
| `UNKNOWN` | 以上都不命中（**没有 SIGNAL 默认值**） |

### 3. TopologyRecognizer（priority=30）

识别常见拓扑并生成 `FunctionalBlock`。**不再是硬编码**的 Type-C CC 单一规则，
而是由三部分协同：

| 子模块 | 职责 |
|--------|------|
| `circuit_graph/primitives.py` | 加载 `primitives/*.json` 基元模板 → 二部图编码 |
| `circuit_graph/vf2_matcher.py` | 在电路二部图里枚举基元嵌入；带超时（默认 2 s）防爆栈 |
| `circuit_graph/scoring.py` | 对每个嵌入打 4 个分（structural / naming / parametric / type）并融合成置信度（high / medium / low） |

当前 `primitives/type-c-cc-pull-down.json` 覆盖 Type-C CC 5.1 kΩ 下拉。后续新增拓扑
只需在 `primitives/` 追加 JSON，无需改 Python。

### 管道执行特点

```text
原始 IR
   │
   ▼
[ValueNormalizer] ──→ 数值标准化
   │
   ▼
[NetClassifier] ────→ 网络分类
   │
   ▼
[TopologyRecognizer] → 基元匹配 + 评分 → FunctionalBlock
   │
   ▼
增强后的 IR
```

- 按 `priority` 从小到大执行；
- 支持 `requires` 字段声明依赖（依赖的增强器未先跑则跳过并记 warning）；
- 每个增强器返回新的 IR，不修改输入；
- 某个增强器失败不会中断整个流程，只会记录 warning。

---

## 五、NetworkX 异构图详解

位置：`circuit_graph/graph.py`

`CircuitGraph` 把 `CircuitIR` 转换成一个**有类型的图**，便于做拓扑分析和算法遍历。

### 节点类型

| 类型 | 例子 | 含义 |
|------|------|------|
| `component` | `R100` | 元件 |
| `pin` | `R100.1` | 元件引脚 |
| `net` | `NetR100_1` | 网络 |
| `functional_block` | `type-c-cc-pull-cc1` | 功能块 |

### 边类型

| 类型 | 方向 | 含义 |
|------|------|------|
| `HAS_PIN` | component → pin | 元件拥有引脚 |
| `CONNECTS_TO` | pin → net | 引脚连接到网络 |
| `BELONGS_TO` | component → functional_block | 元件属于某个功能块 |
| `POWERED_BY` | component → component | 供电关系 |
| `COMMUNICATES_WITH` | component ↔ component | 通信关系 |

### 为什么需要异构图？

1. **关系显式化**：把“元件-引脚-网络”的隐含关系变成可直接遍历的边；
2. **算法友好**：可直接使用 NetworkX 的 BFS、DFS、最短路径等算法；
3. **多层语义**：不仅知道谁连谁，还知道是“供电”“通信”还是“属于功能块”。

---

## 六、图遍历/追踪原语详解

位置：`circuit_graph/query.py` + `circuit_graph/graph.py`

这些原语是上层功能的基石。

| 原语 | 作用 | 典型用途 |
|------|------|---------|
| `trace_component(graph, designator)` | 返回元件每个引脚连接的网络 | `cg explain` |
| `trace_net(graph, net_id)` | 返回网络上连接的所有元件-引脚对 | `cg explain` 找邻居 |
| `neighbors_of(graph, node_id)` | 返回相邻节点及连接边 | 通用邻居查询 |
| `traverse_bfs / traverse_dfs` | 从某节点按条件做图遍历 | `cg view` 生成 ego-subgraph |
| `path_between(start_pin, end_pin)` | 两个引脚间的最短路径 | 信号路径分析 |

### 追踪示例：R100

```text
R100
├── pin 1 ──CONNECTS_TO──► NetR100_1
│                          ├── R101.?
│                          └── U7.?
└── pin 2 ──CONNECTS_TO──► NetR97_1
                           ├── R97.?
                           └── U7.?
```

通过 `trace_component(R100)` 得到 R100 的两个网络；
通过 `trace_net(NetR100_1)` 得到该网络上的其他元件 R101、U7。

---

## 八、与 JSON 的关系

项目不依赖 JSON 作为输入，JSON 是**输出产物**。

### 1. cg-graph JSON（核心产物）

位置：`circuit_graph/generators/cg_graph.py`

由 `cg export graph` 生成，面向 AI agent 消费。

```json
{
  "format": "cg-graph",
  "version": "2.1",
  "circuit": {
    "name": "FX-T268-V6-10米-5065",
    "summary": "...",
    "total_components": N,
    "total_nets": M,
    "main_ics": ["U7"],
    "rails": ["GND", ...]
  },
  "components": {
    "R100": {
      "id": "R100",
      "type": "resistor",
      "value": "100kΩ",
      "resistance": 100000,
      "package": "0402R",
      "pins": [{"pin": "1", "net": "NetR100_1"}, {"pin": "2", "net": "NetR97_1"}]
    }
  },
  "nets": {
    "NetR100_1": {
      "degree": 3,
      "members": ["R100", "R101", "U7"],
      "kind": "signal"
    }
  }
}
```

特点：

- 由增强后的 `CircuitIR` 直接生成；
- 不经过 NetworkX 图；
- `view_component()` 可生成局部 ego-subgraph，结构与全局一致；
- 视图内的 net 带 `degree`（扇出）；`view_component` 对 `degree >= 16` 的“轨线”（如 GND）只列出，不向其下游扩展成员，避免 ego-graph 爆炸；
- 文件格式 `format: cg-graph`，`version: 2.1`。

---

## 九、CLI 命令速查

| 命令 | 输出 | 说明 |
|------|------|------|
| `cg export graph <file>` | `output/circuit.graph.json` | 完整板的 cg-graph JSON（v2.1） |
| `cg export graphml <file>` | `output/circuit.graphml` | yEd 兼容 GraphML |
| `cg view <id> <file> [--hops N] [--pretty]` | stdout JSON | 元件 ego-subgraph（默认 2 hops） |
| `cg power <file> [--reload-knowledge] [--svg path]` | stdout + 可选 SVG | 供电树（外接电源 → 稳压器 → 负载） |
| `cg comm <file>` | stdout | 检测 I2C / UART / SPI / PWM 通信总线 |

---

## 十、模块依赖关系图

```text
                           ┌─────────────┐
                           │   cli.py    │
                           └──────┬──────┘
                                  │
        ┌────────────┬────────────┼─────────────┬────────────┐
        │            │            │             │            │
        ▼            ▼            ▼             ▼            ▼
┌──────────────┐ ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌──────────────┐
│ generators/  │ │exporters│ │ power_   │ │ comm_   │ │  query.py    │
│  cg_graph.py  │ │graphml  │ │ tree_    │ │ topology│ │ trace/       │
│              │ │  .py    │ │ recognizer│ │  .py    │ │ neighbors/   │
│              │ │         │ │   .py    │ │         │ │ bfs/dfs      │
└──────┬───────┘ └────┬────┘ └─────┬────┘ └────┬────┘ └──────┬───────┘
       │              │            │           │             │
       │              │            ▼           │             │
       │              │      ┌──────────┐     │             │
       │              │      │knowledge/│     │             │
       │              │      │  KB +    │     │             │
       │              │      │models/   │     │             │
       │              │      │power_tree│     │             │
       │              │      └─────┬────┘     │             │
       │              │            │           │             │
       │              │            ▼           │             │
       │              │     visualization.py   │             │
       │              │       (SVG 渲染)       │             │
       └──────────────┴────────────┬──────────┴─────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────┐
│                      graph.py                       │
│              CircuitGraph（NetworkX）                │
└─────────────────────────┬───────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│                      ir.py                          │
│              CircuitIR（唯一权威模型）                │
└─────────────────────────┬───────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
┌─────────────┐   ┌─────────────────┐   ┌─────────────────┐
│  parsers/   │   │   enrichers/    │   │  primitives/    │
│ protel.py   │   │ value_normalizer│   │  *.json         │
│             │   │ net_classifier  │   │ (基元模板)      │
│             │   │ topology_       │   └─────────┬───────┘
│             │   │   recognizer    │             │
│             │   │   │             │   ┌─────────┴──────────┐
│             │   │   ├→ primitives │   │  primitives.py     │
│             │   │   ├→ vf2_matcher│   │  vf2_matcher.py    │
│             │   │   └→ scoring.py │   │  scoring.py        │
└─────────────┘   └─────────────────┘   └────────────────────┘
```

---

## 十一、关键设计原则

1. **IR 是唯一权威表示**：所有解析、增强、查询、识别、导出都围绕 `CircuitIR`；
2. **增强器可插拔**：新增识别能力只需添加新的 enricher；
3. **图与 IR 分离**：IR 保存电路语义，NetworkX 图负责拓扑计算；
4. **JSON 是视图不是源**：cg-graph JSON 只是 IR 的一种面向 AI 的序列化；
5. **失败隔离**：增强器和图加载中的错误不会中断主流程，只记录 warning；
6. **拓扑识别数据驱动**：基元 = `primitives/*.json`；增删拓扑不需要改 Python；
7. **领域知识集中维护**：型号 → 类别（DCDC / LDO / 触摸 IC / 桥驱动）走 `knowledge/vendor_db.json`，电气工程师即可扩展。
