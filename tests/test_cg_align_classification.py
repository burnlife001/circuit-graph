"""cg-align 对齐分类：aligned / refined / mismatched 三分法。

覆盖 .claude/skills/cg-align/scripts/align_common.py 的 classify_alignment：
- aligned：剥角色后缀后文本相同；
- refined：文本不同但基准 block 与当前模板 id 相同（同一模板/功能，
  当前只是补充网络、配对、型号、参数等证据）；
- mismatched：其余全部——block 不同（如 R2 下拉→采样）、任一侧 block
  未知（(unmatched)）或缺失。

refined 只做强结构证据（模板 id 相等）的归类，不以文本包含关系认定
语义相同；同义措辞仍由 agent 复核 refined 清单。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "cg-align" / "scripts"
if not (_SCRIPTS / "align_common.py").is_file():
    pytest.skip("cg-align skill is not installed", allow_module_level=True)
sys.path.insert(0, str(_SCRIPTS))

from align_common import (  # noqa: E402
    classify_alignment,
    resolve_block_hint,
    unmatched_strip_note_required,
)


def test_unmatched_strip_requires_note_when_clearing_known_block() -> None:
    """把已有模板归属改成 (unmatched) 必须给 source_note 依据（防静默清 block）。"""
    assert unmatched_strip_note_required("pull-down-resistor", "", {"pull-down-resistor"}) is True
    assert unmatched_strip_note_required("pull-down-resistor", "R2 走 shunt 规则，无模板", {"pull-down-resistor"}) is False


def test_unmatched_strip_no_note_needed_for_unmatched_or_empty_old() -> None:
    assert unmatched_strip_note_required("(unmatched)", "", {"pull-down-resistor"}) is False
    assert unmatched_strip_note_required("", "", {"pull-down-resistor"}) is False


def test_unmatched_strip_no_note_needed_for_unknown_old_block() -> None:
    """旧 block 是已不存在的模板 id（模板删除后的清理）不要求依据。"""
    assert unmatched_strip_note_required("old-deleted-template", "", {"pull-down-resistor"}) is False


def test_block_hint_accepts_known_template_id() -> None:
    assert resolve_block_hint("rc-low-pass", {"rc-low-pass", "pull-up-resistor"}) == "rc-low-pass"


def test_block_hint_accepts_explicit_unmatched() -> None:
    """"(unmatched)" 是显式合法 hint：确认该答案无对应 primitives 模板（如 R2 走 shunt 规则）。"""
    assert resolve_block_hint("(unmatched)", {"rc-low-pass"}) == "(unmatched)"


def test_block_hint_rejects_unknown_template_id() -> None:
    assert resolve_block_hint("not-a-template", {"rc-low-pass"}) is None


def test_block_hint_rejects_empty() -> None:
    assert resolve_block_hint("", {"rc-low-pass"}) is None
    assert resolve_block_hint(None, {"rc-low-pass"}) is None


# --------------------------------------------------------------- confidence 继承

from nl_to_jsonl import build_row  # noqa: E402


def _old_row(function: str, confidence: str) -> dict:
    return {
        "designator": "R2", "type": "resistor", "value": "0.02Ω",
        "function": function, "detail": "旧 detail", "block": "pull-down-resistor",
        "block_instance": "pull-down-resistor", "confidence": confidence,
        "source": "cg-role",
    }


def test_confidence_not_inherited_when_function_changes() -> None:
    """定性变了，旧 confidence（属于旧答案）不得带到新答案上。"""
    row = build_row(
        {"designator": "R2", "function": "电流采样电阻（低边，对地）"},
        "R2", {"type": "resistor"}, _old_row("下拉电阻", "user-confirmed"),
        "session-correction", "(unmatched)",
    )
    assert row["confidence"] == "high"


def test_confidence_inherited_when_function_unchanged() -> None:
    row = build_row(
        {"designator": "R2", "function": "下拉电阻"},
        "R2", {"type": "resistor"}, _old_row("下拉电阻", "user-confirmed"),
        "session-correction", "pull-down-resistor",
    )
    assert row["confidence"] == "user-confirmed"


def test_identical_text_is_aligned() -> None:
    assert (
        classify_alignment(
            "DC 电源去耦电容",
            "dc-decoupling-cap",
            "DC 电源去耦电容",
            "dc-decoupling-cap",
        )
        == "aligned"
    )


def test_role_suffix_stripped_before_compare() -> None:
    """title 尾部 " · C_dec" 角色后缀剥掉后相等 → aligned。"""
    assert (
        classify_alignment(
            "DC 电源去耦电容",
            "dc-decoupling-cap",
            "DC 电源去耦电容 · C_dec",
            "dc-decoupling-cap",
        )
        == "aligned"
    )


def test_same_template_more_detailed_is_refined() -> None:
    """同一模板，当前补充网络证据 → refined（P1 验收：R100 类）。"""
    assert (
        classify_alignment(
            "运放反馈电阻（Rf，同相反馈对）",
            "opamp-noninv-feedback",
            "反馈电阻（U7 LM321 负反馈放大器）",
            "opamp-noninv-feedback",
        )
        == "refined"
    )


@pytest.mark.parametrize(
    "base_fn",
    ["RC 低通滤波器", "RC 滤波电阻", "下拉电阻", "I2C SDA 线上拉电阻"],
)
def test_same_template_added_net_is_refined(base_fn: str) -> None:
    """P0 清单里的答案升级家族：基准措辞不变 + 当前补网络/引脚 → refined。"""
    assert (
        classify_alignment(
            base_fn,
            "rc-low-pass",
            base_fn + "（AIN10（U2.19）） · R_series",
            "rc-low-pass",
        )
        == "refined"
    )


def test_block_change_stays_mismatched() -> None:
    """R2 型：基准 pull-down-resistor，当前换模板 → 真实 mismatch。"""
    assert (
        classify_alignment(
            "下拉电阻",
            "pull-down-resistor",
            "电流采样电阻（低边，对地）",
            "current-shunt-resistor",
        )
        == "mismatched"
    )


def test_unmatched_current_stays_mismatched() -> None:
    """R2 现状：当前走 live-match 无模板 id → mismatched，不得 refined。"""
    assert (
        classify_alignment(
            "下拉电阻",
            "pull-down-resistor",
            "电流采样电阻（低边，对地）",
            "(unmatched)",
        )
        == "mismatched"
    )


def test_both_unmatched_stays_mismatched() -> None:
    assert (
        classify_alignment(
            "检波带通滤波电容",
            "(unmatched)",
            "二极管检波带通 ADC 采样 · C_couple",
            "(unmatched)",
        )
        == "mismatched"
    )


def test_missing_baseline_block_stays_mismatched() -> None:
    """基准缺 block 字段（None/空串）时无从证明同模板 → 保守 mismatched。"""
    assert (
        classify_alignment(
            "检波带通滤波电容",
            None,
            "二极管检波带通 ADC 采样 · C_couple",
            "detector-amp-sampling",
        )
        == "mismatched"
    )


def test_empty_baseline_block_stays_mismatched() -> None:
    assert (
        classify_alignment(
            "检波带通滤波电容",
            "",
            "二极管检波带通 ADC 采样 · C_couple",
            "detector-amp-sampling",
        )
        == "mismatched"
    )
