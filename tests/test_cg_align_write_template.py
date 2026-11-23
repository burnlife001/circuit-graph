"""write_template：手工模板落库校验（防误报守门）。

覆盖 .claude/skills/cg-align/scripts/write_template.py 的纯函数：
- validate_draft：必填字段 / id 格式 / draft- 占位拒绝 / 重复 id /
  类目存在性 / auto 禁写 / 拒绝覆盖；
- guards_present：反例约束（expected_*）守门。

只测纯逻辑，不触真实 primitives/ 目录（target/known_ids/categories 全部注入）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "cg-align" / "scripts"
if not (_SCRIPTS / "write_template.py").is_file():
    pytest.skip("cg-align skill is not installed", allow_module_level=True)
sys.path.insert(0, str(_SCRIPTS))

from write_template import guards_present, validate_draft  # noqa: E402

CATEGORIES = {"power", "passive", "protection"}


def make_draft(**over) -> dict:
    draft = {
        "id": "buck-output-inductor",
        "name": "buck 输出电感",
        "signals": [
            {
                "id": "net_vin",
                "type": "net",
                "name_pattern": "^(VIN|VCC)$",
                "expected_role": "power",
            }
        ],
        "components": [
            {"id": "L1", "type": "Inductor", "required": True,
             "pins": [{"number": "1", "net": "net_vin"}]}
        ],
    }
    draft.update(over)
    return draft


def validate(draft, tmp_path: Path, *, category: str = "power", name: str = "buck-output-inductor"):
    return validate_draft(
        draft,
        known_ids={"voltage-divider"},
        categories=CATEGORIES,
        category=category,
        target=tmp_path / f"{name}.json",
    )


def test_valid_draft_passes(tmp_path: Path) -> None:
    assert validate(make_draft(), tmp_path) == []


def test_missing_required_fields(tmp_path: Path) -> None:
    errors = validate({"id": "x-y"}, tmp_path)
    assert any("name" in e for e in errors)
    assert any("signals" in e for e in errors)
    assert any("components" in e for e in errors)


def test_draft_prefix_id_rejected(tmp_path: Path) -> None:
    errors = validate(make_draft(id="draft-buck-output-inductor"), tmp_path)
    assert any("占位符" in e for e in errors)


def test_uppercase_id_rejected(tmp_path: Path) -> None:
    errors = validate(make_draft(id="Buck-Output"), tmp_path)
    assert any("只允许小写" in e for e in errors)


def test_duplicate_id_rejected(tmp_path: Path) -> None:
    errors = validate(make_draft(id="voltage-divider"), tmp_path)
    assert any("重复" in e for e in errors)


def test_unknown_category_rejected(tmp_path: Path) -> None:
    errors = validate(make_draft(), tmp_path, category="fluxcapacitor")
    assert any("类目不存在" in e for e in errors)


def test_auto_category_rejected(tmp_path: Path) -> None:
    """auto/ 是 cg primitives draft 闸门产物，手工路径禁写。"""
    errors = validate_draft(
        make_draft(),
        known_ids=set(),
        categories={"auto"},
        category="auto",
        target=tmp_path / "x.json",
    )
    assert any("禁写" in e for e in errors)


def test_existing_target_rejected(tmp_path: Path) -> None:
    target = tmp_path / "buck-output-inductor.json"
    target.write_text("{}", encoding="utf-8")
    errors = validate_draft(
        make_draft(),
        known_ids=set(),
        categories=CATEGORIES,
        category="power",
        target=target,
    )
    assert any("拒绝覆盖" in e for e in errors)


def test_signal_without_name_pattern_rejected(tmp_path: Path) -> None:
    draft = make_draft()
    draft["signals"] = [{"id": "net_vin", "type": "net"}]
    errors = validate(draft, tmp_path)
    assert any("name_pattern" in e for e in errors)


def test_guards_present_detection() -> None:
    assert guards_present(make_draft()) is True
    # 只有 name_pattern、无任何 expected_* → 守门不通过
    bare = make_draft()
    for sig in bare["signals"]:
        sig.pop("expected_role")
    assert guards_present(bare) is False
    # 反例约束为 null 视同缺失
    null_guard = make_draft()
    null_guard["signals"][0]["expected_role"] = None
    assert guards_present(null_guard) is False
    # expected_ic_pin / expected_pin_category / expected_connector_pin 都算守门
    for key in ("expected_ic_pin", "expected_pin_category", "expected_connector_pin"):
        d = make_draft()
        d["signals"][0].pop("expected_role")
        d["signals"][0][key] = "required"
        assert guards_present(d) is True, key
