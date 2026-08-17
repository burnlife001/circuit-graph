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

**`extract_volts` 解析规则**（与 `edge_semantic._extract_level` 的输出格式对齐）：

```python
# primitives.py 顶层
_LEVEL_FRAC_RE = re.compile(r"^\+?(\d+)[Vv](\d+)$")        # "3V3" / "1V8" / "1V25"
_LEVEL_PLAIN_RE = re.compile(r"^\+?(\d+(?:\.\d+)?)[Vv]?$") # "5V" / "3.3V" / "5" / "+5"

def extract_volts(s: str | None) -> Decimal | None:
    """Parse a voltage label to a Decimal value in volts.

    Rules (first match wins):
      1. ``"<int>V<frac>"`` → ``int.frac``     (e.g. "3V3" → 3.3, "1V25" → 1.25)
      2. ``"[+]<float>[V]"`` → ``float``       (e.g. "5V" → 5.0, "3.3V" → 3.3, "5" → 5.0)
      3. otherwise → ``None``
    """
```

规则 1 优先是因为 `edge_semantic._extract_level` 输出的 `"3V3"` / `"1V8"` 是**数字化习惯记法**（`V` 作小数点），若按规则 2 解析会得到 `3.0`，与 `"3V0"` 混淆。

#### 3.2.2 严格模式（用户已确认）

**规则**：当基元的某个 signal 声明了任一 `expected_*` 字段时，VF2 在将该 signal 映射到 candidate circuit net 时，必须满足：

1. 基元中至少存在一个 component `C` 通过 pin 连接到该 signal；
2. 对所有这样的 `C`，其映射到 circuit 中的 component `C'` 与 candidate net 之间的 `(C', net)` 边在 `ir.connections` 中**存在**，且 `metadata["edge_semantic"]` 中对应字段**等于** `expected_*` 值。

任一不满足 → `semantic_feasibility` 钩子返回 `False`，VF2 剪枝（机制详见 §3.2.4）。

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
| `circuit_graph/primitives.py` | `PrimitiveSignal` 增加 3 个可选字段（`expected_role` / `expected_direction` / `expected_level`，均默认 `None`）；`_parse_signal` 解析；新增模块级 `extract_volts(s) -> Decimal \| None`（规则见 §3.2.1）；`PrimitiveTemplate` 新增**私有派生字段** `_signal_to_components: dict[str, list[str]]`（key 为 signal id，value 为通过 pin 连到该 signal 的 component id 列表，排序去重），由 `__post_init__` 一次性填好；新增模块级纯函数 `signal_semantic_matches(expected_role, expected_direction, expected_level, edge_semantic_dict) -> bool` |
| `circuit_graph/vf2_matcher.py` | `Vf2TopologyMatcher.__init__` 构建 `(component_id, net_id) → EdgeSemantic` 索引 `self._edge_semantics`；新增内部类 `_SemanticGraphMatcher(GraphMatcher)`，重写 `semantic_feasibility(G1_node, G2_node)` 钩子：先 `super().semantic_feasibility(...)`，再做 `edge_semantic` 检查（通过 `self.core_1` / `self.core_2` 访问 partial mapping）；`find_matches` 中将 `GraphMatcher(...)` 替换为 `_SemanticGraphMatcher(...)`；新增模块级函数 `_match_signal_semantics(template, prim_signal, circ_net_id, partial_mapping, edge_semantics) -> bool`，遍历 `template._signal_to_components[prim_signal.id]` 中的每个 template-side component id，对每个 component 在 `partial_mapping` 中查已映射的 circuit-side component_id，再用 `(circuit_comp_id, circ_net_id)` 查 `edge_semantics` 索引，调纯函数 `signal_semantic_matches` 比对 |
| `primitives/schema.json` | 新增 3 个可选字段定义 |
| `tests/test_vf2_matcher.py` | 新增 3 个测试：role 匹配通过、role 不匹配拒绝、缺 edge_semantic 拒绝（严格模式） |
| `tests/test_primitives.py` | 新增 schema 解析测试：3 字段读取、缺字段默认 `null`、`PrimitiveTemplate._signal_to_components` 派生正确、`extract_volts` 数值比较（含 `"3V3"` / `"1V25"` 习惯记法） |
| `primitives/*.json`（现有 6 个） | **不动** |

**两个函数的角色划分**：
- `signal_semantic_matches(...)`（模块级**纯函数**）：输入 `expected_role/expected_direction/expected_level` 三个值 + 一条 `edge_semantic` 字典，返回是否匹配。**`None` 字段视为"不约束"，跳过比对**。可单测。
- `_match_signal_semantics(template, prim_signal, circ_net_id, partial_mapping, edge_semantics)`（模块级函数）：依赖调用方传入的 `edge_semantics` 索引（由 `_SemanticGraphMatcher` 传入 `self._edge_semantics`）+ partial mapping（`self.core_2`），遍历 `template._signal_to_components[prim_signal.id]`，把每对 `(template_comp_id, circ_net_id)` 翻译成 `(circuit_comp_id, circ_net_id)` 再查索引，**任一对不通过则返回 `False`**。

#### 3.2.4 严格模式语义检查的数据流（方案 b：剪枝于 `semantic_feasibility` 钩子）

**为什么不选方案 a（延迟到 mapping 完成后 post-filter）**：VF2 候选基数在大图上可达数千，若延迟检查会产生大量"映射完成才发现缺 semantic"的浪费匹配。尽早剪枝能显著降低搜索空间。

**为什么不在 `node_match` 内做语义检查**：NetworkX `GraphMatcher` 的 `node_match` 回调签名是 `node_match(G1_attrs, G2_attrs) -> bool` —— 它只接收两个节点的 attrs，**不暴露当前 partial mapping**。严格模式需要"在 net 节点匹配时查询 primitive 内已映射 component 的 circuit 对应物"，这在 `node_match` 里物理上做不到。

**正确路径**：NetworkX `GraphMatcher` 提供 `semantic_feasibility(G1_node, G2_node)` 钩子，在每次尝试扩展 mapping 时被调用，且能通过 `self.core_1` / `self.core_2` 访问当前 partial mapping。子类化 `GraphMatcher` 重写此钩子即可实现"在 net 节点匹配时做语义检查"。

**`PrimitiveTemplate` 派生表**（`primitives.py` 内）：

```python
@dataclass(frozen=True)
class PrimitiveTemplate:
    # ... 既有字段 ...
    _signal_to_components: dict[str, list[str]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Derive signal_id → [component_id, ...] from components[*].pins[*].net.

        Frozen dataclass: use object.__setattr__ to populate the derived index.
        """
        edges_by_net: dict[str, set[str]] = defaultdict(set)
        for comp in self.components:
            for pin in comp.pins:
                edges_by_net[pin.net].add(comp.id)
        derived = {
            sig.id: sorted(edges_by_net.get(sig.id, set()))
            for sig in self.signals
        }
        object.__setattr__(self, "_signal_to_components", derived)
```

**说明**：
- 派生数据放在 `PrimitiveTemplate`（而非 `PrimitiveSignal`），保持 `PrimitiveSignal` 的 frozen 语义不变
- 派生逻辑遍历 `components[*].pins[*].net`（**不是 `template.connections`**——`PrimitiveTemplate` 没有该字段）
- 用 `set` 中间结构去重，最终 `sorted` 保证确定性

**`_SemanticGraphMatcher` 子类**（`vf2_matcher.py` 内）：

```python
class _SemanticGraphMatcher(GraphMatcher):
    """GraphMatcher subclass that honours primitive expected_* semantics."""

    def __init__(self, circuit_graph, primitive_graph, *, template, edge_semantics):
        super().__init__(circuit_graph, primitive_graph)
        self._template = template
        self._edge_semantics = edge_semantics  # (circuit_comp_id, circuit_net_id) -> dict

    def semantic_feasibility(self, G1_node, G2_node) -> bool:
        if not super().semantic_feasibility(G1_node, G2_node):
            return False
        # G1 = circuit graph, G2 = primitive graph (NetworkX convention)
        # Only intercept when the candidate G2 node is a net with expected_* declared
        prim_attrs = self.G2.nodes[G2_node]
        if prim_attrs.get("kind") != "net":
            return True
        prim_signal = prim_attrs["data"]
        if (
            prim_signal.expected_role is None
            and prim_signal.expected_direction is None
            and prim_signal.expected_level is None
        ):
            return True
        circ_net_id = G1_node[2:] if G1_node.startswith("n:") else G1_node
        # core_2: primitive_node -> circuit_node (current partial mapping)
        return _match_signal_semantics(
            template=self._template,
            prim_signal=prim_signal,
            circ_net_id=circ_net_id,
            partial_mapping=self.core_2,
            edge_semantics=self._edge_semantics,
        )
```

**`_match_signal_semantics` 实现要点**（模块级函数）：

```python
def _match_signal_semantics(template, prim_signal, circ_net_id, partial_mapping, edge_semantics) -> bool:
    """对 primitive 内连接到 prim_signal 的每个 component：
    - 在 partial_mapping 中查 template_comp_id → circuit_comp_id
      （注意 prefix 约定：partial_mapping 的 key 是带前缀的 "c:<comp_id>"，
       而 template._signal_to_components[sig.id] 是不带前缀的 comp.id；
       查询时需加 "c:" 前缀，取回 circuit-side 节点后需去 "c:" 前缀）
    - 若该 component 尚未被映射（partial_mapping 中查不到），跳过该对的检查
      （因为 VF2 后续会把它映射到某个 candidate；这里只验证已映射部分）
    - 若已映射，组装 (circuit_comp_id, circ_net_id) 查 edge_semantics
    - 若索引里没有该 (comp, net) 的 edge_semantic：拒绝（严格模式要求存在）
    - 查到了则调 signal_semantic_matches(...) 比对
    - 所有已映射对都通过 → True；任一不通过 → False
    """
    ...
```

**Partial mapping 时序说明**：`semantic_feasibility` 在 VF2 递归扩展时被调用，每次只验证**当前新增的一对映射**与已确立的 mapping 的一致性。当尝试把一个 circuit net 节点映射到 primitive signal 节点时：
- 情况 A：primitive 内所有连接到此 signal 的 component 节点都尚未映射 → 直接返回 `True`（无可验证对），让 VF2 继续推进
- 情况 B：部分已映射 → 仅验证已映射部分，未映射的留待后续 feasibility 调用
- 情况 C：全部已映射 → 严格验证每对

这避免了"在 net 节点匹配时强制所有 component 必须已映射"的不自然约束，也让 VF2 能在搜索早期就剪掉语义不一致的分支。

#### 3.2.5 `_candidate_components` 与严格模式的交互（性能优化）

现有 `_candidate_components(template)` 按 component type 预筛 root 候选。严格模式下，新增一层可选预筛（仅当 primitive signal 声明 `expected_role` 时启用）：

```python
def _candidate_components(self, template):
    base: set[str] = <既有按 type 预筛的逻辑>
    # 严格模式附加：如果某 primitive 的 root component 通过 net 连到 role=power/gnd 的信号，
    # 则只保留其 (component, power_net) 边真实存在 edge_semantic.role 的 candidate
    if any(s.expected_role in ("power", "gnd") for s in template.signals):
        return {c for c in base if self._has_role_edge(c, template)}
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
