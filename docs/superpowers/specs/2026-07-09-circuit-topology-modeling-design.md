# 电路拓扑识别建模设计

日期：2026-07-09 | 状态：待评审 | 修订：v1.1（回应审查意见 14 条）

## 上下文

circuit-graph 是一个"一行命令看穿电路"的平台。当前已有三套独立的拓扑识别系统：
primitives+VF2（6 个基元）、comm_topology（4 种通信总线）、
power_tree_recognizer（2 类稳压器）。三套系统互不通信，
覆盖范围有限，不支持参数推理，不能导出仿真。

目标：全板级识别 + 全仿真级精度。识别范围覆盖模拟信号链、电源拓扑、
数字接口、MCU 外围。识别结果可直接导出 SPICE deck 并通过 Ngspice 验证。

## 架构概览

系统分为 4 个架构层（Layer），按 6 个实现阶段（Phase）交付。
Layer 是静态职责划分，Phase 是动态交付节奏。

```
┌─────────────────────────────────────────────────────────────────┐
│                       Protel .NET 网表                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  解析 → CircuitIR → CircuitGraph → enricher pipeline              │
└─────────────────────────────────────────────────────────────────┘
                              │
                   ┌──────────┼──────────┐
                   ▼          ▼          ▼
              ┌─────────┐ ┌─────────┐ ┌─────────┐
              │ Layer 1 │ │ Layer 2 │ │ Layer 3 │
              │ 器件识别 │ │ 拓扑识别 │ │ 仿真组装 │
              └─────────┘ └─────────┘ └─────────┘
                   │          │          │
                   └──────────┼──────────┘
                              ▼
              ┌─────────────────────────┐
              │ Layer 4: 层级组装        │
              │ 功能块互联图 + 全板仿真   │
              └─────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      SPICE Deck → Ngspice → 结果                  │
└─────────────────────────────────────────────────────────────────┘
```

数据流严格单向：L1 → L2 → L3 → L4。每层独立可测、独立可扩展。

### Layer-Phase 映射

| Layer | 职责 | 对应 Phase |
|-------|------|-----------|
| L1 器件识别 | IC → SPICE 模型 + 端口映射 | Phase 2 |
| L2 拓扑识别 | IC+外围网络 → 功能块实例 | Phase 3, Phase 4 |
| L3 仿真组装 | 功能块 → SPICE deck → Ngspice | Phase 1, Phase 4 |
| L4 层级组装 | 功能块互联图 → 全板仿真 | Phase 5 |

### 层间失败传播

每层都有回退策略，确保整个管道不会因单个 IC 识别失败而崩溃：

```
L1 失败（无 SPICE 模型）
  → L2 仍尝试识别拓扑（引脚功能来自 cg kb pinout）
  → L3 用通用行为模型替代（理想运放/理想比较器/理想 LDO）
  → 输出标注 model_source="generic"，置信度降级

L2 失败（不匹配任何基元）
  → 标记为 "unrecognized_subcircuit"，组件仍在 IR 中
  → L3 跳过该功能块，不阻塞其他功能块的仿真

L3 失败（Ngspice 收敛失败 / 语法错误）
  → 该功能块标记为 "simulation_failed"
  → L4 仍可使用其他功能块的仿真结果
  → 错误信息写入 deck 的 diagnostics 字段供排查
```

## Layer 1: 器件识别

### 职责

给定电路的每个 IC，找到其 SPICE 行为模型和标准化端口映射。

### 两个数据源，不同角色

| 数据源 | 提供什么 | 用于 |
|--------|---------|------|
| `cg kb pinout`（JLCPCB 符号库 / PDF 数据手册） | 物理引脚功能（Pin1=VIN, Pin2=GND...） | L2 拓扑识别时标注引脚电气角色 |
| SPICE `.SUBCKT` 端口定义 | 仿真端口映射（node 顺序、电气类型） | L3 仿真组装时正确连线 IC |

冲突规则：
- **仿真组装（L3）以 SPICE .SUBCKT 端口顺序为准**——这是 Ngspice 的要求，不能协商
- **功能理解（L2）以 JLCPCB pinout 为准**——.SUBCKT 可能把 NC 引脚列为端口或用不同命名
- 当两者端口数量不匹配时，L3 发出 warning 并标注所有不匹配的引脚

### 数据模型

```python
@dataclass(frozen=True)
class SpiceModelPort:
    number: str              # IC 引脚号
    name: str                # 引脚名（来自 .SUBCKT 定义）
    standard_role: str       # VCC | GND | IN+ | IN- | OUT | EN | FB | SW | VIN | VOUT | ...
    electrical_type: str     # power | input | output | bidirectional | passive
    jlcpcb_role: str | None  # JLCPCB 引脚功能（如有），用于交叉验证

@dataclass(frozen=True)
class SpiceModel:
    part_number: str
    vendor: str
    subckt_name: str
    file_path: Path          # .lib/.cir 文件路径
    byte_offset: int         # .SUBCKT 在文件中的字节偏移（延迟加载）
    byte_length: int         # .SUBCKT 文本长度
    ports: tuple[SpiceModelPort, ...]  # 不可变，保证端口顺序
    params: tuple[tuple[str, str], ...]  # (参数名, 默认值) 元组

    def load_subckt(self) -> str:
        """按需从文件中读取 .SUBCKT 原文，不常驻内存"""
        with open(self.file_path, 'r', encoding='utf-8') as f:
            f.seek(self.byte_offset)
            return f.read(self.byte_length)
```

关键设计决策：
- **`raw_subckt` 不常驻内存**：模型库可能数百个文件。`SpiceModel` 只存 file_path + 偏移量，`.SUBCKT` 文本在需要组装 deck 时按需读取
- **`ports` 和 `params` 是不可变元组**：端口顺序对 Ngspice 是敏感的，不应被意外修改
- **`jlcpcb_role` 提供交叉验证**：当 SPICE 端口名和 JLCPCB 引脚功能冲突时，两者都保留供诊断

### 端口标准化

核心挑战：不同 vendor 的 .SUBCKT 使用不同端口命名。例如 LM321 的 V+
在不同模型可能叫 `VCC`, `VDD`, `V+`, `VPOS`, `VSUPPLY`。

双层映射：
1. **硬映射**（优先）：`index.json` 中的 vendor-pin-names → standard_role，手写维护
2. **启发式回退**：端口名正则 + 电气分析（如检测到一对 `IN+`/`IN-` → 运放输入对）


### 模型库结构

```
spice_models/
├── index.json              # part_number → (file_path, subckt_name, byte_offset, byte_length)
├── opamp/LM321.lib
├── regulator/HT7533.lib
├── mcu/MM32F0140.lib
├── mosfet/2N7002.lib
└── reference/              # 基准器件（理想运放、理想比较器等）
```

## Layer 2: 拓扑识别

### 与现有系统的集成方式

现有 `TopologyRecognizer` enricher（priority=30，调用 VF2 matcher + scoring）保留不动。
L2 在其**之前**和**之后**各插入一个处理步骤：

```
enricher pipeline:
  ...
  [priority=28] PortRoleAnnotator     ← 新增：端口角色标注（VF2 之前）
  [priority=30] TopologyRecognizer     ← 现有：VF2 + scoring（不动）
  [priority=32] ElectricalValidator   ← 新增：电气约束验证（VF2 之后）
  ...
```

| 步骤 | 在 VF2 之前/之后 | 做什么 |
|------|-----------------|--------|
| PortRoleAnnotator | 之前 | 给每个 IC 引脚标注电气角色，把标注写入 `Component.metadata["port_roles"]` |
| TopologyRecognizer | 匹配本体 | 不变——VF2 子图匹配 + 四源评分 |
| ElectricalValidator | 之后 | 对 VF2 返回的每个 match 做电气约束验证，过滤不满足的匹配，调整 scoring |

Scoring 权重调整：新增 `electrical` 信号维度，默认权重：

```python
DEFAULT_WEIGHTS_V2 = {
    "structural": 0.25,     # 结构完整性（原 0.30）
    "naming": 0.20,         # 网络名匹配（原 0.25）
    "parametric": 0.20,     # 参数值匹配（原 0.25）
    "type": 0.15,           # 器件类型匹配（原 0.20）
    "electrical": 0.20,     # 电气约束满足（新增）
}
```

### 端口角色标注

`PortRoleAnnotator` 是 L2 的第一步。给定一个 IC 及其邻接网络，标定每个端口的电气角色。

规则以 Python dataclass 定义（不是 JSON DSL，不是伪代码），存储在 `circuit_graph/enrichers/port_roles.py`：

```python
@dataclass(frozen=True)
class PortRoleRule:
    """一条端口角色标注规则。所有字段 AND 逻辑。"""
    ic_category: str          # 适用的 IC 类别（"opamp", "dcdc", "comparator"...）
    pin_role_filter: str      # 从 L1 获得的端口 standard_role（"IN+", "IN-", "OUT", "VCC"...）
    neighbor_condition: str   # 邻接条件类型
    neighbor_params: dict     # 条件参数
    assigned_role: str        # 满足条件时赋予的角色

# 示例规则实例
_OPAMP_RULES = (
    PortRoleRule(
        ic_category="opamp",
        pin_role_filter="IN+",
        neighbor_condition="no_resistor_to",
        neighbor_params={"target_pin_role": "OUT"},
        assigned_role="signal"
    ),
    PortRoleRule(
        ic_category="opamp",
        pin_role_filter="IN-",
        neighbor_condition="has_resistor_to",
        neighbor_params={"target_pin_role": "OUT"},
        assigned_role="feedback"
    ),
    PortRoleRule(
        ic_category="opamp",
        pin_role_filter="IN+",
        neighbor_condition="has_resistor_to",
        neighbor_params={"target_net_type": "power", "voltage_nominal": 2.5},
        assigned_role="reference"
    ),
)
```

规则表在模块初始化时构建为 `dict[str, list[PortRoleRule]]`（按 `ic_category` 索引），
标注时按 IC 类别查找适用规则，逐条评估。新增规则只需追加 `_OPAMP_RULES` 等元组。

### 增强的基元格式（v2）

现有 primitives v1 JSON 保持向后兼容。v2 在原 JSON 上新增两个**可选**顶级字段
`electrical` 和 `simulation`。没有这两个字段的基元 = 跳过电气约束验证和仿真组装。

```jsonc
{
  "$schema": "./schema-v2.json",
  "id": "non-inverting-amplifier",
  "name": "同相放大器",
  "version": "2.0",

  // ── 结构层（与 v1 完全兼容，字段不变）──
  "signals": [...],
  "components": [...],
  "constraints": {...},
  "scoring": {...},

  // ── 电气约束层（v2 新增，可选）──
  "electrical": {
    "port_roles": {
      "opamp.+":   "signal",
      "opamp.-":   "feedback",
      "opamp.OUT": "drive"
    },
    "feedback_path": {
      "from": "opamp.OUT",
      "to": "opamp.-",
      "elements": ["resistor"],
      "dc_path": true
    },
    "gain_formula": "1 + Rf / Rg",
    "assumptions": [
      "运放为理想器件（开环增益足够大）",
      "信号频率远低于 GBW"
    ]
  },

  // ── 仿真层（v2 新增，可选）──
  "simulation": {
    "analyses": ["op", "ac"],
    "stimulus": {
      "type": "ac",
      "node": "in_p",
      "amplitude": 1.0,
      "ac_decade": 10,
      "ac_points": 100,
      "ac_start": 1,
      "ac_stop": "1e6"
    },
    "probes": [
      {"node": "out", "label": "Vout", "expected": {"gain_min": 1, "gain_max": 1000}}
    ]
  }
}
```

### PrimitiveTemplate v2 数据模型

```python
@dataclass(frozen=True)
class ElectricalConstraints:
    port_roles: tuple[tuple[str, str], ...]     # (component_id.pin_number, role)
    feedback_paths: tuple[FeedbackPath, ...]
    gain_formula: str | None = None
    assumptions: tuple[str, ...] = ()

@dataclass(frozen=True)
class FeedbackPath:
    from_pin: str     # "opamp.OUT"
    to_pin: str       # "opamp.-"
    required_elements: tuple[str, ...]  # ("resistor",)
    dc_path: bool = True

@dataclass(frozen=True)
class SimulationTemplate:
    analyses: tuple[str, ...]       # ("op", "ac", "tran")
    stimulus: StimulusDefinition
    probes: tuple[ProbeDefinition, ...]

@dataclass(frozen=True)
class StimulusDefinition:
    type: str          # "ac" | "dc" | "pulse" | "none"
    node: str          # 目标信号 id
    amplitude: float = 1.0
    ac_decade: int = 10
    ac_points: int = 100
    ac_start: float = 1.0
    ac_stop: str = "1e6"

@dataclass(frozen=True)
class ProbeDefinition:
    node: str
    label: str
    expected: dict[str, float] | None = None  # {"gain_min": 1.0, "gain_max": 1000.0}

# PrimitiveTemplate 新增字段（保持 frozen）
@dataclass(frozen=True)
class PrimitiveTemplate:
    # ... 现有字段不变 ...
    electrical: ElectricalConstraints | None = None   # v2 新增
    simulation: SimulationTemplate | None = None       # v2 新增
```

### 向后兼容与迁移路径

- `_parse_primitive()` 不报 unknown 字段错误——遇到 `electrical`/`simulation` 时调用新的解析函数
- 缺少 `electrical` 的旧基元：`ElectricalValidator` 跳过，scoring 不加 electrical 维度
- 缺少 `simulation` 的旧基元：`SimulationDeckBuilder` 使用默认激励和分析（见 Layer 3 激励选择表）
- v1 基元不强制升级——但 v1 基元的仿真需要用户手动指定 `--analysis` 和 `--stimulus`
- 新增基元从 v2 起步
- `version` 字段从 `"0.1.0"`→`"2.0"` 仅在新增 `electrical` + `simulation` 字段时更改

### 基元覆盖计划

| 类别 | 数量 | 示例 | Phase |
|------|------|------|-------|
| 运放基础 | 6 | 同相放大、反相放大、差分放大、跟随器、比较器、积分器 | Phase 3 |
| 电源拓扑 | 6 | Buck, Boost, Buck-Boost, LDO, Load Switch, 电流检测 | Phase 3 |
| 有源滤波器 | 6 | Sallen-Key LP/HP, MFB LP/HP, 双 T 陷波, 状态变量 | Phase 4 |
| 无源网络 | 4 | 分压器、RC 滤波、端接、ESD 钳位 | Phase 4 |
| 数字接口 | 4 | I2C 上拉、UART 电平转换、SPI 端接、JTAG 链 | Phase 4 |
| 振荡器/时钟 | 3 | 晶振、RC 振荡、复位监控 | Phase 6 |
| 功率开关 | 3 | 高边开关、低边开关、H 桥 | Phase 6 |
| **合计** | **32** | |

Phase 5 不涉及基元编写——它专注于层级组装逻辑。剩余 6 个基元（振荡器/时钟 + 功率开关）
推迟到 Phase 6。

## Layer 3: 仿真组装

### 职责

功能块实例（L2 输出） × SPICE 模型（L1 输出） × 实际器件值 → 可运行的 SPICE deck。

### 数据模型

```python
@dataclass(frozen=True)
class SimulationDeck:
    """一个自包含的 SPICE 仿真 deck。不可变——由 DeckBuilder 一次性组装。"""
    name: str
    includes: tuple[Path, ...]       # .include / .lib 文件路径
    models: tuple[str, ...]          # .MODEL 定义（分立器件）
    subckts: tuple[str, ...]         # .SUBCKT 实例化（IC）
    netlist: str                     # 外围 R/C/L + 连线
    stimuli: tuple[str, ...]         # 激励源
    analyses: tuple[str, ...]        # .OP .AC .DC .TRAN .NOISE
    probes: tuple[ProbeDefinition, ...]  # .PROBE / .PRINT
    options: tuple[str, ...]         # .OPTIONS（含收敛控制）
    diagnostics: tuple[str, ...]     # 组装过程中的 warning/error（L3 失败传播）

    def to_spice(self) -> str:
        """渲染为可写入文件的 SPICE 文本"""
        ...

@dataclass(frozen=True)
class SpiceResult:
    """Ngspice 运行结果的结构化视图。"""
    deck: SimulationDeck               # 生成此结果的 deck
    exit_code: int                     # Ngspice 进程退出码
    timestamp: str                     # ISO 时间戳
    analyses: tuple[AnalysisResult, ...]

@dataclass(frozen=True)
class AnalysisResult:
    """单次分析的结果（.OP 或 .AC 或 .TRAN 等）。"""
    kind: str                          # "op" | "ac" | "tran" | "dc" | "noise"
    variables: tuple[str, ...]         # 变量名列表（列名）
    data: tuple[tuple[float, ...], ...]  # 数据行（不可变，供后续计算）
    convergence: bool                  # 是否收敛
    iterations: int | None = None      # 收敛所需的迭代次数

    def as_dict(self) -> dict[str, list[float]]:
        """变量名 → 值列表（便于画图/导出）"""
        ...
```

`SimulationDeck` 用 `frozen=True`——它与 L1 的 `SpiceModel` 保持一致的不可变语义。
Deck 由 `DeckBuilder` 一次性组装后不再修改。需要修改时构造新实例。

### Phase 1：纯基础设施，不依赖基元数据

Phase 1 的目标是让 `cg sim` CLI + Ngspice 管道跑通，但不依赖增强基元的存在。
做法是用**硬编码的测试电路**做概念验证：

```python
# Phase 1 交付的模块结构
circuit_graph/sim/
├── __init__.py
├── spice_runner.py    # Ngspice 进程管理
├── spice_deck.py      # SimulationDeck 数据模型
├── spice_result.py    # SpiceResult + AnalysisResult
├── deck_builder.py    # Deck 组装器（Phase 1 用硬编码模板）
├── deck_validator.py  # 运行前检查
├── stimulus.py        # 激励选择器（拓扑类别 → 默认激励表）
└── convergence.py     # 收敛辅助策略
```

Phase 1 验证路径：
1. 写一个硬编码的 LM321 同相放大电路（Rf=10k, Rg=1k, VCC=5V）
2. `DeckBuilder` 硬编码生成 SPICE deck
3. `SpiceRunner` → Ngspice → 确认 gain ≈ 11
4. `cg sim --dry-run` 输出 deck 文件

Phase 1 **不读基元 JSON**——仿真模板、分析命令、激励源全部硬编码在 deck_builder 的测试路径中。
Phase 3 基元升级到 v2 后，deck_builder 才切换到读基元 `simulation` 字段的模式。

### 组装流程（Phase 3+）

```
1. 模板选择: 根据 L2 基元 id 找到 v2 基元的 simulation 字段
2. 模型实例化: IC .SUBCKT 代入实际引脚连接
3. 参数代入: Rf/Rg/C1 替换为实际值
4. 激励生成: 优先用基元 simulation.stimulus，无则查默认激励表
5. 分析命令: 优先用基元 simulation.analyses，无则查默认表
6. Probe 设置: 优先用基元 simulation.probes
7. 验证: deck_validator 检查 DC 路径、浮空节点、零阻抗环路
```

### 激励源自动选择策略（回退表）

当基元没有 `simulation` 字段时，按拓扑类别使用以下默认值：

| 拓扑类别 | 默认分析 | 默认激励 |
|----------|---------|---------|
| 运放放大器 | .OP + .AC | AC 1V @ 输入端 |
| DCDC 转换器 | .OP + .TRAN | DC @ VIN + 负载阶跃 |
| 比较器 | .OP + .TRAN | PULSE @ 输入端 |
| 滤波器 | .AC | AC 1V sweep 1Hz-10MHz |
| 振荡器 | .TRAN | 无（自激振荡） |
| 无源网络 | .OP + .AC | AC 1V |

### Ngspice 集成 + 收敛策略

```python
@dataclass(frozen=True)
class SpiceRunnerConfig:
    """Ngspice 运行时配置。"""
    ngspice_bin: str = "ngspice"         # 可执行文件路径
    timeout_seconds: float = 30.0         # 单次仿真超时
    tmp_dir: Path | None = None           # 临时文件目录
    # 收敛辅助
    gmin: float = 1e-12                   # GMIN 电导（收敛辅助）
    gmin_steps: int = 0                   # GMIN stepping 步数（0=禁用）
    abstol: float = 1e-12                 # 绝对电流容差
    reltol: float = 1e-3                  # 相对容差
    max_retries: int = 3                  # 收敛失败后自动重试次数
    retry_options: tuple[dict[str, str], ...] = (  # 每次重试放的选项
        {"gmin": "1e-10", "abstol": "1e-10"},
        {"gmin": "1e-9",  "abstol": "1e-9", "method": "gear"},
        {"gmin": "1e-8",  "abstol": "1e-8", "method": "gear", "maxord": "2"},
    )

class SpiceRunner:
    def __init__(self, config: SpiceRunnerConfig | None = None): ...
    def run(self, deck: SimulationDeck) -> SpiceResult: ...
```

收敛失败后的自动重试策略：
1. 第一次收敛失败 → 放宽容差 (GMIN=1e-10, ABSTOL=1e-10)
2. 第二次 → 切换到 Gear 积分方法 + 进一步放宽容差
3. 第三次 → 限制最大积分阶数 maxord=2
4. 三次均失败 → 写入 `SpiceResult(convergence=False)` + `SimulationDeck.diagnostics` 含详情报错

### CLI 接口

```powershell
# 仿真单个元件所在的功能块
cg sim R100 fixtures/protel/FX-T268-V6.NET

# 指定分析类型（覆盖默认）
cg sim U2 fixtures/protel/FX-T268-V6.NET --analysis ac --start 1 --stop 1e6

# 全板仿真
cg sim fixtures/protel/FX-T268-V6.NET --all

# 导出 deck 不运行
cg sim U2 fixtures/protel/FX-T268-V6.NET --dry-run --output deck.cir

# 收敛参数覆盖
cg sim U2 fixtures/protel/FX-T268-V6.NET --ngspice-opt gmin=1e-10 --ngspice-opt max-retries=5
```

## Layer 4: 层级组装

### 职责

所有功能块互联 + 供电树 + 通信总线 → 信号链 → 全板仿真。

L4 不是 L1/L2/L3 的数据源——它消费前三层的输出，不向它们写回。

### 功能块互联图

```
BlockA (同相放大) ──signal──→ BlockB (Sallen-Key LPF) ──signal──→ ADC 输入
    │                              │
    └──power──→ (来自 LDO_3V3) ────┘
```

### 层级组合规则

- 同类型功能块级联自动识别为复合块（放大器 + LPF → 有源滤波通道）
- 复合块可生成全链 SPICE deck（级间自动加 buffer 或直连）
- 接口契约验证：前级输出阻抗 vs 后级输入阻抗 → 不匹配时 warning

## 实现优先级（修订版）

| Phase | 交付内容 | 新基元 | 依赖 | 主 Layer |
|-------|---------|--------|------|---------|
| **Phase 1** | `sim/` 模块（SpiceRunner + DeckBuilder + DeckValidator + 收敛策略）+ 硬编码 LM321 概念验证 + `cg sim` CLI | 0 | 无 | L3（基础设施） |
| **Phase 2** | SPICE 模型库（index.json + 延迟加载）+ 端口映射 + `cg kb spice-*` CLI | 0 | Phase 1 | L1 |
| **Phase 3** | PrimitiveTemplate v2（electrical + simulation 字段）+ PortRoleAnnotator + ElectricalValidator + 6 运放基元 + 6 电源基元 v2 升级 | 12 | Phase 2 | L2 + L3 |
| **Phase 4** | 有源滤波器基元 + 无源网络 + 数字接口 + deck_builder 接基元 simulation 字段 | 14 | Phase 3 | L2 + L3 |
| **Phase 5** | BlockInterconnect + HierarchyBuilder + 全板仿真串联 | 0 | Phase 4 | L4 |
| **Phase 6** | 振荡器/时钟 + 功率开关基元 + 温度扫描 + Monte Carlo 分析 | 6 | Phase 5 | L2 + L3 |

每个 Phase 独立可交付、可测试（见测试策略）。

## 测试策略

### 分层测试方法

| 测试层 | 内容 | 工具 | golden fixture |
|--------|------|------|---------------|
| L1 单元 | SpiceModelIndex 查表、端口标准化映射 | pytest | `tests/fixtures/spice_models/mini_index.json` |
| L2 单元 | PortRoleAnnotator 规则评估、ElectricalValidator 过滤 | pytest | T268 网表（LM321 已知角色） |
| L2 集成 | TopologyRecognizer → 功能块列表 | pytest | T268 网表 + 期望的 FunctionalBlock 列表 |
| L3 单元 | DeckBuilder 输出正确 SPICE 语法、DeckValidator 检测浮空节点 | pytest | 手工编写的期望 .cir 文件 |
| L3 集成 | SpiceRunner → Ngspice → 解析结果 | pytest | T268 的 LM321 同相放大，验证 gain=11±5% |
| L4 集成 | 全板仿真串联 | pytest | T268 完整网表 |

### Golden Fixture 定义

- **最小模型库**: `tests/fixtures/spice_models/` — 3-5 个 .lib 文件 + index.json，覆盖运放/LDO/NMOS
- **最小电路**: T268 网表的 LM321 子电路片段（手工标定期望结果）
- **回归预期**: 每次 PR 跑 `pytest` 全量，含 Ngspice 集成测试（CI 需预装 ngspice）

### 仿真精度验收标准

| 级别 | 标准 | 适用范围 |
|------|------|---------|
| P0 阻塞 | Ngspice 无语法错误、无崩溃、返回有效数据 | 所有 Phase |
| P1 功能 | 关键参数（增益、Vout）误差 <5% vs 手工计算 | Phase 3+ |
| P2 行为 | AC 幅度/相位曲线形状正确 | Phase 4+ |
| P3 全面 | 瞬态波形与设计意图一致 | Phase 5+ |

## 关键设计决策

1. **不推翻现有架构**：VF2 + primitives + scoring 保留，PortRoleAnnotator 和 ElectricalValidator 作为 pipeline 的前后步骤插入
2. **SPICE 模型库是 L1 仿真端口唯一数据源**：物理引脚功能来自 JLCPCB pinout，仿真端口来自 .SUBCKT，两者保留交叉验证但不互相替代
3. **端口角色标注是 L2 的核心创新**：Python dataclass 规则对象，不是 DSL 或伪代码
4. **Phase 1 不读基元**：用硬编码测试电路跑通 SPICE 管道，Phase 3 才接基元
5. **Ngspice 选型**：开源、跨平台、工业验证、命令行可控
6. **渐进式覆盖**：32 基元分 Phase 3/4/6 交付，Phase 5 专注层级组装

## 风险

| 风险 | 缓解 |
|------|------|
| SPICE 模型库不完整（很多国产 IC 无官方模型） | L1 失败时用通用行为模型 + model_source="generic" 标记，不阻塞管道 |
| 图匹配在大板上性能差 | 端口角色预筛选 + VF2 超时保护（已有） |
| Ngspice 收敛失败 | GMIN stepping → Gear 方法 → maxord 限制，三级回退 + 失败标注（不阻塞其他块） |
| 手写 32 基元维护成本高 | 前 12 个手写验证范式，后续靠图学习半自动发现（Phase 6+） |
| 温度/Monte Carlo 未在初期规划中 | Phase 6 预留，不影响 Phase 1-5 架构（SimulationDeck.options 已兼容 .TEMP 和 .MC 语句） |

## 附录：未纳入 Phase 1-5 的功能

以下功能在设计中有预留但推迟到 Phase 6 或以后：

- 温度扫描（.TEMP sweep）和 Monte Carlo 分析（.MC）
- 图学习辅助基元发现（替代手写）
- SPICE 模型库的自动导入（从 vendor 网站抓取）
- 复合功能块的逆识别（从 SPICE deck 反推功能块层级）
