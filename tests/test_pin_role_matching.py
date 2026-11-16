"""引脚角色门控 + IC 级角色子集校验的回归测试（todolist.md 任务 A，A6）。

覆盖：
- JW5065（降压芯片）不得命中 differential-comparator / opamp-unit-* /
  inverting-amplifier —— 它的 VIN/SW/FB/BST/EN 角色集合缺运放必须的
  noninv_in/inv_in。
- RH6016C（触摸芯片）不得命中 differential-comparator —— 它只有
  supply/ground/output 三个已知角色，没有 noninv_in/inv_in。
- LM321 仍命中 inverting-amplifier（回归：门控/子集校验不能误杀真运放）。
- 无引脚名的 IC（KB 没有 pinout）仍可命中 —— 门控条件 2（provider 非空）
  不满足，一律放行，不回归。
- 门控生效性：没有 pin_category_provider 时（裸 Vf2TopologyMatcher）不做
  角色约束 —— tests/test_vf2_matcher.py 的合成 IR 用网络名回显当 pin.name，
  门控不生效才能保持它们的行为不变。
"""

from __future__ import annotations

from dataclasses import replace

from circuit_graph.ir import DeviceKind, NetType
from circuit_graph.primitives import load_primitives
from circuit_graph.vf2_matcher import Vf2TopologyMatcher
from tests.fir_helpers import build_ir, make_component, make_net

_TEMPLATES = {t.id: t for t in load_primitives()}


def _opamp_candidate_ir(pins: list[tuple[str, str]], *, part_number: str) -> object:
    """单个 IC + 供电/地/输入/输出网络，够 inverting-amplifier 之类模板用的拓扑。

    ``pins`` 是 (pin_number, pin_name) 对；net 名字直接取 pin_name，
    让 name_pattern 预筛选能命中（如 VCC/GND/IN+/IN-/OUT/VIN/SW/FB...)。
    """
    comp = make_component(
        "U1", DeviceKind.IC, pins, part_number=part_number, raw_value=part_number
    )
    seen_nets: dict[str, str] = {}
    nets = []
    wiring = []
    for pin_num, pin_name in pins:
        net_id = seen_nets.setdefault(pin_name, f"N_{pin_name}")
        if net_id not in [n.id for n in nets]:
            nets.append(make_net(net_id, pin_name, NetType.SIGNAL))
        wiring.append((f"U1.{pin_num}", net_id))
    return build_ir([comp], nets, wiring)


def _provider_for(pin_names: dict[str, str]):
    """返回一个 pin_category_provider：固定给 U1 一份非空 category dict。

    category 的具体值不重要——`gate_active` 只检查 provider(comp) 非空，
    真正的角色判定走 `role_of_circuit_pin(pin.name)`。
    """

    def provider(comp):
        if comp.id != "U1":
            return None
        return {num: "unknown" for num in pin_names}

    return provider


def _find_u1_match(template_id: str, ir, provider):
    matcher = Vf2TopologyMatcher(ir, ignore_pin_numbers=True, pin_category_provider=provider)
    result = matcher.find_matches(_TEMPLATES[template_id], timeout_seconds=5.0, max_matches=50)
    return [m for m in result.matches if "U1" in m.component_mapping.values()]


# JW5065：VIN/SW/GND/BST/EN/FB —— 降压芯片，没有 noninv_in/inv_in。
_JW5065_PINS = [
    ("1", "MODE"), ("2", "VIN"), ("3", "SW"), ("4", "GND"),
    ("5", "BST"), ("6", "EN"), ("8", "FB"),
]

# RH6016C：CS/GND/TCH/AHLB/VDD/OC —— 触摸芯片，已知角色只有 ground/supply/output。
_RH6016C_PINS = [
    ("1", "CS"), ("2", "GND"), ("3", "TCH"), ("4", "AHLB"),
    ("5", "VDD"), ("6", "OC"),
]

# LM321：真运放引脚，必须仍能命中。
_LM321_PINS = [("1", "+"), ("2", "GND"), ("3", "-"), ("4", "OUT"), ("5", "V+")]


def test_jw5065_rejected_by_differential_comparator() -> None:
    ir = _opamp_candidate_ir(_JW5065_PINS, part_number="JW5065")
    provider = _provider_for(dict(_JW5065_PINS))
    assert _find_u1_match("differential-comparator", ir, provider) == []


def test_jw5065_rejected_by_opamp_unit_differential_amplifier() -> None:
    ir = _opamp_candidate_ir(_JW5065_PINS, part_number="JW5065")
    provider = _provider_for(dict(_JW5065_PINS))
    assert _find_u1_match("opamp-unit-differential-amplifier", ir, provider) == []


def test_jw5065_rejected_by_inverting_amplifier() -> None:
    ir = _opamp_candidate_ir(_JW5065_PINS, part_number="JW5065")
    provider = _provider_for(dict(_JW5065_PINS))
    assert _find_u1_match("inverting-amplifier", ir, provider) == []


def test_rh6016c_rejected_by_differential_comparator() -> None:
    ir = _opamp_candidate_ir(_RH6016C_PINS, part_number="RH6016C")
    provider = _provider_for(dict(_RH6016C_PINS))
    assert _find_u1_match("differential-comparator", ir, provider) == []


def test_lm321_still_matches_inverting_amplifier() -> None:
    """回归：真运放的角色集合全覆盖，门控/子集校验不能误杀它。"""
    # inverting-amplifier 还需要 Rin/Rf 两只电阻，补齐最小拓扑。
    opamp = make_component(
        "U1", DeviceKind.IC, _LM321_PINS, part_number="LM321", raw_value="LM321"
    )
    rin = make_component("R1", DeviceKind.RESISTOR, [("1", "VIN"), ("2", "FBN")])
    rf = make_component("R2", DeviceKind.RESISTOR, [("1", "VOUT"), ("2", "FBN")])
    nets = [
        make_net("N_VCC", "VCC", NetType.POWER),
        make_net("N_GND", "GND", NetType.GND),
        make_net("N_INP", "IN+", NetType.SIGNAL),
        make_net("N_FBN", "FBN", NetType.SIGNAL),
        make_net("N_OUT", "VOUT", NetType.SIGNAL),
        make_net("N_VIN", "VIN", NetType.SIGNAL),
    ]
    wiring = [
        ("U1.5", "N_VCC"), ("U1.2", "N_GND"),
        ("U1.1", "N_INP"), ("U1.3", "N_FBN"), ("U1.4", "N_OUT"),
        ("R1.1", "N_VIN"), ("R1.2", "N_FBN"),
        ("R2.1", "N_OUT"), ("R2.2", "N_FBN"),
    ]
    # IN+ 要接地才符合 inverting-amplifier 对 in_p 的角色约束（noninv_in 通常接 GND）；
    # 这里只验证 IC 级角色子集 + 逐引脚角色一致性，故直接复用上面的 wiring。
    ir = build_ir([opamp, rin, rf], nets, wiring)
    provider = _provider_for(dict(_LM321_PINS))
    matches = _find_u1_match("inverting-amplifier", ir, provider)
    assert matches, "LM321 应命中 inverting-amplifier（角色全覆盖，不应被误杀）"


def test_ic_without_kb_pinout_still_matches_when_topology_fits() -> None:
    """门控条件 2：provider(comp) 为空（不在库/无 pinout）时一律放行，不回归。

    用和 LM321 同样的拓扑，但 pin.name 换成合成测试常见的"网络名回显"
    （不是角色词表里的任何名字），且 provider 对该元件返回 None——
    模拟"KB 没有这个元件的 pinout"。角色约束不应生效，仍应命中。
    """
    pins = [("1", "AINP"), ("2", "GND"), ("3", "AINN"), ("4", "VOUT1"), ("5", "VCC1")]
    opamp = make_component("U1", DeviceKind.IC, pins, part_number="UNKNOWN_PART")
    rin = make_component("R1", DeviceKind.RESISTOR, [("1", "VIN"), ("2", "FBN")])
    rf = make_component("R2", DeviceKind.RESISTOR, [("1", "VOUT1"), ("2", "FBN")])
    nets = [
        make_net("N_VCC", "VCC1", NetType.POWER),
        make_net("N_GND", "GND", NetType.GND),
        make_net("N_INP", "AINP", NetType.SIGNAL),
        make_net("N_FBN", "FBN", NetType.SIGNAL),
        make_net("N_OUT", "VOUT1", NetType.SIGNAL),
        make_net("N_VIN", "VIN", NetType.SIGNAL),
    ]
    wiring = [
        ("U1.5", "N_VCC"), ("U1.2", "N_GND"),
        ("U1.1", "N_INP"), ("U1.3", "N_FBN"), ("U1.4", "N_OUT"),
        ("R1.1", "N_VIN"), ("R1.2", "N_FBN"),
        ("R2.1", "N_OUT"), ("R2.2", "N_FBN"),
    ]
    ir = build_ir([opamp, rin, rf], nets, wiring)

    def provider(comp):
        return None  # 元件不在库

    matches = _find_u1_match("inverting-amplifier", ir, provider)
    assert matches, "provider(comp) 为空时应放行，不应被角色约束误杀"


def test_gate_inactive_without_provider_keeps_synthetic_ir_behaviour() -> None:
    """门控生效性：没有 pin_category_provider 时裸 matcher 不做角色约束。

    tests/test_vf2_matcher.py 的合成 IR 把网络名回显写进 pin.name
    （例如运放 + 脚上写 "VIN"），这些名字会被角色词表误判成 dc_input。
    裸 Vf2TopologyMatcher()（provider=None）必须对此保持宽松——这正是
    A1 门控条件 1 的回归验证。
    """
    pins = [("1", "IN+"), ("2", "GND"), ("3", "FBN"), ("4", "VOUT"), ("5", "VCC")]
    opamp = make_component("U1", DeviceKind.IC, pins, part_number="SYN")
    rin = make_component("R1", DeviceKind.RESISTOR, [("1", "SIGIN"), ("2", "FBN")])
    rf = make_component("R2", DeviceKind.RESISTOR, [("1", "VOUT"), ("2", "FBN")])
    nets = [
        make_net("N_VCC", "VCC", NetType.POWER),
        make_net("N_GND", "GND", NetType.GND),
        make_net("N_INP", "IN+", NetType.SIGNAL),
        make_net("N_FBN", "FBN", NetType.SIGNAL),
        make_net("N_OUT", "VOUT", NetType.SIGNAL),
        make_net("N_SIGIN", "SIGIN", NetType.SIGNAL),
    ]
    wiring = [
        ("U1.5", "N_VCC"), ("U1.2", "N_GND"),
        ("U1.1", "N_INP"), ("U1.3", "N_FBN"), ("U1.4", "N_OUT"),
        ("R1.1", "N_SIGIN"), ("R1.2", "N_FBN"),
        ("R2.1", "N_OUT"), ("R2.2", "N_FBN"),
    ]
    ir = build_ir([opamp, rin, rf], nets, wiring)

    matcher = Vf2TopologyMatcher(ir, ignore_pin_numbers=True)  # 无 provider
    result = matcher.find_matches(
        _TEMPLATES["inverting-amplifier"], timeout_seconds=5.0, max_matches=50
    )
    matches = [m for m in result.matches if "U1" in m.component_mapping.values()]
    assert matches, "没有 provider 时门控必须不生效，行为与改造前一致"


def test_named_power_net_does_not_require_matching_pin_category() -> None:
    """expected_pin_category 是自动命名网络的补充证据，不是命名网络的额外门槛。"""
    resistor = make_component("R1", DeviceKind.RESISTOR, [("1", "VCC"), ("2", "SIG")])
    ic = make_component("U1", DeviceKind.IC, [("1", "OUT")], part_number="SYN")
    nets = [
        make_net("N_VCC", "VCC", NetType.POWER),
        make_net("N_SIG", "SIG", NetType.SIGNAL),
    ]
    ir = build_ir(
        [resistor, ic],
        nets,
        [("R1.1", "N_VCC"), ("R1.2", "N_SIG"), ("U1.1", "N_VCC")],
    )

    def provider(comp):
        return {"1": "output"} if comp.id == "U1" else None

    matcher = Vf2TopologyMatcher(ir, ignore_pin_numbers=True, pin_category_provider=provider)
    template = replace(
        _TEMPLATES["pull-up-resistor"],
        signals=[
            replace(signal, expected_role=None, pin_category_fallback=True)
            for signal in _TEMPLATES["pull-up-resistor"].signals
        ],
    )
    result = matcher.find_matches(template, timeout_seconds=5.0, max_matches=10)
    assert any("R1" in m.component_mapping.values() for m in result.matches), (
        "VCC 网名已命中时必须允许上拉匹配；KB 类别只用于救出自动命名电源轨"
    )


def _pullup_fallback_ir(net_name: str):
    resistor = make_component(
        "R1", DeviceKind.RESISTOR, [("1", "VIN"), ("2", "SIG")], raw_value="1k"
    )
    ic = make_component("U1", DeviceKind.IC, [("2", "VIN")], part_number="SYN")
    nets = [
        make_net("N_POWER", net_name, NetType.UNKNOWN),
        make_net("N_SIG", "SIG", NetType.SIGNAL),
    ]
    return build_ir(
        [resistor, ic],
        nets,
        [("R1.1", "N_POWER"), ("R1.2", "N_SIG"), ("U1.2", "N_POWER")],
        edge_semantics={
            ("R1.1", "N_POWER"): {"role": "digital", "direction": "bidirectional", "level": None},
            ("R1.2", "N_SIG"): {"role": "digital", "direction": "bidirectional", "level": None},
        },
    )


def _power_in_provider(comp):
    return {"2": "power_in"} if comp.id == "U1" else None


def test_pin_category_fallback_requires_automatic_net_name() -> None:
    ir = _pullup_fallback_ir("CUSTOM")
    matcher = Vf2TopologyMatcher(
        ir, ignore_pin_numbers=True, pin_category_provider=_power_in_provider
    )
    result = matcher.find_matches(
        _TEMPLATES["pull-up-resistor"], timeout_seconds=5.0, max_matches=10
    )
    assert result.matches == [], "命名 CUSTOM 不能借 KB 类别绕过 expected_role=power"


def test_pin_category_fallback_is_consistent_in_join_and_vf2() -> None:
    ir = _pullup_fallback_ir("NetU1_1")
    for ignore_pin_numbers in (True, False):
        matcher = Vf2TopologyMatcher(
            ir,
            ignore_pin_numbers=ignore_pin_numbers,
            pin_category_provider=_power_in_provider,
        )
        result = matcher.find_matches(
            _TEMPLATES["pull-up-resistor"], timeout_seconds=5.0, max_matches=10
        )
        assert any("R1" in match.component_mapping.values() for match in result.matches), (
            f"自动命名电源岛在 ignore_pin_numbers={ignore_pin_numbers} 路径应一致命中"
        )
