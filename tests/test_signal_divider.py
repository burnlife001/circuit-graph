"""信号分压器（信号源 → 分压 → IC 采样）的识别与防误判。

背景：R50(100k, NetC50_1↔VDB) 与 R45(10k, VDB→GND) 构成信号分压，
VDB 接 MCU ADC(U2.18)。老的 ``voltage-divider`` 模板要求 vin 必须是电源轨
（name_pattern + expected_role=power），NetC50_1 是检波保持节点 → 不命中，
R45 只被报成下拉电阻。

修法：新增 ``voltage-divider-signal`` 模板，用两个新约束把"普通串联电阻"
挡在外面：
- vin（信号源侧）不得接任何 IC 引脚 —— 运放反馈网络（R100→R97→GND）的
  输入侧接的是 IC 输出，不满足；
- vout（中点）必须接 IC 引脚 —— 没有 IC 负载的普通串联电阻不满足。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from circuit_graph.cli.app import _parse_and_enrich
from circuit_graph.cli.role import _kb_pinout_provider
from circuit_graph.ir import DeviceKind, NetType
from circuit_graph.primitives import load_primitives
from circuit_graph.role import infer_role
from circuit_graph.vf2_matcher import Vf2TopologyMatcher
from tests.fir_helpers import build_ir, make_component, make_net

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "protel" / "FX-T268-V6-10米-5065.NET"
)
KB_PATH = (
    Path(__file__).resolve().parent.parent / "circuit_graph" / "knowledge" / "circuit_graph.db"
)


@pytest.fixture(scope="module")
def ir():
    return _parse_and_enrich(FIXTURE, use_cache=False)


@pytest.fixture(scope="module")
def provider():
    return _kb_pinout_provider(KB_PATH)


@pytest.fixture(scope="module")
def template():
    return next(t for t in load_primitives() if t.id == "voltage-divider-signal")


# ---------------------------------------------------------------------------
# 模板级：正例 + 两个防误判反例（纯合成 IR，不依赖网表）
# ---------------------------------------------------------------------------


def _signal_divider_ir(*, ic_on_mid: bool, ic_on_source: bool):
    """信号源 → 上电阻 → 中点 → 下电阻 → 地，可选在中点/信号源上挂 IC。"""
    components = [
        make_component(
            "R_TOP", DeviceKind.RESISTOR, [("1", "SIG"), ("2", "MID")], raw_value="100K"
        ),
        make_component(
            "R_BOT", DeviceKind.RESISTOR, [("1", "MID"), ("2", "GND")], raw_value="10K"
        ),
    ]
    nets = [
        make_net("N_SIG", "SIG", NetType.SIGNAL),
        make_net("N_MID", "MID", NetType.SIGNAL),
        make_net("N_GND", "GND", NetType.GND),
    ]
    wiring = [
        ("R_TOP.1", "N_SIG"),
        ("R_TOP.2", "N_MID"),
        ("R_BOT.1", "N_MID"),
        ("R_BOT.2", "N_GND"),
    ]
    edge_semantics = {
        ("R_TOP.1", "N_SIG"): {"role": "digital", "direction": "bidirectional", "level": None},
        ("R_TOP.2", "N_MID"): {"role": "digital", "direction": "bidirectional", "level": None},
        ("R_BOT.1", "N_MID"): {"role": "digital", "direction": "bidirectional", "level": None},
        ("R_BOT.2", "N_GND"): {"role": "gnd", "direction": "bidirectional", "level": None},
    }
    if ic_on_mid:
        components.append(make_component("U1", DeviceKind.IC, [("1", "ADC_IN")]))
        nets.append(make_net("N_ADC", "ADC_IN", NetType.SIGNAL))
        wiring.append(("U1.1", "N_MID"))
        edge_semantics[("U1.1", "N_MID")] = {
            "role": "digital", "direction": "bidirectional", "level": None,
        }
    if ic_on_source:
        components.append(make_component("U2", DeviceKind.IC, [("1", "OUT")]))
        nets.append(make_net("N_OUT", "OUT", NetType.SIGNAL))
        wiring.append(("U2.1", "N_SIG"))
        edge_semantics[("U2.1", "N_SIG")] = {
            "role": "digital", "direction": "source", "level": None,
        }
    return build_ir(components, nets, wiring, edge_semantics=edge_semantics)


@pytest.mark.parametrize("ignore_pin_numbers", [True, False])
def test_signal_divider_matches_source_to_ic_input(template, ignore_pin_numbers) -> None:
    """信号源 → 上电阻 → IC 采样点 → 下电阻 → 地：命中，且上下臂角色正确。"""
    ir = _signal_divider_ir(ic_on_mid=True, ic_on_source=False)
    matcher = Vf2TopologyMatcher(ir, ignore_pin_numbers=ignore_pin_numbers)
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.component_mapping == {"R_top": "R_TOP", "R_bottom": "R_BOT"}
    assert match.signal_mapping == {"vin": "N_SIG", "vout": "N_MID", "gnd": "N_GND"}


@pytest.mark.parametrize("ignore_pin_numbers", [True, False])
def test_signal_divider_rejects_series_resistors_without_ic_load(
    template, ignore_pin_numbers
) -> None:
    """中点没有 IC 负载的普通串联电阻对：不得判成信号分压器。"""
    ir = _signal_divider_ir(ic_on_mid=False, ic_on_source=False)
    matcher = Vf2TopologyMatcher(ir, ignore_pin_numbers=ignore_pin_numbers)
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert result.matches == []


@pytest.mark.parametrize("ignore_pin_numbers", [True, False])
def test_signal_divider_rejects_opamp_feedback_network(
    template, ignore_pin_numbers
) -> None:
    """运放反馈网络（信号源侧也接 IC）：不是分压器，不得命中。"""
    ir = _signal_divider_ir(ic_on_mid=True, ic_on_source=True)
    matcher = Vf2TopologyMatcher(ir, ignore_pin_numbers=ignore_pin_numbers)
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert result.matches == []


# ---------------------------------------------------------------------------
# 真实网表：FX-T268-V6-10米-5065.NET
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r45_is_signal_divider_bottom_arm(ir, provider) -> None:
    """R45 是 R50/R45 信号分压的下臂，中点 VDB 送 MCU ADC(U2.18)。

    2026-11-21 行为变更（用户确认）：R50/R45/C41 是检波网络（D3 组）的
    VDB 分压支路，detector-amp-sampling 模板新增可选 vdb-tap 成员后，
    R45 归入检波块担任 R_div_bottom——仍是分压下臂、仍送 VDB 给 ADC，
    只是功能语境从独立分压块升级为完整检波网络。voltage-divider-signal
    模板本身仍命中（见下一个测试），infer_role 优先报包含它的具体块。
    """
    r = infer_role(ir, "R45", provider)
    assert r is not None
    assert "分压" in r.title
    assert "R_div_bottom" in r.title
    assert "下拉" not in r.title
    assert "detector-amp-sampling" in r.rule
    # 中点网络必须落在结论里（vout=VDB）
    assert any("VDB" in line for line in r.evidence)


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_signal_divider_block_covers_r45_and_r50(ir) -> None:
    """R45/R50 作为一个分压块被识别出来（上下臂都在块里）。"""
    blocks = {b.id: b for b in ir.blocks if b.id.startswith("voltage-divider-signal-")}
    assert "voltage-divider-signal-r45" in blocks
    assert set(blocks["voltage-divider-signal-r45"].component_ids) == {"R45", "R50"}


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r65_still_reported_by_power_rail_divider(ir, provider) -> None:
    """回归：5V2 → NTC → GND 的分压仍由电源轨版模板命中。"""
    r = infer_role(ir, "R65", provider)
    assert r is not None
    assert "分压" in r.title
    assert "R_bottom" in r.title
    assert "voltage-divider-signal" not in r.rule
