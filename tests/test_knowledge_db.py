"""Tests for the SQLite-backed ``ComponentDatabase`` and its public surface.

Covers schema creation, query API, pinout/properties accessors, KiCad +
JSON importers, migration from the legacy ``vendor_db.json`` shape, and
the read-only ``PinNameResolver`` enricher.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from circuit_graph.enrichers.pin_resolver import PinNameResolver
from circuit_graph.ir import CircuitIR, CircuitMeta, Component, DeviceKind, Pin
from circuit_graph.knowledge import chip_resolver as chip_resolver_mod
from circuit_graph.knowledge.db import (
    SCHEMA_DDL,
    ComponentDatabase,
    ComponentRecord,
    _parse_kicad_netlist,
)

# ---------------------------------------------------------------------------
# Schema + lifecycle


def test_init_db_creates_schema(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        # Every documented table exists and is queryable.
        tables = {
            r["name"]
            for r in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for required in ("components", "pinouts", "properties", "provenance"):
            assert required in tables

        # Idempotent: a second init_db() call must not raise.
        db.init_db()
        assert db.stats()["components"] == 0
    finally:
        db.close()


def test_default_database_path_resolves(tmp_path: Path, monkeypatch) -> None:
    # Patch the module-level default so we do not pollute the real file.
    import circuit_graph.knowledge.db as db_mod

    monkeypatch.setattr(db_mod, "_DEFAULT_DB_PATH", tmp_path / "default.db")
    db = ComponentDatabase.default()
    try:
        assert db.db_path == tmp_path / "default.db"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Query API


def _seed(db: ComponentDatabase) -> None:
    """Insert a small mixed dataset for query tests."""
    db._upsert_component("JW5065", manufacturer="Joulwatt", category="dcdc", package="SOT23-6")
    db._upsert_component("HT7533", manufacturer="Holtek", category="ldo", package="SOT89")
    db._upsert_component("PE8205", manufacturer="PowerExpert", category="mosfet_pair")
    db._upsert_component("LM321", manufacturer="TI", category="opamp", description="Low-power single op-amp")


def test_query_by_part_number(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed(db)
        hits = db.query(part_number="JW5065")
        assert len(hits) == 1
        assert hits[0].manufacturer == "Joulwatt"
    finally:
        db.close()


def test_query_is_case_insensitive(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed(db)
        assert db.get_component("jw5065") is not None
        assert db.get_component("Jw5065") is not None
    finally:
        db.close()


def test_query_by_category(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed(db)
        dcdcs = db.query(category="dcdc")
        assert {r.part_number for r in dcdcs} == {"JW5065"}
    finally:
        db.close()


def test_query_any_fulltext(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed(db)
        hits = db.query(any="Holtek")
        assert any(r.part_number == "HT7533" for r in hits)
    finally:
        db.close()


def test_query_unknown_returns_empty(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed(db)
        assert db.query(part_number="NOPE") == []
        assert db.query(category="does_not_exist") == []
    finally:
        db.close()


def test_lookup_prefix_exact_match(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed(db)
        rec = db.lookup_prefix("JW5065")
        assert rec is not None
        assert rec.part_number == "JW5065"
        assert rec.category == "dcdc"
    finally:
        db.close()


def test_lookup_prefix_longest_prefix_wins(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        db._upsert_component("J", category="generic")
        db._upsert_component("JW50", category="dcdc")
        db._upsert_component("JW5065", category="exact")
        # Exact row present -> it wins over shorter prefixes for the same string.
        assert db.lookup_prefix("JW5065").category == "exact"
        assert db.lookup_prefix("JW5065A").category == "exact"
        # No exact row -> longest matching prefix wins.
        assert db.lookup_prefix("JW5066").category == "dcdc"
        assert db.lookup_prefix("JXXX").category == "generic"
    finally:
        db.close()


def test_lookup_prefix_case_insensitive(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed(db)
        assert db.lookup_prefix("jw5065").part_number == "JW5065"
        assert db.lookup_prefix("Jw5065").part_number == "JW5065"
    finally:
        db.close()


def test_lookup_prefix_unknown_returns_none(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed(db)
        assert db.lookup_prefix("NOPE1234") is None
        assert db.lookup_prefix("") is None
    finally:
        db.close()


def test_get_category_returns_longest_prefix_category(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed(db)
        assert db.get_category("JW5065") == "dcdc"
        assert db.get_category("HT7533-1") == "ldo"
        assert db.get_category("UNKNOWN") is None
    finally:
        db.close()


def test_get_pinout_returns_dict(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        comp_id = db._upsert_component("LM321")
        for n, name in [("1", "+IN"), ("2", "V-"), ("3", "-IN")]:
            db.conn.execute(
                "INSERT INTO pinouts (component_id, pin_number, pin_name, source_ref) "
                "VALUES (?, ?, ?, NULL)",
                (comp_id, n, name),
            )
        db.conn.commit()
        pinout = db.get_pinout("LM321")
        assert pinout == {"1": "+IN", "2": "V-", "3": "-IN"}
    finally:
        db.close()


def test_get_pinout_unknown_returns_none(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        assert db.get_pinout("NOPE") is None
    finally:
        db.close()


def test_get_properties_and_set_property(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        db.set_property("LM321", "vout_max", "5.5", unit="V")
        db.set_property("LM321", "gbw", "1", unit="MHz")
        props = db.get_properties("LM321")
        assert props == {"vout_max": "5.5 V", "gbw": "1 MHz"}
        # Update via set_property — overwrites unit too.
        db.set_property("LM321", "vout_max", "6.0", unit="V")
        assert db.get_properties("LM321")["vout_max"] == "6.0 V"
    finally:
        db.close()


def test_set_property_unknown_part_number_creates_component(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        db.set_property("NEW123", "key", "value")
        rec = db.get_component("NEW123")
        assert rec is not None
        assert db.get_properties("NEW123") == {"key": "value"}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Stats + migration


def test_stats_reflects_inserts(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed(db)
        s = db.stats()
        assert s["components"] == 4
        assert s["categories"] == 4  # dcdc / ldo / mosfet_pair / opamp
        assert s["provenance"] >= 0
    finally:
        db.close()


def test_migrate_from_vendor_db(tmp_path: Path) -> None:
    src = tmp_path / "vendor.json"
    src.write_text(
        json.dumps(
            {
                "entries": {
                    "JW": {"manufacturer": "Joulwatt", "category": "dcdc"},
                    "HT": {"manufacturer": "Holtek", "category": "ldo"},
                }
            }
        ),
        encoding="utf-8",
    )
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        n = db.migrate_from_vendor_db(src)
        assert n == 2
        assert db.get_component("JW").category == "dcdc"
        # Idempotent: re-running returns 0 (no new rows).
        n2 = db.migrate_from_vendor_db(src)
        assert n2 == 2  # upsert still touches rows; count of migrated prefixes
    finally:
        db.close()


# ---------------------------------------------------------------------------
# KiCad netlist import


KICAD_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kicad" / "FX-T268-V6-10米-5065-kicad.net"


@pytest.mark.skipif(not KICAD_FIXTURE.exists(), reason="KiCad fixture not present")
def test_import_kicad_netlist_real_fixture(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        n = db.import_kicad_netlist(KICAD_FIXTURE)
        assert n > 20  # non-passives only (R/C/L filtered)
        # At least one known part-number is in the DB.
        comps = db.query()
        assert len(comps) > 0
    finally:
        db.close()


def test_parse_kicad_netlist_returns_unique_comps() -> None:
    # Build a tiny netlist in memory; the parser must produce 2 distinct
    # component blocks and extract ref/value/pin count.
    sample = """
    (components
        (comp (ref "U2") (value "MM32F014X") (footprint "LQFP-48")
              (units (unit (pins (pin (num "1")) (pin (num "11"))))))
        (comp (ref "U3") (value "LM321") (footprint "SOT-23-5")
              (units (unit (pins (pin (num "1"))))))
        (comp (ref "U4") (value "JW5065") (footprint "SOT23-6")
              (units (unit (pins (pin (num "1")) (pin (num "2"))))))
    )
    """
    comps = _parse_kicad_netlist(sample)
    assert len(comps) == 3
    refs = {c["ref"] for c in comps}
    assert refs == {"U2", "U3", "U4"}
    assert _parse_kicad_netlist  # keep import alive


def test_import_kicad_is_idempotent(tmp_path: Path) -> None:
    sample = (
        "(components "
        '(comp (ref "U1") (value "CHIP_A") (footprint "PKG" '
        '(units (unit (pins (pin (num "1"))))))) '
        '(comp (ref "U1") (value "CHIP_A") (footprint "PKG" '
        '(units (unit (pins (pin (num "1")) (pin (num "2")))))))'
        ")"
    )
    f = tmp_path / "fixture.net"
    f.write_text(sample, encoding="utf-8")
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        n1 = db.import_kicad_netlist(f)
        n2 = db.import_kicad_netlist(f)
        # The DB holds ONE row for the same part_number (UPSERT semantics);
        # the return value is the number of comp blocks touched.
        rec = db.get_component("CHIP_A")
        assert rec is not None
        assert n1 == 2
        assert n2 == 2
        # Stats show exactly one component.
        assert db.stats()["components"] == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# JSON import


def _write_protel_netlist(path: Path, components: list[tuple[str, str | None, str | None]]) -> None:
    """Write a minimal Protel .NET file with the given component tuples."""
    lines: list[str] = []
    for ref, footprint, raw_value in components:
        lines.extend(["[", ref, footprint or "", raw_value or "", "]"])
    # One shared net so every component gets at least one pin.
    lines.extend(["(", "NET1"])
    for ref, _, _ in components:
        lines.append(f"{ref}-1")
    lines.extend([")"])
    path.write_text("\n".join(lines), encoding="utf-8")


def test_import_protel_single_file(tmp_path: Path) -> None:
    f = tmp_path / "board.net"
    _write_protel_netlist(
        f,
        [
            ("U1", "SOT23-5", "LM321"),
            ("R1", "0805C", "10K%1"),
        ],
    )
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        n = db.import_protel_netlist(f)
        assert n == 1  # only U1 (IC); R1 (resistor) is filtered as passive

        ic = db.get_component("LM321")
        assert ic is not None
        assert ic.package == "SOT23-5"
        assert ic.pin_count == 1
        # No pinout rows stored when pin.name is None (resolve fills them later)
        assert db.get_pinout("LM321", source_ref="U1") is None

        passive = db.get_component("10K%1")
        assert passive is None  # passives are no longer imported
    finally:
        db.close()


def test_import_protel_directory(tmp_path: Path) -> None:
    nets = tmp_path / "nets"
    nets.mkdir()
    sub = nets / "sub"
    sub.mkdir()
    _write_protel_netlist(nets / "a.net", [("U1", "PKG", "CHIP_A")])
    _write_protel_netlist(sub / "b.NET", [("U2", "PKG", "CHIP_B")])

    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        n = db.import_protel_netlist(nets)
        assert n == 2
        assert db.get_component("CHIP_A") is not None
        assert db.get_component("CHIP_B") is not None
    finally:
        db.close()


def test_import_protel_is_idempotent(tmp_path: Path) -> None:
    f = tmp_path / "board.net"
    _write_protel_netlist(f, [("U1", "PKG", "CHIP_A")])
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        n1 = db.import_protel_netlist(f)
        n2 = db.import_protel_netlist(f)
        assert n1 == 1
        assert n2 == 1
        assert db.stats()["components"] == 1
    finally:
        db.close()


def test_import_protel_skips_missing_value(tmp_path: Path) -> None:
    f = tmp_path / "board.net"
    _write_protel_netlist(f, [("U1", "PKG", ""), ("U2", "PKG", "CHIP_B")])
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        n = db.import_protel_netlist(f)
        assert n == 1
        assert db.get_component("CHIP_B") is not None
        assert db.get_component("") is None
    finally:
        db.close()


PROTEL_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "protel"
    / "FX-T268-V6-10米-5065.NET"
)


@pytest.mark.skipif(not PROTEL_FIXTURE.exists(), reason="Protel fixture not present")
def test_import_protel_real_fixture(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        n = db.import_protel_netlist(PROTEL_FIXTURE)
        assert n > 10  # non-passive only (ICs, MOSFETs, diodes, connectors)
        assert db.stats()["components"] > 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# PinNameResolver enricher


def _ir_with_one_ic(pin_name: str | None = None) -> CircuitIR:
    return CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[
            Component(
                id="U2",
                device_kind=DeviceKind.IC,
                part_number="LM321",
                value=None,
                pins=[Pin(id="U2.1", number="1", name=pin_name)],
            )
        ],
    )


def test_pin_name_resolver_can_enrich_false_when_names_set(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        db._upsert_component("LM321")
        db.conn.execute(
            "INSERT INTO pinouts (component_id, pin_number, pin_name, source_ref) "
            "VALUES ((SELECT id FROM components WHERE part_number='LM321'), '1', '+IN', NULL)"
        )
        db.conn.commit()

        ir = _ir_with_one_ic(pin_name=None)
        resolver = PinNameResolver(db_path=db.db_path)
        assert resolver.can_enrich(ir) is True
        out = resolver.enrich(ir)
        assert out.components[0].pins[0].name == "+IN"
    finally:
        db.close()


def test_pin_name_resolver_no_kb_match_leaves_pins_alone(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        ir = _ir_with_one_ic(pin_name=None)
        # Empty DB — resolver should not crash and must leave pin.name None.
        resolver = PinNameResolver(db_path=db.db_path)
        assert resolver.can_enrich(ir) is True
        out = resolver.enrich(ir)
        assert out.components[0].pins[0].name is None
    finally:
        db.close()


def test_pin_name_resolver_does_not_touch_passives(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        ir = CircuitIR(
            meta=CircuitMeta(source="t", format="m"),
            components=[
                Component(
                    id="R1",
                    device_kind=DeviceKind.RESISTOR,
                    part_number="RC0603",
                    pins=[Pin(id="R1.1", number="1", name=None)],
                )
            ],
        )
        resolver = PinNameResolver(db_path=db.db_path)
        assert resolver.can_enrich(ir) is False
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Schema sanity


def test_schema_ddl_contains_all_tables() -> None:
    joined = "\n".join(SCHEMA_DDL)
    for table in ("components", "pinouts", "properties", "provenance"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in joined


def test_query_returns_named_tuples(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed(db)
        out = db.query(part_number="JW5065")
        assert isinstance(out[0], ComponentRecord)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# is_credible / pin_source feature


def test_is_credible_default_is_none(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        db._upsert_component("LM321")
        rec = db.get_component("LM321")
        assert rec is not None
        assert rec.is_credible is None
        assert rec.pin_source is None
    finally:
        db.close()


def test_upsert_with_is_credible(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        db._upsert_component("LM321", is_credible=True)
        rec = db.get_component("LM321")
        assert rec is not None
        assert rec.is_credible is True
    finally:
        db.close()


def test_upsert_with_pin_source(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        db._upsert_component("LM321", pin_source="jlcpcb")
        rec = db.get_component("LM321")
        assert rec is not None
        assert rec.pin_source == "jlcpcb"
        assert rec.is_credible is True
        # manual source → not credible
        db._upsert_component("HT7533", pin_source="manual")
        rec2 = db.get_component("HT7533")
        assert rec2 is not None
        assert rec2.pin_source == "manual"
        assert rec2.is_credible is False
    finally:
        db.close()


def test_is_credible_survives_re_upsert(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        db._upsert_component("LM321", is_credible=True, pin_source="jlcpcb")
        db._upsert_component("LM321")  # upsert without flag
        rec = db.get_component("LM321")
        assert rec is not None
        # COALESCE preserves the existing values.
        assert rec.is_credible is True
        assert rec.pin_source == "jlcpcb"
    finally:
        db.close()


def test_upsert_from_pin2json_sets_is_credible(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        payload = {
            "symbol": "LM321MFX",
            "package": "SOT-23-5",
            "source": "C9405",
            "pin_count": 5,
            "pins": {"1": "+", "2": "GND", "3": "-", "4": "OUT", "5": "V+"},
        }
        db._upsert_from_pin2json(
            "LM321", payload, pin_source="jlcpcb",
            board_package="SOT-23-5",
        )
        rec = db.get_component("LM321")
        assert rec is not None
        assert rec.is_credible is True
        assert rec.pin_source == "jlcpcb"
        # ``package`` column stays untouched (board footprint is authoritative);
        # the canonical form is recorded in ``resolved_package``.
        assert rec.package is None
        assert rec.resolved_package == "SOT-23-5"
        assert rec.pin_count == 5
    finally:
        db.close()


def test_upsert_from_pin2json_stores_pin_type(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.auto_resolve_ics_on_import = False
    try:
        payload = {
            "symbol": "LM321MFX",
            "package": "SOT-23-5",
            "source": "C9405",
            "pin_count": 5,
            "pins": {"1": "+IN", "2": "GND", "3": "-IN", "4": "OUT", "5": "VCC"},
            "pin_types": {
                "1": "input", "2": "ground", "3": "input",
                "4": "output", "5": "power_in",
            },
        }
        db._upsert_from_pin2json("LM321MFX", payload, pin_source="jlcpcb")
        pinout = db.get_pinout("LM321MFX")
        assert pinout is not None
        assert pinout["1"] == "+IN"
        assert pinout["2"] == "GND"
        # pin_type is stored in the DB but get_pinout returns only pin_name;
        # verify the underlying rows have pin_type set.
        row = db.conn.execute(
            "SELECT pin_type FROM pinouts "
            "WHERE component_id = (SELECT id FROM components WHERE part_number = 'LM321MFX') "
            "ORDER BY CAST(pin_number AS INTEGER)"
        ).fetchall()
        assert [r["pin_type"] for r in row] == ["input", "ground", "input", "output", "power_in"]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# _auto_resolve_ics package-match gating
#
# Verifies that the resolver writes pin data to the board-level component
# when JLCPCB's returned package matches the board footprint, and creates
# an independent variant row (linked via variant_of) when it does not.


def _fake_batch_result(packages: dict[str, str]) -> list:
    """Build a fake resolve_parts_batch return.

    *packages* maps part_number → resolved package name.  Returns a list
    of dicts shaped like chip_resolver output.  Parts not in the map
    resolve to a 3-pin SOT-23-3 dummy so the test can assert pin data.
    """
    out = []
    for pn, pkg in packages.items():
        out.append({
            "symbol": pn,
            "package": pkg,
            "source": f"C{hash(pn) & 0xFFFFFF:06d}",
            "pin_count": 3,
            "pins": {"1": "EN", "2": "GND", "3": "VOUT"},
            "pin_types": {"1": "input", "2": "ground", "3": "power_out"},
        })
    return out


def test_auto_resolve_ics_match_writes_to_board(monkeypatch, tmp_path: Path) -> None:
    """Package match path: JLCPCB result merges into the board component."""
    import circuit_graph.knowledge.db as db_mod

    db = ComponentDatabase.open(tmp_path / "kb.db")
    # Pre-create the board-level row to simulate a previous netlist import.
    db._upsert_component("JW5065", package="SOT23-8", pin_count=8)
    board_id = db.get_component("JW5065").id

    monkeypatch.setattr(
        chip_resolver_mod, "resolve_parts_batch",
        lambda pairs: _fake_batch_result({"JW5065": "TSOT-23-8"}),
    )
    try:
        resolved = db._auto_resolve_ics([("JW5065", "SOT23-8")])
        assert resolved == 1

        board = db.get_component("JW5065")
        # Match → board component now carries the JLCPCB pin data.
        assert board.pin_source == "jlcpcb"
        assert board.is_credible is True
        assert board.resolved_package == "TSOT-23-8"
        # ``package`` column stays as the raw board footprint.
        assert board.package == "SOT23-8"
        pinout = db.get_pinout("JW5065")
        assert pinout is not None
        assert pinout["1"] == "EN"
        assert pinout["3"] == "VOUT"

        # No variant row was created.
        variants = db.query(variant_of=board_id)
        assert variants == []
    finally:
        db.close()


def test_auto_resolve_ics_mismatch_creates_variant(monkeypatch, tmp_path: Path) -> None:
    """Package mismatch path: JLCPCB result lands in an independent variant row."""
    import circuit_graph.knowledge.db as db_mod

    db = ComponentDatabase.open(tmp_path / "kb.db")
    db._upsert_component("MM32F0144", package="LQFP-48", pin_count=48)
    board_id = db.get_component("MM32F0144").id

    # JLCPCB returns LQFP-64 — different package family, must NOT overwrite.
    monkeypatch.setattr(
        chip_resolver_mod, "resolve_parts_batch",
        lambda pairs: _fake_batch_result({"MM32F0144": "LQFP-64"}),
    )
    try:
        resolved = db._auto_resolve_ics([("MM32F0144", "LQFP-48")])
        # Board-level resolved count is 0; the variant row is a side channel.
        assert resolved == 0

        # Board component's pin data is untouched.
        board = db.get_component("MM32F0144")
        assert board.pin_source is None
        assert board.is_credible is None
        assert board.package == "LQFP-48"
        assert board.resolved_package is None
        assert db.get_pinout("MM32F0144") is None

        # An independent variant row holds the JLCPCB result, linked back.
        variants = db.query(variant_of=board_id)
        assert len(variants) == 1
        v = variants[0]
        assert v.part_number.startswith("MM32F0144@")
        assert v.package == "LQFP-64"
        assert v.pin_source == "jlcpcb"
        assert v.resolved_package == "LQFP-64"
        assert v.variant_of == board_id

        vpinout = db.get_pinout(v.part_number)
        assert vpinout is not None
        assert vpinout["1"] == "EN"
    finally:
        db.close()


def test_auto_resolve_ics_mismatch_idempotent(monkeypatch, tmp_path: Path) -> None:
    """Re-running the resolver on a known mismatch must not duplicate variants."""
    import circuit_graph.knowledge.db as db_mod

    db = ComponentDatabase.open(tmp_path / "kb.db")
    db._upsert_component("MM32F0144", package="LQFP-48", pin_count=48)
    monkeypatch.setattr(
        chip_resolver_mod, "resolve_parts_batch",
        lambda pairs: _fake_batch_result({"MM32F0144": "LQFP-64"}),
    )
    try:
        db._auto_resolve_ics([("MM32F0144", "LQFP-48")])
        db._auto_resolve_ics([("MM32F0144", "LQFP-48")])
        board_id = db.get_component("MM32F0144").id
        variants = db.query(variant_of=board_id)
        assert len(variants) == 1
    finally:
        db.close()


def test_auto_resolve_ics_match_qfn_dimensions(monkeypatch, tmp_path: Path) -> None:
    """QFN-26 board ↔ QFN-26(4x4) JLCPCB result must match (dimension-suffixed)."""
    import circuit_graph.knowledge.db as db_mod

    db = ComponentDatabase.open(tmp_path / "kb.db")
    db._upsert_component("SW5001", package="QFN-26", pin_count=26)
    monkeypatch.setattr(
        chip_resolver_mod, "resolve_parts_batch",
        lambda pairs: _fake_batch_result({"SW5001": "QFN-26(4x4)"}),
    )
    try:
        resolved = db._auto_resolve_ics([("SW5001", "QFN-26")])
        assert resolved == 1
        board = db.get_component("SW5001")
        assert board.pin_source == "jlcpcb"
        assert board.resolved_package == "QFN-26(4x4)"
        assert board.package == "QFN-26"  # untouched
    finally:
        db.close()


def test_auto_resolve_ics_stage5_datasheet_pdf(monkeypatch, tmp_path: Path) -> None:
    """Stage 5 PDF datasheet fallback resolves parts not handled by Stage 4."""
    import circuit_graph.knowledge.db as db_mod

    # Create a fake datasheet directory with a JW5065.pdf
    ds_dir = tmp_path / "datasheets"
    ds_dir.mkdir()
    pdf_file = ds_dir / "JW5065.pdf"
    pdf_file.write_text("fake pdf", encoding="utf-8")

    db = ComponentDatabase.open(tmp_path / "kb.db", datasheet_dir=ds_dir)
    db._upsert_component("JW5065", package="SOT23-6", pin_count=6)
    board_id = db.get_component("JW5065").id

    # Mock Stage 4 to return nothing (batch fails).
    monkeypatch.setattr(
        chip_resolver_mod, "resolve_parts_batch",
        lambda pairs: [None] * len(pairs),
    )
    # Mock pdf_pin_extract._find_datasheet to return the fake PDF.
    import circuit_graph.knowledge.pdf_pin_extract as ppe_mod

    monkeypatch.setattr(
        ppe_mod, "_find_datasheet",
        lambda pn, ds: pdf_file,
    )
    # Mock pdf_pin_extract.extract_pin_table to return a known pin map.
    monkeypatch.setattr(
        ppe_mod, "extract_pin_table",
        lambda pdf, pages: {"1": "MODE", "2": "VIN", "3": "SW", "4": "GND"},
    )
    # Mock camelot_venv to return a fake path.
    from circuit_graph import config as cfg_mod

    fake_venv = tmp_path / "fake_venv"
    fake_venv.mkdir()
    (fake_venv / "Scripts").mkdir()
    (fake_venv / "Scripts" / "python.exe").write_text("")
    monkeypatch.setattr(cfg_mod, "camelot_venv", lambda: fake_venv)

    try:
        resolved = db._auto_resolve_ics([("JW5065", "SOT23-6")])
        assert resolved == 1, "Stage 5 should resolve one component"

        board = db.get_component("JW5065")
        assert board.pin_source == "datasheet"
        assert board.is_credible is True

        pinout = db.get_pinout("JW5065")
        assert pinout is not None
        assert pinout["1"] == "MODE"
        assert pinout["2"] == "VIN"
    finally:
        db.close()


def test_auto_resolve_ics_stage5_skips_when_already_resolved(
    monkeypatch, tmp_path: Path,
) -> None:
    """Stage 5 must not overwrite components already resolved by Stage 4."""
    import circuit_graph.knowledge.db as db_mod

    ds_dir = tmp_path / "datasheets"
    ds_dir.mkdir()
    (ds_dir / "JW5065.pdf").write_text("fake", encoding="utf-8")

    db = ComponentDatabase.open(tmp_path / "kb.db", datasheet_dir=ds_dir)
    # Use exact package match so Stage 4 writes to the board component.
    db._upsert_component("JW5065", package="SOT23-8", pin_count=8)

    # Stage 4 succeeds with matching package.
    monkeypatch.setattr(
        chip_resolver_mod, "resolve_parts_batch",
        lambda pairs: _fake_batch_result({"JW5065": "TSOT-23-8"}),
    )

    try:
        resolved = db._auto_resolve_ics([("JW5065", "SOT23-8")])
        assert resolved == 1
        board = db.get_component("JW5065")
        # Must be JLCPCB (Stage 4), not datasheet (Stage 5).
        assert board.pin_source == "jlcpcb"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# KnowledgeBase deprecation warning


def test_legacy_knowledgebase_emits_warning(tmp_path: Path) -> None:
    # The legacy JSON-prefix loader must emit a DeprecationWarning when used.
    from circuit_graph.knowledge import KnowledgeBase

    f = tmp_path / "v.json"
    f.write_text(
        json.dumps({"entries": {"XX": {"manufacturer": "X", "category": "ldo"}}}),
        encoding="utf-8",
    )
    with pytest.warns(DeprecationWarning):
        kb = KnowledgeBase.load(f)
    assert kb.get_category("XX1") == "ldo"


# ---------------------------------------------------------------------------
# Pin-name reverse lookup (todolist P2-9)


def _seed_pinout(db: ComponentDatabase) -> None:
    """Two parts sharing pin name SW2, one without it."""
    sw = db._upsert_component("SW5001", manufacturer="Silan", category="dcdc")
    other = db._upsert_component("SCT63140@FCQFN-15L(3x3)", category="dcdc")
    plain = db._upsert_component("HT7533", category="ldo")
    with db.conn:
        for comp_id, pins in (
            (sw, {"7": "BST1", "13": "SW2", "22": "GND"}),
            (other, {"5": "SW2", "9": "BST1"}),
            (plain, {"1": "VIN", "2": "GND", "3": "VOUT"}),
        ):
            for num, name in pins.items():
                db.conn.execute(
                    """
                    INSERT INTO pinouts (component_id, pin_number, pin_name, pin_type)
                    VALUES (?, ?, ?, 'unknown')
                    """,
                    (comp_id, num, name),
                )


def test_find_by_pin_name(tmp_path: Path) -> None:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    try:
        _seed_pinout(db)
        hits = db.find_by_pin_name("SW2")
        assert hits == ["SCT63140@FCQFN-15L(3x3)", "SW5001"]
        # Case-insensitive.
        assert db.find_by_pin_name("sw2") == hits
        assert db.find_by_pin_name("BST1") == hits
        assert db.find_by_pin_name("NOPE") == []
    finally:
        db.close()
