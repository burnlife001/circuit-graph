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
    assert r.rule.startswith("primitive:opamp-noninv-feedback-")
    # 功能定性来自模板（标题不再被手写规则改写），实例上下文在 evidence 里。
    assert r.title.startswith("运放反馈电阻")
    blob = f"{r.title} {r.summary} {' '.join(r.evidence)}"
    assert "反馈电阻" in blob
    assert "U7" in blob and "LM321" in blob
    assert r.data.get("gain") == pytest.approx(101.0)
    assert r.data.get("rg") == "R97"


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r97_is_gain_leg_rg(ir) -> None:
    """Rg 判定：输出引脚不在 R97 自己的网络上，要靠同网络反馈电阻找回。"""
    r = infer_role(ir, "R97", _provider)
    assert r is not None
    assert r.rule.startswith("primitive:opamp-noninv-feedback-")
    assert r.data.get("gain") == pytest.approx(101.0)
    assert r.data.get("rf") == "R100"


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r101_is_rc_filter(ir) -> None:
    r = infer_role(ir, "R101", _provider)
    assert r is not None
    assert r.rule == "primitive-live:rc-low-pass"
    # 3kΩ × 10nF → fc ≈ 5.3 kHz
    assert r.data.get("fc_hz") == pytest.approx(5305.0, rel=0.01)


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r2_is_current_shunt(ir) -> None:
    r = infer_role(ir, "R2", _provider)
    assert r is not None
    assert r.rule == "shunt"
    assert "电流采样" in r.title
    assert "低边" in r.title
    assert r.confidence == "high"


def test_shunt_ground_bridge_without_tap_is_honest_medium(tmp_path) -> None:
    """双端地族且无采样触碰：必须报"地桥"并降置信度，不许断言采样。"""
    netlist = "\n".join(
        [
            "[",
            "R1",
            "1206W",
            "0.05R%1",
            "",
            "",
            "",
            "]",
            "[",
            "U1",
            "SOT23-5",
            "LM321",
            "",
            "",
            "",
            "]",
            "[",
            "C1",
            "0402C",
            "100N",
            "",
            "",
            "",
            "]",
            "(",
            "GND",
            "R1-1",
            "U1-2",
            ")",
            "(",
            "CGND",
            "R1-2",
            "C1-1",
            ")",
        ]
    )
    p = tmp_path / "mini.NET"
    p.write_text(netlist, encoding="utf-8")
    mini_ir = _parse_and_enrich(p, use_cache=False)
    r = infer_role(mini_ir, "R1", _provider)
    assert r is not None
    assert r.rule.startswith("primitive:current-shunt-monitor-")
    assert r.title.startswith("毫欧地桥电阻")
    assert "跨接" in f"{r.title} {r.summary} {' '.join(r.evidence)}"
    assert r.confidence == "medium"
    assert r.data["sense_tap"] is None


def test_bare_zero_ohm_is_not_current_shunt(tmp_path) -> None:
    """裸 0Ω 不能绕过模板负例，经直接 shunt 规则变成采样电阻。"""
    netlist = "\n".join([
        "[", "R1", "1206W", "0R", "", "", "", "]",
        "(", "DCIN", "R1-1", ")",
        "(", "DCIN_1", "R1-2", ")",
    ])
    path = tmp_path / "zero.NET"
    path.write_text(netlist, encoding="utf-8")
    zero_ir = _parse_and_enrich(path, use_cache=False)
    result = infer_role(zero_ir, "R1", _provider)
    assert result is not None
    assert result.rule != "shunt"
    assert not result.rule.startswith(
        ("primitive:current-shunt-monitor-", "primitive-live:current-shunt-monitor")
    )


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_r24_is_pulldown_without_kb_pinout(ir) -> None:
    """U2 无 KB pinout：靠拓扑 + 未知类别放宽仍可定性下拉。"""
    r = infer_role(ir, "R24", _provider)
    assert r is not None
    assert r.rule == "primitive-live:pull-down-resistor"
    assert r.title.startswith("下拉电阻")
    assert "BOOT" in f"{r.title} {r.summary} {' '.join(r.evidence)}"


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_c66_is_decoupling(ir) -> None:
    r = infer_role(ir, "C66", _provider)
    assert r is not None
    # 预识别块被 flood 降级时，同一模板可经 live match 命中。
    assert r.rule.startswith(("primitive:dc-decoupling-cap-", "primitive-live:dc-decoupling-cap"))
    assert r.title.startswith("DC 电源去耦电容")
    assert "USB_5V" in f"{r.title} {r.summary} {' '.join(r.evidence)}"


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_c82_is_filter_cap_on_opamp_input(ir) -> None:
    r = infer_role(ir, "C82", _provider)
    assert r is not None
    assert r.rule == "primitive-live:rc-low-pass"
    assert r.title.startswith("RC 低通滤波器")
    assert "U7.1" in f"{r.title} {r.summary} {' '.join(r.evidence)}"


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_unknown_designator_returns_none(ir) -> None:
    assert infer_role(ir, "ZZZ999", _provider) is None
