"""role_attrs 属性识别的单元测试 —— 三极管型号表只认可靠通用器件事实。

背景（T193 终审发现）：型号表曾把 PNP 的 9012/MMBT3906 列进 NPN、把 NPN 的
9014 列进 PNP，还混进了两表重复或不存在的型号（KSC184/KSC285/KSC301、
MMBT3401、MMBT4930、8551、2N3901 …）。这里的合同：

* 同一型号绝不能同时是 NPN 和 PNP；
* MOSFET 型号不得被 BJT 规则认走；
* 表里只放通用器件事实可确认的型号——拿不准就不收录（宁可 unknown）。
"""

from __future__ import annotations

import pytest

from circuit_graph.ir import Component, DeviceKind
from circuit_graph.role_attrs import (
    ATTR_NPN,
    ATTR_PNP,
    _NPN_PARTS,
    _PNP_PARTS,
    classify,
)


def _comp(part_number: str) -> Component:
    return Component(
        id="Q1",
        device_kind=DeviceKind.UNKNOWN,
        footprint="SOT-23",
        part_number=part_number,
    )


def test_npn_and_pnp_tables_are_disjoint() -> None:
    """同一型号不得同时出现在 NPN 与 PNP 两张表里。"""
    overlap = _NPN_PARTS & _PNP_PARTS
    assert not overlap, f"NPN/PNP 两表重叠的型号: {sorted(overlap)}"


@pytest.mark.parametrize("part", sorted(_NPN_PARTS))
def test_npn_table_entries_are_npn(part: str) -> None:
    facts = classify(_comp(part))
    assert facts.kind == ATTR_NPN, f"{part} 应判为 NPN，实际 {facts.kind}"


@pytest.mark.parametrize("part", sorted(_PNP_PARTS))
def test_pnp_table_entries_are_pnp(part: str) -> None:
    facts = classify(_comp(part))
    assert facts.kind == ATTR_PNP, f"{part} 应判为 PNP，实际 {facts.kind}"


@pytest.mark.parametrize(
    "part,expected",
    [
        # 曾被错误收录的型号：9012 是 PNP、9014 是 NPN，方向必须正确
        ("9012", ATTR_PNP),
        ("9014", ATTR_NPN),
        ("MMBT3906", ATTR_PNP),
        ("MMBT3904", ATTR_NPN),
        ("2N3906", ATTR_PNP),
        ("2N3904", ATTR_NPN),
        # T193 板上的既有正确判断不得回退
        ("8050", ATTR_NPN),
        ("S8050", ATTR_NPN),
        ("8550", ATTR_PNP),
        ("S8550", ATTR_PNP),
    ],
)
def test_polarity_of_common_parts(part: str, expected: str) -> None:
    assert classify(_comp(part)).kind == expected


@pytest.mark.parametrize(
    "part",
    [
        # MOSFET 型号不得被 BJT 规则认走
        "2N7002",
        "AO3400",
        "AO3401",
        "IRLZ44N",
        "SI2302",
        # 拿不准/不存在的型号：宁可 unknown，不乱定性
        "KSC184",
        "KSC285",
        "KSC301",
        "MMBT3401",
        "MMBT4930",
        "8551",
        "2N3901",
        "MMBT5411",
        # 旧表 (?:S)? 前缀把 "S3904"/"S3906" 这类不存在的型号也认走
        "S3904",
        "S3906",
    ],
)
def test_unverifiable_or_mosfet_parts_stay_unknown(part: str) -> None:
    facts = classify(_comp(part))
    assert facts.kind is None, f"{part} 不应被定性，实际 {facts.kind}"
