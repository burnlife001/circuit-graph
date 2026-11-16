"""cg role 的"答案契约"回归测试。

两个契约（都源于 FX-T268 基准对齐暴露出的根因）：

1. **功能名以模板为准**：模板命中后，标题必须是模板给出的功能定性
   （``DC 电源去耦电容 · C_dec``），不能被旧手写规则整体改写成
   ``去耦电容（DCIN ↔ GND）``。实例细节（网络/配对位号/型号/增益）
   仍然必须出现在 summary / evidence 里——只是不再占用标题。
2. **角色与基准一致**：基准 ``*.answers.jsonl`` 里 ``block`` 指向某个
   模板的元件，``cg role`` 必须报出同一个模板（block 判据）。

另附：新补的通用角色规则（测试点 / LED / CC 开关 / SWD / USB / 触摸 IC /
0R 跳线 / 触摸电极串阻 / 信号串联电阻）的行为守护。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from circuit_graph.cli.app import _parse_and_enrich
from circuit_graph.primitives import load_primitives
from circuit_graph.role import infer_role

REPO = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO / "fixtures" / "protel"
NET = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
ANSWERS = FIXTURE_DIR / "FX-T268-V6-10米-5065.answers.jsonl"

_RULE_RE = re.compile(r"^(?:primitive|primitive-live):(.+)$")


@pytest.fixture(scope="module")
def ir():
    return _parse_and_enrich(NET)


@pytest.fixture(scope="module")
def template_names() -> dict[str, str]:
    return {t.id: t.name for t in load_primitives()}


@pytest.fixture(scope="module")
def baseline() -> dict[str, dict]:
    if not ANSWERS.is_file():
        pytest.skip(f"基准文件不存在: {ANSWERS}")
    rows = {}
    for line in ANSWERS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["designator"]] = row
    return rows


def _template_of(rule: str, template_ids: set[str]) -> str | None:
    m = _RULE_RE.match(rule or "")
    if not m:
        return None
    instance = m.group(1)
    best = None
    for tid in template_ids:
        if not instance.startswith(tid):
            continue
        rest = instance[len(tid):]
        if rest and not rest.startswith("-"):
            continue
        if best is None or len(tid) > len(best):
            best = tid
    return best


def _provider(comp):
    """只给 U7 提供 LM321 pinout（其余 IC 模拟 KB 缺失）。"""
    if comp.id == "U7":
        return {"1": "+", "2": "GND", "3": "-", "4": "OUT", "5": "V+"}
    return None


# ---------------------------------------------------------------------------
# 契约 1：模板命中 → 标题是模板的功能名
# ---------------------------------------------------------------------------

#: 这些元件模板命中后，旧实现会把标题整体换成手写规则的措辞。
TITLE_CONTRACT_CASES = [
    ("C1", "dc-decoupling-cap"),
    ("R31", "pull-down-resistor"),
    ("R100", "opamp-noninv-feedback"),
    ("R102", "rc-low-pass"),
    ("R96", "current-shunt-monitor"),
    ("C39", "dc-decoupling-cap"),
]


@pytest.mark.skipif(not NET.exists(), reason=f"Fixture not found: {NET}")
@pytest.mark.parametrize("designator,template_id", TITLE_CONTRACT_CASES)
def test_title_is_the_template_function_name(ir, template_names, designator, template_id):
    """模板命中的元件：标题 = 模板功能名（可带 " · 角色" 后缀）。"""
    r = infer_role(ir, designator, _provider)
    assert r is not None, designator
    assert r.rule.startswith(f"primitive:{template_id}") or r.rule.startswith(
        f"primitive-live:{template_id}"
    ), f"{designator} 的 rule 应指向模板 {template_id}，实际 {r.rule}"
    head = re.sub(r"\s*·\s*\S+$", "", r.title).strip()
    role_label = r.title.rsplit(" · ", 1)[-1] if " · " in r.title else None
    template = next(t for t in load_primitives() if t.id == template_id)
    member = next((c for c in template.components if c.id == role_label), None)
    expected_head = (
        member.title if member is not None and member.title else template_names[template_id]
    )
    assert head == expected_head, (
        f"{designator} 标题的功能名应为模板成员名 {expected_head!r}，"
        f"实际 {r.title!r}"
    )


@pytest.mark.skipif(not NET.exists(), reason=f"Fixture not found: {NET}")
def test_instance_evidence_survives_the_title_change(ir):
    """改标题不丢证据：网络 / IC 型号 / 配对位号仍在 summary 与 evidence 里。"""
    r = infer_role(ir, "R100", _provider)
    assert r is not None
    blob = f"{r.title} {r.summary} {' '.join(r.evidence)}"
    assert "反馈电阻" in blob
    assert "U7" in blob and "LM321" in blob
    assert r.data.get("gain") == pytest.approx(101.0)
    assert r.data.get("rg") == "R97"

    c = infer_role(ir, "C66", _provider)
    assert c is not None
    assert "USB_5V" in f"{c.title} {c.summary} {' '.join(c.evidence)}"


# ---------------------------------------------------------------------------
# 契约 2：cg 报出的模板必须与基准 block 一致
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not NET.exists(), reason=f"Fixture not found: {NET}")
def test_role_template_matches_baseline_block(ir, baseline, template_names):
    """基准 block 指向模板的元件，cg 必须报同一个模板。

    基准里 ``block`` 为 ``(unmatched)`` 的行表示"该答案没有对应模板"，
    这一类由 cg 的规则层给出答案（block 天然为空），不在本契约内。
    所有模板归属都必须对齐，不允许例外清单。
    """
    template_ids = set(template_names)
    checked = 0
    problems: list[str] = []
    for designator, row in sorted(baseline.items()):
        block = str(row.get("block") or "")
        if not block or block == "(unmatched)" or block not in template_ids:
            continue
        r = infer_role(ir, designator, _provider)
        if r is None:
            problems.append(f"{designator}: cg role 无结果")
            continue
        checked += 1
        got = _template_of(r.rule, template_ids)
        if got != block:
            problems.append(
                f"{designator}: 基准 block={block}，cg 报 {got or r.rule}（{r.title}）"
            )
    assert checked >= 60, f"样本太少，基准 block 覆盖的元件只有 {checked} 个"
    assert not problems, "以下元件的模板归属与基准不一致：\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 新补的通用角色规则
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not NET.exists(), reason=f"Fixture not found: {NET}")
@pytest.mark.parametrize(
    "designator,expected_rule,expected_title",
    [
        ("A1", "test_point", "测试点"),
        ("T5", "test_point", "测试点"),
        ("T11", "test_point", "接地测试点"),
        ("T3", "test_point", "串口测试点"),
        ("T10", "test_point", "调试串口测试点"),
        ("LED1", "led_indicator", "LED 指示灯"),
        ("LED2", "led_indicator", "LED 指示灯"),
        ("R46", "led_series_resistor", "LED 限流电阻"),
        ("R47", "led_series_resistor", "LED 限流电阻"),
        ("Q1", "cc_series_switch", "CC 线串接开关"),
        ("Q4", "cc_series_switch", "CC 线串接开关"),
        ("USB1", "usb_type_c", "USB Type-C 输入接口"),
        ("USB2", "usb_power_port", "USB 供电口（只出 5V/GND）"),
        ("J1", "swd_debug_connector", "SWD 调试下载接口"),
        ("U3", "touch_key_ic", "电容式触摸按键 IC"),
        ("U1", "ic_function", "LDO 稳压器"),
        ("L3", "zero_ohm_jumper", "0R 跳线网络"),
        ("R74", "touch_electrode_resistor", "触摸电极串阻"),
        ("R64", "series_signal", "信号串联电阻"),
    ],
)
def test_new_role_rules(ir, designator, expected_rule, expected_title):
    r = infer_role(ir, designator, _provider)
    assert r is not None, designator
    assert r.rule == expected_rule, f"{designator}: rule={r.rule}"
    assert r.title == expected_title, f"{designator}: title={r.title}"


@pytest.mark.skipif(not NET.exists(), reason=f"Fixture not found: {NET}")
def test_test_point_is_not_claimed_by_generic_rules(ir):
    """探针位不能被"串联电阻/滤波电容"这类通用规则抢走。"""
    for designator in ("A1", "A3", "T3", "T5", "T11"):
        r = infer_role(ir, designator, _provider)
        assert r is not None
        assert r.rule == "test_point", f"{designator} → {r.rule}"


# ---------------------------------------------------------------------------
# 模板侧的修正（回归守护）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not NET.exists(), reason=f"Fixture not found: {NET}")
@pytest.mark.parametrize("designator", ["C20", "C22", "C24", "C26"])
def test_dc_decoupling_cap_accepts_suffixed_rail_names(ir, designator):
    """Protel 会把重名的电源轨改名成 DCIN_1；去耦模板必须认。"""
    r = infer_role(ir, designator, _provider)
    assert r is not None
    assert r.rule.startswith("primitive:dc-decoupling-cap-"), f"{designator} → {r.rule}"


@pytest.mark.skipif(not NET.exists(), reason=f"Fixture not found: {NET}")
def test_supply_island_decoupling_excludes_named_signal_filters(ir):
    """自动命名供电岛可去耦；NTC 等命名信号上的 RC 滤波不能被抢走。"""
    c15 = infer_role(ir, "C15", _provider)
    assert c15 is not None
    assert c15.rule.startswith(("primitive:dc-decoupling-cap-", "primitive-live:dc-decoupling-cap"))

    c64 = infer_role(ir, "C64", _provider)
    assert c64 is not None
    assert not c64.rule.startswith(
        ("primitive:dc-decoupling-cap-", "primitive-live:dc-decoupling-cap")
    )

    for designator in ("C60", "C61"):
        result = infer_role(ir, designator, _provider)
        assert result is not None
        assert not result.rule.startswith(
            ("primitive:dc-decoupling-cap-", "primitive-live:dc-decoupling-cap")
        ), f"{designator} 的 VIN 引脚名已知，不应套用无引脚名供电岛推断"


@pytest.mark.skipif(not NET.exists(), reason=f"Fixture not found: {NET}")
@pytest.mark.parametrize("designator", ["C5", "C62", "C72", "C73"])
def test_resonant_cap_matches_parallel_cap_bank(ir, designator):
    """谐振电容组（NetC5_1 ↔ VBAK）必须命中谐振/换流电容模板。"""
    r = infer_role(ir, designator, _provider)
    assert r is not None
    assert r.rule.startswith("primitive:resonant-commutation-cap-"), f"{designator} → {r.rule}"


@pytest.mark.skipif(not NET.exists(), reason=f"Fixture not found: {NET}")
@pytest.mark.parametrize("designator", ["C27", "C28", "C51", "C52"])
def test_bootstrap_cap_still_wins_over_resonant_cap(ir, designator):
    """自举电容（BST ↔ SW）不能被谐振电容模板抢走。"""
    r = infer_role(ir, designator, _provider)
    assert r is not None
    assert r.rule.startswith("primitive:bootstrap-cap-"), f"{designator} → {r.rule}"


@pytest.mark.skipif(not NET.exists(), reason=f"Fixture not found: {NET}")
def test_transistor_inside_function_block_reports_the_block(ir):
    """Q6 已经是 LED 驱动块的一部分：块的身份比"通用三极管角色"更准。"""
    r = infer_role(ir, "Q6", _provider)
    assert r is not None
    assert r.rule.startswith("primitive:led-driver-switch-"), f"Q6 → {r.rule}"
    assert r.title.startswith("LED 驱动低边开关")


@pytest.mark.skipif(not NET.exists(), reason=f"Fixture not found: {NET}")
def test_feedback_divider_upper_arm_is_not_reported_as_pull_up(ir):
    """FB 分压上臂（USB_5V → U4.8 FB，R76 到地）不能被 pull-up 模板抢走。"""
    r = infer_role(ir, "R67", _provider)
    assert r is not None
    assert r.rule == "feedback_divider_arm", f"R67 → {r.rule}"
    assert "反馈分压" in r.title
    assert "R76" in r.summary
    assert any("FB" in e for e in r.evidence)
