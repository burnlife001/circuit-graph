import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import _parse_and_enrich, main

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "protel"


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_graph_graphml(runner: CliRunner, tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    output = tmp_path / "t268.graphml"
    result = runner.invoke(main, ["export", "graphml", str(fixture), "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert output.exists()
    assert "Wrote GraphML" in result.output


def test_cli_export_graph(runner: CliRunner, tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    output = tmp_path / "t268.graph.json"
    result = runner.invoke(main, ["export", "graph", str(fixture), "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert output.exists()
    assert "Wrote cg-graph JSON" in result.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["format"] == "cg-graph"
    assert "R70" in data["components"]


def test_cli_view_component(runner: CliRunner, tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    result = runner.invoke(main, ["view", "R70", str(fixture)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["designator"] == "R70"
    assert data["nets"]["PGND"]["kind"] == "ground"
    # Without --with-power / --with-comm, the optional context fields are
    # absent so default output stays lean.
    assert "power_context" not in data
    assert "comm_context" not in data


def test_cli_view_with_power(runner: CliRunner, tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    # U2 is the main MCU on T268; it sits on the 5V2 / GND rails.
    result = runner.invoke(main, ["view", "U2", str(fixture), "--with-power"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "power_context" in data
    ctx = data["power_context"]
    # T268 fixture does not annotate regulator part numbers, so the source
    # is unknown but the rail name is still surfaced.
    assert ctx["rail"] in {"5V2", "5V", "3.3V"}
    assert isinstance(ctx["upstream_regulators"], list)


def test_cli_view_without_power_flag_omits_context(runner: CliRunner) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    result = runner.invoke(main, ["view", "U2", str(fixture)])
    data = json.loads(result.output)
    assert "power_context" not in data


def test_cli_view_with_comm(runner: CliRunner, tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    # U2 is the main MCU on T268; it sits on I2C / UART / PWM buses.
    result = runner.invoke(main, ["view", "U2", str(fixture), "--with-comm"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "comm_context" in data
    assert "buses" in data["comm_context"]
    assert isinstance(data["comm_context"]["buses"], list)
    assert len(data["comm_context"]["buses"]) >= 1


def test_cli_view_without_comm_flag_omits_context(runner: CliRunner) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    result = runner.invoke(main, ["view", "U2", str(fixture)])
    data = json.loads(result.output)
    assert "comm_context" not in data


def test_cli_view_with_all(runner: CliRunner, tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    result = runner.invoke(main, ["view", "U2", str(fixture), "--with-all"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "power_context" in data
    assert "comm_context" in data
    assert "blocks" in data


def test_cli_view_with_blocks(runner: CliRunner) -> None:
    """--with-blocks lists functional blocks touching the ego subgraph."""
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    # R100 is the feedback resistor of the U7 (LM321) amplifier stage.
    result = runner.invoke(main, ["view", "R100", str(fixture), "--with-blocks"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    blocks = {b["id"]: b for b in data["blocks"]}
    assert "inverting-amplifier-u7" in blocks
    assert {"R100", "R101", "U7"} <= set(blocks["inverting-amplifier-u7"]["component_ids"])
    assert "voltage-divider-r100" in blocks


def test_cli_view_without_blocks_flag_omits_context(runner: CliRunner) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    result = runner.invoke(main, ["view", "R100", str(fixture)])
    data = json.loads(result.output)
    assert "blocks" not in data


def test_cli_view_with_blocks_empty_when_unrecognised(runner: CliRunner) -> None:
    """A component with no recognised blocks gets an empty list, not a missing key."""
    fixture = FIXTURE_DIR / "433_RX.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    # E1 is the 433MHz antenna pin header; no primitive covers it.  hops=0
    # keeps the ego to E1 alone so no neighbourhood block spills in.
    result = runner.invoke(main, ["view", "E1", str(fixture), "--hops", "0", "--with-blocks"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["blocks"] == []


def test_cli_view_component_not_on_power_net(runner: CliRunner) -> None:
    """A component on a signal-only net has no `power_context`."""
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    # R70 sits on PGND + CC2 (signal).  Neither is a POWER-typed net, so
    # power_context is omitted.
    result = runner.invoke(main, ["view", "R70", str(fixture), "--with-power"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "power_context" not in data


def test_cli_view_component_with_no_comm_bus(runner: CliRunner) -> None:
    """A non-bus component has no `comm_context`."""
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    # R70 is a power-path resistor; it should not be on a comm bus.
    result = runner.invoke(main, ["view", "R70", str(fixture), "--with-comm"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "comm_context" not in data


def test_cli_view_missing_component(runner: CliRunner, tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    result = runner.invoke(main, ["view", "MISSING", str(fixture)])
    assert result.exit_code == 1
    assert "not found" in result.output


def _split_units(ir, designator: str) -> dict[str, str]:
    """Return ``{unit_id: unit_type}`` for the units split from ``designator``.

    Expectations are derived from the enriched IR itself so the multi-unit
    tests follow whatever unit letters the device table assigns (LM358 →
    A/B/C, LM324 → A..E) instead of hardcoding e.g. U2B.
    """
    return {
        c.id: c.metadata.get("unit_type")
        for c in ir.components
        if c.metadata.get("split_from") == designator
    }


def test_cli_view_multi_unit_parent_lists_split_units(runner: CliRunner) -> None:
    """`cg view U2` on a split multi-unit device must list the split units.

    U2 in add-primitives.NET is a multi-unit op-amp; MultiUnitSplitter turns
    it into per-unit virtual components, so the bare designator no longer
    exists.  The error must name every unit, annotate the power unit, and
    suggest a concrete non-power unit to view.
    """
    fixture = FIXTURE_DIR / "add-primitives.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    units = _split_units(_parse_and_enrich(fixture), "U2")
    assert len(units) >= 2, f"U2 was not split into units; got {sorted(units)}"

    result = runner.invoke(main, ["view", "U2", str(fixture)])
    assert result.exit_code == 1
    assert "Component U2 not found" in result.output
    assert "已拆分为多单元" in result.output
    for unit_id, unit_type in units.items():
        expected = f"{unit_id}(电源)" if unit_type == "power" else unit_id
        assert expected in result.output
    match = re.search(r"请用 cg view (\S+) 查看", result.output)
    assert match, f"no unit suggestion in: {result.output}"
    suggested = match.group(1)
    assert suggested in units
    assert units[suggested] != "power"


def test_cli_view_multi_unit_with_blocks(runner: CliRunner) -> None:
    """`cg view <unit> --with-blocks` hits the opamp-unit block after splitting.

    The unit anchoring the differential amplifier depends on the device
    (LM358 → B/C, LM324 → any of A..D), so it is discovered from the
    enriched IR rather than hardcoded.
    """
    fixture = FIXTURE_DIR / "add-primitives.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = _parse_and_enrich(fixture)
    units = _split_units(ir, "U2")
    blocks = [
        b for b in ir.blocks if b.id.startswith("opamp-unit-differential-amplifier-")
    ]
    assert blocks, (
        f"no opamp-unit-differential-amplifier block; "
        f"got: {sorted(b.id for b in ir.blocks)}"
    )
    block = blocks[0]
    focal = next(u for u in sorted(units) if u in block.component_ids)

    result = runner.invoke(main, ["view", focal, str(fixture), "--with-blocks"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["designator"] == focal
    assert block.id in {b["id"] for b in data["blocks"]}


def test_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "view" in result.output
    assert "export" in result.output
    # power / comm are no longer top-level commands — they are flags under view.
    assert "power" not in result.output
    assert "comm " not in result.output
    assert "generate" not in result.output
    assert "query" not in result.output


def test_cli_default_pipeline_resolves_pin_names(tmp_path: Path, monkeypatch) -> None:
    """`cg view`'s default enricher pipeline must run ``PinNameResolver``.

    Regression for YG-6: prior to the fix, ``_parse_and_enrich`` left every
    non-passive component's ``pin.name`` as ``None`` even when the KB had a
    matching pinout because ``PinNameResolver`` was neither imported nor
    added to the enricher chain.
    """
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    # Seed a tiny KB so PinNameResolver has something to look up.  U1 in
    # the T268 fixture is a Holtek HT7533 — feed it the real SOT-89 pin map.
    # The netlist stores the part-number as "HT7533 -23" (with the trailing
    # "-23" suffix the package variant), which is the literal lookup key
    # PinNameResolver uses.
    from circuit_graph.enrichers import pin_resolver
    from circuit_graph.knowledge.db import ComponentDatabase

    db_path = tmp_path / "kb.db"
    db = ComponentDatabase.open(db_path)
    try:
        db._upsert_component("HT7533 -23", manufacturer="Holtek", category="ldo", package="SOT89")
        comp_id = db.get_component("HT7533 -23").id  # type: ignore[union-attr]
        for n, name in [("1", "VIN"), ("2", "GND"), ("3", "VOUT")]:
            db.conn.execute(
                "INSERT INTO pinouts (component_id, pin_number, pin_name, source_ref) "
                "VALUES (?, ?, ?, NULL)",
                (comp_id, n, name),
            )
        db.conn.commit()
    finally:
        db.close()

    monkeypatch.setattr(pin_resolver.ComponentDatabase, "_DEFAULT_DB_PATH", db_path, raising=False)
    # Also patch the imported module-level reference used by `default()`.
    import circuit_graph.knowledge.db as kb_db_mod

    monkeypatch.setattr(kb_db_mod, "_DEFAULT_DB_PATH", db_path)

    ir = _parse_and_enrich(fixture)
    u1 = next(c for c in ir.components if c.id == "U1")
    # All three HT7533 pins must have their name filled in by the default
    # pipeline — the regression was that they were all None.
    pin_names = {p.number: p.name for p in u1.pins}
    assert pin_names == {"1": "VIN", "2": "GND", "3": "VOUT"}, pin_names


# ---------------------------------------------------------------------------
# KB Protel import


def test_cli_kb_import_protel_file(runner: CliRunner, tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    db_path = tmp_path / "kb.db"
    result = runner.invoke(
        main, ["kb", "--db-path", str(db_path), "import", "protel", str(fixture)]
    )
    assert result.exit_code == 0, result.output
    assert "Imported" in result.output
    assert "component(s) from" in result.output


def test_cli_kb_import_protel_directory(runner: CliRunner, tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    nets_dir = tmp_path / "nets"
    nets_dir.mkdir()
    # Copy the fixture into a temp directory so we exercise directory traversal.
    import shutil

    shutil.copy(fixture, nets_dir / "t268.NET")

    db_path = tmp_path / "kb.db"
    result = runner.invoke(
        main, ["kb", "--db-path", str(db_path), "import", "protel", str(nets_dir)]
    )
    assert result.exit_code == 0, result.output
    assert "Imported" in result.output
