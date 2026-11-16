"""自动模板（无人审直入 ``primitives/auto/``）的测试。

覆盖三件事：

1. **值放宽**：草稿不再绑定参考板实测值，也不泄漏板网名 / 位号。
2. **机器闸门 0–3**：手工优先、state 必须 ready、回归不得改块归属、
   基准层不得有别的具体模板。
3. **手工优先**：同一元件被手工模板与 auto 模板同时命中时，手工块拿走。

CLI 的 ``cg primitives draft`` 也在这里覆盖（不写 / 写），写的目标一律是
``tmp_path`` 下的临时模板库——**测试不得修改仓库真实 primitives/**。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from circuit_graph import primitive_autodraft as pad
from circuit_graph.ir import (
    BlockRole,
    CircuitIR,
    CircuitMeta,
    Component,
    DeviceKind,
    FunctionalBlock,
)
from circuit_graph.primitives import component_value_matches, load_primitives, template_of_block

REPO = Path(__file__).resolve().parent.parent
AUTO_DIR = REPO / "primitives" / "auto"
FIXTURE_DIR = REPO / "fixtures" / "protel"
FX = "FX-T268-V6-10米-5065.NET"
FX_PATH = FIXTURE_DIR / FX


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ir_with_blocks(assignments: dict[str, list[str]]) -> CircuitIR:
    """造一个只含功能块的 IR（``模板id -> 元件``）。"""
    blocks = [
        FunctionalBlock(
            id=f"{tid}-x",
            name=tid,
            role=BlockRole.UNKNOWN,
            component_ids=list(comps),
        )
        for tid, comps in assignments.items()
    ]
    comp_ids = {cid for comps in assignments.values() for cid in comps}
    components = [Component(id=cid, device_kind=DeviceKind.RESISTOR) for cid in sorted(comp_ids)]
    meta = CircuitMeta(source="test", format="test")
    return CircuitIR(
        meta=meta, components=components, nets=[], connections=[], blocks=blocks
    )


def _fake_draft_result(tmp_primitives: Path, **overrides) -> dict:
    """一个最小可用的草稿结果（闸门测试用）。"""
    doc = {
        "id": "auto-fake",
        "name": "fake",
        "description": "fake",
        "source": "auto",
        "reference": {"board": FX, "chip": "U4"},
        "signals": [],
        "components": [],
    }
    result = {
        "draft": doc,
        "state": "ready",
        "conflicts": [],
        "warnings": [],
        "board": FX,
        "board_path": str(FX_PATH),
        "chip": "U4",
        "chip_id": "U4",
        "part_number": "",
        "replay": {"matched_components": [], "blocks": []},
    }
    result.update(overrides)
    return result


@pytest.fixture(scope="module")
def u4_draft() -> dict:
    """U4（JW5065）的自动草稿（回放不便宜，整个模块共用一次）。"""
    return pad.auto_draft(FX, "U4")


# ---------------------------------------------------------------------------
# 1. 值放宽
# ---------------------------------------------------------------------------


def test_draft_has_no_board_designators_or_net_names(u4_draft: dict) -> None:
    """草稿不得出现参考板的位号 / 网名（它们是板特有的，会过拟合）。"""
    blob = json.dumps(u4_draft["draft"], ensure_ascii=False)
    assert "C70" not in blob, "draft leaked a board designator"
    assert "NetC70" not in blob, "draft leaked a board net name"
    patterns = {s["name_pattern"] for s in u4_draft["draft"]["signals"]}
    assert patterns <= {".*", pad._GND_NET_PATTERN}


def test_draft_members_use_value_type_bands_not_measured_values(u4_draft: dict) -> None:
    """成员的值约束是"值类型区间"，不是参考板实测值。"""
    chip = next(c for c in u4_draft["draft"]["components"] if c["id"] == "chip")
    assert chip["strict_value"] is True, "chip must stay bound to its part number"
    assert chip["value_pattern"] == re.escape("JW5065")

    members = [c for c in u4_draft["draft"]["components"] if c["id"] != "chip"]
    assert members, "draft should keep at least one member"
    for comp in members:
        pattern = comp["value_pattern"]
        assert pattern, f"{comp['id']} has no value pattern"
        if pattern == pad._ZERO_OHM_PATTERN:
            continue  # 0Ω 跳线：值类型就是"零欧"
        # 区间正则：必须带数量级锚点，而不是精确转义的实测值
        assert re.search(r"\[1-9\]", pattern), f"{comp['id']} value pattern is not a band"


def test_value_type_pattern_spans_one_decade() -> None:
    """值类型区间：100nF 的成员也接受 10nF / 1uF。"""
    ir = pad.parse_board_ir(FX_PATH)
    comp = next(c for c in ir.components if c.id == "C70")
    assert comp.raw_value == "100nF"
    pattern = pad.value_type_pattern(comp)
    assert pattern is not None
    assert pattern != re.escape("100nF")
    assert component_value_matches(pattern, comp) is True


def test_zero_ohm_member_keeps_zero_ohm_pattern() -> None:
    """0Ω 跳线的值类型是"零欧"，不是"接近零"。"""
    ir = pad.parse_board_ir(FX_PATH)
    comp = next(c for c in ir.components if c.id == "R99")
    assert comp.raw_value == "0R-1206"
    assert pad.value_type_pattern(comp) == pad._ZERO_OHM_PATTERN


def test_members_without_value_are_dropped(u4_draft: dict) -> None:
    """实测值为空（NC）的成员不进模板。"""
    assert "R77" in u4_draft["no_value_members"]
    member_ids = {c["id"] for c in u4_draft["draft"]["components"]}
    assert not any("r77" in mid.lower() for mid in member_ids)


# ---------------------------------------------------------------------------
# 2. 机器闸门
# ---------------------------------------------------------------------------


def test_gate0_rejects_when_a_manual_template_already_covers_the_chip() -> None:
    """闸门 0：参考板上已有手工模板命中该芯片 → 跳过（不重复写）。"""
    result = pad.auto_draft(FX, "U4")
    gates = pad.evaluate_gates(result)
    assert not gates["passed"]
    assert any(r.startswith("gate0:manual:") for r in gates["reasons"]), gates["reasons"]


def test_gate1_rejects_when_state_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """闸门 1：草稿 state 不是 ready → 拒。"""
    prim = tmp_path / "primitives"
    prim.mkdir()
    monkeypatch.setattr(pad, "recognize_board", lambda *a, **k: _ir_with_blocks({}))
    result = _fake_draft_result(prim, state="needs_review", conflicts=["no_match"])
    gates = pad.evaluate_gates(result, primitives_dir=prim)
    assert not gates["passed"]
    assert any(r.startswith("gate1:") for r in gates["reasons"]), gates["reasons"]


def test_gate2_rejects_when_an_already_covered_component_changes_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """闸门 2：加入 auto 模板后，已被手工模板认领的元件改了块归属 → 拒。"""
    prim = tmp_path / "primitives"
    prim.mkdir()
    (prim / "manual-x.json").write_text(
        json.dumps({"id": "manual-x", "name": "x", "signals": [], "components": []}),
        encoding="utf-8",
    )

    def fake_recognize(board_path, *, primitives_dir, **kwargs):
        # 加模板前：C1 归手工模板；加模板后：C1 归 auto 模板 → 块归属变了。
        if Path(primitives_dir) == prim:
            return _ir_with_blocks({"manual-x": ["C1"]})
        return _ir_with_blocks({"auto-fake": ["C1"]})

    monkeypatch.setattr(pad, "recognize_board", fake_recognize)
    result = _fake_draft_result(prim)
    gates = pad.evaluate_gates(result, primitives_dir=prim)
    assert not gates["passed"]
    assert any(r.startswith("gate2:") for r in gates["reasons"]), gates["reasons"]


def test_gate3_rejects_when_the_answer_layer_names_another_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """闸门 3：auto 块覆盖的元件在基准层里已归到**另一个**具体模板 → 拒。"""
    prim = tmp_path / "primitives"
    prim.mkdir()
    (prim / "manual-x.json").write_text(
        json.dumps({"id": "manual-x", "name": "x", "signals": [], "components": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        pad, "recognize_board", lambda *a, **k: _ir_with_blocks({"auto-fake": ["C1"]})
    )
    monkeypatch.setattr(
        pad,
        "_answers_index",
        lambda _d, _stem=None: {
            "C1": {"block": "voltage-divider", "confidence": "high", "source": "human"},
            "C2": {"block": "(unmatched)", "confidence": "low", "source": "cg-role"},
        },
    )
    result = _fake_draft_result(prim)
    gates = pad.evaluate_gates(result, primitives_dir=prim)
    assert not gates["passed"]
    assert any(r.startswith("gate3:") for r in gates["reasons"]), gates["reasons"]


def test_gate3_allows_unmatched_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """基准层写的是 (unmatched) 或本 auto id 时不拦。"""
    prim = tmp_path / "primitives"
    prim.mkdir()
    (prim / "manual-x.json").write_text(
        json.dumps({"id": "manual-x", "name": "x", "signals": [], "components": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        pad,
        "recognize_board",
        lambda *a, **k: _ir_with_blocks({"auto-fake": ["C1", "C2"]}),
    )
    monkeypatch.setattr(
        pad,
        "_answers_index",
        lambda _d, _stem=None: {
            "C1": {"block": "(unmatched)", "confidence": "low", "source": "cg-role"},
            "C2": {"block": "auto-fake", "confidence": "high", "source": "session-correction"},
        },
    )
    result = _fake_draft_result(prim)
    gates = pad.evaluate_gates(result, primitives_dir=prim)
    assert not any(r.startswith("gate3:") for r in gates["reasons"]), gates["reasons"]


def test_gate3_reads_only_the_reference_board_answers(tmp_path) -> None:
    """基准层按板过滤：两张板的同名位号（都有 C70）不会互相污染。"""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for stem, block in (("A", "manual-a"), ("B", "manual-b")):
        (fixtures / f"{stem}.answers.jsonl").write_text(
            json.dumps({"designator": "C70", "block": block}) + "\n", encoding="utf-8"
        )
    assert pad._answers_index(fixtures, "A")["C70"]["block"] == "manual-a"
    assert pad._answers_index(fixtures, "B")["C70"]["block"] == "manual-b"


# ---------------------------------------------------------------------------
# 3. 手工优先
# ---------------------------------------------------------------------------


def test_manual_template_wins_over_auto_template(tmp_path) -> None:
    """同一个元件被手工模板和 auto 模板同时命中时，块归手工模板。"""
    draft = pad.auto_draft(FX, "U3")
    doc = draft["draft"]
    manual = dict(doc)
    manual["id"] = "manual-rh6016c-test"
    manual["source"] = ""
    manual.pop("reference", None)

    prim = tmp_path / "primitives"
    prim.mkdir()
    (prim / "auto.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (prim / "manual.json").write_text(
        json.dumps(manual, ensure_ascii=False), encoding="utf-8"
    )

    ir = pad.recognize_board(FX_PATH, primitives_dir=prim)
    owners: dict[str, str] = {}
    for block in ir.blocks:
        tid = template_of_block(block.id, ["auto-rh6016c", "manual-rh6016c-test"])
        if tid is None:
            continue
        for cid in block.component_ids:
            owners.setdefault(cid, tid)
    assert owners, "neither template matched"
    assert owners.get("U3") == "manual-rh6016c-test", owners


# ---------------------------------------------------------------------------
# 4. 库里的 auto 模板
# ---------------------------------------------------------------------------


def test_auto_library_files_are_tagged() -> None:
    """库里的 auto 模板必须带 source=auto 与 reference（board + chip）。"""
    files = sorted(AUTO_DIR.glob("*.json")) if AUTO_DIR.exists() else []
    if not files:
        pytest.skip("no auto template in the library yet")
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["source"] == "auto", path
        assert raw["id"].startswith("auto-"), path
        # 落在 primitives/auto/，schema 在上一层目录（相对路径写错会静默失效）。
        assert raw["$schema"] == "../schema.json", path
        ref = raw["reference"]
        assert ref["board"] and ref["chip"], path


@pytest.mark.parametrize(
    "path",
    sorted(AUTO_DIR.glob("*.json")) if AUTO_DIR.exists() else [],
    ids=lambda p: p.stem,
)
def test_auto_template_hits_its_reference_chip(path: Path) -> None:
    """每个 auto 模板都要在它的 reference 板上命中 reference 芯片。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    ref = raw["reference"]
    board = FIXTURE_DIR / ref["board"]
    if not board.exists():
        pytest.skip(f"reference board missing: {board}")
    result = pad.match_primitive_on_board(raw, board)
    assert ref["chip"] in result["matched_components"], result


def test_auto_templates_are_recognised_as_auto() -> None:
    """加载器把 source=auto 的模板标成 is_auto（手工模板不是）。"""
    auto_ids = set()
    if AUTO_DIR.exists():
        for path in AUTO_DIR.glob("*.json"):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("source") == "auto":
                auto_ids.add(raw["id"])
    templates = {t.id: t for t in load_primitives(REPO / "primitives")}
    for tid in auto_ids:
        assert templates[tid].is_auto is True
        assert templates[tid].reference and templates[tid].reference.get("board")
    manual = [t for t in templates.values() if t.id not in auto_ids]
    assert all(t.is_auto is False for t in manual)


# ---------------------------------------------------------------------------
# 5. cg role 里的自动模板
# ---------------------------------------------------------------------------


def test_auto_template_does_not_override_builtin_role_rules() -> None:
    """auto-rh6016c 覆盖 U3 / R64，但 cg role 仍按内置规则定性。

    自动模板是"这一块板上推导出来的"，内置规则是跨板知识；auto 只兜底。
    """
    from circuit_graph.role import infer_role

    ir = pad.parse_board_ir(FX_PATH)
    assert infer_role(ir, "R64", lambda comp: None).rule == "series_signal"
    assert infer_role(ir, "U3", lambda comp: None).rule == "touch_key_ic"


def test_role_marks_auto_blocks_and_lowers_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """来自 auto 块的 cg role 结果：标题带"自动模板"、置信度 medium。"""
    from dataclasses import replace

    from circuit_graph import role as role_mod

    ir = replace(
        pad.parse_board_ir(FX_PATH),
        blocks=[
            FunctionalBlock(
                id="auto-rh6016c-u3",
                name="RH6016C 典型应用（自动）",
                role=BlockRole.UNKNOWN,
                component_ids=["C63"],
                metadata={"template_source": "auto"},
            )
        ],
    )
    # 隔离"标注"这一层：不跑现场手工模板匹配与最后兜底规则。
    monkeypatch.setattr(role_mod, "_match_focal_live", lambda *a, **k: None)
    monkeypatch.setattr(role_mod.role_rules, "apply_last_rules", lambda *a, **k: None)

    result = role_mod.infer_role(ir, "C63", lambda comp: None)
    assert result is not None
    assert result.rule == "primitive:auto-rh6016c-u3"
    assert "自动模板" in result.title
    assert result.confidence == "medium"
    assert result.data.get("auto_template") is True


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------


def _cli_draft(tmp_path, monkeypatch: pytest.MonkeyPatch, *extra: str):
    """在临时目录里跑 ``cg primitives draft``（模板库 / fixtures 都指到 tmp）。"""
    from click.testing import CliRunner

    from circuit_graph.cli.app import main

    prim = tmp_path / "primitives"
    prim.mkdir(exist_ok=True)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(exist_ok=True)
    # 闸门 2 的回归只需要一块板；复制默认板过去，让闸门跑得快。
    (fixtures / FX).write_bytes(FX_PATH.read_bytes())
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    return runner.invoke(
        main,
        [
            "primitives",
            "draft",
            str(FX_PATH),
            "--chip",
            "U3",
            "--primitives-dir",
            str(prim),
            "--fixtures-dir",
            str(fixtures),
            *extra,
        ],
    )


def test_cli_draft_without_write_does_not_touch_the_library(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有 --write 就只出报告，不写模板文件。"""
    result = _cli_draft(tmp_path, monkeypatch)
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "primitives").rglob("*.json")) == []
    assert (tmp_path / "output" / "auto-draft-report.md").exists()


def test_cli_draft_with_write_writes_into_the_given_library(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--write 才写模板文件，且写进 --primitives-dir 指定的目录。"""
    result = _cli_draft(tmp_path, monkeypatch, "--write")
    assert result.exit_code == 0, result.output
    written = sorted((tmp_path / "primitives").glob("auto/*.json"))
    assert written, result.output
    raw = json.loads(written[0].read_text(encoding="utf-8"))
    assert raw["source"] == "auto"
    assert raw["reference"]["chip"] == "U3"
