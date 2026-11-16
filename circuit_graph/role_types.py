"""role 引擎的公共数据类型 —— 结果对象与数值格式化。

单独成模块，避免 :mod:`circuit_graph.role` 与 :mod:`circuit_graph.role_rules`
互相 import 形成环。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RoleResult:
    """一个规则命中结果。"""

    rule: str
    title: str  # 第 1 行功能定性，如 "反馈电阻（U7 LM321 同相放大器）"
    summary: str  # 一句作用说明（含关键数值）
    evidence: list[str] = field(default_factory=list)  # 拓扑证据行
    confidence: str = "medium"  # high / medium / low
    data: dict[str, object] = field(default_factory=dict)  # 机读附加数据


def fmt_num(x: float) -> str:
    """数值 → 短文本（整数不带小数点）。"""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.2g}"


def fmt_freq(x: float) -> str:
    """频率（Hz） → 带单位的短文本（保留足够有效位，如 32.768 kHz）。"""
    for unit, div in (("MHz", 1e6), ("kHz", 1e3), ("Hz", 1.0)):
        if x >= div:
            return f"{x / div:.6g} {unit}"
    return f"{x:.6g} Hz"


__all__ = ["RoleResult", "fmt_freq", "fmt_num"]
