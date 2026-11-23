# circuit-graph 架构说明

## 唯一目标

**一行命令说清楚电路用途**：以已有电路为输入，说明元件与功能块的作用，并提供可核对的依据。主入口是 `cg role`；查询、上下文视图、知识库和图导出服务于同一目标。

不提供电路生成、参数化设计、SPICE 仿真、自动布局或电气/布局规则检查。

## 数据流

```text
Protel 格式网表
  → parser registry → CircuitIR
  → 多单元拆分 / 数值归一化 / 网络分类 / 引脚补全 / 边语义 / 拓扑识别
  → role：元件用途 + 依据
  → query / view：连接、供电、通信和功能块上下文
  → export：JSON / GraphML 分析视图
```

`cg role --all` 对整板仅解析一次，逐个输出元件作用。它不承诺由局部连接自动判断整机产品用途。

## 核心数据模型

`circuit_graph/ir.py` 中的 `CircuitIR` 是唯一权威表示，保存：

- `components`：元件、参数、封装、型号和引脚。
- `nets` / `connections`：网络与引脚连接。
- `blocks`：识别出的功能块及其元件集合。
- `semantic_edges`：供电、通信等语义关系。

`circuit_graph/graph.py` 将 IR 转为 NetworkX 异构图，用于连接追踪、图遍历和路径查询。JSON、GraphML 是分析输出，不是生成新电路的输入规格。

## 解析与增强

`circuit_graph/cli/app.py::_parse_and_enrich` 是 CLI 共用管线：

1. `parsers/registry.py` 选择解析器，当前注册 `ProtelNetParser`。
2. `MultiUnitSplitter` 将多单元 IC 拆成可分别识别的虚拟元件。
3. `ValueNormalizer` 解析阻容感等参数。
4. `NetClassifier` 分类电源、地、时钟等网络。
5. `PinNameResolver` 使用知识库补全引脚名。
6. `EdgeSemanticEnricher` 增补连接的角色、方向等语义。
7. `TopologyRecognizer` 匹配基元模板，产出功能块及置信度。

主分析链读取 Protel 格式 `.NET` / `.net`；知识库另有 KiCad 网表导入器，不等于主分析链支持原生 KiCad 原理图。

增强器失败记录为 warning，不能把缺失知识伪装成已知事实。缓存键包含网表内容、增强器版本和基元库 hash；`--no-cache` 绕过缓存。

## 元件用途推理

`role.py` 编排器件属性规则、模板成员角色、连接拓扑规则和兜底输出；`role_attrs.py`、`role_rules.py` 提供具体规则，`role_types.py` 定义结果。

结论先于依据；依据包括实际连接、KB 引脚、相关元件和可计算参数。模板成员的 `title` 表达成员在功能块中的具体作用。识别不了时明确返回未命中或器件类别，不编造功能。

## 拓扑模板

`primitives/<category>/*.json` 保存识别模板，`primitives/schema.json` 定义字段。模板仅描述识别所需的信号、元件、连接、约束、成员角色、评分与重复结构，不包含设计输入、选型建议或生成参数。

- `primitives.py` 加载模板并建立二部图。
- `vf2_matcher.py` 通过候选预筛、join 枚举或 VF2 寻找结构匹配。
- `scoring.py` 融合结构、命名、参数和类型证据。
- `symmetry.py` 对重复功能结构分组。

`expected_pin_category`、`expected_role`、严格值约束与可选件约束用于防止拓扑相似造成误判。新增模板必须同时验证正例与反例。

同一元件命中多个模板块时，`role.py` 按"严格包含者优先 → 模板显式 `priority`（数字小者优先，缺省 0）→ 块成员数升序 → block.id 字典序"挑选代表块。`priority` 让更具体的模板压过更宽泛的模板，不必依赖 id 命名的偶然顺序。

块归属只有一份来源：`role` 把选定的模板 id 写进 `RoleResult.data["template_id"]`，`cg primitives coverage` 直接消费它，不再按 `ir.blocks` 顺序自行归组。

板级答案存放在 `fixtures/protel/*.answers.jsonl`，与通用模板分开维护。共建流程见 [template-system-assets.md](template-system-assets.md)。

### 自动模板（`primitives/auto/`）

`primitive_autodraft.py` 从"一块板 + 一颗芯片位号"推导模板草稿，**不经人审**，由机器闸门 0–3 决定写不写。落点是 `primitives/auto/<型号slug>.json`，顶层带 `"source": "auto"` 与 `"reference": {"board": ..., "chip": ...}`；模板 id 为 `auto-<型号slug>`。`primitive_autodraft.auto_draft()` 出草稿并回放，`evaluate_gates()` 判闸门，`write_auto_primitive()` 落盘，`run_drafts()` 批量跑。

- **值约束不绑参考板实测值**：芯片按型号严格匹配（`strict_value`）；成员只写"值类型正则"——按元件类型给出容值 / 阻值 / 感值的 ±1 个数量级区间（`band_value_pattern()`），实测值为空的 NC 件不进模板。整数区间在生成期编译成紧凑正则（`_number_range_regex()`），避免 `.*` 网模式叠加后 VF2 组合爆炸。
- **规模上限**：最多 2 个可选组（变体数 2²）、8 个成员、16 个芯片引脚；超出的成员按设计剪枝并记 `member_overflow` 警告。单模板回放预算 2s（`AUTO_MATCH_TIMEOUT_SECONDS`），超时判 `needs_review` 不入库——模板进了库就会在每块板上跑一遍。
- **机器闸门**（任一失败只记录原因、不写文件）：`gate0` 参考板上该芯片已被手工模板命中 → 跳过；`gate1` state 必须是 `ready`；`gate2` 在 `fixtures/**/*.NET` 上比较"加模板前/后"的块归属，已被手工模板认领的元件改了归属 → 拒；`gate3` 参考板自己的 `<板>.answers.jsonl` 里 auto 块覆盖的元件若属于另一个具体模板 id → 拒（不同板的位号会重名，不能跨板合并答案）。
- **优先级**：`TopologyRecognizer` 先跑手工模板，auto 模板排在后面且不抢已被手工模板认领的元件；块 metadata 标 `template_source=auto`。`cg role` 里 auto 结果排在身份 / 器件 / 拓扑规则与手工模板之后（只兜底），标题带"（自动模板）"、置信度 `medium`。

批量入口是 `cg primitives draft <board> [--all|--chip U3] [--write]`，报告落 `output/auto-draft-report.md`。

## 知识库

`knowledge/db.py` 提供 SQLite 元件库，保存型号、引脚、类别、来源和属性；`chip_resolver.py`、`pdf_pin_extract.py` 提供引脚数据获取能力。

引脚表分两层：**通用表**（`pinouts.source_ref IS NULL`）描述器件自身的引脚定义，**板级映射**（`source_ref` = 位号）只描述某块板上该元件的引脚名。读取时通用表优先、板级映射兜底（`get_pinout`）；导入时只有通用表会阻止覆盖，板级映射不阻止补通用表 —— 否则先导入过板级映射的型号（如 2N7002、MMBT3904）永远补不上通用表，换板后引脚名就会漂移。

`cg role` 默认离线。仅显式指定 `--kb-source` 才调用 `kb_source.py` 补全；必须校验型号、封装和引脚，不在失败后暗中切换来源。`cg kb import` 的联网行为与 `role` 不同，详见 [CLI 参考](cli-reference.md)。本机知识库数据不随代码清理删除。

## CLI 与辅助工具

| 模块 | 职责 |
|---|---|
| `cli/role.py` | 单元件 / 整板逐元件功能解释 |
| `cli/query.py` | 紧凑连接、元件、网络查询 |
| `cli/view.py` | 局部图与供电 / 通信 / 功能块上下文 |
| `cli/primitives.py` | 模板覆盖率与未识别元件 |
| `cli/app.py` | 主命令组、分析图导出、共享管线 |
| `cli/kb.py` | 知识库维护与引脚查询 |
| `cli/init.py` / `bootstrap.py` | 项目配置与引导 |
| `generators/cg_graph.py` | JSON 分析视图 |
| `exporters/graphml.py` | GraphML 分析图 |
| `power_tree_recognizer.py` / `comm_topology.py` | 供电和通信关系识别 |

`cg init` 只写入项目配置并初始化知识库，不分发开发工具配置。

## 验证

```powershell
./.venv/Scripts/python.exe -m pytest -q
```

默认经 pytest-xdist 全核并行（`addopts = "-n auto"`，约 2.5 min；串行 `-p no:xdist`，约 8.5 min）。
匹配器热路径已做索引化 / 记忆化（正则编译缓存、`sem_ok` 按变体 memoize、`(comp,net)→pin`
与 KB 类别索引、gate_active 按元件缓存），冷板 enrich 约 5.4 s → 3.3 s。
注意：`primitive_autodraft.AUTO_MATCH_TIMEOUT_SECONDS`（默认 2 s）是**墙钟**预算，
xdist 满载下 conftest 会放宽到 30 s，产品默认值不变。

验收重点是结论和证据的真实性、模板误判约束、无知识库时的降级，以及单元件 / 整板查询一致性。
