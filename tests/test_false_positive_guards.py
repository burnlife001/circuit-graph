"""模板防误报守卫（docs/template-system-next-plan.md P3）。

两起真实误报/漏报的回归：

- **Q5 也是 SW5001 却未命中全桥模板**：Q5 与 Q7 同为 SW5001，但 Q5 的
  PGND(6/14) 与 GND(22) 同在一个网络 "GND" 上；模板信号→网络的绑定是
  单射（``vf2_matcher.assign_nets`` 的 injective 约束），``pgnd``/``gnd``
  两个信号无法共用一网 → Q5 回溯失败。修复后的模板不再要求 pin 22 独立
  信号（datasheet 上 6/14/22 全是地，识别依据是 strict_value "SW5001"
  + VIN/SW/PWM/BST/VDD 物理引脚）。这里守非 SW5001 不得靠引脚拓扑蒙混。

- **U2（MM32F014X-QFP48 MCU）被 opamp-unit-voltage-follower 误命中**：
  KB 无该型号 pinout → 引脚类别门控不生效；live-match 路径忽略引脚号，
  模板又只有 in/out 两个宽松信号（``AI[NP]?`` 恰好匹配 MCU 的 AIN10），
  48 脚 MCU 被凑成"电压跟随器"且 confidence=high。修复：运放单元组件加
  ``pin_count_max``（信号脚 3 + 电源 2 + 余量 = 8；MCU 动辄 20+ 脚直接
  在候选阶段排除）。该约束不依赖 KB pinout，符合"KB 无 pinout 保持降级、
  不为覆盖率瞎猜"的纪律。

正例对照（真运放单元必须仍然命中）同在本文，防止约束收紧误杀。
"""

from __future__ import annotations

from pathlib import Path

from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.enrichers.net_classifier import NetClassifier
from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
from circuit_graph.ir import DeviceKind, NetType
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from circuit_graph.primitives import load_primitives
from circuit_graph.vf2_matcher import Vf2TopologyMatcher
from tests.fir_helpers import build_ir, make_component, make_net

_TEMPLATES = {t.id: t for t in load_primitives()}


def _matches(template_id: str, ir, *, ignore_pin_numbers: bool = True) -> list:
    matcher = Vf2TopologyMatcher(ir, ignore_pin_numbers=ignore_pin_numbers)
    result = matcher.find_matches(_TEMPLATES[template_id], timeout_seconds=5.0)
    return result.matches


# --------------------------------------------------------------------------- P3.1


def _sw5001_like_ir(value: str):
    """SW5001 引脚拓扑（Q7 接法：PGND 与 GND 分网），型号可换。"""
    pins = [
        ("4", "VIN"), ("16", "VIN"), ("5", "SW1"), ("15", "SW2"),
        ("6", "PGND"), ("14", "PGND"), ("22", "GND"),
        ("23", "VDD"), ("24", "VDD"),
        ("7", "BST1"), ("13", "BST2"), ("8", "PWM1"), ("9", "PWM2"),
    ]
    comp = make_component("Q1", DeviceKind.MOSFET, pins, raw_value=value)
    nets = [
        make_net("N_VIN", "DCIN", NetType.POWER),
        make_net("N_SW1", "SW1", NetType.SIGNAL),
        make_net("N_SW2", "SW2", NetType.SIGNAL),
        make_net("N_PGND", "CGND", NetType.GND),
        make_net("N_GND", "GND", NetType.GND),
        make_net("N_VDD", "5V2", NetType.POWER),
        make_net("N_BST1", "NetC51_1", NetType.SIGNAL),
        make_net("N_BST2", "NetC52_1", NetType.SIGNAL),
        make_net("N_PWM1", "T17PWM", NetType.SIGNAL),
        make_net("N_PWM2", "T17PWMN", NetType.SIGNAL),
    ]
    pin_nets = {
        "4": "N_VIN", "16": "N_VIN", "5": "N_SW1", "15": "N_SW2",
        "6": "N_PGND", "14": "N_PGND", "22": "N_GND",
        "23": "N_VDD", "24": "N_VDD",
        "7": "N_BST1", "13": "N_BST2", "8": "N_PWM1", "9": "N_PWM2",
    }
    wiring = [(f"Q1.{num}", net_id) for num, net_id in pin_nets.items()]
    return build_ir([comp], nets, wiring)


def test_sw5001_full_bridge_rejects_non_sw5001_value() -> None:
    """非 SW5001 即使引脚拓扑雷同也不得命中（strict_value 守卫）。"""
    ir = _sw5001_like_ir("SW5002")
    assert _matches("sw5001-full-bridge", ir, ignore_pin_numbers=False) == []


# --------------------------------------------------------------------------- P3.2


def _mcu_48pin_ir():
    """仿 U2：48 脚 MCU，AIN10 与 NetC15_1 都落在芯片引脚上，无 KB provider。"""
    pins = [(str(n), f"P{n}") for n in range(1, 49)]
    comp = make_component(
        "U2", DeviceKind.IC, pins, part_number="MM32F014X-QFP48",
        raw_value="MM32F014X-QFP48",
    )
    nets = [
        make_net("N_AIN10", "AIN10", NetType.SIGNAL),
        make_net("N_NC15", "NetC15_1", NetType.SIGNAL),
        make_net("N_GND", "GND", NetType.GND),
        make_net("N_5V2", "5V2", NetType.POWER),
    ]
    wiring = [("U2.19", "N_AIN10"), ("U2.9", "N_NC15")]
    wiring += [(f"U2.{n}", "N_GND") for n in (8, 23, 47)]
    wiring += [(f"U2.{n}", "N_5V2") for n in (24, 48)]
    return build_ir([comp], nets, wiring)


def test_opamp_follower_rejects_48pin_mcu() -> None:
    """MCU 不得当运放：48 脚 MM32 即使凑得出 in/out 两个网络也不行。

    ignore_pin_numbers=True 复刻 role --all 的 live-match 路径 —— 误报就发生
    在这条路径上；修复（pin_count_max）必须在两条路径（join/VF2 候选过滤）
    都生效。
    """
    ir = _mcu_48pin_ir()
    assert _matches("opamp-unit-voltage-follower", ir, ignore_pin_numbers=True) == []


def test_opamp_follower_still_matches_small_unit() -> None:
    """正例对照：真运放单元（3 信号脚）仍命中，pin_count_max 不误杀。"""
    comp = make_component(
        "U1", DeviceKind.IC, [("1", "IN+"), ("2", "IN-"), ("3", "OUT")],
        part_number="LM321", raw_value="LM321",
    )
    nets = [
        make_net("N_IN", "AIN10", NetType.SIGNAL),
        make_net("N_OUT", "NetC15_1", NetType.SIGNAL),
    ]
    wiring = [("U1.1", "N_IN"), ("U1.2", "N_OUT"), ("U1.3", "N_OUT")]
    ir = build_ir([comp], nets, wiring)
    assert _matches("opamp-unit-voltage-follower", ir, ignore_pin_numbers=True) != []


# --------------------------------------------------------------------------- 晶振 vs 低边开关


def _t193_blocks():
    """T193 的功能块（最小 enricher 链，与模板 fixture 测试同一条链）。

    T193 的 Y1/Y2 是 32.768kHz 晶振：第三脚在网表里没接，被解析器按 GND 处理，
    于是"两脚信号 + 一脚地"正好凑成 npn-low-side-switch 要求的
    "集电极→负载 / 发射极→地 / 基极→控制"——晶振被误判成低边开关。
    """
    netlist = Path(__file__).resolve().parent.parent / "fixtures" / "protel" / "T193.NET"
    ir = ProtelNetParser().parse(ParserInput(file_path=str(netlist)))
    return run_enrichers(
        ir,
        [
            MultiUnitSplitter(),
            NetClassifier(),
            EdgeSemanticEnricher(),
            TopologyRecognizer(),
        ],
    ).ir


def test_crystal_does_not_match_npn_low_side_switch() -> None:
    """晶振（Y1/Y2）不得命中 npn-low-side-switch。"""
    ir = _t193_blocks()
    bogus = [b.id for b in ir.blocks if b.id.startswith("npn-low-side-switch-")]
    assert bogus == []
    for block in ir.blocks:
        assert not (set(block.component_ids) & {"Y1", "Y2"})


def test_npn_transistor_still_matched_after_crystal_guard() -> None:
    """正例对照：真 NPN 三极管仍走 bjt-npn-low-side-switch（收紧不误杀）。"""
    ir = _t193_blocks()
    ids = {b.id for b in ir.blocks}
    assert "bjt-npn-low-side-switch-q1" in ids
    assert "bjt-npn-low-side-switch-q2" in ids
