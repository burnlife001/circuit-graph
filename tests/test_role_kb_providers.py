"""role.py KB provider 回归测试：板级变体行（无数据）不得遮蔽基础行。

真实案例：网表值 ``MM32F014X-QFP48`` 在 KB 有一条网表导入产生的空变体行，
基础行 ``MM32F014X`` 才带 48 脚引脚数据与事实。provider 必须沿
精确→严格前缀 链降级，而不是命中空变体行就短路返回 None。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from circuit_graph.cli.role import _kb_facts_provider, _kb_pinout_provider
from circuit_graph.knowledge.db import ComponentDatabase


@pytest.fixture
def kb_path(tmp_path: Path) -> Path:
    db = ComponentDatabase.open(tmp_path / "test_kb.db")
    with db.conn:
        db._upsert_component(
            "MM32F014X",
            category="mcu",
            subcategory="test-sub",
            package="LQFP48",
            pin_source="manual",
        )
        base_id = db._upsert_component("MM32F014X")  # ON CONFLICT → 同 id
        for num, name in (("1", "PC13"), ("2", "PC14")):
            db.conn.execute(
                """
                INSERT INTO pinouts (
                    component_id, pin_number, pin_name, pin_type, source_ref
                ) VALUES (?, ?, ?, ?, NULL)
                """,
                (base_id, num, name, "io"),
            )
        # 板级变体行：网表导入产物，无事实、无引脚。
        db._upsert_component("MM32F014X-QFP48", package="LQFP48")
    return tmp_path / "test_kb.db"


def test_lookup_candidates_exact_first_then_strict_prefix(kb_path: Path) -> None:
    db = ComponentDatabase.open(kb_path)
    cands = db.lookup_candidates("MM32F014X-QFP48")
    assert [c.part_number for c in cands] == ["MM32F014X-QFP48", "MM32F014X"]
    assert db.lookup_candidates("MM32F014X") == [] or (
        [c.part_number for c in db.lookup_candidates("MM32F014X")][0]
        == "MM32F014X"
    )
    assert db.lookup_candidates("UNKNOWN9") == []


def test_pinout_provider_skips_empty_variant_row(kb_path: Path) -> None:
    provider = _kb_pinout_provider(kb_path)
    comp = SimpleNamespace(part_number="MM32F014X-QFP48", raw_value="MM32F014X-QFP48")
    assert provider(comp) == {"1": "PC13", "2": "PC14"}


def test_facts_provider_skips_empty_variant_row(kb_path: Path) -> None:
    provider = _kb_facts_provider(kb_path)
    comp = SimpleNamespace(part_number="MM32F014X-QFP48", raw_value="MM32F014X-QFP48")
    assert provider(comp) == {"category": "mcu", "subcategory": "test-sub"}


def test_providers_exact_match_still_works(kb_path: Path) -> None:
    pinout = _kb_pinout_provider(kb_path)
    facts = _kb_facts_provider(kb_path)
    comp = SimpleNamespace(part_number="MM32F014X", raw_value="MM32F014X")
    assert pinout(comp) == {"1": "PC13", "2": "PC14"}
    assert facts(comp)["category"] == "mcu"


def test_providers_unknown_part_degrade(kb_path: Path) -> None:
    pinout = _kb_pinout_provider(kb_path)
    facts = _kb_facts_provider(kb_path)
    comp = SimpleNamespace(part_number="UNKNOWN9", raw_value="UNKNOWN9")
    assert pinout(comp) is None
    assert facts(comp) == {}


def test_get_pinout_effective_unit(kb_path: Path) -> None:
    db = ComponentDatabase.open(kb_path)
    assert db.get_pinout_effective("MM32F014X-QFP48") == {
        "1": "PC13",
        "2": "PC14",
    }
    assert db.get_pinout_effective("MM32F014X") == {"1": "PC13", "2": "PC14"}
    assert db.get_pinout_effective("UNKNOWN9") is None
    assert db.get_pinout_effective("") is None


def test_pin_name_resolver_fills_variant_pins(kb_path: Path) -> None:
    from circuit_graph.enrichers.pin_resolver import PinNameResolver
    from circuit_graph.ir import CircuitIR, Component, DeviceKind, Pin

    ir = CircuitIR(
        meta={"source": "test", "format": "manual"},
        components=[
            Component(
                id="U9",
                device_kind=DeviceKind.IC,
                part_number="MM32F014X-QFP48",
                raw_value="MM32F014X-QFP48",
                pins=[Pin(id="U9.1", number="1"), Pin(id="U9.2", number="2")],
            ),
        ],
        nets=[],
        connections=[],
    )
    out = PinNameResolver(db_path=kb_path).enrich(ir)
    assert [p.name for p in out.components[0].pins] == ["PC13", "PC14"]

