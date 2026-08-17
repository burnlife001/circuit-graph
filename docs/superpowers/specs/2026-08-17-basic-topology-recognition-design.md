# 基础电路拓扑识别扩展设计

**日期**：2026-08-17
**状态**：草案，待用户审查
**作者**：burnlife001 + Claude

## 1. 目标与范围

让 circuit-graph 能够识别**常见基础电路拓扑**（上拉/下拉、RC/LC 滤波、运放、晶体管开关、二极管组、晶振、桥式整流等），并把识别结果以 `FunctionalBlock` 形式写回 IR，供 `cg view` / `cg export graph` / 下游 AI 分析使用。

**不在本设计范围内**：
- 复合电源拓扑（Buck/Boost/反激）—— 留待后续按需走"专用识别器"路径（参考 `power_tree_recognizer.py`）
- 传感器前端（惠斯通电桥、RTD、热电偶）—— 同上
- 信号流向的时序分析 —— 仅识别结构，不仿真

## 2. 现状摘要

### 2.1 已有识别路径

| 路径 | 模块 | 触发 | 输出 | 写回 IR |
|---|---|---|---|---|
| A | `enrichers/topology_recognizer.py` | 管线自动 (priority=30) | `FunctionalBlock` | 是（`ir.blocks`） |
| B | `power_tree_recognizer.py` | CLI `--with-power` | `PowerTree` | 否 |
| C | `comm_topology.py` | CLI `--with-comm` | `CommTopologyResult` | 否 |

### 2.2 Enricher 管线（`cli.py:274 _parse_and_enrich`）

```
ProtelNetParser.parse
  → ValueNormalizer(priority=10)
  → NetClassifier(priority=20)
  → PinNameResolver
  → EdgeSemanticEnricher(priority=25, requires=[NetClassifier])
  → TopologyRecognizer(priority=30)
```

### 2.3 关键缺口

`EdgeSemanticEnricher` 已为每条 pin→net 边算好 `direction` / `role` / `level` 三元组（写入 `Connection.metadata["edge_semantic"]`），但 `vf2_matcher._node_match_circuit_to_primitive` **只检查 `device_kind` + `net.name`**，浪费了这些已标注的语义。结果是：

- **分压电阻 vs 上拉电阻**：结构完全相同（一个电阻两端各接一根 net），只能靠 net 名字区分。命名不规范时识别失败或误判。
- **NPN 低边开关 vs 高边开关**：结构对称，只能看 BJT 三脚的 net role。
- **LDO vs DCDC**：引脚名相似，靠 SW pin / direction 才能精确区分。

## 3. 设计方案

### 3.1 总体策略

**两步走**：

1. **第 1 步（引擎增强）**：让 VF2 匹配器消费 `EdgeSemanticEnricher` 已算好的 `edge_semantic` 元数据。改动 `primitives.py` + `vf2_matcher.py` + `schema.json`，向后兼容。
2. **第 2 步（基元扩展）**：在 `primitives/` 目录新增 16 个基元 JSON，覆盖常见基础拓扑。按"识别价值 / 实现可行性"分三批落地。

### 3.2 第 1 步：Schema 与匹配器扩展

#### 3.2.1 `PrimitiveSignal` 新增 3 个可选字段

```json
{
  "id": "vcc",
  "type": "net",
  "name_pattern": "^(VCC|VDD|\\+5V).*",
  "expected_role": "power",
  "expected_direction": "source",
  "expected_level": "5V",
  "required": true
}
```

| 字段 | 类型 | 取值 | 默认 | 备注 |
|---|---|---|---|---|
| `expected_role` | string \| null | `"power"` / `"gnd"` / `"analog"` / `"digital"` / `"clock"` | `null` | 严格匹配 `edge_semantic.role` |
| `expected_direction` | string \| null | `"source"` / `"load"` / `"bidirectional"` | `null` | 严格匹配 `edge_semantic.direction` |
| `expected_level` | string \| null | 任意字符串 | `null` | 见下方规范化约定 |
| `required` | bool | true / false | (沿用既有默认) | **已有字段**，本设计不动 |

三字段全部可选，默认 `null`（不参与匹配）。

**`expected_level` 字符串规范化约定**：
- 写入与读取两侧都跑 `extract_volts(s)`（定义见 `primitives.py` 顶层）得到 `Decimal` 数值
- 比对的是数值而非原字符串；任一侧解析失败则该对记为"不匹配"
- 这样 `"5V"`、`"5.0V"`、`"+5V"`、`"5"` 都视为同一电平
- 若用户希望区分电源域（如"主 5V" vs "副 5V"），应改用 `expected_role` 或加 `name_pattern`，不要靠 `expected_level` 区分

#### 3.2.2 严格模式（用户已确认）

**规则**：当基元的某个 signal 声明了任一 `expected_*` 字段时，VF2 在将该 signal 映射到 candidate circuit net 时，必须满足：

1. 基元中至少存在一个 component `C` 通过 pin 连接到该 signal；
2. 对所有这样的 `C`，其映射到 circuit 中的 component `C'` 与 candidate net 之间的 `(C', net)` 边在 `ir.connections` 中**存在**，且 `metadata["edge_semantic"]` 中对应字段**等于** `expected_*` 值。

任一不满足 → `node_match` 返回 `False`，VF2 剪枝。

**含义**：
- 现有 6 个基元（未声明 `expected_*`）**完全不受影响**，继续按原有逻辑匹配。
- 新基元（声明了 `expected_*`）**强制要求** `EdgeSemanticEnricher` 已经在该 circuit IR 上跑过；否则一个匹配都得不到（不是出错，是无结果）。

**严格模式 vs 宽松模式对比**：

| 情形 | 严格（本设计） | 宽松 |
|---|---|---|
| 基元声明 `expected_role="power"`，circuit 边有 `role="power"` | 通过 | 通过 |
| 基元声明 `expected_role="power"`，circuit 边有 `role="digital"` | 拒绝 | 拒绝 |
| 基元声明 `expected_role="power"`，circuit 边**无** `edge_semantic` | **拒绝** | 通过 |
| 基元未声明 `expected_role` | 走原逻辑 | 走原逻辑 |

#### 3.2.3 代码改动清单

| 文件 | 改动 |
|---|---|
| `circuit_graph/primitives.py` | `PrimitiveSignal` 增加 3 字段；`_parse_signal` 解析；新增 `extract_volts(s) -> Decimal \| None`；新增派生属性 `_linked_component_ids: list[str]`（primitive 内部"该 signal 节点直接连接的 component 节点 id 列表"），在 `PrimitiveTemplate` 构造后由 `_compute_linked_components()` 一次性填好；新增模块级纯函数 `signal_semantic_matches(expected_role, expected_direction, expected_level, edge_semantic_dict) -> bool`（逐字段布尔比对，`None` 字段跳过） |
| `circuit_graph/vf2_matcher.py` | `Vf2TopologyMatcher.__init__` 构建 `(component_id, net_id) → EdgeSemantic` 索引 `self._edge_semantics`；新增实例方法 `_match_signal_semantics(prim_data, circ_data) -> bool`，遍历 `prim_data._linked_component_ids` 中的每个 template-side component id，对每个 component 在 VF2 当前 partial mapping 中查已映射的 circuit-side component_id，再用 `(circuit_comp_id, circ_net_id)` 查 `self._edge_semantics` 得到 edge_semantic，调纯函数 `signal_semantic_matches` 比对；`_node_match_circuit_to_primitive` 在 net 分支调用 `_match_signal_semantics` |
| `primitives/schema.json` | 新增 3 个可选字段定义 |
| `tests/test_vf2_matcher.py` | 新增 3 个测试：role 匹配通过、role 不匹配拒绝、缺 edge_semantic 拒绝（严格模式） |
| `tests/test_primitives.py` | 新增 schema 解析测试：3 字段读取、缺字段默认 `null`、`_linked_component_ids` 派生正确、`extract_volts` 数值比较 |
| `primitives/*.json`（现有 6 个） | **不动** |

**两个函数的角色划分**：
- `signal_semantic_matches(...)`（模块级**纯函数**）：输入 `expected_role/expected_direction/expected_level` 三个值 + 一条 `edge_semantic` 字典，返回是否匹配。**`None` 字段视为"不约束"，跳过比对**。可单测。
- `_match_signal_semantics(prim_data, circ_data)`（matcher **实例方法**）：依赖 `self._edge_semantics` 索引 + VF2 当前 partial mapping，遍历 primitive signal 在 primitive 内部连到的所有 component，把每对 `(component_template_id, net_circuit_id)` 翻译成 `(component_circuit_id, net_circuit_id)` 再查索引，**任一对不通过则返回 `False`**。

#### 3.2.4 严格模式语义检查的数据流（方案 b：剪枝于 net 节点）

**为什么不选方案 a（延迟到 mapping 完成后）**：VF2 候选基数在大图上可达数千，若延迟检查会产生大量"映射完成才发现缺 semantic"的浪费匹配。net 节点剪枝是单步 O(deg×3) 操作，能尽早剪掉不可能的 candidate。

**PrimitiveSignal 派生表**（`primitives.py` 内）：

```python
@dataclass
class PrimitiveSignal:
    # ... 既有字段 ...
    expected_role: str | None = None
    expected_direction: str | None = None
    expected_level: str | None = None
    _linked_component_ids: list[str] = field(default_factory=list, repr=False)

def _compute_linked_components(template: PrimitiveTemplate) -> None:
    """在模板解析后一次性填好每个 signal 的 _linked_component_ids。"""
    edges_by_net: dict[str, list[str]] = defaultdict(list)
    for conn in template.connections:
        edges_by_net[conn.net_id].append(conn.component_id)
    for sig in template.signals:
        if sig.type == "net":
            sig._linked_component_ids = sorted(edges_by_net.get(sig.id, []))
```

**Matcher 初始化时构建语义索引**（在 `__init__` 中直接赋值，不抽单独方法）：

```python
# Vf2TopologyMatcher.__init__ 新增代码
self._edge_semantics: dict[tuple[str, str], dict[str, Any]] = {}
for conn in ir.connections:
    sem = (conn.metadata or {}).get("edge_semantic")
    if sem is None:
        continue
    comp_id, _ = parse_pin_id(conn.pin_id)
    if comp_id is None:
        continue
    self._edge_semantics[(comp_id, conn.net_id)] = sem
```

**`_node_match_circuit_to_primitive` 在 net 分支**：

```python
if circ_kind == "net":
    if not isinstance(circ_data, Net):
        return False
    if not signal_name_matches(prim_data.name_pattern, circ_data.name):
        return False
    # 严格模式：声明了 expected_* 就必须找到匹配的 edge_semantic
    if (
        prim_data.expected_role is not None
        or prim_data.expected_direction is not None
        or prim_data.expected_level is not None
    ):
        return self._match_signal_semantics(prim_data, circ_data)
    return True
```

**`_match_signal_semantics` 实现要点**：

```python
def _match_signal_semantics(self, prim_data, circ_data) -> bool:
    """对 primitive 内连接到 prim_data 的每个 component：
    - 在 VF2 当前 partial mapping 中查 template_comp_id → circuit_comp_id
    - 若该 component 尚未被映射（partial mapping 中查不到），视为 partial 通过
      （因为 VF2 后续会把它映射到某个 candidate；这里只验证已映射部分）
    - 若已映射，组装 (circuit_comp_id, circ_net_id) 查 self._edge_semantics
    - 若索引里没有该 component 的 edge_semantic：拒绝（严格模式要求存在）
    - 查到了则调 signal_semantic_matches(...) 比对
    """
    ...
```

**Partial mapping 时序说明**：VF2 在递归过程中对 primitive 节点按一定策略（如 BFS）逐个尝试映射。当尝试映射一个 signal 节点时，可能：
- 情况 A：primitive 内所有连接到此 signal 的 component 节点都尚未映射 → `_match_signal_semantics` 直接返回 `True`，让 VF2 继续推进
- 情况 B：部分已映射 → 仅验证已映射部分，未映射的留待后续
- 情况 C：全部已映射 → 严格验证每对

这避免了"在 net 节点匹配时强制所有 component 必须已映射"的不自然约束。

#### 3.2.5 `_candidate_components` 与严格模式的交互（性能优化）

现有 `_candidate_components(template)` 按 component type 预筛 root 候选。严格模式下，新增一层可选预筛（仅当 primitive signal 声明 `expected_role` 时启用）：

```python
def _candidate_components(self, template):
    base = <既有按 type 预筛的逻辑>
    # 严格模式附加：如果某 primitive 的 root component 通过 net 连到 role=power/gnd 的信号，
    # 则只保留其 (component, power_net) 边真实存在 edge_semantic.role 的 candidate
    if any(s.expected_role in ("power", "gnd") for s in template.signals):
        return [c for c in base if self._has_role_edge(c.id, template)]
    return base
```

此优化**不改变匹配正确性**（`_match_signal_semantics` 已能剪掉假阳性），仅减少 VF2 候选基数。若 T268 fixture 上 16 个新基元跑完 < 10s，可不实现此节，列入可选优化。

按"实现可行性 / 识别价值"分三批：

#### 组 1（5 个）—— 纯结构 + 现有字段即可，无需新字段

| ID | 名称 | 关键特征 |
|---|---|---|
| `voltage-follower` | 电压跟随器 | 运放 + IN- 直连 OUT |
| `rc-low-pass` | RC 低通 | R 串联 + C 到 GND |
| `rc-high-pass` | RC 高通 | C 串联 + R 到 GND |
| `crystal-with-load-caps` | 晶振 + 负载电容 | XTAL + 2×C 到 GND |
| `bridge-rectifier` | 桥式整流 | 4×二极管，AC×2 输入，DC± 输出 |

#### 组 2（6 个）—— 依赖 `expected_role`

| ID | 名称 | 关键 role 约束 |
|---|---|---|
| `pull-up-resistor` | 上拉电阻 | 一端 role=power，另一端 role=digital |
| `pull-down-resistor` | 下拉电阻 | 一端 role=gnd，另一端 role=digital |
| `tvs-protection` | TVS 保护 | 一端 role=power 或 analog，另一端 role=gnd |
| `npn-low-side-switch` | NPN 低边开关 | C 接负载，E 接 role=gnd，B 接 role=digital |
| `nmos-low-side-switch` | NMOS 低边开关 | D 接负载，S 接 role=gnd，G 接 role=digital |
| `pmos-high-side-switch` | PMOS 高边开关 | D 接负载，S 接 role=power，G 接 role=digital |

> 高边 NPN（结构与 PMOS 高边类似，但驱动电流能力不同）暂列入后续 YAGNI，本设计不覆盖。

#### 组 3（5 个）—— 依赖 `expected_role` + `expected_direction`

| ID | 名称 | 关键约束 |
|---|---|---|
| `flyback-diode` | 续流二极管 | 跨接在电感/继电器两端，direction=bidirectional |
| `current-shunt-monitor` | 电流采样 | R_shunt 串联在 power 路径，两端 direction=source/load |
| `fet-level-shifter` | FET 电平转换 | NMOS + 2×上拉，两侧 role=power 但 level 不同 |
| `lc-pi-filter` | π 型 LC 滤波 | C-L-C，输入/输出 role=power，direction=source/load |
| `sallen-key-low-pass` | Sallen-Key 低通 | 运放 + 2R + 2C 特定拓扑 |

#### 每个基元的交付物

- `primitives/<id>.json` — 基元模板
- `tests/fixtures/primitives/<id>.json` — 最小可识别电路 IR fixture（手写）
- `tests/test_vf2_matcher.py::test_<id>` — 正例匹配 + 反例不匹配

### 3.4 第 3 步：专用识别器（按需）

不在本次范围。若第 2 步发现某拓扑无法用 VF2 + JSON 表达（典型：参数约束、跨层复合、需要 KB 查询），再启动。

候选：
- `buck_recognizer.py` — 完整 Buck（MOS + L + D + 反馈分压 + 输入/输出电容）
- `wheatstone_bridge_recognizer.py` — 惠斯通电桥
- `opto_isolator_recognizer.py` — 光耦隔离

## 4. 数据流

```
.NET 网表
   ↓ ProtelNetParser.parse
CircuitIR (无 edge_semantic)
   ↓ ValueNormalizer
CircuitIR (values normalized)
   ↓ NetClassifier
CircuitIR (nets typed: POWER/GND/SIGNAL/...)
   ↓ PinNameResolver
CircuitIR (IC pins named: VCC/OUT/IN+/...)
   ↓ EdgeSemanticEnricher
CircuitIR (connections[*].metadata.edge_semantic = {direction, role, level})
   ↓ TopologyRecognizer  ← 本设计改动点
   ├─ load_primitives()  ← 解析新 expected_* 字段，调用 _compute_linked_components()
   ├─ Vf2TopologyMatcher(ir)
   │   ├─ __init__: 构建 self._edge_semantics 索引  ← 新增
   │   └─ find_matches(template)
   │       └─ node_match()  ← 新增 semantic 检查（严格模式，调 _match_signal_semantics）
   ├─ score_matches()
   └─ group → FunctionalBlock
   ↓
CircuitIR.blocks += [FunctionalBlock, ...]
```

## 5. 错误处理

| 场景 | 行为 |
|---|---|
| `primitives/*.json` 字段缺失 | `PrimitiveValidationError`，加载失败 |
| `expected_role` 值不在 `{power, gnd, analog, digital, clock}` | `PrimitiveValidationError` |
| `expected_direction` 值不在 `{source, load, bidirectional}` | `PrimitiveValidationError` |
| Circuit 缺 `edge_semantic` 但基元声明了 `expected_*` | 该 net 节点匹配失败，VF2 剪枝；不报错，仅可能导致该基元识别不出 |
| Circuit 完全没有 `edge_semantic`（未跑 EdgeSemanticEnricher） | 所有声明 `expected_*` 的基元识别为零；未声明的基元照常工作 |
| 加载阶段基元有 `expected_*` 但 `ir.connections` 上无任何 `edge_semantic` | `TopologyRecognizer.warnings` 追加 `EDGE_SEMANTIC_MISSING: primitive=<id> declared expected_* but no edge_semantic in IR`；不报错 |

**警告（warning）通道**：复用 `TopologyRecognizer.warnings: list[str]`，调用方（CLI / 测试）可读 `result.warnings` 判断"严格模式基元识别失败是因为数据缺失"而非其他原因。

## 6. 测试策略

### 6.1 单元测试

- `test_primitives.py`：schema 解析（3 个新字段、默认值、错误值拒绝）
- `test_vf2_matcher.py`：每个新基元的正例 + 反例；严格模式边界（缺 semantic 拒绝）
- `test_scoring.py`：现有 6 个基元评分回归

### 6.2 集成测试

- `test_enrichers.py::test_enricher_pipeline_on_t268`：跑完整管线，验证 6 个旧基元 + 16 个新基元在 T268 fixture 上的识别结果
- 手工核对 T268 fixture 中真实存在的拓扑（用 `cg view` 检查），验证对应基元至少产出 1 个 `FunctionalBlock` 且 `confidence != "unknown"`

### 6.3 回归测试

- 跑 `uv run pytest` 全套；特别关注 `test_enricher_pipeline_on_t268`、`test_vf2_matcher.py`、`test_scoring.py`

## 7. 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| 严格模式让某些基元在 fixture 上识别不出 | 中 | 先在 T268 fixture 上手动验证 `EdgeSemanticEnricher` 的输出覆盖；若发现某些 net 没标，补 `EdgeSemanticEnricher` 规则 |
| 新基元引入假阳性（特别是 pull-up vs 分压） | 中 | 提高 confidence 阈值到 `high`；通过 `expected_role` 强约束区分（上拉需要 power↔digital，分压两端口 role 都是 analog/power，不冲突） |
| VF2 性能下降（新字段增加剪枝维度） | 低 | 严格模式反而减少候选；监控 `test_vf2_matcher.py::test_find_matches_large_graph_within_default_timeout` |
| 现有 6 个基元回归 | 低 | 它们未声明 `expected_*`，走原逻辑；CI 全绿才算完成 |

## 8. 成功标准

1. 引擎：`PrimitiveSignal` 支持 3 个新字段；`Vf2TopologyMatcher` 在严格模式下正确消费 `edge_semantic`；现有 6 个基元零回归。
2. 基元：16 个新基元 JSON + fixture + 测试全部到位，`pytest` 全绿。
3. 集成：`cg export graph fixtures/protel/FX-T268-V6-10米-5065.NET` 输出的 `blocks` 中至少能稳定识别出 fixture 中真实存在的对应拓扑。
4. 文档：`Readme.md` 第 11 行的"新增拓扑 = 追加 primitives/*.json"补充 `expected_*` 字段说明。

## 9. 非目标（YAGNI）

- 不做基元版本管理 / 依赖图
- 不做基元间组合（如"运放 + RC = 有源滤波"作为派生基元）
- 不做基元的可视化编辑器
- 不重写现有 6 个基元（即使它们可以加上 `expected_*` 提升精度——这是后续可选优化）
