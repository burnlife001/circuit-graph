"""回归测试：三个元件识别修正（R102 / R65 / Q7）。

断言的是**定性结论**（标题里出现/不出现什么），而不是内部 rule id ——
命中路径从手写规则换成 primitives 模板后 rule id 会变，但结论必须稳定。

- R102：C82 旁边构成的 RC 低通网络里的串联电阻（不是下拉电阻）
- R65：5V2 → NTC → GND 分压网络的下臂（不是下拉电阻）
- Q7：SW5001 全桥（full-bridge）15W 无线充发射端功率级
"""

from pathlib import Path

import pytest

from circuit_graph.cli.app import _parse_and_enrich
from circuit_graph.cli.role import _kb_pinout_provider
from circuit_graph.role import infer_role

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


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r102_is_rc_low_pass_series_resistor(ir, provider) -> None:
    """R102 与 C82 构成 RC 滤波网络：R102 是串联电阻，不能再报下拉电阻。"""
    r = infer_role(ir, "R102", provider)
    assert r is not None
    assert "RC 低通滤波" in r.title
    assert "R_series" in r.title
    assert "下拉" not in r.title


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_c82_is_rc_low_pass_shunt_cap(ir, provider) -> None:
    """同一个 RC 滤波网络里的旁路电容 —— 与 R102 的结论必须成对。"""
    c = infer_role(ir, "C82", provider)
    assert c is not None
    assert "RC 低通滤波" in c.title
    assert "C_shunt" in c.title


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r65_is_voltage_divider_lower_arm(ir, provider) -> None:
    """R65 是 5V2 → NTC → GND 分压网络的下臂，不能再报下拉电阻。"""
    r = infer_role(ir, "R65", provider)
    assert r is not None
    assert "分压" in r.title
    assert "R_bottom" in r.title
    assert "下拉" not in r.title


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r61_is_voltage_divider_upper_arm(ir, provider) -> None:
    """R61 是同一分压网络的上臂（5V2 → NTC），结论与 R65 成对。"""
    r = infer_role(ir, "R61", provider)
    assert r is not None
    assert "分压" in r.title
    assert "R_top" in r.title


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_q7_is_sw5001_full_bridge_power_stage(ir, provider) -> None:
    """Q7 = SW5001：全桥（full-bridge）功率级，不能只报"MOSFET SW5001"。"""
    r = infer_role(ir, "Q7", provider)
    assert r is not None
    assert "SW5001" in r.title
    assert "全桥" in r.title
    assert "full-bridge" in r.summary
    # 未定性提示不应再出现
    assert "未命中自动定性规则" not in r.summary
