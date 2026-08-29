# Primitives 设计输出（cg design）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 primitives 目录从"只用于看图（识别）"扩展为"可做设计输出"——给定规格参数，把拓扑模板实例化成具体 CircuitIR，并反向写出 Protel .NET 网表。

**Architecture:** 保持现有 primitives 识别语义完全不变（向后兼容），在 schema 上增量加三个 optional 字段（`design_inputs` / `parameters` / `recommended_parts`）。新增 `circuit_graph/design/` 模块：`expr.py` 提供安全表达式求值（ast 白名单），`instantiate.py` 做参数依赖解析 + 拓扑实例化产出 `CircuitIR`。新增 `circuit_graph/exporters/protel_netlist.py` 作为现有 protel parser 的逆过程，实现 IR → .NET 网表，并以 round-trip 测试保证对称。CLI 暴露为 `cg design <primitive-id> --spec <file>`。

**Tech Stack:** Python 3 + click + dataclasses（frozen）；pytest + CliRunner；uv；networkx 不参与本特性。

## Global Constraints

- 识别方向（vf2_matcher 匹配）语义**不得改变**：现有 24 个 primitive 文件不加新字段也能照常加载与匹配（Task 1 的向后兼容测试锁定这一点）。
- `CircuitIR` 是唯一产出载体：所有中间态不得发明新格式；序列化复用 `ir_to_json`（circuit_graph/ir.py:333）。
- Pin id 规范：`{component_id}.{pin_number}`（`make_pin_id`，circuit_graph/ir.py:298）；.NET 网表 token 规范：`{RefDes}-{PinNum}`。
- 编码 UTF-8、LF 行尾（写入 .NET 时用 `"\n"`；protel parser 已支持 UTF-8/GBK 自动检测，UTF-8 输出可被自身解析）。
- 每个 Task 结束必须 `git commit`（两行 message：`feat(design): ...` + body）。
- 表达式引擎**禁止** `eval` / `exec`：只允许 `ast.parse(mode="eval")` + 白名单节点。
- 新 JSON 顶层字段不影响 `scripts/build_primitive_index.py`（它只读 id/name/description 与 signals 的 expected_*），Task 4 用 `--check` 验证。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `primitives/schema.json`（改） | 增加 `design_inputs` / `parameters` / `recommended_parts` 三个 optional 定义 |
| `circuit_graph/primitives.py`（改） | 解析上述三字段为 frozen dataclass，挂到 `PrimitiveTemplate` |
| `circuit_graph/design/__init__.py`（新） | 公共出口：`instantiate`, `DesignSpec`, `DesignResult`, `DesignError`, `evaluate`, `parse_number` |
| `circuit_graph/design/expr.py`（新） | 安全表达式求值 + SI 后缀数字解析 + `expr_names` 依赖提取 |
| `circuit_graph/design/instantiate.py`（新） | 规格 → 参数拓扑排序求值 → CircuitIR 实例化 |
| `circuit_graph/exporters/protel_netlist.py`（新） | CircuitIR → Protel .NET 文本 |
| `circuit_graph/cli.py`（改） | 注册 `cg design` 子命令 |
| `primitives/power/voltage-divider.json`（改） | 首个可设计 primitive：补 design 三字段 |
| `tests/test_primitives.py`（改） | Task 1 解析/兼容测试 |
| `tests/design/test_expr.py`（新） | Task 2 表达式引擎测试 |
| `tests/design/test_instantiate.py`（新） | Task 3/4 实例化测试 |
| `tests/exporters/test_protel_netlist.py`（新） | Task 5 round-trip 测试 |
| `tests/test_cli_design.py`（新） | Task 6 CLI 测试 |

---

### Task 1: Schema 扩展 + Python 侧解析（向后兼容）

**Files:**
- Modify: `primitives/schema.json`
- Modify: `circuit_graph/primitives.py`（`_parse_primitive` 附近 :207，`PrimitiveTemplate` :142）
- Test: `tests/test_primitives.py`（文件末尾追加）

**Interfaces:**
- Consumes: 无（基础层）。
- Produces: `PrimitiveTemplate` 新增三个字段，后续所有 Task 消费：
  - `design_inputs: list[DesignInput]` — `DesignInput(id: str, label: str | None = None, unit: str | None = None, default: str | float | None = None)`
  - `parameters: dict[str, str]` — 键格式 `"<CompId>.<field>"`（MVP 仅允许 `.value`），值为表达式字符串
  - `recommended_parts: dict[str, list[RecommendedPart]]` — `RecommendedPart(pn: str, footprint: str | None = None, notes: str | None = None)`

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_primitives.py`：

```python
# --- design output extension (Task 1) ---
import json

from circuit_graph.primitives import (
    PrimitiveValidationError,
    load_primitives,
)


def _write_primitive(tmp_path, name: str, raw: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def test_load_primitive_with_design_fields(tmp_path: Path) -> None:
    _write_primitive(
        tmp_path,
        "div.json",
        {
            "id": "div",
            "name": "分压",
            "signals": [
                {"id": "vin", "type": "net", "name_pattern": "VIN"},
                {"id": "vout", "type": "net", "name_pattern": "VOUT"},
                {"id": "gnd", "type": "net", "name_pattern": "GND"},
            ],
            "components": [
                {
                    "id": "R_top",
                    "type": "Resistor",
                    "pins": [
                        {"number": "1", "net": "vin"},
                        {"number": "2", "net": "vout"},
                    ],
                },
                {
                    "id": "R_bottom",
                    "type": "Resistor",
                    "pins": [
                        {"number": "1", "net": "vout"},
                        {"number": "2", "net": "gnd"},
                    ],
                },
            ],
            "design_inputs": [
                {"id": "vin", "label": "输入电压", "unit": "V"},
                {"id": "vout", "label": "输出电压", "unit": "V"},
                {"id": "r_bottom", "label": "下电阻", "unit": "Ω", "default": 10000},
            ],
            "parameters": {
                "R_top.value": "(vin - vout) * r_bottom / vout",
                "R_bottom.value": "r_bottom",
            },
            "recommended_parts": {
                "R_top": [{"pn": "0603 1%", "footprint": "0603"}],
                "R_bottom": [{"pn": "0603 1%", "footprint": "0603"}],
            },
        },
    )
    templates = load_primitives(tmp_path)
    assert len(templates) == 1
    t = templates[0]
    assert [d.id for d in t.design_inputs] == ["vin", "vout", "r_bottom"]
    assert t.design_inputs[2].default == 10000
    assert t.parameters == {
        "R_top.value": "(vin - vout) * r_bottom / vout",
        "R_bottom.value": "r_bottom",
    }
    assert t.recommended_parts["R_top"][0].pn == "0603 1%"
    assert t.recommended_parts["R_top"][0].footprint == "0603"


def test_primitive_without_design_fields_still_loads(tmp_path: Path) -> None:
    _write_primitive(
        tmp_path,
        "old.json",
        {
            "id": "old",
            "name": "老模板",
            "signals": [{"id": "gnd", "type": "net", "name_pattern": "GND"}],
            "components": [
                {
                    "id": "R1",
                    "type": "Resistor",
                    "pins": [{"number": "1", "net": "gnd"}, {"number": "2", "net": "gnd"}],
                }
            ],
        },
    )
    templates = load_primitives(tmp_path)
    assert len(templates) == 1
    t = templates[0]
    assert t.design_inputs == []
    assert t.parameters == {}
    assert t.recommended_parts == {}


def test_parameter_key_must_reference_known_component(tmp_path: Path) -> None:
    _write_primitive(
        tmp_path,
        "bad.json",
        {
            "id": "bad",
            "name": "坏模板",
            "signals": [{"id": "gnd", "type": "net", "name_pattern": "GND"}],
            "components": [
                {
                    "id": "R1",
                    "type": "Resistor",
                    "pins": [{"number": "1", "net": "gnd"}, {"number": "2", "net": "gnd"}],
                }
            ],
            "parameters": {"R_missing.value": "1000"},
        },
    )
    with pytest.raises(PrimitiveValidationError, match="R_missing"):
        load_primitives(tmp_path)
```

注意：`tests/test_primitives.py` 顶部需确认已 import `Path` 与 `pytest`；若无，在测试头部补 `from pathlib import Path`（该文件已大量使用 pytest，`pytest` 应已可用）。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_primitives.py -k "design or parameter_key" -v`
Expected: `AttributeError: 'PrimitiveTemplate' object has no attribute 'design_inputs'`（第一个测试失败，其余两个为收集到的同级失败/错误）

- [ ] **Step 3: 实现解析**

`circuit_graph/primitives.py` — 在 `PrimitiveScoring` 之后（:139 后）新增三个 dataclass，并扩展 `PrimitiveTemplate` 与 `_parse_primitive`：

```python
@dataclass(frozen=True)
class DesignInput:
    """A numeric input the designer must supply to instantiate the primitive."""

    id: str
    label: str | None = None
    unit: str | None = None
    default: str | float | None = None


@dataclass(frozen=True)
class RecommendedPart:
    """A concrete part suggestion for a primitive component."""

    pn: str
    footprint: str | None = None
    notes: str | None = None
```

`PrimitiveTemplate` 字段追加（放在 `_signal_to_components` 之前，均带 default 保持向后兼容）：

```python
    design_inputs: list[DesignInput] = field(default_factory=list)
    parameters: dict[str, str] = field(default_factory=dict)
    recommended_parts: dict[str, list[RecommendedPart]] = field(default_factory=dict)
```

`_parse_primitive` 返回处追加解析（在构造 `PrimitiveTemplate` 之前）：

```python
    design_inputs = _parse_design_inputs(raw.get("design_inputs", []))
    parameters = _parse_parameters(raw.get("parameters", {}), components)
    recommended_parts = _parse_recommended_parts(raw.get("recommended_parts", {}), components)
```

并在构造调用中加入 `design_inputs=design_inputs, parameters=parameters, recommended_parts=recommended_parts`。

新增三个私有解析函数（放在 `_parse_scoring` 附近）：

```python
_PARAM_KEY_RE = re.compile(r"^([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)$")


def _parse_design_inputs(raw: Any) -> list[DesignInput]:
    if not isinstance(raw, list):
        raise PrimitiveValidationError("design_inputs must be a list")
    inputs: list[DesignInput] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise PrimitiveValidationError(f"Invalid design_input entry: {item!r}")
        inputs.append(
            DesignInput(
                id=item["id"],
                label=item.get("label"),
                unit=item.get("unit"),
                default=item.get("default"),
            )
        )
    return inputs


def _parse_parameters(raw: Any, components: list[PrimitiveComponent]) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PrimitiveValidationError("parameters must be an object")
    comp_ids = {c.id for c in components}
    params: dict[str, str] = {}
    for key, expr in raw.items():
        m = _PARAM_KEY_RE.match(key)
        if not m or m.group(1) not in comp_ids:
            raise PrimitiveValidationError(
                f"Parameter key {key!r} must be '<ComponentId>.<field>' "
                f"referencing a known component"
            )
        if not isinstance(expr, str) or not expr.strip():
            raise PrimitiveValidationError(f"Parameter {key!r} must be a non-empty string")
        params[key] = expr
    return params


def _parse_recommended_parts(
    raw: Any, components: list[PrimitiveComponent]
) -> dict[str, list[RecommendedPart]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PrimitiveValidationError("recommended_parts must be an object")
    comp_ids = {c.id for c in components}
    parts: dict[str, list[RecommendedPart]] = {}
    for comp_id, entries in raw.items():
        if comp_id not in comp_ids:
            raise PrimitiveValidationError(
                f"recommended_parts key {comp_id!r} references an unknown component"
            )
        if not isinstance(entries, list) or not entries:
            raise PrimitiveValidationError(
                f"recommended_parts[{comp_id!r}] must be a non-empty list"
            )
        parsed: list[RecommendedPart] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("pn"), str):
                raise PrimitiveValidationError(
                    f"Invalid recommended part entry for {comp_id!r}: {entry!r}"
                )
            parsed.append(
                RecommendedPart(
                    pn=entry["pn"],
                    footprint=entry.get("footprint"),
                    notes=entry.get("notes"),
                )
            )
        parts[comp_id] = parsed
    return parts
```

`primitives/schema.json` — 在 `"scoring"` 属性之后加三个 optional 属性：

```json
    "design_inputs": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id"],
        "properties": {
          "id": {"type": "string"},
          "label": {"type": "string"},
          "unit": {"type": "string"},
          "default": {"type": ["number", "string"]}
        }
      }
    },
    "parameters": {
      "type": "object",
      "additionalProperties": {"type": "string"}
    },
    "recommended_parts": {
      "type": "object",
      "additionalProperties": {
        "type": "array",
        "minItems": 1,
        "items": {
          "type": "object",
          "required": ["pn"],
          "properties": {
            "pn": {"type": "string"},
            "footprint": {"type": "string"},
            "notes": {"type": "string"}
          }
        }
      }
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_primitives.py -v`
Expected: 全 PASS（含既有 primitive 测试，证明无回归）

- [ ] **Step 5: Commit**

```bash
git add primitives/schema.json circuit_graph/primitives.py tests/test_primitives.py
git commit -m "feat(primitives): add design_inputs/parameters/recommended_parts schema fields" -m "Backward compatible: existing templates without the new fields load unchanged."
```

---

### Task 2: 表达式引擎 `circuit_graph/design/expr.py`

**Files:**
- Create: `circuit_graph/design/__init__.py`
- Create: `circuit_graph/design/expr.py`
- Test: `tests/design/test_expr.py`（`tests/design/` 目录需新建，无 `__init__.py` 也可以被 pytest 收集；为与 tests/parsers 等一致，建空 `tests/design/__init__.py`）

**Interfaces:**
- Consumes: 无（纯函数模块，不依赖 Task 1）。
- Produces（Task 3 消费，签名必须一致）:
  - `evaluate(expr: str, bindings: dict[str, float]) -> float`
  - `parse_number(text: str) -> float` — 支持 SI 后缀：`p/n/u/m/k/K/M/G`，如 `"4.7k" -> 4700.0`
  - `expr_names(expr: str) -> list[str]` — 表达式引用的 Name 节点列表（排序去重）
  - `class ExpressionError(ValueError)`

- [ ] **Step 1: 写失败测试** — `tests/design/test_expr.py`：

```python
import pytest

from circuit_graph.design.expr import (
    ExpressionError,
    evaluate,
    expr_names,
    parse_number,
)


def test_evaluate_arithmetic_with_bindings() -> None:
    result = evaluate("(vin - vout) * r_bottom / vout", {"vin": 12, "vout": 5, "r_bottom": 10000})
    assert result == 14000.0


def test_evaluate_unary_minus() -> None:
    assert evaluate("-vout + 10", {"vout": 5}) == 5.0


def test_parse_number_si_suffixes() -> None:
    assert parse_number("4.7k") == 4700.0
    assert parse_number("10u") == 1e-5
    assert parse_number("100") == 100.0
    assert parse_number("2M") == 2e6


def test_evaluate_undefined_name_raises() -> None:
    with pytest.raises(ExpressionError, match="Undefined"):
        evaluate("vout / 2", {})


def test_evaluate_rejects_arbitrary_code() -> None:
    with pytest.raises(ExpressionError):
        evaluate("__import__('os').system('echo pwned')", {})
    with pytest.raises(ExpressionError):
        evaluate("(lambda: 1)()", {})


def test_evaluate_rejects_unknown_suffix() -> None:
    with pytest.raises(ExpressionError, match="SI suffix"):
        parse_number("10x")


def test_expr_names_extracts_references() -> None:
    assert expr_names("(vin - vout) * r_bottom / vout") == ["r_bottom", "vin", "vout"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/design/test_expr.py -v`
Expected: `ModuleNotFoundError: No module named 'circuit_graph.design'`

- [ ] **Step 3: 实现**

`circuit_graph/design/__init__.py`（先建最小版，Task 3 再扩）：

```python
"""Parametric design output: turn primitives into concrete circuits."""

from circuit_graph.design.expr import ExpressionError, evaluate, expr_names, parse_number

__all__ = ["ExpressionError", "evaluate", "expr_names", "parse_number"]
```

`circuit_graph/design/expr.py`：

```python
"""Minimal, safe expression evaluator for primitive design parameters.

Only arithmetic over numbers is allowed: the AST whitelist rejects calls,
attribute access, subscripts, comprehensions and everything else, so template
expressions can never execute arbitrary code.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

_SI_SUFFIXES = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "": 1.0,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class ExpressionError(ValueError):
    """Raised when a design parameter expression is invalid."""


def evaluate(expr: str, bindings: dict[str, float]) -> float:
    """Evaluate ``expr`` with numeric ``bindings``; only arithmetic is allowed."""
    tree = ast.parse(expr, mode="eval")
    return float(_eval(tree.body, dict(bindings)))


def _eval(node: ast.AST, bindings: dict[str, float]) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in bindings:
            raise ExpressionError(f"Undefined parameter: {node.id!r}")
        return bindings[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left, bindings), _eval(node.right, bindings))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
        return _UNARYOPS[type(node.op)](_eval(node.operand, bindings))
    raise ExpressionError(f"Unsupported expression node: {type(node).__name__}")


def expr_names(expr: str) -> list[str]:
    """Return the sorted Name references in ``expr`` (for dependency resolution)."""
    tree = ast.parse(expr, mode="eval")
    return sorted({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)})


def parse_number(text: str) -> float:
    """Parse a numeric literal with an optional single-letter SI suffix."""
    t = str(text).strip()
    suffix = ""
    while t and t[-1].isalpha():
        suffix = t[-1] + suffix
        t = t[:-1]
    if suffix not in _SI_SUFFIXES:
        raise ExpressionError(f"Unknown SI suffix: {suffix!r}")
    return float(t) * _SI_SUFFIXES[suffix]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/design/test_expr.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/design tests/design
git commit -m "feat(design): add safe expression evaluator for design parameters" -m "AST whitelist evaluator: arithmetic + SI-suffixed literals only, no eval()."
```

---

### Task 3: 实例化器 `circuit_graph/design/instantiate.py`

**Files:**
- Create: `circuit_graph/design/instantiate.py`
- Modify: `circuit_graph/design/__init__.py`（导出新 API）
- Test: `tests/design/test_instantiate.py`

**Interfaces:**
- Consumes:
  - `PrimitiveTemplate`（Task 1 的 `design_inputs` / `parameters` 字段）
  - `evaluate`, `expr_names`, `ExpressionError`（Task 2）
  - `CircuitIR`, `Component`, `Net`, `Pin`, `Connection`, `DeviceKind`, `CircuitMeta`, `Provenance`, `make_pin_id`, `ResistanceValue`, `CapacitanceValue`, `InductanceValue`, `NormalizedValue`（circuit_graph/ir.py）
- Produces（Task 4/6 消费）:
  - `class DesignError(ValueError)`
  - `@dataclass(frozen=True) class DesignSpec` — 字段 `inputs: dict[str, float]`、`nets: dict[str, str]`、`designators: dict[str, str]`、`start_index: int = 1`
  - `@dataclass(frozen=True) class DesignResult` — 字段 `ir: CircuitIR`、`resolved: dict[str, float]`、`warnings: list[str]`
  - `instantiate(template: PrimitiveTemplate, spec: DesignSpec) -> DesignResult`

**语义约定（写死，后续 Task 依赖）:**
- net 命名：`spec.nets[signal_id]` 优先；缺省时取 signal id 大写（`"gnd" -> "GND"`，`"vin" -> "VIN"`）。
- designator：`spec.designators[comp_id]` 优先；缺省时 `{前缀}{序号}`，前缀映射 `Resistor->R, Capacitor->C, Inductor->L, Diode->D, Mosfet->Q, IC->U, BridgeRectifier->BR, Unknown->U`，序号从 `spec.start_index` 起按 components 声明顺序递增。
- component 的 `type` 是 `str | tuple[str,...]` 时取第一个能映射进 `DeviceKind` 的成员；全部映射失败抛 `DesignError`。
- 参数值最终单位：Resistor→Ω、Capacitor→µF、Inductor→µH（magnitude 不换算，直接存数值），IC/Unknown 无 value。
- 未在 `parameters` 中出现的组件（如 IC）value 为 None。
- `design_inputs` 有 `default` 且 spec 未提供 → 用 default（经 `parse_number(str(default))`）；无 default 且未提供 → `DesignError`。

- [ ] **Step 1: 写失败测试** — `tests/design/test_instantiate.py`：

```python
from circuit_graph.design import DesignError, DesignSpec, instantiate
from circuit_graph.ir import DeviceKind, ResistanceValue
from circuit_graph.primitives import load_primitives


def _divider_template(tmp_path) -> PrimitiveTemplate:
    import json

    from pathlib import Path

    p = tmp_path / "div.json"
    p.write_text(
        json.dumps(
            {
                "id": "div",
                "name": "分压",
                "signals": [
                    {"id": "vin", "type": "net", "name_pattern": "VIN"},
                    {"id": "vout", "type": "net", "name_pattern": "VOUT"},
                    {"id": "gnd", "type": "net", "name_pattern": "GND"},
                ],
                "components": [
                    {
                        "id": "R_top",
                        "type": "Resistor",
                        "pins": [
                            {"number": "1", "net": "vin"},
                            {"number": "2", "net": "vout"},
                        ],
                    },
                    {
                        "id": "R_bottom",
                        "type": "Resistor",
                        "pins": [
                            {"number": "1", "net": "vout"},
                            {"number": "2", "net": "gnd"},
                        ],
                    },
                ],
                "design_inputs": [
                    {"id": "vin"},
                    {"id": "vout"},
                    {"id": "r_bottom", "default": 10000},
                ],
                "parameters": {
                    "R_top.value": "(vin - vout) * r_bottom / vout",
                    "R_bottom.value": "r_bottom",
                },
            }
        ),
        encoding="utf-8",
    )
    return load_primitives(tmp_path)[0]


def test_instantiate_divider_resolves_parameters(tmp_path) -> None:
    template = _divider_template(tmp_path)
    result = instantiate(template, DesignSpec(inputs={"vin": 12, "vout": 5}))
    assert result.resolved == {"R_top.value": 14000.0, "R_bottom.value": 10000.0}
    by_id = {c.id: c for c in result.ir.components}
    assert isinstance(by_id["R1"].value, ResistanceValue)
    assert by_id["R1"].value.magnitude == 14000.0
    assert by_id["R2"].value.magnitude == 10000.0
    assert {n.name for n in result.ir.nets} == {"VIN", "VOUT", "GND"}


def test_instantiate_pin_and_connection_wiring(tmp_path) -> None:
    template = _divider_template(tmp_path)
    result = instantiate(template, DesignSpec(inputs={"vin": 12, "vout": 5}))
    ir = result.ir
    # R1 pins: 1->VIN, 2->VOUT; R2 pins: 1->VOUT, 2->GND
    pin_to_net = {p.id: p.net_id for c in ir.components for p in c.pins}
    assert pin_to_net["R1.1"] == "VIN"
    assert pin_to_net["R1.2"] == "VOUT"
    assert pin_to_net["R2.1"] == "VOUT"
    assert pin_to_net["R2.2"] == "GND"
    conns = {(c.pin_id, c.net_id) for c in ir.connections}
    assert conns == {
        ("R1.1", "VIN"),
        ("R1.2", "VOUT"),
        ("R2.1", "VOUT"),
        ("R2.2", "GND"),
    }


def test_instantiate_net_and_designator_overrides(tmp_path) -> None:
    template = _divider_template(tmp_path)
    result = instantiate(
        template,
        DesignSpec(
            inputs={"vin": 12, "vout": 5},
            nets={"vin": "VIN_12V", "gnd": "GND_D"},
            designators={"R_top": "R12", "R_bottom": "R34"},
        ),
    )
    assert {c.id for c in result.ir.components} == {"R12", "R34"}
    assert {n.name for n in result.ir.nets} == {"VIN_12V", "VOUT", "GND_D"}


def test_instantiate_missing_required_input_raises(tmp_path) -> None:
    template = _divider_template(tmp_path)
    with pytest.raises(DesignError, match="vin"):
        instantiate(template, DesignSpec(inputs={"vout": 5}))


def test_instantiate_circular_dependency_raises(tmp_path) -> None:
    import json

    from pathlib import Path

    p = tmp_path / "cycle.json"
    p.write_text(
        json.dumps(
            {
                "id": "cyc",
                "name": "环",
                "signals": [{"id": "gnd", "type": "net", "name_pattern": "GND"}],
                "components": [
                    {
                        "id": "R1",
                        "type": "Resistor",
                        "pins": [{"number": "1", "net": "gnd"}, {"number": "2", "net": "gnd"}],
                    },
                    {
                        "id": "R2",
                        "type": "Resistor",
                        "pins": [{"number": "1", "net": "gnd"}, {"number": "2", "net": "gnd"}],
                    },
                ],
                "parameters": {"R1.value": "R2.value", "R2.value": "R1.value"},
            }
        ),
        encoding="utf-8",
    )
    template = load_primitives(tmp_path)[0]
    with pytest.raises(DesignError, match="[Cc]ircular"):
        instantiate(template, DesignSpec())
```

注意：该文件顶部需 `import pytest` 与 `from circuit_graph.primitives import PrimitiveTemplate`（供类型标注；如嫌啰嗦可去掉标注）。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/design/test_instantiate.py -v`
Expected: `ImportError: cannot import name 'instantiate' from 'circuit_graph.design'`

- [ ] **Step 3: 实现**

`circuit_graph/design/instantiate.py`：

```python
"""Instantiate a primitive template into a concrete CircuitIR.

The instantiated IR is a real, serialisable circuit: components carry resolved
values, nets are named, pins and connections follow the canonical id formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from circuit_graph.design.expr import ExpressionError, evaluate, expr_names, parse_number
from circuit_graph.ir import (
    CapacitanceValue,
    CircuitIR,
    CircuitMeta,
    Component,
    Connection,
    DeviceKind,
    InductanceValue,
    Net,
    NormalizedValue,
    Pin,
    Provenance,
    ResistanceValue,
    make_pin_id,
)
from circuit_graph.primitives import PrimitiveTemplate

_KIND_BY_TYPE = {kind.value: kind for kind in DeviceKind}

_DESIGNATOR_PREFIX = {
    DeviceKind.RESISTOR: "R",
    DeviceKind.CAPACITOR: "C",
    DeviceKind.INDUCTOR: "L",
    DeviceKind.DIODE: "D",
    DeviceKind.MOSFET: "Q",
    DeviceKind.IC: "U",
    DeviceKind.BRIDGE_RECTIFIER: "BR",
    DeviceKind.UNKNOWN: "U",
}


class DesignError(ValueError):
    """Raised when a design spec cannot be instantiated."""


@dataclass(frozen=True)
class DesignSpec:
    """Designer-provided values for instantiating a primitive."""

    inputs: dict[str, float] = field(default_factory=dict)
    nets: dict[str, str] = field(default_factory=dict)
    designators: dict[str, str] = field(default_factory=dict)
    start_index: int = 1


@dataclass(frozen=True)
class DesignResult:
    ir: CircuitIR
    resolved: dict[str, float]
    warnings: list[str] = field(default_factory=list)


def instantiate(template: PrimitiveTemplate, spec: DesignSpec) -> DesignResult:
    """Resolve ``template`` parameters against ``spec`` and build a CircuitIR."""
    inputs = _gather_inputs(template, spec)
    resolved = _resolve_parameters(template.parameters, inputs)
    ir = _build_ir(template, spec, resolved)
    return DesignResult(ir=ir, resolved=resolved)


def _gather_inputs(template: PrimitiveTemplate, spec: DesignSpec) -> dict[str, float]:
    inputs = dict(spec.inputs)
    for design_input in template.design_inputs:
        if design_input.id in inputs:
            continue
        if design_input.default is not None:
            inputs[design_input.id] = parse_number(str(design_input.default))
        else:
            raise DesignError(f"Missing required design input: {design_input.id!r}")
    return inputs


def _resolve_parameters(parameters: dict[str, str], inputs: dict[str, float]) -> dict[str, float]:
    """Resolve parameter expressions in dependency order (topological, cycle-safe)."""
    bound = dict(inputs)
    resolved: dict[str, float] = {}
    pending = dict(parameters)
    while pending:
        progress = False
        for key, expr in list(pending.items()):
            if any(name in pending for name in expr_names(expr)):
                continue
            try:
                value = evaluate(expr, bound)
            except ZeroDivisionError as exc:
                raise DesignError(f"Division by zero in parameter {key!r}") from exc
            except ExpressionError as exc:
                raise DesignError(f"Invalid expression for {key!r}: {exc}") from exc
            bound[key] = value
            resolved[key] = value
            del pending[key]
            progress = True
        if not progress:
            raise DesignError(f"Circular parameter dependency: {', '.join(sorted(pending))}")
    return resolved


def _to_device_kind(comp_type: str | tuple[str, ...]) -> DeviceKind:
    candidates = (comp_type,) if isinstance(comp_type, str) else comp_type
    for candidate in candidates:
        if candidate in _KIND_BY_TYPE:
            return _KIND_BY_TYPE[candidate]
    raise DesignError(f"No DeviceKind mapping for component type {comp_type!r}")


def _make_value(kind: DeviceKind, magnitude: float | None) -> NormalizedValue | None:
    if magnitude is None:
        return None
    if kind is DeviceKind.RESISTOR:
        return ResistanceValue(magnitude=magnitude, unit="Ω")
    if kind is DeviceKind.CAPACITOR:
        return CapacitanceValue(magnitude=magnitude, unit="µF")
    if kind is DeviceKind.INDUCTOR:
        return InductanceValue(magnitude=magnitude, unit="µH")
    return None


def _build_ir(
    template: PrimitiveTemplate, spec: DesignSpec, resolved: dict[str, float]
) -> CircuitIR:
    comps_by_id = {c.id: c for c in template.components}
    kinds: dict[str, DeviceKind] = {}
    designators: dict[str, str] = {}
    for index, comp in enumerate(template.components):
        kind = _to_device_kind(comp.type)
        kinds[comp.id] = kind
        designators[comp.id] = spec.designators.get(
            comp.id, f"{_DESIGNATOR_PREFIX[kind]}{spec.start_index + index}"
        )

    net_names = {
        sig.id: spec.nets.get(sig.id, sig.id.upper()) for sig in template.signals
    }

    components: list[Component] = []
    connections: list[Connection] = []
    for comp in template.components:
        comp_id = designators[comp.id]
        magnitude = resolved.get(f"{comp.id}.value")
        pins = [
            Pin(
                id=make_pin_id(comp_id, pin.number),
                number=str(pin.number),
                net_id=net_names[pin.net],
            )
            for pin in comp.pins
        ]
        for pin in comp.pins:
            connections.append(
                Connection(
                    pin_id=make_pin_id(comp_id, pin.number),
                    net_id=net_names[pin.net],
                    provenance=Provenance.MANUAL,
                )
            )
        components.append(
            Component(
                id=comp_id,
                device_kind=kinds[comp.id],
                value=_make_value(kinds[comp.id], magnitude),
                raw_value=_format_raw(magnitude) if magnitude is not None else None,
                pins=pins,
                metadata={"primitive": template.id, "primitive_component": comp.id},
            )
        )

    nets = [Net(id=name, name=name) for name in dict.fromkeys(net_names.values())]
    return CircuitIR(
        meta=CircuitMeta(source=template.id, format="primitive-design"),
        components=components,
        nets=nets,
        connections=connections,
    )


def _format_raw(magnitude: float) -> str:
    """Compact engineering-ish string for the .NET value line, e.g. 14000.0 -> '14k'."""
    if magnitude >= 1e6:
        return f"{magnitude / 1e6:g}M"
    if magnitude >= 1e3:
        return f"{magnitude / 1e3:g}k"
    return f"{magnitude:g}"
```

`circuit_graph/design/__init__.py` 更新为：

```python
"""Parametric design output: turn primitives into concrete circuits."""

from circuit_graph.design.expr import ExpressionError, evaluate, expr_names, parse_number
from circuit_graph.design.instantiate import (
    DesignError,
    DesignResult,
    DesignSpec,
    instantiate,
)

__all__ = [
    "DesignError",
    "DesignResult",
    "DesignSpec",
    "ExpressionError",
    "evaluate",
    "expr_names",
    "instantiate",
    "parse_number",
]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/design/ -v`
Expected: 12 PASS（Task 2 的 7 个 + 本 Task 5 个）

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/design tests/design
git commit -m "feat(design): instantiate primitives into CircuitIR from a design spec" -m "Topological parameter resolution, DeviceKind mapping, canonical pin ids, net naming with overrides."
```

---

### Task 4: 首个可设计 primitive — voltage-divider

**Files:**
- Modify: `primitives/power/voltage-divider.json`
- Test: `tests/design/test_instantiate.py`（追加集成测试）

**Interfaces:**
- Consumes: `load_primitives`（circuit_graph/primitives.py:185）、`instantiate`（Task 3）。
- Produces: 仓库内第一个带 design 字段的真实 primitive，作为后续 primitive 补 design 字段的范本。

- [ ] **Step 1: 写失败测试** — 追加到 `tests/design/test_instantiate.py`：

```python
def test_real_voltage_divider_primitive_is_instantiable() -> None:
    """Integration: the shipped primitives/power/voltage-divider.json must instantiate."""
    template = next(t for t in load_primitives() if t.id == "voltage-divider")
    result = instantiate(template, DesignSpec(inputs={"vin": 12, "vout": 5}))
    by_id = {c.id: c for c in result.ir.components}
    assert isinstance(by_id["R1"].value, ResistanceValue)
    assert by_id["R1"].value.magnitude == 14000.0
    assert by_id["R2"].value.magnitude == 10000.0
    assert template.recommended_parts  # 选型建议已落地
```

（`load_primitives` 已在文件顶部 import；`ResistanceValue` 亦然。）

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/design/test_instantiate.py::test_real_voltage_divider_primitive_is_instantiable -v`
Expected: `KeyError: 'R1'`（r_bottom 默认值不存在，R_top 求值失败抛 DesignError 之前就先在 by_id 查找处失败——实际首个失败点可能是 `DesignError`，两者皆可视为"失败"）

- [ ] **Step 3: 给 voltage-divider.json 补 design 字段**

在 `primitives/power/voltage-divider.json` 的 `"scoring"` 之后追加（注意 JSON 逗号）：

```json
  "design_inputs": [
    {"id": "vin", "label": "输入电压", "unit": "V"},
    {"id": "vout", "label": "输出电压", "unit": "V"},
    {"id": "r_bottom", "label": "下电阻阻值", "unit": "Ω", "default": 10000}
  ],
  "parameters": {
    "R_top.value": "(vin - vout) * r_bottom / vout",
    "R_bottom.value": "r_bottom"
  },
  "recommended_parts": {
    "R_top": [
      {"pn": "RC0603FR-07 系列 1%", "footprint": "0603"},
      {"pn": "RC0805FR-07 系列 1%", "footprint": "0805"}
    ],
    "R_bottom": [
      {"pn": "RC0603FR-07 系列 1%", "footprint": "0603"},
      {"pn": "RC0805FR-07 系列 1%", "footprint": "0805"}
    ]
  }
```

- [ ] **Step 4: 运行测试 + 索引检查**

Run:
1. `uv run pytest tests/design/ -v` — Expected: 全 PASS
2. `uv run pytest tests/test_primitives.py -v` — Expected: 全 PASS（loader 仍接受新字段）
3. `uv run python scripts/build_primitive_index.py --check` — Expected: 退出码 0（新字段不影响索引；若漂移则跑 `uv run python scripts/build_primitive_index.py` 重新生成并提交生成物）

- [ ] **Step 5: Commit**

```bash
git add primitives/power/voltage-divider.json tests/design/test_instantiate.py
git commit -m "feat(primitives): make voltage-divider the first designable primitive" -m "Adds design_inputs/parameters/recommended_parts; serves as the template for other primitives."
```

---

### Task 5: Protel .NET 导出器（IR → 网表）

**Files:**
- Create: `circuit_graph/exporters/protel_netlist.py`
- Modify: `circuit_graph/exporters/__init__.py`（导出 `write_protel_netlist`）
- Test: `tests/exporters/test_protel_netlist.py`

**Interfaces:**
- Consumes: `CircuitIR`（含 Task 3 产出物与 protel parser 产出物两种来源）、`ProtelNetParser` + `ParserInput`（circuit_graph/parsers/protel.py）。
- Produces（Task 6 消费）:
  - `write_protel_netlist(ir: CircuitIR, path: Path | str) -> None` — 写出 UTF-8 / LF 的 .NET 文件
  - `format_value(value: NormalizedValue | None, raw_value: str | None) -> str` — 组件的 value 行

**.NET 格式（与 parser 对称，见 circuit_graph/parsers/protel.py:152-202）:**
- component 块：`[` + 3 行（id / footprint / value）+ 3 空行 + `]`
- net 块：`(` + net 名 + 每连接一行 `{RefDes}-{PinNum}` + `)`
- pin id `"R1.1"` 反转为 token `"R1-1"`

- [ ] **Step 1: 写失败测试** — `tests/exporters/test_protel_netlist.py`：

```python
from pathlib import Path

from circuit_graph.design import DesignSpec, instantiate
from circuit_graph.exporters.protel_netlist import write_protel_netlist
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from circuit_graph.primitives import load_primitives

FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "protel" / "add-primitives.NET"
)


def test_write_and_reparse_roundtrip(tmp_path: Path) -> None:
    parser = ProtelNetParser()
    ir = parser.parse(ParserInput(source_id=str(FIXTURE), file_path=str(FIXTURE)))
    out = tmp_path / "roundtrip.NET"
    write_protel_netlist(ir, out)
    ir2 = parser.parse(ParserInput(source_id=str(out), file_path=str(out)))
    assert [c.id for c in ir2.components] == [c.id for c in ir.components]
    assert sorted(n.name for n in ir2.nets) == sorted(n.name for n in ir.nets)
    assert sorted(c.pin_id for c in ir2.connections) == sorted(
        c.pin_id for c in ir.connections
    )


def test_designed_ir_writes_valid_netlist(tmp_path: Path) -> None:
    template = next(t for t in load_primitives() if t.id == "voltage-divider")
    result = instantiate(template, DesignSpec(inputs={"vin": 12, "vout": 5}))
    out = tmp_path / "divider.NET"
    write_protel_netlist(result.ir, out)
    text = out.read_text(encoding="utf-8")
    assert "[R1" not in text  # 组件块以 '[' 独占一行
    assert "R1" in text
    assert "14k" in text
    # 重新解析验证结构
    ir2 = ProtelNetParser().parse(ParserInput(source_id=str(out), file_path=str(out)))
    by_id = {c.id: c for c in ir2.components}
    assert set(by_id) == {"R1", "R2"}
    assert by_id["R1"].raw_value == "14k"
    conns = {(c.pin_id, c.net_id) for c in ir2.connections}
    assert ("R1.1", "VIN") in conns
    assert ("R2.2", "GND") in conns
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/exporters/test_protel_netlist.py -v`
Expected: `ModuleNotFoundError: No module named 'circuit_graph.exporters.protel_netlist'`

- [ ] **Step 3: 实现**

`circuit_graph/exporters/protel_netlist.py`：

```python
"""Protel .NET netlist writer — the inverse of circuit_graph/parsers/protel.py.

Component blocks::

    [
    R1
    0603
    14k



    ]

Net blocks::

    (
    VIN
    R1-1
    )

Output is UTF-8 with LF line endings; the parser's encoding auto-detection
reads it back without conversion.
"""

from __future__ import annotations

from pathlib import Path

from circuit_graph.ir import CircuitIR, NormalizedValue


def format_value(value: NormalizedValue | None, raw_value: str | None) -> str:
    """Return the third line of a .NET component block."""
    if raw_value:
        return raw_value
    if value is None:
        return ""
    return f"{value.magnitude:g}{value.unit}"


def write_protel_netlist(ir: CircuitIR, path: Path | str) -> None:
    """Write ``ir`` as a Protel .NET netlist file at ``path``."""
    lines: list[str] = []
    for comp in ir.components:
        lines.append("[")
        lines.append(comp.id)
        lines.append(comp.footprint or "")
        lines.append(format_value(comp.value, comp.raw_value))
        lines.extend(["", "", ""])
        lines.append("]")
    for net in ir.nets:
        lines.append("(")
        lines.append(net.name)
        for conn in ir.connections:
            if conn.net_id != net.id:
                continue
            comp_id, _, pin_num = conn.pin_id.rpartition(".")
            lines.append(f"{comp_id}-{pin_num}")
        lines.append(")")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

`circuit_graph/exporters/__init__.py` 更新：

```python
"""Exporters: CircuitIR -> external formats."""

from circuit_graph.exporters.graphml import write_graphml
from circuit_graph.exporters.protel_netlist import format_value, write_protel_netlist

__all__ = ["format_value", "write_graphml", "write_protel_netlist"]
```

（若现有 `__init__.py` 仅一行 `from circuit_graph.exporters.graphml import write_graphml`，则改写成上面形式。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/exporters/test_protel_netlist.py tests/parsers/test_protel.py -v`
Expected: 全 PASS（round-trip 同时证明 parser 未回归）

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/exporters tests/exporters
git commit -m "feat(exporters): add Protel .NET netlist writer" -m "Inverse of the protel parser; round-trip tested against add-primitives.NET."
```

---

### Task 6: CLI 子命令 `cg design`

**Files:**
- Modify: `circuit_graph/cli.py`（顶部常量区 :37-41 与命令注册区）
- Test: `tests/test_cli_design.py`

**Interfaces:**
- Consumes: `load_primitives`、`DesignSpec`、`instantiate`、`DesignError`、`parse_number`（design 包）、`write_protel_netlist`、`ir_to_json`（circuit_graph/ir.py:333）、click 惯例（`@main.command()`，参照 cli.py:76-104 的 `view`）。
- Produces: `cg design <primitive-id> --spec <file> [-o out.NET] [--json]`

**CLI 契约（写死）:**
- `--spec` 为 JSON 文件，结构 `{"inputs": {...}, "nets": {...}, "designators": {...}, "start_index": n}`（除 inputs 外均可省略）。
- 默认输出 `<repo>/output/design.NET`；`--json` 时写 `output/design.json`（IR JSON）。
- 未知 primitive → stderr 报错 + exit 1；`DesignError` → stderr + exit 1；warnings → stderr 逐行 `warning: ...`。

- [ ] **Step 1: 写失败测试** — `tests/test_cli_design.py`：

```python
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import main


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_cli_design_writes_netlist(runner: CliRunner, tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"inputs": {"vin": 12, "vout": 5}}), encoding="utf-8")
    out = tmp_path / "out.NET"
    result = runner.invoke(
        main, ["design", "voltage-divider", "--spec", str(spec), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert "R1" in text and "R2" in text
    assert "14k" in text


def test_cli_design_json_output(runner: CliRunner, tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"inputs": {"vin": 12, "vout": 5}}), encoding="utf-8")
    out = tmp_path / "out.json"
    result = runner.invoke(
        main, ["design", "voltage-divider", "--spec", str(spec), "-o", str(out), "--json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {c["id"] for c in data["components"]} == {"R1", "R2"}


def test_cli_design_unknown_primitive(runner: CliRunner, tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text("{}", encoding="utf-8")
    result = runner.invoke(main, ["design", "no-such-primitive", "--spec", str(spec)])
    assert result.exit_code == 1
    assert "no-such-primitive" in result.output
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_cli_design.py -v`
Expected: `No such command 'design'`（click 报错，exit code 2）

- [ ] **Step 3: 实现**

`circuit_graph/cli.py` 顶部常量区（:41 之后）加：

```python
DEFAULT_DESIGN_OUTPUT = _PROJECT_ROOT / "output" / "design.NET"
DEFAULT_DESIGN_JSON = _PROJECT_ROOT / "output" / "design.json"
```

import 区加：

```python
from circuit_graph.design import DesignError, DesignSpec, instantiate, parse_number
from circuit_graph.exporters.protel_netlist import write_protel_netlist
from circuit_graph.ir import ir_to_json
from circuit_graph.primitives import load_primitives
```

命令注册（放在 `layout_check` 之后）：

```python
@main.command(name="design")
@click.argument("primitive_id")
@click.option(
    "--spec",
    "spec_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="JSON design spec: {inputs, nets, designators, start_index}.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output .NET netlist (default: <repo>/output/design.NET).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit CircuitIR JSON instead of a .NET netlist.",
)
def design(primitive_id: str, spec_path: Path, output: Path | None, as_json: bool) -> None:
    """Instantiate a primitive template into a concrete netlist."""
    templates = load_primitives()
    template = next((t for t in templates if t.id == primitive_id), None)
    if template is None:
        click.echo(f"Unknown primitive: {primitive_id}", err=True)
        sys.exit(1)

    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    spec = DesignSpec(
        inputs={k: parse_number(v) for k, v in raw.get("inputs", {}).items()},
        nets=raw.get("nets", {}),
        designators=raw.get("designators", {}),
        start_index=int(raw.get("start_index", 1)),
    )
    try:
        result = instantiate(template, spec)
    except DesignError as exc:
        click.echo(f"Design error: {exc}", err=True)
        sys.exit(1)
    for warning in result.warnings:
        click.echo(f"warning: {warning}", err=True)

    out = output or (DEFAULT_DESIGN_JSON if as_json else DEFAULT_DESIGN_OUTPUT)
    out.parent.mkdir(parents=True, exist_ok=True)
    if as_json:
        out.write_text(ir_to_json(result.ir), encoding="utf-8")
    else:
        write_protel_netlist(result.ir, out)
    click.echo(f"Wrote {len(result.ir.components)} components to {out}")
```

（`_PROJECT_ROOT` 已在 cli.py:37 定义，`json`/`sys`/`Path`/`click` 均已 import。）

- [ ] **Step 4: 运行测试确认通过**

Run:
1. `uv run pytest tests/test_cli_design.py -v` — Expected: 3 PASS
2. `uv run cg design --help` — Expected: 显示 usage（冒烟验证注册成功）

- [ ] **Step 5: Commit**

```bash
git add circuit_graph/cli.py tests/test_cli_design.py
git commit -m "feat(cli): add cg design to instantiate primitives into netlists" -m "cg design <primitive-id> --spec spec.json [-o out.NET] [--json]"
```

---

### Task 7: README 文档 + 全量回归

**Files:**
- Modify: `README.md`（新章节）
- Test: 全量 pytest（无新增测试文件）

**Interfaces:**
- Consumes: Task 1-6 全部产出。
- Produces: 面向用户的用法文档。

- [ ] **Step 1: README 新增章节**

在 README 合适位置（CLI 命令说明区之后）加：

```markdown
## 设计输出（design output）

`primitives/` 拓扑模板除识别外，还可用作**参数化设计宏**：给定规格，实例化出具体电路并反向写出 Protel .NET 网表。

```bash
# 1. 写规格文件 spec.json
#    {"inputs": {"vin": 12, "vout": 5}}   # 其余可选：nets / designators / start_index
# 2. 实例化
cg design voltage-divider --spec spec.json -o divider.NET
# 3. 输出 IR JSON（调试用）
cg design voltage-divider --spec spec.json --json -o divider.json
```

- 支持的设计字段（向后兼容，不影响识别）：`design_inputs`（必填规格项，可带默认值）、`parameters`（`"<组件>.value"` → 四则表达式，可引用设计输入或其他参数）、`recommended_parts`（选型建议：型号 + footprint）。
- 目前仅 `voltage-divider` 落地 design 字段；其余模板可照 `primitives/power/voltage-divider.json` 补写。
```

- [ ] **Step 2: 全量回归**

Run: `uv run pytest -v`
Expected: 全 PASS（新增 ~18 个测试 + 既有全部）

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document cg design output workflow" -m "Spec JSON format, design fields, and the voltage-divider reference template."
```

---

## Self-Review 记录

1. **Spec 覆盖**：原始评估结论的四个可执行点全部落地 — schema 增量扩展（Task 1）、参数引擎（Task 2）、实例化产出 CircuitIR（Task 3/4）、Protel 写回（Task 5）、CLI（Task 6）、文档（Task 7）。识别方向回归由 Task 1 兼容测试 + Task 4 索引检查 + Task 7 全量回归三重锁定。
2. **类型一致性**：`DesignSpec/DesignResult/instantiate/parse_number/evaluate/expr_names/write_protel_netlist` 在 Interfaces 块与代码中命名一致；pin id（`{comp}.{num}`）与 .NET token（`{comp}-{num}`）约定在 Global Constraints 声明并被 Task 3/5 的测试断言。
3. **无占位符**：所有步骤含可运行代码与预期输出。
