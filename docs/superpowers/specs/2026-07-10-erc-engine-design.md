# ERC 引擎 — 设计 spec

**Date**: 2026-07-10
**Status**: 实施中
**Author**: burnlife001

---

## 1. 背景与目标

`docs/erc-todolist.md` 规划了 3 个 Level 的 ERC 实施路线。本 spec 覆盖 **Level 1 + Level 2**:

- **Level 1**: 包装 `kicad-cli sch erc`,零算法开发,作为 Level 2 的对标基准。
- **Level 2**: 纯 Python S-expression 解析 + ERC 引擎,不依赖 KiCad 安装。

**不做**: Level 3 领域规则(独立 spec)。

**核心目标**: `cg erc <file.kicad_sch>` 一行命令出 KiCad 标准 ERC 结果,支持 native / kicad-cli 双引擎切换。

---

## 2. 架构概览

```
┌─────────────────────────────────────────────────────┐
│                    cg erc (CLI)                     │
└─────────────────────┬───────────────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
┌────────────────┐         ┌──────────────────────┐
│ KicadCliRunner │         │ ElectricalRules-     │
│ (Level 1)      │         │     Checker (Level 2)│
│                │         │                      │
│ subprocess +   │         │  NetlistExtractor →  │
│ json parse     │         │  NetMap              │
└────────┬───────┘         │     →                │
         │                 │  4 × Validator       │
         │                 │  (PinType/Driver/    │
         │                 │   Conn/Comp/Power)   │
         │                 └──────────┬───────────┘
         │                            │
         └────────────┬───────────────┘
                      ▼
              ┌──────────────┐
              │  ErcReport   │  ← 统一输出格式
              │  (dataclass) │
              └──────────────┘
```

**借鉴来源**: `E:\__electric\__research\kicad-sch-api\kicad_sch_api\validation\` 已有的 `PinConflictMatrix` / `ElectricalRulesChecker` / 4 个 validator / `ERCViolation` 数据结构。本项目复制其核心算法(去除对 `kicad_sch_api` 的依赖),直接用 `sexpdata` 解析(已在 venv)。

---

## 3. 数据结构

### 3.1 `PinContext`(extractor 内部输出)

```python
@dataclass(frozen=True)
class PinContext:
    pin_number: str                # e.g. "1"
    pin_name: str | None           # e.g. "V+" (None if not named)
    electrical_type: ElectricalPinType  # 12 种类型之一
    component_ref: str             # e.g. "R100"
    component_lib_id: str          # e.g. "Device:R"
    position: tuple[float, float]  # (x, y) in mm
    sheet_path: str = "/"          # 单 sheet 时为 "/",层级未实现
```

### 3.2 `ElectricalPinType`(对齐 KiCad `common/pin_type.h`)

```python
class ElectricalPinType(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    BIDIRECTIONAL = "bidirectional"
    TRISTATE = "tristate"
    PASSIVE = "passive"
    FREE = "free"                  # Not Internally Connected
    UNSPECIFIED = "unspecified"
    POWER_IN = "power_in"
    POWER_OUT = "power_out"
    OPEN_COLLECTOR = "open_collector"
    OPEN_EMITTER = "open_emitter"
    NO_CONNECT = "no_connect"      # NC marker
```

### 3.3 `ErcViolation`(统一输出格式)

```python
@dataclass(frozen=True)
class ErcViolation:
    severity: Literal["error", "warning", "info"]
    type: str                      # e.g. "pin_conflict", "dangling_wire"
    code: str                      # 唯一错误码, e.g. "E001", "W042"
    message: str                   # 人类可读描述
    net_name: str | None           # 关联 net(如适用)
    component_refs: list[str]      # 涉及的 component reference
    pin_numbers: list[str]         # 涉及的 pin number
    position: tuple[float, float] | None
    sheet_path: str = "/"
    suggested_fix: str | None = None
```

### 3.4 `ErcReport`(顶层结果)

```python
@dataclass
class ErcReport:
    source_file: str
    engine: Literal["kicad-cli", "native"]
    kicad_version: str | None      # 仅 Level 1 填充
    violations: list[ErcViolation]
    duration_ms: float

    @property
    def error_count(self) -> int: ...
    @property
    def warning_count(self) -> int: ...
    def to_dict(self) -> dict: ...  # → JSON 序列化
```

### 3.5 `NetMap`(extractor 核心输出)

```python
NetMap = dict[str, list[PinContext]]   # net_name → pins on that net
```

匿名 net(无 label 也没有 power symbol)用自动生成的 id(如 `net_42`)作为 key。

---

## 4. 模块布局

新增 `circuit_graph/erc/` 包:

```
circuit_graph/erc/
├── __init__.py        # 公共 API
├── models.py          # PinContext, ErcViolation, ErcReport, ElectricalPinType
├── pin_matrix.py      # PinConflictMatrix (12×12)
├── extractor.py       # NetlistExtractor (S-expression → NetMap)
├── engine.py          # ElectricalRulesChecker + 4 validator
├── kicad_cli.py       # KicadCliRunner
└── render.py          # 终端美化(优先级最低,最末)
```

`cli.py` 新增 `@main.command(name="erc")` 注册到 `cg` 命令组。

---

## 5. 关键组件设计

### 5.1 `NetlistExtractor`(extractor.py)

**输入**: `.kicad_sch` 文件路径
**输出**: `(components: list[Component], nets: NetMap, warnings: list[str])`

**步骤**:

1. 用 `sexpdata.loads(content)` 解析整个 S-expression
2. 遍历顶层 `(lib_symbols ...)` → 提取每个 symbol 定义的所有 pin(number / name / electrical_type / 相对坐标)
3. 遍历顶层 `(symbol ...)` 实例 → 解析 `lib_id` / `Reference` / `Value` / `Footprint` / `(at x y rotation)` 位置
4. 计算每个 pin 绝对坐标 = `symbol.position + pin.relative_position`(考虑 rotation)
5. 遍历 `(wire ...)`,收集线段 `{start, end}` 集合
6. 遍历 `(junction ...)`,标记 T 型连接点
7. 遍历 `(label ...)` / `(hierarchical_label ...)` / `(global_label ...)` → 收集 name + position
8. **构建连通图**: 几何共点 + 同名 label 自动合并。用 `networkx.Graph`,node = `(x, y, label_name_or_None)`,edge = wire/junction
9. **Power symbol 合并**: 相同 `Value`(GND, +12V, ...)`(power global)` 的 symbol 自动归入同一 net
10. **导出 NetMap**: 连通分量 + 同名 label/power_value 合并后,`net_name → [PinContext]`

**未实现(后续)**:
- `(sheet ...)` 跨页连接(2.1.8)
- `(bus ...)` 总线(暂按普通 label 处理)

### 5.2 `PinConflictMatrix`(pin_matrix.py)

**默认矩阵**(对齐 KiCad `erc_settings.cpp`):

| Rule | Severity |
|------|----------|
| OUTPUT ↔ OUTPUT | ERROR |
| POWER_OUT ↔ POWER_OUT | ERROR |
| OUTPUT ↔ POWER_OUT | ERROR |
| NO_CONNECT ↔ {INPUT, OUTPUT, BIDI, TRISTATE, POWER_IN, POWER_OUT, OPEN_COLLECTOR, OPEN_EMITTER} | ERROR |
| UNSPECIFIED ↔ {INPUT, OUTPUT, BIDI, TRISTATE, PASSIVE, POWER_IN, POWER_OUT, OPEN_COLLECTOR, OPEN_EMITTER, UNSPECIFIED} | WARNING |
| TRISTATE ↔ OUTPUT | WARNING |
| TRISTATE ↔ TRISTATE | WARNING |
| PASSIVE ↔ {所有} | OK |
| FREE ↔ {所有} | OK |

**API**:
```python
class PinConflictMatrix:
    def check(self, t1: ElectricalPinType, t2: ElectricalPinType) -> Literal["ok", "warning", "error"]: ...
    def set_rule(self, t1, t2, severity) -> None: ...
    @classmethod
    def default(cls) -> "PinConflictMatrix": ...
```

### 5.3 `ElectricalRulesChecker`(engine.py)

**4 个 validator**:

1. **`PinTypeValidator`** — 遍历每个 net 的所有 pin pair,查 `PinConflictMatrix`。
   - 跳过 stacked pin(同 symbol 同 type 同 name 同位置)
2. **`ConnectivityValidator`** — 拓扑图遍历:
   - Dangling wire: 端点不在任何 pin / label / junction → WARNING
   - Single-pin net: 只有一个 pin 的 net → WARNING
   - Unconnected input: `INPUT` 类型 pin 端点无任何连接 → ERROR
3. **`ComponentValidator`**:
   - 重复 Reference(R1 出现两次)→ ERROR
   - 缺少 Value → WARNING
   - 缺少 Footprint → WARNING
4. **`PowerValidator`**:
   - `POWER_IN` pin 所在 net 缺少 `POWER_OUT` driver → ERROR
   - 电源 net 缺少 `PWR_FLAG` 标记 → INFO

**API**:
```python
class ElectricalRulesChecker:
    def __init__(self, components, nets, pin_matrix=None) -> None: ...
    def run(self) -> ErcReport: ...
    def run_check(self, check_name: str) -> list[ErcViolation]: ...
```

### 5.4 `KicadCliRunner`(kicad_cli.py)

**检测顺序**:
1. 环境变量 `KICAD_CLI_PATH`
2. `D:/Programs/KiCad/*/bin/kicad-cli.exe`(Windows)
3. `which kicad-cli`(PATH)
4. 不可用 → 抛 `KicadCliNotFoundError`

**API**:
```python
class KicadCliRunner:
    def __init__(self, binary_path: str | None = None, timeout: float = 60.0) -> None: ...
    def run_erc(self, sch_file: Path, *, severities: list[str] = None) -> ErcReport: ...
```

**输出解析**: `kicad-cli sch erc --format json --severity-all` → 读 `<stem>-erc.json` → 转换为 `ErcReport`。

**已知差异**: kicad-cli 默认输出 UTF-8,但 stderr 是 GBK 中文乱码;只关心 stdout + 输出文件。

### 5.5 `cg erc` CLI 接口

```
cg erc <file.kicad_sch>            # auto: native 优先, fallback kicad-cli
    --engine auto|native|kicad     # 默认 auto
    --format json|text             # 默认 json
    --severity error|warning|all   # 默认 all
    --output PATH                  # 写入文件而非 stdout
    --pretty                       # 终端美化(末位实现,见 §6)
```

**退出码**:
- `0` — 无 violation
- `1` — 有 ERROR 级 violation
- `2` — 引擎错误(native 解析失败 / kicad-cli 不可用)
- `64` — 用法错误(cli 校验失败)

---

## 6. 实施顺序(按依赖)

1. **`models.py` + `pin_matrix.py` + 单元测试** — 纯算法,无外部依赖
2. **`extractor.py` + 单元测试** — 用 `lm321_ccs/1A_CCS_LM321_BJT.kicad_sch` 验证能 parse 出 8 个 symbol
3. **`engine.py` 4 个 validator + 单元测试** — 手工构造 NetMap 测试
4. **`kicad_cli.py` + 单元测试** — 解析 kicad-cli 的 json 输出
5. **`cli.py` 注册 `cg erc`** — 端到端通,优先级错误时使用 Level 2 引擎
6. **README 更新 + 端到端测试** — 对 `lm321_ccs/1A_CCS_LM321_BJT.kicad_sch` 跑 Level 1 + Level 2,对比 4 个 violations 是否复现
7. **`render.py` 终端美化(末位)** — 不阻塞核心交付

每个步骤完成 → `commit + push`。

---

## 7. 验收标准

**Level 1**:
- ✅ `cg erc lm321_ccs/1A_CCS_LM321_BJT.kicad_sch --engine kicad` 返回 4 个 violations(2 × `power_pin_not_driven` + 1 × `lib_symbol_mismatch` + 1 × `multiple_net_names`)
- ✅ kicad-cli 不可用时给清晰降级提示
- ✅ `--format json|text` / `--severity error|warning|all` / `--output` 都正常

**Level 2**:
- ✅ `cg erc lm321_ccs/1A_CCS_LM321_BJT.kicad_sch --engine native` 至少复现 2 × `power_pin_not_driven` ERROR
- ✅ `lib_symbol_mismatch` 不强求复现(需要 KB 库支持,首版可降级为 INFO)
- ✅ `multiple_net_names` 复现为目标
- ✅ 单元测试覆盖率 ≥ 80%(extractor / engine / pin_matrix)
- ✅ 端到端测试通过

**集成**:
- ✅ `cg erc --engine auto` 默认走 native,失败时降级 kicad-cli
- ✅ 两种引擎输出格式完全统一(`ErcReport.to_dict()` 一致)
- ✅ README 含使用示例

---

## 8. 范围之外(后续 spec)

- Level 3 自定义领域规则
- `.kicad_sch` sheet 层级结构解析(2.1.8)
- ERC 结果回写到 `.kicad_sch` 的 `(marker ...)`(`--annotate`)
- `(bus ...)` 总线连接
- `--explain <violation_id>` 详细解释

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| sexpr 解析对 KiCad 9.0 新格式不兼容 | 优先用 `lm321_ccs/1A_CCS_LM321_BJT.kicad_sch`(已生成的 KiCad 9 格式)做 fixture |
| Native 引擎与 kicad-cli 差异 > 5% | 提交时同时跑两个引擎,在测试中报告 `agreement_rate` 指标 |
| 跨页 sheet 结构暂未支持 | 在 `ErcReport` 暴露 `unsupported_features: list[str]`,明确告知用户 |
| Power symbol 合并逻辑错误 | 用 LM321 fixture 验证:同 Value 的 GND symbol 归入同一 net |

---

## 10. 引用

- `docs/erc-todolist.md` — 实施路线图
- `E:\__electric\__research\kicad-sch-api\kicad_sch_api\validation\erc.py` — `ElectricalRulesChecker` 参考实现
- `E:\__electric\__research\kicad-sch-api\kicad_sch_api\validation\pin_matrix.py` — `PinConflictMatrix` 默认矩阵参考
- `E:\__electric\__research\kicad-sch-api\kicad_sch_api\core\parser.py` — SExpressionParser 参考
- KiCad 源码 `eeschema/erc/erc_settings.cpp` — 官方默认矩阵
