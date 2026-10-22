"""role 引擎单元测试 —— 用真实板卡拓扑 + mock pinout provider。"""

from pathlib import Path

import pytest

from circuit_graph.cli.app import _parse_and_enrich
from circuit_graph.role import infer_role

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "protel" / "FX-T268-V6-10米-5065.NET"
)


def _provider(comp):
    """只给 U7 提供 LM321 pinout（其余 IC 模拟 KB 缺失）。"""
    if comp.id == "U7":
        return {"1": "+", "2": "GND", "3": "-", "4": "OUT", "5": "V+"}
    return None


@pytest.fixture(scope="module")
def ir():
    return _parse_and_enrich(FIXTURE)


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r100_is_feedback_resistor(ir) -> None:
    r = infer_role(ir, "R100", _provider)
    assert r is not None
    assert r.rule == "opamp_feedback"
    assert "反馈电阻" in r.title
    assert "U7" in r.title and "LM321" in r.title
    assert r.data.get("gain") == pytest.approx(101.0)
    assert r.data.get("rg") == "R97"


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r97_is_gain_leg_rg(ir) -> None:
    """Rg 判定：输出引脚不在 R97 自己的网络上，要靠同网络反馈电阻找回。"""
    r = infer_role(ir, "R97", _provider)
    assert r is not None
    assert r.rule == "opamp_feedback_rg"
    assert r.data.get("gain") == pytest.approx(101.0)
    assert r.data.get("rf") == "R100"


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r101_is_rc_filter(ir) -> None:
    r = infer_role(ir, "R101", _provider)
    assert r is not None
    assert r.rule == "rc_filter"
    # 3kΩ × 10nF → fc ≈ 5.3 kHz
    assert r.data.get("fc_hz") == pytest.approx(5305.0, rel=0.01)


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r2_is_current_shunt(ir) -> None:
    r = infer_role(ir, "R2", _provider)
    assert r is not None
    assert r.rule == "shunt"
    assert "电流采样" in r.title


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r24_is_pulldown_without_kb_pinout(ir) -> None:
    """U2 无 KB pinout：靠拓扑 + 未知类别放宽仍可定性下拉。"""
    r = infer_role(ir, "R24", _provider)
    assert r is not None
    assert r.rule == "pull_down"
    assert "BOOT" in r.title


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_c66_is_decoupling(ir) -> None:
    r = infer_role(ir, "C66", _provider)
    assert r is not None
    assert r.rule == "decoupling"
    assert "USB_5V" in r.title


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_c82_is_filter_cap_on_opamp_input(ir) -> None:
    r = infer_role(ir, "C82", _provider)
    assert r is not None
    assert r.rule == "filter_cap"
    assert "U7.1" in r.title


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_unknown_designator_returns_none(ir) -> None:
    assert infer_role(ir, "ZZZ999", _provider) is None
