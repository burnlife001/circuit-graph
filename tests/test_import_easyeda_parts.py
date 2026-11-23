"""scripts/import_easyeda_parts.py 的导入规则测试。

全部用 tmp DB + 内联小样本，不读真实快照，因此不会污染 bundled KB，
也不依赖 easyeda-agent 仓库是否在位。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from circuit_graph.knowledge.db import ComponentDatabase

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_easyeda_parts.py"

PARTS = {
    "buck.demo_buck": {
        "mpn": "DEMO-BUCK",
        "lcsc": "C1",
        "manufacturer": "Acme",
        "desc": "2A 同步 buck",
        "datasheetUrl": "https://example.test/buck.pdf",
    },
    "ldo.demo_ldo": {"mpn": "DEMO-LDO", "lcsc": "C2", "desc": "3.3V LDO"},
    "esd.demo_merge_first": {"mpn": "DEMO-ESD", "lcsc": "C3", "desc": "ESD 首条"},
    "esd.demo_merge_second": {"mpn": "DEMO-ESD", "lcsc": "C4", "desc": "ESD 次条"},
    "ic.demo_no_pins": {"mpn": "DEMO-IC", "desc": "无引脚快照"},
    "conn.demo_conn": {"mpn": "DEMO-CONN", "lcsc": "C5", "desc": "连接器"},
    "res.demo_res": {"mpn": "DEMO-R"},
    "cap.demo_cap": {"mpn": "DEMO-C"},
    "ind.demo_ind": {"mpn": "DEMO-L"},
    "mcu.demo_no_mpn": {"value": "X", "desc": "没有 MPN"},
}

PINS = {
    "buck.demo_buck": [{"n": "1", "name": "IN"}, {"n": "2", "name": "FB"}],
    "esd.demo_merge_second": [{"n": "1", "name": "I/O1"}],
    "conn.demo_conn": [{"n": "1", "name": "VBUS"}],
}


@pytest.fixture()
def importer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("import_easyeda_parts", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass 要靠 sys.modules 解析延迟注解，先注册再执行模块。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db(tmp_path):
    database = ComponentDatabase.open(tmp_path / "kb.db")
    yield database
    database.close()


def _plan(importer: ModuleType, db: ComponentDatabase):
    return importer.plan_import(PARTS, PINS, db)


def test_imports_non_passive_parts_keyed_by_mpn(importer, db) -> None:
    """slug 只是快照键，入库身份是 mpn；无源器件不进 KB。"""
    importer.apply_plan(db, _plan(importer, db))

    for mpn in ("DEMO-BUCK", "DEMO-LDO", "DEMO-ESD", "DEMO-IC", "DEMO-CONN"):
        assert db.get_component(mpn) is not None, mpn
    assert db.get_component("buck.demo_buck") is None
    assert db.get_component("DEMO-R") is None
    assert db.get_component("DEMO-C") is None
    assert db.get_component("DEMO-L") is None


def test_scope_report_skips_passives_and_no_mpn(importer, db) -> None:
    report = _plan(importer, db).report

    assert report["scope"]["passives_skipped"] == 3
    assert report["scope"]["no_mpn_skipped"] == 1
    assert report["scope"]["non_passive_parts"] == 7
    assert report["components"]["new"] == 5
    assert report["components"]["existing"] == 0


def test_duplicate_mpn_merges_and_keeps_pin_entry(importer, db) -> None:
    """同 MPN 两条条目合并：事实取首条，引脚取有引脚表的那条。"""
    plan = _plan(importer, db)
    importer.apply_plan(db, plan)

    assert plan.report["scope"]["duplicate_mpn_merged"] == 1
    detail = plan.report["scope"]["duplicate_mpn_details"][0]
    assert detail["part_number"] == "DEMO-ESD"
    assert detail["pin_slug"] == "esd.demo_merge_second"

    record = db.get_component("DEMO-ESD")
    assert record is not None
    assert record.description == "ESD 首条"
    assert db.get_pinout("DEMO-ESD") == {"1": "I/O1"}


def test_category_written_only_for_explicit_mapping(importer, db) -> None:
    importer.apply_plan(db, _plan(importer, db))

    buck = db.get_component("DEMO-BUCK")
    ldo = db.get_component("DEMO-LDO")
    ic = db.get_component("DEMO-IC")
    assert buck is not None and buck.category == "dcdc"
    assert ldo is not None and ldo.category == "ldo"
    assert ic is not None and ic.category is None


def test_pins_recorded_as_easyeda_and_credible(importer, db) -> None:
    importer.apply_plan(db, _plan(importer, db))

    record = db.get_component("DEMO-BUCK")
    assert record is not None
    assert record.pin_source == "easyeda"
    assert record.is_credible is True
    assert db.get_pinout("DEMO-BUCK") == {"1": "IN", "2": "FB"}
    assert db.get_properties("DEMO-BUCK")["lcsc"] == "C1"
    assert db.get_properties("DEMO-BUCK")["datasheet_url"] == "https://example.test/buck.pdf"


def test_existing_facts_and_pinout_are_not_overwritten(importer, db) -> None:
    db.set_component_facts("DEMO-BUCK", description="人工描述", manufacturer="人工厂商")
    db.upsert_resolved_pins("DEMO-BUCK", {"1": "OLD"}, pin_source="local")

    plan = _plan(importer, db)
    report = plan.report
    importer.apply_plan(db, plan)

    record = db.get_component("DEMO-BUCK")
    assert record is not None
    assert record.description == "人工描述"
    assert record.manufacturer == "人工厂商"
    assert record.category == "dcdc"  # 空字段仍被补上
    assert record.pin_source == "local"
    assert db.get_pinout("DEMO-BUCK") == {"1": "OLD"}
    assert {"part_number": "DEMO-BUCK", "reason": "generic_pinout_exists"} in report["conflicts"]
    assert report["components"]["existing"] == 1
    assert report["pinouts"]["skipped_existing"] == 1


def test_entry_without_pin_table_writes_no_pinout(importer, db) -> None:
    """快照里没有引脚表的条目：计入 no_pin_map，且绝不写空/编造的引脚行。"""
    plan = _plan(importer, db)
    # 7 条非无源里只有 buck / esd 次条 / conn 带引脚表。
    assert plan.report["pinouts"]["no_pin_map"] == 4
    importer.apply_plan(db, plan)

    record = db.get_component("DEMO-IC")
    assert record is not None
    rows = db.conn.execute(
        "SELECT COUNT(*) AS n FROM pinouts WHERE component_id = ?", (record.id,)
    ).fetchone()
    assert rows["n"] == 0
    assert db.get_pinout("DEMO-IC") is None


def test_board_level_pinout_does_not_block_generic_import(importer, db) -> None:
    """只有板级映射（source_ref=位号）时仍要补通用表，不能被当成已有引脚表。"""
    comp_id = db._upsert_component("DEMO-BUCK")
    db.conn.execute(
        "INSERT INTO pinouts (component_id, pin_number, pin_name, source_ref) "
        "VALUES (?, '1', 'GND', 'U9')",
        (comp_id,),
    )
    db.conn.commit()
    # 没有通用表时读取会兜底到板级映射。
    assert db.get_pinout("DEMO-BUCK") == {"1": "GND"}

    plan = _plan(importer, db)
    buck = next(p for p in plan.parts if p.part_number == "DEMO-BUCK")
    assert buck.write_pins is True
    assert plan.report["conflicts"] == []
    importer.apply_plan(db, plan)

    # 通用表优先；显式要板级映射时仍返回该板的引脚名。
    assert db.get_pinout("DEMO-BUCK") == {"1": "IN", "2": "FB"}
    assert db.get_pinout("DEMO-BUCK", source_ref="U9") == {"1": "GND"}


def test_second_run_is_idempotent(importer, db) -> None:
    importer.apply_plan(db, _plan(importer, db))
    first = db.stats()

    plan = _plan(importer, db)
    applied = importer.apply_plan(db, plan)
    second = db.stats()

    assert plan.report["components"]["new"] == 0
    assert plan.report["pinouts"]["new"] == 0
    assert plan.report["pinouts"]["skipped_existing"] == 3
    assert plan.report["facts"]["filled"] == 0
    assert applied["components_created"] == 0
    assert applied["pinouts_written"] == 0
    assert applied["properties_written"] == 0
    for key in ("components", "pinouts", "properties"):
        assert second[key] == first[key], key


def _write_snapshot(root: Path, tag: str = "v1") -> Path:
    """造一个最小快照目录（内容带 tag，便于制造上游/本地差异）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "standard-parts.json").write_text(
        json.dumps({"parts": {"ldo.demo": {"mpn": f"DEMO-{tag}"}}}), encoding="utf-8"
    )
    (root / "symbol-pins.json").write_text(
        json.dumps({"parts": {"ldo.demo": [{"n": "1", "name": "GND"}]}}), encoding="utf-8"
    )
    return root


def _write_manifest(
    importer: ModuleType, snapshot_dir: Path, source_repo: Path | None = None
) -> None:
    manifest = importer.build_manifest(snapshot_dir, source_repo)
    (snapshot_dir / importer.MANIFEST_NAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def test_snapshot_check_passes_when_untouched(importer, tmp_path) -> None:
    snapshot = _write_snapshot(tmp_path / "snap")
    _write_manifest(importer, snapshot)

    issues, notes = importer.check_snapshot(snapshot)

    assert issues == []
    assert any("未找到上游仓库" in note for note in notes)


def test_snapshot_check_flags_local_edit(importer, tmp_path) -> None:
    snapshot = _write_snapshot(tmp_path / "snap")
    _write_manifest(importer, snapshot)
    _write_snapshot(snapshot, tag="edited")

    issues, _ = importer.check_snapshot(snapshot)

    assert any("本地快照已被修改：standard-parts.json" in issue for issue in issues)


def test_snapshot_check_flags_upstream_drift(importer, tmp_path) -> None:
    snapshot = _write_snapshot(tmp_path / "snap")
    upstream = _write_snapshot(tmp_path / "easyeda-agent", tag="v1")
    # 上游文件放在 manifest 记录的真实相对路径下。
    refs = upstream / importer.UPSTREAM_REL_DIR
    refs.mkdir(parents=True)
    for name in importer.SNAPSHOT_FILES:
        (upstream / name).replace(refs / name)
    _write_manifest(importer, snapshot, source_repo=upstream)

    issues, _ = importer.check_snapshot(snapshot)
    assert issues == []

    (refs / "standard-parts.json").write_text(
        json.dumps({"parts": {"ldo.demo": {"mpn": "DEMO-v2"}}}), encoding="utf-8"
    )
    issues, _ = importer.check_snapshot(snapshot)
    assert any("上游 standard-parts.json 内容已变化" in issue for issue in issues)


def test_snapshot_check_flags_missing_manifest(importer, tmp_path) -> None:
    snapshot = _write_snapshot(tmp_path / "snap")

    issues, _ = importer.check_snapshot(snapshot)

    assert len(issues) == 1
    assert "manifest 缺失" in issues[0]


def test_cli_check_snapshot_exit_codes(importer, tmp_path) -> None:
    snapshot = _write_snapshot(tmp_path / "snap")
    _write_manifest(importer, snapshot)

    assert importer.main(["--check-snapshot", "--snapshot-dir", str(snapshot)]) == 0

    _write_snapshot(snapshot, tag="edited")
    assert importer.main(["--check-snapshot", "--snapshot-dir", str(snapshot)]) == 1


def test_cli_update_manifest_roundtrip(importer, tmp_path) -> None:
    snapshot = _write_snapshot(tmp_path / "snap")

    assert importer.main(["--update-manifest", "--snapshot-dir", str(snapshot)]) == 0

    manifest = json.loads((snapshot / importer.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(manifest["files"]) == set(importer.SNAPSHOT_FILES)
    assert importer.check_snapshot(snapshot)[0] == []
