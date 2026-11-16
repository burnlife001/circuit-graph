# 基础电路拓扑识别扩展实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 circuit-graph 通过基元库 + VF2 子图匹配识别 15 个常见基础电路拓扑，引擎侧接入 `EdgeSemanticEnricher` 已算好的 `direction`/`role`/`level` 元数据（严格模式），并将识别结果以 `FunctionalBlock` 写回 IR。

**Architecture:** 两阶段交付。阶段一（Task 1–4）扩展引擎：`primitives.py` 加 `expected_role`/`expected_direction`/`expected_level` 三字段、`extract_volts` 与 `signal_semantic_matches` 纯函数、`PrimitiveTemplate._signal_to_components` 派生索引；`vf2_matcher.py` 新增 `_SemanticGraphMatcher` 子类重写 `semantic_feasibility` 钩子实现严格模式剪枝。阶段二（Task 5–8）在 `primitives/` 目录新增 15 个基元 JSON，每个配套 fixture 测试。

**Tech Stack:** Python 3.11+, NetworkX (`GraphMatcher`), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-08-17-basic-topology-recognition-design.md`

## Global Constraints

- **严格模式**：基元声明 `expected_*` 后，circuit 边必须存在 `edge_semantic` 且值匹配；缺失即拒绝（spec §3.2.2）
- **现有 6 个基元**（voltage-divider / non-inverting / inverting / differential / parallel-termination / type-c-cc-pull-down）**完全不动**（spec §3.2.3）
- **不可变数据**：`PrimitiveSignal` / `PrimitiveTemplate` 保持 `@dataclass(frozen=True)`；派生字段通过 `object.__setattr__` 在 `__post_init__` 填充
- **TDD**：每个任务先写失败测试，再写实现
- **提交频率**：每个 Task 完成后 `git commit`，提交信息用中文 + 类型前缀（`feat:` / `test:` / `refactor:`）
- **测试运行**：`uv run pytest <path> -v`（不用 `python -m pytest`，项目使用 uv）
- **`pin.number` 不参与匹配**：`primitive_to_bipartite_graph` 只用 `pin.net`，所以基元的 `"number"` 字段可以是任意字符串（沿用现有运放基元的写法，用引脚名）
- **`DeviceKind` 无 BJT/CRYSTAL/RELAY**：这些元件的 `type` 字段写 `"Unknown"`，靠 `name_pattern` + 结构约束

## 范围说明（与 spec 的差异）

- spec §3.3 组 1 原计划 5 个基元，含 `voltage-follower`。实现层面发现 VF2 bipgraph 不区分 pin number，无法表达"IN- 直连 OUT"的 pin 级约束，会把所有单输出运放误判为跟随器。**voltage-follower 移出本期范围**，组 1 实际 4 个基元，总数 16 → 15。
- spec §3.3 "每个基元的交付物" 提到 `tests/fixtures/primitives/<id>.json`，但项目现有 6 个基元的测试都用 `tests/fir_helpers.py` 的构造函数模式（无独立 fixture JSON 文件）。**沿用现有模式**，fixture 通过 `make_component` / `make_net` / `build_ir` 在测试函数内构造。

---

### Task 1: `primitives.py` Schema 扩展 + `extract_volts` + `signal_semantic_matches`

**Files:**
- Modify: `circuit_graph/primitives.py` (PrimitiveSignal dataclass, `_parse_signal`, 顶层新增 2 个函数)
- Test: `tests/test_primitives.py`

**Interfaces:**
- Consumes: 无（纯新增）
- Produces:
  - `PrimitiveSignal.expected_role: str | None`
  - `PrimitiveSignal.expected_direction: str | None`
  - `PrimitiveSignal.expected_level: str | None`
  - `extract_volts(s: str | None) -> Decimal | None`
  - `signal_semantic_matches(expected_role, expected_direction, expected_level, edge_semantic_dict) -> bool`

- [ ] **Step 1: 写 `extract_volts` 失败测试**

在 `tests/test_primitives.py` 末尾追加：

```python
def test_extract_volts_parses_plain_voltage_labels() -> None:
    """extract_volts handles '5V' / '3.3V' / '+5V' / '5' → Decimal."""
    from decimal import Decimal
    from circuit_graph.primitives import extract_volts

    assert extract_volts("5V") == Decimal("5")
    assert extract_volts("3.3V") == Decimal("3.3")
    assert extract_volts("+5V") == Decimal("5")
    assert extract_volts("5") == Decimal("5")
    assert extract_volts("5.0V") == Decimal("5.0")


def test_extract_volts_parses_digit_v_digit_shorthand() -> None:
    """extract_volts handles edge_semantic's 'V-as-decimal-point' shorthand."""
    from decimal import Decimal
    from circuit_graph.primitives import extract_volts

    # _extract_level in edge_semantic.py emits "3V3" for 3.3V, "1V8" for 1.8V, "1V25" for 1.25V
    assert extract_volts("3V3") == Decimal("3.3")
    assert extract_volts("1V8") == Decimal("1.8")
    assert extract_volts("1V25") == Decimal("1.25")


def test_extract_volts_rejects_garbage() -> None:
    """extract_volts returns None for unparseable input."""
    from circuit_graph.primitives import extract_volts

    assert extract_volts(None) is None
    assert extract_volts("") is None
    assert extract_volts("VCC") is None
    assert extract_volts("3.3.3V") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_primitives.py::test_extract_volts_parses_plain_voltage_labels -v`
Expected: FAIL with `ImportError: cannot import name 'extract_volts'`

- [ ] **Step 3: 实现 `extract_volts`**

在 `circuit_graph/primitives.py` 顶层（`import re` 之后、`_NodeKind` 之前）插入：

```python
from decimal import Decimal

_LEVEL_FRAC_RE = re.compile(r"^\+?(\d+)[Vv](\d+)$")
_LEVEL_PLAIN_RE = re.compile(r"^\+?(\d+(?:\.\d+)?)[Vv]?$")


def extract_volts(s: str | None) -> Decimal | None:
    """Parse a voltage label to a Decimal value in volts.

    Rules (first match wins):
      1. ``"<int>V<frac>"`` → ``int.frac``   (e.g. "3V3" → 3.3, "1V25" → 1.25)
      2. ``"[+]<float>[V]"`` → ``float``     (e.g. "5V" → 5.0, "3.3V" → 3.3, "5" → 5.0)
      3. otherwise → ``None``
    """
    if not s:
        return None
    s = s.strip()
    m = _LEVEL_FRAC_RE.match(s)
    if m:
        return Decimal(f"{m.group(1)}.{m.group(2)}")
    m = _LEVEL_PLAIN_RE.match(s)
    if m:
        return Decimal(m.group(1))
    return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_primitives.py -k extract_volts -v`
Expected: 3 passed

- [ ] **Step 5: 写 `signal_semantic_matches` 失败测试**

在 `tests/test_primitives.py` 末尾追加：

```python
def test_signal_semantic_matches_all_none_is_unconstrained() -> None:
    """When all expected_* are None, any edge_semantic passes."""
    from circuit_graph.primitives import signal_semantic_matches

    assert signal_semantic_matches(None, None, None, {"role": "power", "direction": "source", "level": "5V"})
    assert signal_semantic_matches(None, None, None, {"role": "digital", "direction": "load", "level": None})


def test_signal_semantic_matches_role_only() -> None:
    """expected_role alone matches against edge_semantic.role."""
    from circuit_graph.primitives import signal_semantic_matches

    assert signal_semantic_matches("power", None, None, {"role": "power", "direction": "source", "level": "5V"})
    assert not signal_semantic_matches("power", None, None, {"role": "digital", "direction": "source", "level": "5V"})


def test_signal_semantic_matches_direction_only() -> None:
    """expected_direction alone matches against edge_semantic.direction."""
    from circuit_graph.primitives import signal_semantic_matches

    assert signal_semantic_matches(None, "source", None, {"role": "power", "direction": "source", "level": "5V"})
    assert not signal_semantic_matches(None, "source", None, {"role": "power", "direction": "load", "level": "5V"})


def test_signal_semantic_matches_level_compares_decimally() -> None:
    """expected_level is compared as Decimal volts, not raw string."""
    from circuit_graph.primitives import signal_semantic_matches

    # "3.3V" in the primitive should match "3V3" from edge_semantic
    assert signal_semantic_matches(None, None, "3.3V", {"role": "power", "direction": "source", "level": "3V3"})
    assert signal_semantic_matches(None, None, "5V", {"role": "power", "direction": "source", "level": "5V"})
    assert not signal_semantic_matches(None, None, "5V", {"role": "power", "direction": "source", "level": "3V3"})


def test_signal_semantic_matches_none_field_skips_check() -> None:
    """None expected_* fields are skipped, others still enforced."""
    from circuit_graph.primitives import signal_semantic_matches

    # role matches, direction is None (skip), level matches
    assert signal_semantic_matches("power", None, "5V", {"role": "power", "direction": "load", "level": "5V"})
    # role matches, direction is None (skip), level mismatches
    assert not signal_semantic_matches("power", None, "5V", {"role": "power", "direction": "load", "level": "3V3"})


def test_signal_semantic_matches_unparseable_level_is_mismatch() -> None:
    """If either side's level fails to parse, treat as mismatch (strict)."""
    from circuit_graph.primitives import signal_semantic_matches

    # Primitive side unparseable → mismatch
    assert not signal_semantic_matches(None, None, "garbage", {"role": "power", "direction": "source", "level": "5V"})
    # Circuit side unparseable (None) → mismatch
    assert not signal_semantic_matches(None, None, "5V", {"role": "power", "direction": "source", "level": None})
```

- [ ] **Step 6: 跑测试确认失败**

Run: `uv run pytest tests/test_primitives.py -k signal_semantic_matches -v`
Expected: FAIL with `ImportError: cannot import name 'signal_semantic_matches'`

- [ ] **Step 7: 实现 `signal_semantic_matches`**

在 `circuit_graph/primitives.py` 顶层（`extract_volts` 之后）插入：

```python
def signal_semantic_matches(
    expected_role: str | None,
    expected_direction: str | None,
    expected_level: str | None,
    edge_semantic: dict[str, Any],
) -> bool:
    """Check one edge_semantic dict against the primitive's expected_* constraints.

    ``None`` expected fields are unconstrained and skipped. Level comparison is
    decimal-based via :func:`extract_volts`; if either side fails to parse, the
    pair is treated as a mismatch (strict mode).
    """
    if expected_role is not None and edge_semantic.get("role") != expected_role:
        return False
    if expected_direction is not None and edge_semantic.get("direction") != expected_direction:
        return False
    if expected_level is not None:
        expected_v = extract_volts(expected_level)
        actual_v = extract_volts(edge_semantic.get("level"))
        if expected_v is None or actual_v is None:
            return False
        if expected_v != actual_v:
            return False
    return True
```

- [ ] **Step 8: 跑测试确认通过**

Run: `uv run pytest tests/test_primitives.py -k signal_semantic_matches -v`
Expected: 6 passed

- [ ] **Step 9: 写 `PrimitiveSignal.expected_*` 字段解析失败测试**

在 `tests/test_primitives.py` 末尾追加：

```python
def test_parse_signal_reads_expected_fields() -> None:
    """_parse_signal picks up expected_role/direction/level when present."""
    from circuit_graph.primitives import _parse_signal

    raw = {
        "id": "vcc",
        "type": "net",
        "name_pattern": "^VCC",
        "expected_role": "power",
        "expected_direction": "source",
        "expected_level": "5V",
    }
    sig = _parse_signal(raw)
    assert sig.expected_role == "power"
    assert sig.expected_direction == "source"
    assert sig.expected_level == "5V"


def test_parse_signal_defaults_expected_fields_to_none() -> None:
    """Omitted expected_* fields default to None (backwards compatible)."""
    from circuit_graph.primitives import _parse_signal

    sig = _parse_signal({"id": "vcc", "type": "net", "name_pattern": "^VCC"})
    assert sig.expected_role is None
    assert sig.expected_direction is None
    assert sig.expected_level is None


def test_parse_signal_rejects_invalid_role() -> None:
    """expected_role outside the canonical set is rejected."""
    from circuit_graph.primitives import _parse_signal

    with pytest.raises(PrimitiveValidationError):
        _parse_signal({
            "id": "vcc", "type": "net", "name_pattern": "^VCC",
            "expected_role": "invalid_role",
        })


def test_parse_signal_rejects_invalid_direction() -> None:
    """expected_direction outside the canonical set is rejected."""
    from circuit_graph.primitives import _parse_signal

    with pytest.raises(PrimitiveValidationError):
        _parse_signal({
            "id": "vcc", "type": "net", "name_pattern": "^VCC",
            "expected_direction": "invalid_direction",
        })
```

- [ ] **Step 10: 跑测试确认失败**

Run: `uv run pytest tests/test_primitives.py -k parse_signal -v`
Expected: FAIL with `AttributeError: 'PrimitiveSignal' object has no attribute 'expected_role'`

- [ ] **Step 11: 扩展 `PrimitiveSignal` 与 `_parse_signal`**

在 `circuit_graph/primitives.py` 中：

修改 `PrimitiveSignal` dataclass（保留 `@dataclass(frozen=True)`）：

```python
_VALID_ROLES = frozenset({"power", "gnd", "analog", "digital", "clock"})
_VALID_DIRECTIONS = frozenset({"source", "load", "bidirectional"})


@dataclass(frozen=True)
class PrimitiveSignal:
    """A net that participates in the primitive boundary."""

    id: str
    type: str
    name_pattern: str
    required: bool = True
    expected_role: str | None = None
    expected_direction: str | None = None
    expected_level: str | None = None
```

修改 `_parse_signal`：

```python
def _parse_signal(raw: dict[str, Any]) -> PrimitiveSignal:
    _require_keys(raw, {"id", "type", "name_pattern"})
    expected_role = raw.get("expected_role")
    expected_direction = raw.get("expected_direction")
    expected_level = raw.get("expected_level")
    if expected_role is not None and expected_role not in _VALID_ROLES:
        raise PrimitiveValidationError(
            f"Invalid expected_role {expected_role!r}; must be one of {sorted(_VALID_ROLES)}"
        )
    if expected_direction is not None and expected_direction not in _VALID_DIRECTIONS:
        raise PrimitiveValidationError(
            f"Invalid expected_direction {expected_direction!r}; must be one of {sorted(_VALID_DIRECTIONS)}"
        )
    return PrimitiveSignal(
        id=raw["id"],
        type=raw["type"],
        name_pattern=raw["name_pattern"],
        required=raw.get("required", True),
        expected_role=expected_role,
        expected_direction=expected_direction,
        expected_level=expected_level,
    )
```

- [ ] **Step 12: 跑测试确认通过**

Run: `uv run pytest tests/test_primitives.py -v`
Expected: 全绿（含已有测试）

- [ ] **Step 13: Commit**

```bash
git add circuit_graph/primitives.py tests/test_primitives.py
git commit -m "feat(primitives): 扩展 PrimitiveSignal 支持 expected_role/direction/level

新增 extract_volts 与 signal_semantic_matches 两个模块级函数，为后续严格模式
语义匹配（VF2 semantic_feasibility 钩子）提供基础。现有 6 个基元未声明
expected_*，零回归。"
```

---

### Task 2: `PrimitiveTemplate._signal_to_components` 派生索引

**Files:**
- Modify: `circuit_graph/primitives.py` (PrimitiveTemplate dataclass, 新增 `__post_init__`)
- Test: `tests/test_primitives.py`

**Interfaces:**
- Consumes: Task 1 的 `PrimitiveSignal.expected_*`
- Produces: `PrimitiveTemplate._signal_to_components: dict[str, list[str]]` — key 为 signal id，value 为通过 pin 连到该 signal 的 component id 排序列表

- [ ] **Step 1: 写派生索引失败测试**

在 `tests/test_primitives.py` 末尾追加：

```python
def test_template_signal_to_components_derived() -> None:
    """PrimitiveTemplate derives signal_id → [component_id, ...] from pins."""
    template = next(t for t in load_primitives() if t.id == "voltage-divider")
    # voltage-divider 中：vin 连到 R_top, vout 连到 R_top+R_bottom, gnd 连到 R_bottom
    assert template._signal_to_components["vin"] == ["R_top"]
    assert template._signal_to_components["vout"] == ["R_bottom", "R_top"]
    assert template._signal_to_components["gnd"] == ["R_bottom"]


def test_template_signal_to_components_deterministic() -> None:
    """Derived index is sorted so repeated loads give identical lists."""
    t1 = next(t for t in load_primitives() if t.id == "non-inverting-amplifier")
    t2 = next(t for t in load_primitives() if t.id == "non-inverting-amplifier")
    assert t1._signal_to_components == t2._signal_to_components


def test_template_signal_to_components_empty_for_unconnected_signal() -> None:
    """A signal no component pin references gets an empty list."""
    from circuit_graph.primitives import _parse_primitive

    raw = {
        "id": "orphan-signal",
        "name": "Orphan",
        "signals": [
            {"id": "unused", "type": "net", "name_pattern": "^UNUSED$"},
            {"id": "used", "type": "net", "name_pattern": "^USED$"},
        ],
        "components": [
            {
                "id": "R1", "type": "Resistor",
                "pins": [{"number": "1", "net": "used"}, {"number": "2", "net": "used"}],
            }
        ],
    }
    template = _parse_primitive(raw)
    assert template._signal_to_components["unused"] == []
    assert template._signal_to_components["used"] == ["R1"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_primitives.py -k signal_to_components -v`
Expected: FAIL with `AttributeError: 'PrimitiveTemplate' object has no attribute '_signal_to_components'`

- [ ] **Step 3: 实现 `PrimitiveTemplate.__post_init__`**

修改 `circuit_graph/primitives.py` 中 `PrimitiveTemplate` dataclass：

```python
@dataclass(frozen=True)
class PrimitiveTemplate:
    """In-memory representation of a primitive JSON template."""

    id: str
    name: str
    description: str
    role: str
    version: str
    signals: list[PrimitiveSignal]
    components: list[PrimitiveComponent]
    constraints: PrimitiveConstraints
    scoring: PrimitiveScoring
    raw: dict[str, Any]
    _signal_to_components: dict[str, list[str]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Populate ``_signal_to_components`` from components[*].pins[*].net.

        The dataclass is frozen, so we use ``object.__setattr__`` to set the
        derived index. The mapping is sorted to guarantee determinism across
        parser runs.
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

确认 `from collections import defaultdict` 已在文件顶部（若没有则加上）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_primitives.py -v`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/primitives.py tests/test_primitives.py
git commit -m "feat(primitives): PrimitiveTemplate 派生 _signal_to_components 索引

在 __post_init__ 中根据 components[*].pins[*].net 一次性算出
signal_id → [component_id, ...] 映射，供严格模式 semantic_feasibility
钩子查询 primitive 内每个 signal 关联的 component 列表。"
```

---

### Task 3: `vf2_matcher.py` `_SemanticGraphMatcher` + 严格模式集成

**Files:**
- Modify: `circuit_graph/vf2_matcher.py` (`Vf2TopologyMatcher.__init__`, `find_matches`, 新增 `_SemanticGraphMatcher` 内部类与 `_match_signal_semantics` 模块级函数)
- Test: `tests/test_vf2_matcher.py`

**Interfaces:**
- Consumes: Task 1 的 `signal_semantic_matches`、Task 2 的 `template._signal_to_components`、`circuit_graph.ir.EdgeSemantic`、`circuit_graph.ir.parse_pin_id`
- Produces:
  - `_SemanticGraphMatcher(GraphMatcher)`（内部类，仅在 `find_matches` 内使用）
  - `_match_signal_semantics(template, prim_signal, circ_net_id, partial_mapping, edge_semantics) -> bool`

- [ ] **Step 1: 扩展 `tests/fir_helpers.py` 支持 edge_semantic 注入**

严格模式测试需要 fixture 带 `edge_semantic` 元数据。修改 `tests/fir_helpers.py`：

```python
def build_ir(
    components: list[Component],
    nets: list[Net],
    wiring: Iterable[tuple[str, str]],
    *,
    source: str = "synthetic-fixture",
    edge_semantics: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> CircuitIR:
    """Wire components to nets and return a CircuitIR.

    ``wiring`` is an iterable of ``(component_id.pin_number, net_id)``
    tuples — same shape the protel parser produces.

    ``edge_semantics`` optionally maps ``(pin_id, net_id)`` to a dict that will
    be stored as ``metadata["edge_semantic"]`` on the corresponding Connection,
    emulating what EdgeSemanticEnricher produces.
    """
    edge_semantics = edge_semantics or {}
    connections = []
    for pin_ref, net_id in wiring:
        sem = edge_semantics.get((pin_ref, net_id))
        metadata = {"edge_semantic": sem} if sem is not None else {}
        connections.append(Connection(pin_id=pin_ref, net_id=net_id, metadata=metadata))
    return CircuitIR(
        meta=CircuitMeta(source=source, format="synthetic"),
        components=components,
        nets=nets,
        connections=connections,
        blocks=[],
        semantic_edges=[],
    )
```

同时更新文件顶部 import：`from typing import Any, Iterable`。

确认现有调用方零回归：跑 `uv run pytest tests/ -v --co -q | tail -5` 确认无 import 错误。

- [ ] **Step 2: 写严格模式正向匹配失败测试**

在 `tests/test_vf2_matcher.py` 末尾追加（fixture：一个电阻两端，一端 `VCC` (power) 一端 `GPIO` (digital)，验证 pull-up 识别）。**注意**：此时 `pull-up-resistor.json` 基元还不存在，我们先创建一个**测试专用的内联模板**：

```python
def _make_pull_up_template():
    """Inline primitive used to drive strict-mode tests (not a real library entry)."""
    from circuit_graph.primitives import (
        PrimitiveComponent,
        PrimitiveConstraints,
        PrimitivePin,
        PrimitiveScoring,
        PrimitiveSignal,
        PrimitiveTemplate,
    )

    return PrimitiveTemplate(
        id="test-pull-up",
        name="Test Pull-Up",
        description="",
        role="unknown",
        version="0.1.0",
        signals=[
            PrimitiveSignal(
                id="vcc", type="net", name_pattern="^VCC$",
                expected_role="power", expected_direction="bidirectional",
            ),
            PrimitiveSignal(
                id="gpio", type="net", name_pattern="^GPIO",
                expected_role="digital",
            ),
        ],
        components=[
            PrimitiveComponent(
                id="R_pull", type="Resistor",
                pins=[
                    PrimitivePin(number="1", net="vcc"),
                    PrimitivePin(number="2", net="gpio"),
                ],
            ),
        ],
        constraints=PrimitiveConstraints(),
        scoring=PrimitiveScoring(),
        raw={},
    )


def _pull_up_ir(*, with_semantics: bool = True, vcc_role: str = "power"):
    """Build a 1-resistor IR; resistor between VCC and GPIO nets."""
    r = make_component(
        "R1", DeviceKind.RESISTOR,
        [("1", "VCC"), ("2", "GPIO1")],
        raw_value="10K",
    )
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    gpio = make_net("N_GPIO", "GPIO1", NetType.SIGNAL)
    edge_semantics = {}
    if with_semantics:
        edge_semantics = {
            ("R1.1", "N_VCC"):  {"role": vcc_role, "direction": "bidirectional", "level": "5V"},
            ("R1.2", "N_GPIO"): {"role": "digital", "direction": "bidirectional", "level": None},
        }
    return build_ir([r], [vcc, gpio],
                    [("R1.1", "N_VCC"), ("R1.2", "N_GPIO")],
                    edge_semantics=edge_semantics)


def test_strict_mode_role_match_passes():
    """When edge_semantic matches expected_role, the primitive matches."""
    matcher = Vf2TopologyMatcher(_pull_up_ir(with_semantics=True, vcc_role="power"))
    result = matcher.find_matches(_make_pull_up_template(), timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].signal_mapping["vcc"] == "N_VCC"
    assert result.matches[0].signal_mapping["gpio"] == "N_GPIO"


def test_strict_mode_role_mismatch_rejected():
    """When edge_semantic.role disagrees with expected_role, no match."""
    matcher = Vf2TopologyMatcher(_pull_up_ir(with_semantics=True, vcc_role="digital"))
    result = matcher.find_matches(_make_pull_up_template(), timeout_seconds=2.0)

    assert not result.aborted
    assert result.matches == []


def test_strict_mode_missing_edge_semantic_rejected():
    """Strict mode: edges without edge_semantic metadata are rejected."""
    matcher = Vf2TopologyMatcher(_pull_up_ir(with_semantics=False))
    result = matcher.find_matches(_make_pull_up_template(), timeout_seconds=2.0)

    assert not result.aborted
    assert result.matches == []
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_vf2_matcher.py -k strict_mode -v`
Expected: 3 个 FAIL — 前两个目前会通过因为还没做严格剪枝，第三个也应该失败（因为缺语义但仍会匹配）。具体失败原因：当前 `find_matches` 不感知 `expected_*`，所以三个测试都不会按预期拒绝/通过。

实际上前两个**会误通过**（因为匹配成功），第三个**会误通过**（因为匹配成功）。这三个测试的真正"失败信号"是：

- `test_strict_mode_role_mismatch_rejected`：期望 `matches == []`，实际会有 1 个 match → FAIL
- `test_strict_mode_missing_edge_semantic_rejected`：期望 `matches == []`，实际会有 1 个 match → FAIL
- `test_strict_mode_role_match_passes`：期望 1 个 match，实际也是 1 个 → PASS（巧合）

确认至少 2 个 FAIL。

- [ ] **Step 4: 实现 `_match_signal_semantics` 模块级函数**

在 `circuit_graph/vf2_matcher.py` 顶部 import 之后、`_node_match_circuit_to_primitive` 之前插入：

```python
from circuit_graph.primitives import (
    PrimitiveTemplate,
    PrimitiveSignal,
    circuit_to_bipartite_graph,
    component_type_matches,
    primitive_to_bipartite_graph,
    signal_name_matches,
    signal_semantic_matches,
)
from circuit_graph.ir import CircuitIR, Component, Net, parse_pin_id


def _match_signal_semantics(
    template: PrimitiveTemplate,
    prim_signal: PrimitiveSignal,
    circ_net_id: str,
    partial_mapping: dict[str, str],
    edge_semantics: dict[tuple[str, str], dict[str, Any]],
) -> bool:
    """Verify the candidate circuit net satisfies the primitive signal's expected_*.

    For each template-side component linked to ``prim_signal``, look up its
    mapped circuit-side component in ``partial_mapping`` (which maps
    ``"c:<template_comp_id>" -> "c:<circuit_comp_id>"``). Components not yet
    mapped are skipped — VF2 will map them later, and feasibility will be
    re-evaluated then.

    For each mapped pair, look up ``(circuit_comp_id, circ_net_id)`` in
    ``edge_semantics``; absence is a strict-mode rejection. Otherwise call
    :func:`signal_semantic_matches` to compare fields.

    Returns True iff every mapped pair passes.
    """
    linked_template_comp_ids = template._signal_to_components.get(prim_signal.id, [])
    for template_comp_id in linked_template_comp_ids:
        circuit_node_key = partial_mapping.get(f"c:{template_comp_id}")
        if circuit_node_key is None:
            continue  # not yet mapped — defer judgement
        circuit_comp_id = circuit_node_key[2:] if circuit_node_key.startswith("c:") else circuit_node_key
        sem = edge_semantics.get((circuit_comp_id, circ_net_id))
        if sem is None:
            return False  # strict mode: edge_semantic required
        if not signal_semantic_matches(
            prim_signal.expected_role,
            prim_signal.expected_direction,
            prim_signal.expected_level,
            sem,
        ):
            return False
    return True
```

- [ ] **Step 5: 实现 `_SemanticGraphMatcher` 子类**

继续在 `circuit_graph/vf2_matcher.py` 中（`_match_signal_semantics` 之后）插入：

```python
class _SemanticGraphMatcher(GraphMatcher):
    """GraphMatcher that enforces primitive expected_* semantics during VF2.

    NetworkX's ``node_match`` callback receives only the two node attribute
    dicts and cannot see the current partial mapping. ``semantic_feasibility``
    is the hook NetworkX calls every time it considers extending the mapping;
    it has access to ``self.core_1`` / ``self.core_2`` (the partial mapping),
    which lets us validate edge semantics at each step and prune early.
    """

    def __init__(
        self,
        circuit_graph: nx.Graph,
        primitive_graph: nx.Graph,
        *,
        template: PrimitiveTemplate,
        edge_semantics: dict[tuple[str, str], dict[str, Any]],
        node_match: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
    ):
        super().__init__(circuit_graph, primitive_graph, node_match=node_match)
        self._template = template
        self._edge_semantics = edge_semantics

    def semantic_feasibility(self, G1_node: str, G2_node: str) -> bool:
        if not super().semantic_feasibility(G1_node, G2_node):
            return False
        # G1 = circuit graph, G2 = primitive graph (NetworkX convention).
        prim_attrs = self.G2.nodes[G2_node]
        if prim_attrs.get("kind") != "net":
            return True
        prim_signal: PrimitiveSignal = prim_attrs["data"]
        if (
            prim_signal.expected_role is None
            and prim_signal.expected_direction is None
            and prim_signal.expected_level is None
        ):
            return True
        circ_net_id = G1_node[2:] if G1_node.startswith("n:") else G1_node
        # core_2 maps primitive-graph node -> circuit-graph node.
        return _match_signal_semantics(
            template=self._template,
            prim_signal=prim_signal,
            circ_net_id=circ_net_id,
            partial_mapping=self.core_2,
            edge_semantics=self._edge_semantics,
        )
```

确认 `import networkx as nx` 已在文件顶部（若没有则加上）。

- [ ] **Step 6: 修改 `Vf2TopologyMatcher.__init__` 构建 edge_semantic 索引**

在 `circuit_graph/vf2_matcher.py` 中 `Vf2TopologyMatcher.__init__` 末尾追加：

```python
def __init__(self, ir: CircuitIR):
    self.ir = ir
    self.circuit_graph = circuit_to_bipartite_graph(ir)
    self.component_map: dict[str, Component] = {c.id: c for c in ir.components}
    self.net_map: dict[str, Net] = {n.id: n for n in ir.nets}
    # Index edge_semantic metadata by (component_id, net_id) for strict-mode checks.
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

- [ ] **Step 7: 修改 `find_matches` 使用 `_SemanticGraphMatcher`**

在 `circuit_graph/vf2_matcher.py` 中 `find_matches` 内，把：

```python
gm = GraphMatcher(
    self.circuit_graph,
    primitive_graph,
    node_match=node_match,
)
```

替换为：

```python
gm = _SemanticGraphMatcher(
    self.circuit_graph,
    primitive_graph,
    template=template,
    edge_semantics=self._edge_semantics,
    node_match=node_match,
)
```

- [ ] **Step 8: 跑严格模式测试确认通过**

Run: `uv run pytest tests/test_vf2_matcher.py -k strict_mode -v`
Expected: 3 passed

- [ ] **Step 9: 跑全部 vf2_matcher 测试确认零回归**

Run: `uv run pytest tests/test_vf2_matcher.py -v`
Expected: 全绿（含原有 5 个测试）

- [ ] **Step 10: 跑 enricher 管线集成测试**

Run: `uv run pytest tests/enrichers/test_enrichers.py -v`
Expected: 全绿

- [ ] **Step 11: Commit**

```bash
git add circuit_graph/vf2_matcher.py tests/fir_helpers.py tests/test_vf2_matcher.py
git commit -m "feat(vf2): 严格模式语义匹配 — _SemanticGraphMatcher 子类化

VF2 通过 semantic_feasibility 钩子在每次扩展 mapping 时验证
primitive signal 的 expected_role/direction/level 是否被 candidate
circuit net 的 edge_semantic 满足；缺失或 mismatched 即剪枝。
fir_helpers.build_ir 新增 edge_semantics 参数支持 fixture 注入元数据。"
```

---

### Task 4: 新建 `primitives/schema.json`

**Files:**
- Create: `primitives/schema.json`

**Interfaces:**
- Consumes: Task 1 的字段定义
- Produces: JSON Schema 供后续基元 JSON 引用（`"$schema": "./schema.json"`）

- [ ] **Step 1: 创建 schema.json**

写入 `primitives/schema.json`：

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "circuit-graph topology primitive",
  "type": "object",
  "required": ["id", "name", "signals", "components"],
  "properties": {
    "$schema": {"type": "string"},
    "id": {"type": "string", "pattern": "^[a-z0-9-]+$"},
    "name": {"type": "string"},
    "description": {"type": "string"},
    "role": {"type": "string"},
    "version": {"type": "string"},
    "signals": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "type", "name_pattern"],
        "properties": {
          "id": {"type": "string"},
          "type": {"type": "string", "enum": ["net"]},
          "name_pattern": {"type": "string"},
          "required": {"type": "boolean", "default": true},
          "expected_role": {
            "type": ["string", "null"],
            "enum": ["power", "gnd", "analog", "digital", "clock", null]
          },
          "expected_direction": {
            "type": ["string", "null"],
            "enum": ["source", "load", "bidirectional", null]
          },
          "expected_level": {"type": ["string", "null"]}
        }
      }
    },
    "components": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "type", "pins"],
        "properties": {
          "id": {"type": "string"},
          "type": {"type": "string"},
          "required": {"type": "boolean", "default": true},
          "value_pattern": {"type": ["string", "null"]},
          "pins": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["number", "net"],
              "properties": {
                "number": {"type": ["string", "integer"]},
                "net": {"type": "string"}
              }
            }
          }
        }
      }
    },
    "constraints": {
      "type": "object",
      "properties": {
        "min_components": {"type": "integer", "minimum": 1},
        "exact_component_count": {"type": "boolean"}
      }
    },
    "scoring": {
      "type": "object",
      "properties": {
        "weights": {"type": "object"},
        "thresholds": {"type": "object"}
      }
    }
  }
}
```

- [ ] **Step 2: 验证现有 6 个基元仍可加载**

Run: `uv run pytest tests/test_primitives.py::test_load_primitives_finds_type_c_template -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add primitives/schema.json
git commit -m "feat(primitives): 新增 schema.json 定义基元模板 JSON Schema

覆盖既有字段 + 新增 expected_role/direction/level 三字段（可选）。
现有 6 个基元已通过 \$schema 引用此文件，此前文件缺失。"
```

---

### Task 5: 组 1 基元（rc-low-pass / rc-high-pass / crystal-with-load-caps / bridge-rectifier）

**Files:**
- Create: `primitives/rc-low-pass.json`, `primitives/rc-high-pass.json`, `primitives/crystal-with-load-caps.json`, `primitives/bridge-rectifier.json`
- Test: `tests/test_vf2_matcher.py`

**Interfaces:**
- Consumes: Task 1–4 全部产物
- Produces: 4 个新基元 ID 可被 `load_primitives()` 加载

- [ ] **Step 1: 创建 `primitives/rc-low-pass.json`**

```json
{
  "$schema": "./schema.json",
  "id": "rc-low-pass",
  "name": "RC 低通滤波器",
  "description": "电阻串联、电容从输出节点到地：低频通过、高频被电容旁路。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {"id": "vin",  "type": "net", "name_pattern": ".*", "required": true},
    {"id": "vout", "type": "net", "name_pattern": ".*", "required": true},
    {"id": "gnd",  "type": "net", "name_pattern": "^(GND|PGND|AGND|DGND|VSS)$", "required": true}
  ],
  "components": [
    {
      "id": "R_series", "type": "Resistor", "required": true,
      "pins": [
        {"number": "1", "net": "vin"},
        {"number": "2", "net": "vout"}
      ]
    },
    {
      "id": "C_shunt", "type": "Capacitor", "required": true,
      "pins": [
        {"number": "1", "net": "vout"},
        {"number": "2", "net": "gnd"}
      ]
    }
  ],
  "constraints": {"min_components": 2, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.45, "naming": 0.10, "parametric": 0.20, "type": 0.25},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

- [ ] **Step 2: 创建 `primitives/rc-high-pass.json`**

```json
{
  "$schema": "./schema.json",
  "id": "rc-high-pass",
  "name": "RC 高通滤波器",
  "description": "电容串联、电阻从输出节点到地：高频通过、低频/直流被电容阻断。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {"id": "vin",  "type": "net", "name_pattern": ".*", "required": true},
    {"id": "vout", "type": "net", "name_pattern": ".*", "required": true},
    {"id": "gnd",  "type": "net", "name_pattern": "^(GND|PGND|AGND|DGND|VSS)$", "required": true}
  ],
  "components": [
    {
      "id": "C_series", "type": "Capacitor", "required": true,
      "pins": [
        {"number": "1", "net": "vin"},
        {"number": "2", "net": "vout"}
      ]
    },
    {
      "id": "R_shunt", "type": "Resistor", "required": true,
      "pins": [
        {"number": "1", "net": "vout"},
        {"number": "2", "net": "gnd"}
      ]
    }
  ],
  "constraints": {"min_components": 2, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.45, "naming": 0.10, "parametric": 0.20, "type": 0.25},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

- [ ] **Step 3: 创建 `primitives/crystal-with-load-caps.json`**

```json
{
  "$schema": "./schema.json",
  "id": "crystal-with-load-caps",
  "name": "晶振 + 负载电容",
  "description": "晶体 / 陶瓷谐振器两端各接一个负载电容到地（Pierce 振荡器拓扑）。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {"id": "xtal_p", "type": "net", "name_pattern": "(XTAL|OSC|XIN|XI|CLK_IN|XT1).*", "required": true},
    {"id": "xtal_n", "type": "net", "name_pattern": "(XTAL|OSC|XOUT|XO|CLK_OUT|XT2).*", "required": true},
    {"id": "gnd",    "type": "net", "name_pattern": "^(GND|PGND|AGND|DGND|VSS)$", "required": true}
  ],
  "components": [
    {
      "id": "X1", "type": "Unknown", "required": true,
      "pins": [
        {"number": "1", "net": "xtal_p"},
        {"number": "2", "net": "xtal_n"}
      ]
    },
    {
      "id": "C_p", "type": "Capacitor", "required": true, "value_pattern": "^\\d+\\s*p?F",
      "pins": [
        {"number": "1", "net": "xtal_p"},
        {"number": "2", "net": "gnd"}
      ]
    },
    {
      "id": "C_n", "type": "Capacitor", "required": true, "value_pattern": "^\\d+\\s*p?F",
      "pins": [
        {"number": "1", "net": "xtal_n"},
        {"number": "2", "net": "gnd"}
      ]
    }
  ],
  "constraints": {"min_components": 3, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.35, "naming": 0.30, "parametric": 0.20, "type": 0.15},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

**注**：`X1` 的 `type` 用 `"Unknown"` 因为 `DeviceKind` 无 `CRYSTAL`。`name_pattern` 用宽松模式（含 `XTAL`/`OSC`/`XIN`/`XOUT` 等）。

- [ ] **Step 4: 创建 `primitives/bridge-rectifier.json`**

```json
{
  "$schema": "./schema.json",
  "id": "bridge-rectifier",
  "name": "桥式整流器",
  "description": "4 个二极管组成全波整流桥：2 个 AC 输入，1 个 DC+ 输出，1 个 DC- 输出（通常接地）。",
  "role": "power",
  "version": "0.1.0",
  "signals": [
    {"id": "ac1",     "type": "net", "name_pattern": "(AC|AC1|AC_L|AC_N|L|N)$", "required": true},
    {"id": "ac2",     "type": "net", "name_pattern": "(AC|AC2|AC_L|AC_N|L|N)$", "required": true},
    {"id": "dc_plus", "type": "net", "name_pattern": "(VDC|\\+V|DC\\+|VOUT|VBUS|\\+DC).*", "required": true},
    {"id": "dc_minus","type": "net", "name_pattern": "^(GND|PGND|VSS|DC\\-)$", "required": true}
  ],
  "components": [
    {
      "id": "D1", "type": "Diode", "required": true,
      "pins": [{"number": "A", "net": "ac1"}, {"number": "K", "net": "dc_plus"}]
    },
    {
      "id": "D2", "type": "Diode", "required": true,
      "pins": [{"number": "A", "net": "ac2"}, {"number": "K", "net": "dc_plus"}]
    },
    {
      "id": "D3", "type": "Diode", "required": true,
      "pins": [{"number": "A", "net": "dc_minus"}, {"number": "K", "net": "ac1"}]
    },
    {
      "id": "D4", "type": "Diode", "required": true,
      "pins": [{"number": "A", "net": "dc_minus"}, {"number": "K", "net": "ac2"}]
    }
  ],
  "constraints": {"min_components": 4, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.40, "naming": 0.30, "parametric": 0.10, "type": 0.20},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

**注**：`"number"` 字段 `A`/`K` 不参与匹配（VF2 忽略 pin number），仅作文档说明。

- [ ] **Step 5: 写 4 个基元的正向匹配测试**

在 `tests/test_vf2_matcher.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# Group 1 primitives: rc-low-pass / rc-high-pass / crystal / bridge-rectifier
# ---------------------------------------------------------------------------

def _rc_low_pass_ir():
    r = make_component("R10", DeviceKind.RESISTOR, [("1", "VIN"), ("2", "VOUT")], raw_value="1K")
    c = make_component("C10", DeviceKind.CAPACITOR, [("1", "VOUT"), ("2", "GND")], raw_value="100nF")
    vin = make_net("N_VIN", "VIN", NetType.SIGNAL)
    vout = make_net("N_VOUT", "VOUT", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [r, c], [vin, vout, gnd],
        [("R10.1", "N_VIN"), ("R10.2", "N_VOUT"),
         ("C10.1", "N_VOUT"), ("C10.2", "N_GND")],
    )


def test_find_matches_rc_low_pass():
    template = next(t for t in load_primitives() if t.id == "rc-low-pass")
    matcher = Vf2TopologyMatcher(_rc_low_pass_ir())
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    sig = result.matches[0].signal_mapping
    comp = result.matches[0].component_mapping
    assert comp["R_series"] == "R10"
    assert comp["C_shunt"] == "C10"
    assert sig["vout"] == "N_VOUT"
    assert sig["gnd"] == "N_GND"


def _rc_high_pass_ir():
    c = make_component("C20", DeviceKind.CAPACITOR, [("1", "VIN"), ("2", "VOUT")], raw_value="100nF")
    r = make_component("R20", DeviceKind.RESISTOR, [("1", "VOUT"), ("2", "GND")], raw_value="10K")
    vin = make_net("N_VIN", "VIN", NetType.SIGNAL)
    vout = make_net("N_VOUT", "VOUT", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [c, r], [vin, vout, gnd],
        [("C20.1", "N_VIN"), ("C20.2", "N_VOUT"),
         ("R20.1", "N_VOUT"), ("R20.2", "N_GND")],
    )


def test_find_matches_rc_high_pass():
    template = next(t for t in load_primitives() if t.id == "rc-high-pass")
    matcher = Vf2TopologyMatcher(_rc_high_pass_ir())
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    assert comp["C_series"] == "C20"
    assert comp["R_shunt"] == "R20"


def _crystal_ir():
    x = make_component("X1", DeviceKind.UNKNOWN, [("1", "XTAL1"), ("2", "XTAL2")], raw_value="8MHz")
    cp = make_component("C30", DeviceKind.CAPACITOR, [("1", "XTAL1"), ("2", "GND")], raw_value="22pF")
    cn = make_component("C31", DeviceKind.CAPACITOR, [("1", "XTAL2"), ("2", "GND")], raw_value="22pF")
    xp = make_net("N_XP", "XTAL1", NetType.SIGNAL)
    xn = make_net("N_XN", "XTAL2", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [x, cp, cn], [xp, xn, gnd],
        [("X1.1", "N_XP"), ("X1.2", "N_XN"),
         ("C30.1", "N_XP"), ("C30.2", "N_GND"),
         ("C31.1", "N_XN"), ("C31.2", "N_GND")],
    )


def test_find_matches_crystal_with_load_caps():
    template = next(t for t in load_primitives() if t.id == "crystal-with-load-caps")
    matcher = Vf2TopologyMatcher(_crystal_ir())
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    assert comp["X1"] == "X1"
    assert {comp["C_p"], comp["C_n"]} == {"C30", "C31"}


def _bridge_rectifier_ir():
    d1 = make_component("D1", DeviceKind.DIODE, [("A", "AC1"), ("K", "VDC")], raw_value="1N4007")
    d2 = make_component("D2", DeviceKind.DIODE, [("A", "AC2"), ("K", "VDC")], raw_value="1N4007")
    d3 = make_component("D3", DeviceKind.DIODE, [("A", "GND"), ("K", "AC1")], raw_value="1N4007")
    d4 = make_component("D4", DeviceKind.DIODE, [("A", "GND"), ("K", "AC2")], raw_value="1N4007")
    ac1 = make_net("N_AC1", "AC1", NetType.SIGNAL)
    ac2 = make_net("N_AC2", "AC2", NetType.SIGNAL)
    vdc = make_net("N_VDC", "VDC_OUT", NetType.POWER)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [d1, d2, d3, d4], [ac1, ac2, vdc, gnd],
        [("D1.A", "N_AC1"), ("D1.K", "N_VDC"),
         ("D2.A", "N_AC2"), ("D2.K", "N_VDC"),
         ("D3.A", "N_GND"), ("D3.K", "N_AC1"),
         ("D4.A", "N_GND"), ("D4.K", "N_AC2")],
    )


def test_find_matches_bridge_rectifier():
    template = next(t for t in load_primitives() if t.id == "bridge-rectifier")
    matcher = Vf2TopologyMatcher(_bridge_rectifier_ir())
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    # All four diodes must be in the mapping
    assert {comp["D1"], comp["D2"], comp["D3"], comp["D4"]} == {"D1", "D2", "D3", "D4"}
```

- [ ] **Step 6: 跑组 1 测试**

Run: `uv run pytest tests/test_vf2_matcher.py -k "rc_low_pass or rc_high_pass or crystal or bridge_rectifier" -v`
Expected: 4 passed

若有 FAIL，检查 `name_pattern` 是否匹配 fixture 中的 net 名。常见失败：bridge-rectifier 的 `ac1` 模式 `(AC|AC1|AC_L|AC_N|L|N)$` 中 `N$` 会误匹配 `VIN`；调整为更严格模式如 `^(AC1?|AC_L)$`。

- [ ] **Step 7: 跑完整 test_vf2_matcher 确认零回归**

Run: `uv run pytest tests/test_vf2_matcher.py -v`
Expected: 全绿

- [ ] **Step 8: Commit**

```bash
git add primitives/rc-low-pass.json primitives/rc-high-pass.json primitives/crystal-with-load-caps.json primitives/bridge-rectifier.json tests/test_vf2_matcher.py
git commit -m "feat(primitives): 组 1 新增 4 个无源/二极管基元

rc-low-pass / rc-high-pass / crystal-with-load-caps / bridge-rectifier
均仅依赖结构 + name_pattern，无需 expected_* 字段。"
```

---

### Task 6: 组 2 基元（pull-up / pull-down / tvs / npn-low / nmos-low / pmos-high）

**Files:**
- Create: `primitives/pull-up-resistor.json`, `primitives/pull-down-resistor.json`, `primitives/tvs-protection.json`, `primitives/npn-low-side-switch.json`, `primitives/nmos-low-side-switch.json`, `primitives/pmos-high-side-switch.json`
- Test: `tests/test_vf2_matcher.py`

**Interfaces:**
- Consumes: Task 1–5 全部产物（尤其严格模式 `_SemanticGraphMatcher`）
- Produces: 6 个新基元 ID

- [ ] **Step 1: 创建 `primitives/pull-up-resistor.json`**

```json
{
  "$schema": "./schema.json",
  "id": "pull-up-resistor",
  "name": "上拉电阻",
  "description": "电阻一端接电源（role=power），另一端接数字信号（role=digital），将信号默认拉到高电平。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {
      "id": "vcc", "type": "net",
      "name_pattern": "^(VCC|VDD|VBUS|\\+?\\d+\\.?\\d*V|\\+?\\d+V\\d+|3V3|5V).*",
      "expected_role": "power",
      "required": true
    },
    {
      "id": "signal", "type": "net",
      "name_pattern": ".*",
      "expected_role": "digital",
      "required": true
    }
  ],
  "components": [
    {
      "id": "R_pull", "type": "Resistor", "required": true,
      "value_pattern": "^\\d+(\\.\\d+)?\\s*[kK]",
      "pins": [
        {"number": "1", "net": "vcc"},
        {"number": "2", "net": "signal"}
      ]
    }
  ],
  "constraints": {"min_components": 1, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.20, "naming": 0.20, "parametric": 0.20, "type": 0.40},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

- [ ] **Step 2: 创建 `primitives/pull-down-resistor.json`**

```json
{
  "$schema": "./schema.json",
  "id": "pull-down-resistor",
  "name": "下拉电阻",
  "description": "电阻一端接地（role=gnd），另一端接数字信号（role=digital），将信号默认拉到低电平。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {
      "id": "gnd", "type": "net",
      "name_pattern": "^(GND|PGND|AGND|DGND|VSS)$",
      "expected_role": "gnd",
      "required": true
    },
    {
      "id": "signal", "type": "net",
      "name_pattern": ".*",
      "expected_role": "digital",
      "required": true
    }
  ],
  "components": [
    {
      "id": "R_pull", "type": "Resistor", "required": true,
      "value_pattern": "^\\d+(\\.\\d+)?\\s*[kK]",
      "pins": [
        {"number": "1", "net": "signal"},
        {"number": "2", "net": "gnd"}
      ]
    }
  ],
  "constraints": {"min_components": 1, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.20, "naming": 0.20, "parametric": 0.20, "type": 0.40},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

- [ ] **Step 3: 创建 `primitives/tvs-protection.json`**

```json
{
  "$schema": "./schema.json",
  "id": "tvs-protection",
  "name": "TVS 保护二极管",
  "description": "TVS 二极管跨接在被保护信号/电源与地之间，过压时击穿钳位。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {
      "id": "protected", "type": "net",
      "name_pattern": ".*",
      "expected_role": "power",
      "required": true
    },
    {
      "id": "gnd", "type": "net",
      "name_pattern": "^(GND|PGND|AGND|DGND|VSS)$",
      "expected_role": "gnd",
      "required": true
    }
  ],
  "components": [
    {
      "id": "D_tvs", "type": "Diode", "required": true,
      "value_pattern": "(SMBJ|SMCJ|P6KE|TVS|ESD)",
      "pins": [
        {"number": "A", "net": "gnd"},
        {"number": "K", "net": "protected"}
      ]
    }
  ],
  "constraints": {"min_components": 1, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.20, "naming": 0.20, "parametric": 0.40, "type": 0.20},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

- [ ] **Step 4: 创建 `primitives/npn-low-side-switch.json`**

```json
{
  "$schema": "./schema.json",
  "id": "npn-low-side-switch",
  "name": "NPN 低边开关",
  "description": "NPN 三极管集电极接负载，发射极接地，基极通过电阻接控制信号。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {
      "id": "load_net", "type": "net", "name_pattern": ".*", "required": true
    },
    {
      "id": "gnd", "type": "net",
      "name_pattern": "^(GND|PGND|AGND|DGND|VSS)$",
      "expected_role": "gnd",
      "required": true
    },
    {
      "id": "ctrl", "type": "net",
      "name_pattern": ".*",
      "expected_role": "digital",
      "required": true
    }
  ],
  "components": [
    {
      "id": "Q1", "type": "Unknown", "required": true,
      "pins": [
        {"number": "C", "net": "load_net"},
        {"number": "E", "net": "gnd"},
        {"number": "B", "net": "ctrl"}
      ]
    }
  ],
  "constraints": {"min_components": 1, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.30, "naming": 0.20, "parametric": 0.10, "type": 0.40},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

**注**：`Q1.type` 用 `"Unknown"` 因为 `DeviceKind` 无 `BJT`。精度依赖 `expected_role` + 三脚结构。

- [ ] **Step 5: 创建 `primitives/nmos-low-side-switch.json`**

```json
{
  "$schema": "./schema.json",
  "id": "nmos-low-side-switch",
  "name": "NMOS 低边开关",
  "description": "NMOS 漏极接负载，源极接地，栅极接控制信号。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {"id": "load_net", "type": "net", "name_pattern": ".*", "required": true},
    {
      "id": "gnd", "type": "net",
      "name_pattern": "^(GND|PGND|AGND|DGND|VSS)$",
      "expected_role": "gnd",
      "required": true
    },
    {
      "id": "ctrl", "type": "net",
      "name_pattern": ".*",
      "expected_role": "digital",
      "required": true
    }
  ],
  "components": [
    {
      "id": "Q1", "type": "Mosfet", "required": true,
      "pins": [
        {"number": "D", "net": "load_net"},
        {"number": "S", "net": "gnd"},
        {"number": "G", "net": "ctrl"}
      ]
    }
  ],
  "constraints": {"min_components": 1, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.30, "naming": 0.20, "parametric": 0.10, "type": 0.40},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

- [ ] **Step 6: 创建 `primitives/pmos-high-side-switch.json`**

```json
{
  "$schema": "./schema.json",
  "id": "pmos-high-side-switch",
  "name": "PMOS 高边开关",
  "description": "PMOS 源极接电源，漏极接负载，栅极接控制信号（通常需拉低导通）。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {
      "id": "vcc", "type": "net",
      "name_pattern": "^(VCC|VDD|VBUS|\\+?\\d+\\.?\\d*V|\\+?\\d+V\\d+).*",
      "expected_role": "power",
      "required": true
    },
    {"id": "load_net", "type": "net", "name_pattern": ".*", "required": true},
    {
      "id": "ctrl", "type": "net",
      "name_pattern": ".*",
      "expected_role": "digital",
      "required": true
    }
  ],
  "components": [
    {
      "id": "Q1", "type": "Mosfet", "required": true,
      "pins": [
        {"number": "S", "net": "vcc"},
        {"number": "D", "net": "load_net"},
        {"number": "G", "net": "ctrl"}
      ]
    }
  ],
  "constraints": {"min_components": 1, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.30, "naming": 0.20, "parametric": 0.10, "type": 0.40},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

- [ ] **Step 7: 写 6 个基元的正向 + 负向测试**

在 `tests/test_vf2_matcher.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# Group 2 primitives: pull-up / pull-down / tvs / switches
# All rely on strict-mode expected_role matching.
# ---------------------------------------------------------------------------

def _pull_up_ir_with_semantics():
    r = make_component("R100", DeviceKind.RESISTOR, [("1", "VCC"), ("2", "GPIO1")], raw_value="10K")
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    sig = make_net("N_SIG", "GPIO1", NetType.SIGNAL)
    return build_ir(
        [r], [vcc, sig],
        [("R100.1", "N_VCC"), ("R100.2", "N_SIG")],
        edge_semantics={
            ("R100.1", "N_VCC"): {"role": "power",    "direction": "bidirectional", "level": "5V"},
            ("R100.2", "N_SIG"): {"role": "digital",  "direction": "bidirectional", "level": None},
        },
    )


def test_find_matches_pull_up():
    template = next(t for t in load_primitives() if t.id == "pull-up-resistor")
    matcher = Vf2TopologyMatcher(_pull_up_ir_with_semantics())
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["R_pull"] == "R100"
    assert result.matches[0].signal_mapping == {"vcc": "N_VCC", "signal": "N_SIG"}


def test_find_matches_pull_up_rejected_when_vcc_mistagged_digital():
    """Same topology, but VCC net is mis-tagged as digital → strict-mode rejects."""
    r = make_component("R100", DeviceKind.RESISTOR, [("1", "VCC"), ("2", "GPIO1")], raw_value="10K")
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    sig = make_net("N_SIG", "GPIO1", NetType.SIGNAL)
    ir = build_ir(
        [r], [vcc, sig],
        [("R100.1", "N_VCC"), ("R100.2", "N_SIG")],
        edge_semantics={
            ("R100.1", "N_VCC"): {"role": "digital", "direction": "bidirectional", "level": None},
            ("R100.2", "N_SIG"): {"role": "digital", "direction": "bidirectional", "level": None},
        },
    )
    template = next(t for t in load_primitives() if t.id == "pull-up-resistor")
    result = Vf2TopologyMatcher(ir).find_matches(template, timeout_seconds=2.0)
    assert result.matches == []


def _pull_down_ir():
    r = make_component("R101", DeviceKind.RESISTOR, [("1", "GPIO2"), ("2", "GND")], raw_value="4.7K")
    sig = make_net("N_SIG", "GPIO2", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [r], [sig, gnd],
        [("R101.1", "N_SIG"), ("R101.2", "N_GND")],
        edge_semantics={
            ("R101.1", "N_SIG"): {"role": "digital", "direction": "bidirectional", "level": None},
            ("R101.2", "N_GND"): {"role": "gnd",     "direction": "bidirectional", "level": None},
        },
    )


def test_find_matches_pull_down():
    template = next(t for t in load_primitives() if t.id == "pull-down-resistor")
    result = Vf2TopologyMatcher(_pull_down_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["R_pull"] == "R101"


def _tvs_ir():
    d = make_component("D10", DeviceKind.DIODE, [("A", "GND"), ("K", "VBUS")], raw_value="SMBJ5.0A")
    vbus = make_net("N_VBUS", "VBUS", NetType.POWER)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [d], [vbus, gnd],
        [("D10.A", "N_GND"), ("D10.K", "N_VBUS")],
        edge_semantics={
            ("D10.A", "N_GND"):  {"role": "gnd",   "direction": "bidirectional", "level": None},
            ("D10.K", "N_VBUS"): {"role": "power", "direction": "bidirectional", "level": "5V"},
        },
    )


def test_find_matches_tvs_protection():
    template = next(t for t in load_primitives() if t.id == "tvs-protection")
    result = Vf2TopologyMatcher(_tvs_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["D_tvs"] == "D10"


def _npn_low_side_ir():
    q = make_component("Q1", DeviceKind.UNKNOWN, [("C", "MOTOR"), ("E", "GND"), ("B", "CTRL")],
                       raw_value="2N2222", part_number="2N2222")
    load = make_net("N_LOAD", "MOTOR", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    ctrl = make_net("N_CTRL", "CTRL_PWM", NetType.SIGNAL)
    return build_ir(
        [q], [load, gnd, ctrl],
        [("Q1.C", "N_LOAD"), ("Q1.E", "N_GND"), ("Q1.B", "N_CTRL")],
        edge_semantics={
            ("Q1.E", "N_GND"):  {"role": "gnd",     "direction": "bidirectional", "level": None},
            ("Q1.B", "N_CTRL"): {"role": "digital", "direction": "load",          "level": None},
        },
    )


def test_find_matches_npn_low_side():
    template = next(t for t in load_primitives() if t.id == "npn-low-side-switch")
    result = Vf2TopologyMatcher(_npn_low_side_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["Q1"] == "Q1"


def _nmos_low_side_ir():
    q = make_component("Q2", DeviceKind.MOSFET, [("D", "LED"), ("S", "GND"), ("G", "PWM")],
                       raw_value="2N7002", part_number="2N7002")
    load = make_net("N_LOAD", "LED", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    ctrl = make_net("N_CTRL", "PWM", NetType.SIGNAL)
    return build_ir(
        [q], [load, gnd, ctrl],
        [("Q2.D", "N_LOAD"), ("Q2.S", "N_GND"), ("Q2.G", "N_CTRL")],
        edge_semantics={
            ("Q2.S", "N_GND"):  {"role": "gnd",     "direction": "bidirectional", "level": None},
            ("Q2.G", "N_CTRL"): {"role": "digital", "direction": "load",          "level": None},
        },
    )


def test_find_matches_nmos_low_side():
    template = next(t for t in load_primitives() if t.id == "nmos-low-side-switch")
    result = Vf2TopologyMatcher(_nmos_low_side_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["Q1"] == "Q2"


def _pmos_high_side_ir():
    q = make_component("Q3", DeviceKind.MOSFET, [("S", "VCC"), ("D", "LOAD"), ("G", "EN")],
                       raw_value="AO3401", part_number="AO3401")
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    load = make_net("N_LOAD", "LOAD", NetType.SIGNAL)
    ctrl = make_net("N_CTRL", "EN", NetType.SIGNAL)
    return build_ir(
        [q], [vcc, load, ctrl],
        [("Q3.S", "N_VCC"), ("Q3.D", "N_LOAD"), ("Q3.G", "N_CTRL")],
        edge_semantics={
            ("Q3.S", "N_VCC"):  {"role": "power",   "direction": "bidirectional", "level": "5V"},
            ("Q3.G", "N_CTRL"): {"role": "digital", "direction": "load",          "level": None},
        },
    )


def test_find_matches_pmos_high_side():
    template = next(t for t in load_primitives() if t.id == "pmos-high-side-switch")
    result = Vf2TopologyMatcher(_pmos_high_side_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["Q1"] == "Q3"
```

- [ ] **Step 8: 跑组 2 测试**

Run: `uv run pytest tests/test_vf2_matcher.py -k "pull_up or pull_down or tvs or npn or nmos or pmos" -v`
Expected: 7 passed（6 个正向 + 1 个负向 pull_up_rejected）

- [ ] **Step 9: 跑完整 test_vf2_matcher 确认零回归**

Run: `uv run pytest tests/test_vf2_matcher.py -v`
Expected: 全绿

- [ ] **Step 10: Commit**

```bash
git add primitives/pull-up-resistor.json primitives/pull-down-resistor.json primitives/tvs-protection.json primitives/npn-low-side-switch.json primitives/nmos-low-side-switch.json primitives/pmos-high-side-switch.json tests/test_vf2_matcher.py
git commit -m "feat(primitives): 组 2 新增 6 个依赖 expected_role 的基元

pull-up / pull-down / tvs-protection / npn-low-side / nmos-low-side / pmos-high-side
全部依赖严格模式 expected_role 字段做精确匹配，包含 1 个负向测试
（role mistagged 时必须拒绝）。"
```

---

### Task 7: 组 3 基元（flyback / current-shunt / fet-level-shifter / lc-pi / sallen-key）

**Files:**
- Create: `primitives/flyback-diode.json`, `primitives/current-shunt-monitor.json`, `primitives/fet-level-shifter.json`, `primitives/lc-pi-filter.json`, `primitives/sallen-key-low-pass.json`
- Test: `tests/test_vf2_matcher.py`

**Interfaces:**
- Consumes: Task 1–6 全部产物
- Produces: 5 个新基元 ID

- [ ] **Step 1: 创建 `primitives/flyback-diode.json`**

```json
{
  "$schema": "./schema.json",
  "id": "flyback-diode",
  "name": "续流二极管",
  "description": "二极管反向并联在电感/继电器线圈两端，关断时为感应电流提供续流路径。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {
      "id": "high_side", "type": "net",
      "name_pattern": ".*",
      "expected_role": "power",
      "required": true
    },
    {
      "id": "low_side", "type": "net",
      "name_pattern": ".*",
      "required": true
    }
  ],
  "components": [
    {
      "id": "L1", "type": "Inductor", "required": true,
      "pins": [
        {"number": "1", "net": "high_side"},
        {"number": "2", "net": "low_side"}
      ]
    },
    {
      "id": "D_fly", "type": "Diode", "required": true,
      "pins": [
        {"number": "A", "net": "low_side"},
        {"number": "K", "net": "high_side"}
      ]
    }
  ],
  "constraints": {"min_components": 2, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.40, "naming": 0.20, "parametric": 0.15, "type": 0.25},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

- [ ] **Step 2: 创建 `primitives/current-shunt-monitor.json`**

```json
{
  "$schema": "./schema.json",
  "id": "current-shunt-monitor",
  "name": "电流采样电阻",
  "description": "小阻值精密电阻串联在电源路径中，两端压降反映电流；常配合电流检测放大器使用。",
  "role": "power",
  "version": "0.1.0",
  "signals": [
    {
      "id": "pwr_in", "type": "net",
      "name_pattern": ".*",
      "expected_role": "power",
      "required": true
    },
    {
      "id": "pwr_out", "type": "net",
      "name_pattern": ".*",
      "expected_role": "power",
      "required": true
    }
  ],
  "components": [
    {
      "id": "R_shunt", "type": "Resistor", "required": true,
      "value_pattern": "^(0\\.|R0|mΩ|mR|\\d+mR|\\d+\\s*mΩ)",
      "pins": [
        {"number": "1", "net": "pwr_in"},
        {"number": "2", "net": "pwr_out"}
      ]
    }
  ],
  "constraints": {"min_components": 1, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.20, "naming": 0.20, "parametric": 0.40, "type": 0.20},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

**注**：`value_pattern` 匹配毫欧级（`0.1`, `R01`, `10mR`, `10mΩ`）。

- [ ] **Step 3: 创建 `primitives/fet-level-shifter.json`**

```json
{
  "$schema": "./schema.json",
  "id": "fet-level-shifter",
  "name": "FET 双向电平转换",
  "description": "NMOS + 两个上拉电阻实现双向电平转换（常见于 I2C 5V↔3V3）。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {
      "id": "vcc_low", "type": "net",
      "name_pattern": "^(3V3|\\+?3\\.3V).*",
      "expected_role": "power", "expected_level": "3V3",
      "required": true
    },
    {
      "id": "vcc_high", "type": "net",
      "name_pattern": "^(5V|\\+?5V|VCC|VDD).*",
      "expected_role": "power", "expected_level": "5V",
      "required": true
    },
    {
      "id": "sig_low", "type": "net",
      "name_pattern": ".*",
      "expected_role": "digital",
      "required": true
    },
    {
      "id": "sig_high", "type": "net",
      "name_pattern": ".*",
      "expected_role": "digital",
      "required": true
    }
  ],
  "components": [
    {
      "id": "Q1", "type": "Mosfet", "required": true,
      "pins": [
        {"number": "S", "net": "sig_low"},
        {"number": "D", "net": "sig_high"},
        {"number": "G", "net": "vcc_low"}
      ]
    },
    {
      "id": "R_low", "type": "Resistor", "required": true,
      "pins": [
        {"number": "1", "net": "sig_low"},
        {"number": "2", "net": "vcc_low"}
      ]
    },
    {
      "id": "R_high", "type": "Resistor", "required": true,
      "pins": [
        {"number": "1", "net": "sig_high"},
        {"number": "2", "net": "vcc_high"}
      ]
    }
  ],
  "constraints": {"min_components": 3, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.35, "naming": 0.20, "parametric": 0.15, "type": 0.30},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

**注**：此基元同时依赖 `expected_role` 和 `expected_level`，是组 3 中最复杂的。

- [ ] **Step 4: 创建 `primitives/lc-pi-filter.json`**

```json
{
  "$schema": "./schema.json",
  "id": "lc-pi-filter",
  "name": "π 型 LC 滤波器",
  "description": "C-L-C π 型滤波：输入电容 + 串联电感 + 输出电容，用于电源纹波抑制。",
  "role": "power",
  "version": "0.1.0",
  "signals": [
    {
      "id": "pwr_in", "type": "net",
      "name_pattern": ".*",
      "expected_role": "power",
      "required": true
    },
    {
      "id": "pwr_out", "type": "net",
      "name_pattern": ".*",
      "expected_role": "power",
      "required": true
    },
    {
      "id": "gnd", "type": "net",
      "name_pattern": "^(GND|PGND|AGND|DGND|VSS)$",
      "expected_role": "gnd",
      "required": true
    }
  ],
  "components": [
    {
      "id": "C_in", "type": "Capacitor", "required": true,
      "pins": [
        {"number": "1", "net": "pwr_in"},
        {"number": "2", "net": "gnd"}
      ]
    },
    {
      "id": "L1", "type": "Inductor", "required": true,
      "pins": [
        {"number": "1", "net": "pwr_in"},
        {"number": "2", "net": "pwr_out"}
      ]
    },
    {
      "id": "C_out", "type": "Capacitor", "required": true,
      "pins": [
        {"number": "1", "net": "pwr_out"},
        {"number": "2", "net": "gnd"}
      ]
    }
  ],
  "constraints": {"min_components": 3, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.40, "naming": 0.20, "parametric": 0.15, "type": 0.25},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

- [ ] **Step 5: 创建 `primitives/sallen-key-low-pass.json`**

```json
{
  "$schema": "./schema.json",
  "id": "sallen-key-low-pass",
  "name": "Sallen-Key 二阶低通滤波器",
  "description": "运放 + 2R + 2C 构成的单位增益二阶有源低通。",
  "role": "unknown",
  "version": "0.1.0",
  "signals": [
    {"id": "vin",  "type": "net", "name_pattern": ".*", "required": true},
    {"id": "vout", "type": "net", "name_pattern": ".*", "required": true},
    {
      "id": "gnd", "type": "net",
      "name_pattern": "^(GND|PGND|AGND|DGND|VSS)$",
      "expected_role": "gnd",
      "required": true
    },
    {
      "id": "vcc", "type": "net",
      "name_pattern": "^(VCC|VDD|\\+?\\d+\\.?\\d*V).*",
      "expected_role": "power",
      "required": true
    }
  ],
  "components": [
    {
      "id": "U_op", "type": "IC", "required": true,
      "pins": [
        {"number": "+",  "net": "vin"},
        {"number": "-",  "net": "vout"},
        {"number": "OUT","net": "vout"},
        {"number": "V+", "net": "vcc"},
        {"number": "V-", "net": "gnd"}
      ]
    },
    {
      "id": "R1", "type": "Resistor", "required": true,
      "pins": [
        {"number": "1", "net": "vin"},
        {"number": "2", "net": "vout"}
      ]
    },
    {
      "id": "R2", "type": "Resistor", "required": true,
      "pins": [
        {"number": "1", "net": "vin"},
        {"number": "2", "net": "vout"}
      ]
    },
    {
      "id": "C1", "type": "Capacitor", "required": true,
      "pins": [
        {"number": "1", "net": "vout"},
        {"number": "2", "net": "gnd"}
      ]
    },
    {
      "id": "C2", "type": "Capacitor", "required": true,
      "pins": [
        {"number": "1", "net": "vout"},
        {"number": "2", "net": "gnd"}
      ]
    }
  ],
  "constraints": {"min_components": 5, "exact_component_count": true},
  "scoring": {
    "weights": {"structural": 0.40, "naming": 0.15, "parametric": 0.20, "type": 0.25},
    "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.0}
  }
}
```

**注**：Sallen-Key 的精确拓扑依赖 pin 级连接（VF2 看不到 pin number），本基元是近似匹配 —— 高 score 时需人工/AI 二次确认。

- [ ] **Step 6: 写 5 个基元的正向测试**

在 `tests/test_vf2_matcher.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# Group 3 primitives: flyback / current-shunt / fet-level-shifter / lc-pi / sallen-key
# ---------------------------------------------------------------------------

def _flyback_ir():
    l = make_component("L1", DeviceKind.INDUCTOR, [("1", "VCC"), ("2", "SW")], raw_value="10uH")
    d = make_component("D5", DeviceKind.DIODE, [("A", "SW"), ("K", "VCC")], raw_value="1N4148")
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    sw = make_net("N_SW", "SW", NetType.SIGNAL)
    return build_ir(
        [l, d], [vcc, sw],
        [("L1.1", "N_VCC"), ("L1.2", "N_SW"),
         ("D5.A", "N_SW"), ("D5.K", "N_VCC")],
        edge_semantics={
            ("L1.1", "N_VCC"): {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("D5.K", "N_VCC"): {"role": "power", "direction": "bidirectional", "level": "5V"},
        },
    )


def test_find_matches_flyback_diode():
    template = next(t for t in load_primitives() if t.id == "flyback-diode")
    result = Vf2TopologyMatcher(_flyback_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    assert comp["L1"] == "L1"
    assert comp["D_fly"] == "D5"


def _current_shunt_ir():
    r = make_component("R200", DeviceKind.RESISTOR, [("1", "VBUS"), ("2", "VDD")], raw_value="0.1")
    vin = make_net("N_VIN", "VBUS", NetType.POWER)
    vout = make_net("N_VOUT", "VDD", NetType.POWER)
    return build_ir(
        [r], [vin, vout],
        [("R200.1", "N_VIN"), ("R200.2", "N_VOUT")],
        edge_semantics={
            ("R200.1", "N_VIN"):  {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("R200.2", "N_VOUT"): {"role": "power", "direction": "bidirectional", "level": "5V"},
        },
    )


def test_find_matches_current_shunt():
    template = next(t for t in load_primitives() if t.id == "current-shunt-monitor")
    result = Vf2TopologyMatcher(_current_shunt_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["R_shunt"] == "R200"


def _fet_level_shifter_ir():
    q = make_component("Q4", DeviceKind.MOSFET, [("S", "SDA_3V3"), ("D", "SDA_5V"), ("G", "3V3")],
                       raw_value="BSS138", part_number="BSS138")
    r_lo = make_component("R300", DeviceKind.RESISTOR, [("1", "SDA_3V3"), ("2", "3V3")], raw_value="10K")
    r_hi = make_component("R301", DeviceKind.RESISTOR, [("1", "SDA_5V"), ("2", "5V")], raw_value="10K")
    v3 = make_net("N_3V3", "3V3", NetType.POWER)
    v5 = make_net("N_5V", "5V", NetType.POWER)
    sl = make_net("N_SL", "SDA_3V3", NetType.SIGNAL)
    sh = make_net("N_SH", "SDA_5V", NetType.SIGNAL)
    return build_ir(
        [q, r_lo, r_hi], [v3, v5, sl, sh],
        [("Q4.S", "N_SL"), ("Q4.D", "N_SH"), ("Q4.G", "N_3V3"),
         ("R300.1", "N_SL"), ("R300.2", "N_3V3"),
         ("R301.1", "N_SH"), ("R301.2", "N_5V")],
        edge_semantics={
            ("Q4.G", "N_3V3"):   {"role": "power",   "direction": "bidirectional", "level": "3V3"},
            ("R300.2", "N_3V3"): {"role": "power",   "direction": "bidirectional", "level": "3V3"},
            ("R301.2", "N_5V"):  {"role": "power",   "direction": "bidirectional", "level": "5V"},
            ("Q4.S", "N_SL"):    {"role": "digital", "direction": "bidirectional", "level": None},
            ("R300.1", "N_SL"):  {"role": "digital", "direction": "bidirectional", "level": None},
            ("Q4.D", "N_SH"):    {"role": "digital", "direction": "bidirectional", "level": None},
            ("R301.1", "N_SH"):  {"role": "digital", "direction": "bidirectional", "level": None},
        },
    )


def test_find_matches_fet_level_shifter():
    template = next(t for t in load_primitives() if t.id == "fet-level-shifter")
    result = Vf2TopologyMatcher(_fet_level_shifter_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    assert comp["Q1"] == "Q4"
    assert {comp["R_low"], comp["R_high"]} == {"R300", "R301"}


def _lc_pi_ir():
    c_in = make_component("C40", DeviceKind.CAPACITOR, [("1", "VIN"), ("2", "GND")], raw_value="10uF")
    l = make_component("L2", DeviceKind.INDUCTOR, [("1", "VIN"), ("2", "VOUT")], raw_value="10uH")
    c_out = make_component("C41", DeviceKind.CAPACITOR, [("1", "VOUT"), ("2", "GND")], raw_value="10uF")
    vin = make_net("N_VIN", "VIN", NetType.POWER)
    vout = make_net("N_VOUT", "VOUT", NetType.POWER)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [c_in, l, c_out], [vin, vout, gnd],
        [("C40.1", "N_VIN"), ("C40.2", "N_GND"),
         ("L2.1", "N_VIN"), ("L2.2", "N_VOUT"),
         ("C41.1", "N_VOUT"), ("C41.2", "N_GND")],
        edge_semantics={
            ("C40.1", "N_VIN"):  {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("L2.1", "N_VIN"):   {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("L2.2", "N_VOUT"):  {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("C41.1", "N_VOUT"): {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("C40.2", "N_GND"):  {"role": "gnd",   "direction": "bidirectional", "level": None},
            ("C41.2", "N_GND"):  {"role": "gnd",   "direction": "bidirectional", "level": None},
        },
    )


def test_find_matches_lc_pi_filter():
    template = next(t for t in load_primitives() if t.id == "lc-pi-filter")
    result = Vf2TopologyMatcher(_lc_pi_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    assert comp["L1"] == "L2"
    assert {comp["C_in"], comp["C_out"]} == {"C40", "C41"}


def _sallen_key_ir():
    u = make_component("U10", DeviceKind.IC,
                       [("+", "VIN_MID"), ("-", "VOUT"), ("OUT", "VOUT"),
                        ("V+", "VCC"), ("V-", "GND")],
                       raw_value="LM358", part_number="LM358")
    r1 = make_component("R400", DeviceKind.RESISTOR, [("1", "VIN"), ("2", "VIN_MID")], raw_value="10K")
    r2 = make_component("R401", DeviceKind.RESISTOR, [("1", "VIN_MID"), ("2", "VOUT_MID")], raw_value="10K")
    c1 = make_component("C50", DeviceKind.CAPACITOR, [("1", "VOUT_MID"), ("2", "VOUT")], raw_value="10nF")
    c2 = make_component("C51", DeviceKind.CAPACITOR, [("1", "VIN_MID"), ("2", "GND")], raw_value="10nF")
    # Build nets
    vin = make_net("N_VIN", "VIN", NetType.SIGNAL)
    vin_mid = make_net("N_VIN_MID", "VIN_MID", NetType.SIGNAL)
    vout_mid = make_net("N_VOUT_MID", "VOUT_MID", NetType.SIGNAL)
    vout = make_net("N_VOUT", "VOUT", NetType.SIGNAL)
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [u, r1, r2, c1, c2], [vin, vin_mid, vout_mid, vout, vcc, gnd],
        [("U10.+", "N_VIN_MID"), ("U10.-", "N_VOUT"), ("U10.OUT", "N_VOUT"),
         ("U10.V+", "N_VCC"), ("U10.V-", "N_GND"),
         ("R400.1", "N_VIN"), ("R400.2", "N_VIN_MID"),
         ("R401.1", "N_VIN_MID"), ("R401.2", "N_VOUT_MID"),
         ("C50.1", "N_VOUT_MID"), ("C50.2", "N_VOUT"),
         ("C51.1", "N_VIN_MID"), ("C51.2", "N_GND")],
        edge_semantics={
            ("U10.V+", "N_VCC"): {"role": "power", "direction": "load",         "level": "5V"},
            ("U10.V-", "N_GND"): {"role": "gnd",   "direction": "bidirectional", "level": None},
            ("C51.2", "N_GND"):  {"role": "gnd",   "direction": "bidirectional", "level": None},
        },
    )


def test_find_matches_sallen_key_low_pass():
    template = next(t for t in load_primitives() if t.id == "sallen-key-low-pass")
    result = Vf2TopologyMatcher(_sallen_key_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    # Sallen-Key 是近似匹配（VF2 不见 pin number），允许 matches 数量 > 1
    assert len(result.matches) >= 1
```

- [ ] **Step 7: 跑组 3 测试**

Run: `uv run pytest tests/test_vf2_matcher.py -k "flyback or current_shunt or level_shifter or lc_pi or sallen_key" -v`
Expected: 5 passed

- [ ] **Step 8: 跑完整 test_vf2_matcher + test_primitives 确认零回归**

Run: `uv run pytest tests/test_vf2_matcher.py tests/test_primitives.py -v`
Expected: 全绿

- [ ] **Step 9: Commit**

```bash
git add primitives/flyback-diode.json primitives/current-shunt-monitor.json primitives/fet-level-shifter.json primitives/lc-pi-filter.json primitives/sallen-key-low-pass.json tests/test_vf2_matcher.py
git commit -m "feat(primitives): 组 3 新增 5 个依赖 expected_role+direction+level 的基元

flyback-diode / current-shunt-monitor / fet-level-shifter / lc-pi-filter / sallen-key-low-pass
fet-level-shifter 是首个同时使用 expected_level 的基元（区分 3V3/5V 两侧）。"
```

---

### Task 8: 全套回归 + 文档更新

**Files:**
- Modify: `Readme.md`
- Test: 全部测试

**Interfaces:**
- Consumes: 所有前置 Task
- Produces: 全绿 pytest，更新后的 Readme

- [ ] **Step 1: 跑完整 pytest**

Run: `uv run pytest -v`
Expected: 全绿

若有失败：
- 失败在 `test_enrichers.py` → 检查 `TopologyRecognizer` 是否因严格模式拒绝了 T268 fixture 上原有的识别（现有 6 个基元未声明 `expected_*`，不应受影响；但确认 `EdgeSemanticEnricher` 跑过之后 `TopologyRecognizer` 不重复消费语义导致误判）
- 失败在 `test_cli*.py` → 检查 `_parse_and_enrich` 管线顺序是否仍正确
- 失败在 `test_scoring.py` → 确认 `signal_semantic_matches` 不影响 scoring

- [ ] **Step 2: 跑 T268 fixture 集成验证**

Run: `uv run cg export graph fixtures/protel/FX-T268-V6-10米-5065.NET --pretty`
Expected: `output/circuit.graph.json` 生成成功；检查 `blocks` 字段中是否有新基元的识别结果

查看 `blocks`：
```bash
grep -o '"id": "[^"]*"' output/circuit.graph.json | sort -u | head -30
```

记录哪些新基元在 T268 上被识别，哪些没有。对没有识别出的，用 `cg view <id>` 检查 fixture 中是否存在对应拓扑；若存在但识别失败，记录到 todolist。

- [ ] **Step 3: 更新 Readme.md**

在 `Readme.md` 第 11 行附近（"新增拓扑 = 追加 `primitives/*.json`"）下方补充：

```markdown
**基元 JSON 支持的新字段**（自 2026-08-17 起）：

| 字段 | 取值 | 作用 |
|---|---|---|
| `expected_role` | `"power"` / `"gnd"` / `"analog"` / `"digital"` / `"clock"` | 严格匹配 `edge_semantic.role` |
| `expected_direction` | `"source"` / `"load"` / `"bidirectional"` | 严格匹配 `edge_semantic.direction` |
| `expected_level` | 任意电压字符串（`"5V"` / `"3V3"` / `"1V25"`） | 数值化比对（`"3.3V"` ≡ `"3V3"`） |

**严格模式**：声明了任一 `expected_*` 的基元，circuit 边必须存在 `edge_semantic` 且值匹配；否则该基元识别为零（不是报错，是无结果）。现有未声明的基元不受影响。

当前基元清单（21 个）：
- 既有 6 个：voltage-divider / non-inverting-amplifier / inverting-amplifier / differential-amplifier / parallel-termination / type-c-cc-pull-down
- 组 1（4 个，纯结构）：rc-low-pass / rc-high-pass / crystal-with-load-caps / bridge-rectifier
- 组 2（6 个，依赖 expected_role）：pull-up-resistor / pull-down-resistor / tvs-protection / npn-low-side-switch / nmos-low-side-switch / pmos-high-side-switch
- 组 3（5 个，依赖 expected_role + direction + level）：flyback-diode / current-shunt-monitor / fet-level-shifter / lc-pi-filter / sallen-key-low-pass
```

- [ ] **Step 4: 跑 lint / type check（若项目配置）**

Run: `uv run ruff check circuit_graph/ tests/`（若项目有 ruff 配置）
Run: `uv run mypy circuit_graph/`（若项目有 mypy 配置）

若未配置，跳过。

- [ ] **Step 5: 最终 commit + push**

```bash
git add Readme.md
git commit -m "docs(readme): 补充 15 个新基元说明 + 严格模式 expected_* 字段文档

列出全部 21 个基元分组、新增 expected_role/direction/level 字段表、
严格模式行为说明。规划文档见 docs/superpowers/{specs,plans}/2026-08-17-*"

# 按项目约定（CLAUDE.md "每完成一个小任务都 修Readme.md + git commit + git push"）
git push
```

---

## Self-Review 记录

**1. Spec 覆盖**：

| Spec 章节 | 对应 Task |
|---|---|
| §3.2.1 Schema 三字段 + extract_volts | Task 1 |
| §3.2.2 严格模式 | Task 1 (signal_semantic_matches) + Task 3 (semantic_feasibility) |
| §3.2.3 代码改动清单 | Task 1, 2, 3, 4 |
| §3.2.4 semantic_feasibility 钩子 + _signal_to_components 派生 | Task 2, 3 |
| §3.2.5 _candidate_components 性能优化 | **不实施**（spec 已注明"若 T268 上 16 个基元跑完 < 10s 可不实现"） |
| §3.3 组 1（去掉 voltage-follower 后 4 个） | Task 5 |
| §3.3 组 2（6 个） | Task 6 |
| §3.3 组 3（5 个） | Task 7 |
| §6 测试策略 | Task 1-7 各含 TDD + Task 8 回归 |
| §8 成功标准 | Task 8 |

**与 spec 的偏差**（已在"范围说明"声明）：
- voltage-follower 移出（VF2 不支持 pin 级约束）
- fixture 不用 JSON 文件，沿用 fir_helpers 构造函数模式

**2. Placeholder 扫描**：
- 无 "TBD" / "TODO" / "add appropriate" / "Similar to" / "implement later"
- 所有测试代码、JSON、实现代码均完整给出

**3. 类型一致性**：
- `signal_semantic_matches(expected_role, expected_direction, expected_level, edge_semantic)` — Task 1 定义，Task 3 调用，签名一致
- `_match_signal_semantics(template, prim_signal, circ_net_id, partial_mapping, edge_semantics)` — Task 3 定义，spec §3.2.4 一致
- `template._signal_to_components: dict[str, list[str]]` — Task 2 定义，Task 3 使用，类型一致
- `build_ir(..., edge_semantics=...)` — Task 3 扩展 fir_helpers，Task 6/7 fixture 使用，签名一致
- `PrimitiveSignal.expected_role/direction/level: str | None` — Task 1 定义，Task 6/7 基元 JSON 使用，字段名一致

**4. 未发现缺失。**
