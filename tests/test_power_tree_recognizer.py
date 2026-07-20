"""Unit + integration tests for the power-tree recognizer."""

from __future__ import annotations

import pytest

from circuit_graph.graph import create_graph
from circuit_graph.ir import CircuitIR, DeviceKind, NetType
from circuit_graph.knowledge.db import ComponentDatabase
from circuit_graph.models.power_tree import PowerTree
from circuit_graph.power_tree_recognizer import (
    build_power_tree,
    find_source_nets,
    identify_regulator,
    identify_regulators,
)
from tests.fir_helpers import (
    build_ir,
    make_component,
    make_net,
)

KB_PATH = (
    pytest.importorskip("pathlib").Path(__file__).resolve().parents[1]
    / "circuit_graph"
    / "knowledge"
    / "vendor_db.json"
)


@pytest.fixture
def component_db(tmp_path: Path) -> ComponentDatabase:
    db = ComponentDatabase.open(tmp_path / "kb.db")
    db.migrate_from_vendor_db(KB_PATH)
    return db


# ---------------------------------------------------------------------------
# Helpers


def _jit_load(ir: CircuitIR):
    return create_graph().load(ir)


# ---------------------------------------------------------------------------
# Section 2 — Source discovery


def net_lookup_name(ir: CircuitIR, net_id: str) -> str:
    """Resolve a net id back to its human name; helper for source-list asserts."""
    for n in ir.nets:
        if n.id == net_id:
            return n.name
    return net_id


def test_find_source_nets_t268_excludes_downstream_rails(
    component_db: ComponentDatabase,
) -> None:
    """The real T268 fixture: ``USB_5V`` is a source, 5V/3.3V are not."""
    from pathlib import Path

    from circuit_graph.enrichers import run_enrichers
    from circuit_graph.enrichers.net_classifier import NetClassifier
    from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
    from circuit_graph.enrichers.value_normalizer import ValueNormalizer
    from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

    fixture = Path("fixtures/protel/FX-T268-V6-10米-5065.NET")
    if not fixture.exists():
        pytest.skip(f"fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(source_id=str(fixture), file_path=str(fixture)))
    ir = run_enrichers(ir, [ValueNormalizer(), NetClassifier(), TopologyRecognizer()]).ir
    graph = _jit_load(ir).graph
    sources = find_source_nets(ir, graph)
    names = [(s[0], s[1]) for s in sources]
    ids_to_names = {n.id: n.name for n in ir.nets}
    resolved = [(ids_to_names.get(net_id, net_id), kind) for net_id, kind in names]
    # USB_5V must be present and labelled as usb.
    assert any(k == "usb" for _net_name, k in resolved)
    # Downstream rails must not appear.
    for downstream in ("5V", "5V1", "5V2", "3.3V"):
        assert all(name != downstream for name, _kind in resolved)


def test_find_source_nets_excludes_mistagged_ground_by_name_pattern(
    component_db: ComponentDatabase,
) -> None:
    """Defensive: a net wrongly labelled POWER but named like ground must be filtered out.

    The NetType filter is the primary gate; this test exercises the *secondary*
    name-pattern gate, which must still hold even if NetClassifier regresses.
    """
    cgnd = make_net("N_CGND", "CGND", NetType.POWER)  # mis-tagged on purpose
    fake_vbus = make_net("N_VBUS", "VBUS", NetType.POWER)
    ir = build_ir(components=[], nets=[cgnd, fake_vbus], wiring=[])
    graph = _jit_load(ir).graph
    sources = find_source_nets(ir, graph)
    names = sorted(net_lookup_name(ir, net_id) for net_id, _kind in sources)
    assert names == ["VBUS"], f"CGND leaked through name-pattern gate: {names}"


# ---------------------------------------------------------------------------
# Section 3 — Regulator identification


def _synth_jw5065_ir() -> CircuitIR:
    """JW5065 buck on USB_5V→5V; inductor + caps are parts of the fixture."""
    jw = make_component(
        "U2",
        DeviceKind.IC,
        [("1", "VIN"), ("2", "EN"), ("3", "GND"), ("4", "SW"), ("5", "VOUT")],
        part_number="JW5065",
    )
    l1 = make_component("L1", DeviceKind.INDUCTOR, [("1", None), ("2", None)])
    c1 = make_component("C1", DeviceKind.CAPACITOR, [("1", None), ("2", None)], raw_value="22uF")
    c2 = make_component("C2", DeviceKind.CAPACITOR, [("1", None), ("2", None)], raw_value="22uF")

    usb_5v = make_net("N_USB5V", "USB_5V", NetType.POWER)
    sw_net = make_net("N_SW", "SW", NetType.POWER)
    five_v = make_net("N_5V", "5V", NetType.POWER)
    gnd = make_net("N_GND", "GND", NetType.GND)

    return build_ir(
        components=[jw, l1, c1, c2],
        nets=[usb_5v, sw_net, five_v, gnd],
        wiring=[
            ("U2.1", usb_5v.id),
            ("U2.2", usb_5v.id),
            ("U2.3", gnd.id),
            ("U2.4", sw_net.id),
            ("U2.5", five_v.id),
            ("L1.1", sw_net.id),
            ("L1.2", five_v.id),
            ("C1.1", sw_net.id),
            ("C1.2", gnd.id),
            ("C2.1", five_v.id),
            ("C2.2", gnd.id),
        ],
    )


def test_identify_jw5065_as_dcdc_buck(component_db: ComponentDatabase) -> None:
    ir = _synth_jw5065_ir()
    graph = _jit_load(ir).graph
    regs = [identify_regulator(c, graph, component_db) for c in ir.components]
    jw_reg = next(r for r in regs if r and r.component_id == "U2")
    assert jw_reg is not None
    assert jw_reg.kind == "dcdc_buck"
    assert jw_reg.input_net_id.endswith("USB_5V") or jw_reg.input_net_id == "N_USB5V"
    assert jw_reg.output_net_id.endswith("5V") or jw_reg.output_net_id == "N_5V"


def _synth_ht7533_ir() -> CircuitIR:
    """HT7533 LDO on 5V→3.3V."""
    ht = make_component(
        "U3",
        DeviceKind.IC,
        [("1", "VIN"), ("2", "GND"), ("3", "VOUT")],
        part_number="HT7533",
    )
    caps_in = make_component(
        "CIN", DeviceKind.CAPACITOR, [("1", None), ("2", None)], raw_value="1uF"
    )
    caps_out = make_component(
        "COUT", DeviceKind.CAPACITOR, [("1", None), ("2", None)], raw_value="1uF"
    )
    five_v = make_net("N_5V", "5V", NetType.POWER)
    three = make_net("N_3V3", "3.3V", NetType.POWER)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        components=[ht, caps_in, caps_out],
        nets=[five_v, three, gnd],
        wiring=[
            ("U3.1", five_v.id),
            ("U3.2", gnd.id),
            ("U3.3", three.id),
            ("CIN.1", five_v.id),
            ("CIN.2", gnd.id),
            ("COUT.1", three.id),
            ("COUT.2", gnd.id),
        ],
    )


def test_identify_ht7533_as_ldo(component_db: ComponentDatabase) -> None:
    ir = _synth_ht7533_ir()
    graph = _jit_load(ir).graph
    regs = [identify_regulator(c, graph, component_db) for c in ir.components]
    ht_reg = next(r for r in regs if r and r.component_id == "U3")
    assert ht_reg is not None
    assert ht_reg.kind == "ldo"
    assert ht_reg.input_net_id.endswith("5V") or ht_reg.input_net_id == "N_5V"
    assert ht_reg.output_net_id.endswith("3.3V") or ht_reg.output_net_id == "N_3V3"


def test_identify_regulator_returns_none_for_unknown_prefix(
    component_db: ComponentDatabase,
) -> None:
    ir = _synth_jw5065_ir()
    graph = _jit_load(ir).graph
    inductor = next(c for c in ir.components if c.id == "L1")
    assert inductor.part_number is None
    assert identify_regulator(inductor, graph, component_db) is None


# ---------------------------------------------------------------------------
# Section 4 — Tree assembly + integration with T268 path


def _synth_full_path_ir() -> CircuitIR:
    """USB_5V → JW5065 (buck) → 5V → HT7533 (LDO) → 3.3V, plus a load.

    Topology mirrors a real board: the DCDC output and the LDO input share
    the same 5V net, and bypass caps sit on that shared rail. Input/output
    caps are present so the LDO cap-count rule passes by default.
    """
    return build_ir(
        components=[
            make_component(
                "USB1",
                DeviceKind.CONNECTOR,
                [("1", "VBUS"), ("2", "GND"), ("3", "DP"), ("4", "DM")],
                part_number=None,
            ),
            make_component(
                "U_JW",
                DeviceKind.IC,
                [("1", "VIN"), ("2", "EN"), ("3", "GND"), ("4", "SW"), ("5", "VOUT")],
                part_number="JW5065",
            ),
            make_component(
                "U_HT",
                DeviceKind.IC,
                [("1", "VIN"), ("2", "GND"), ("3", "VOUT")],
                part_number="HT7533",
            ),
            make_component(
                "U_MCU",
                DeviceKind.IC,
                [("1", "VDD")],
                part_number="STM32G0",
            ),
            # Caps on the shared 5V rail (counted as both DCDC output cap and
            # LDO input cap — that's how real boards wire them) and on 3V3.
            make_component(
                "C_5V_1",
                DeviceKind.CAPACITOR,
                [("1", None), ("2", None)],
                raw_value="22uF",
            ),
            make_component(
                "C_5V_2",
                DeviceKind.CAPACITOR,
                [("1", None), ("2", None)],
                raw_value="1uF",
            ),
            make_component(
                "C_3V3_1",
                DeviceKind.CAPACITOR,
                [("1", None), ("2", None)],
                raw_value="1uF",
            ),
            make_component(
                "C_3V3_2",
                DeviceKind.CAPACITOR,
                [("1", None), ("2", None)],
                raw_value="100nF",
            ),
        ],
        nets=[
            make_net("N_USB", "USB_5V", NetType.POWER),
            make_net("N_SW", "SW", NetType.POWER),
            make_net("N_5V", "5V", NetType.POWER),
            make_net("N_3V3", "3.3V", NetType.POWER),
            make_net("N_GND", "GND", NetType.GND),
        ],
        wiring=[
            ("USB1.1", "N_USB"),
            ("USB1.2", "N_GND"),
            ("U_JW.1", "N_USB"),
            ("U_JW.2", "N_USB"),
            ("U_JW.3", "N_GND"),
            ("U_JW.4", "N_SW"),
            ("U_JW.5", "N_5V"),
            # Shared 5V rail: DCDC output feeds LDO input directly.
            ("U_HT.1", "N_5V"),
            ("U_HT.2", "N_GND"),
            ("U_HT.3", "N_3V3"),
            ("U_MCU.1", "N_3V3"),
            # Caps on the shared 5V rail.
            ("C_5V_1.1", "N_5V"),
            ("C_5V_1.2", "N_GND"),
            ("C_5V_2.1", "N_5V"),
            ("C_5V_2.2", "N_GND"),
            # Caps on the LDO output rail.
            ("C_3V3_1.1", "N_3V3"),
            ("C_3V3_1.2", "N_GND"),
            ("C_3V3_2.1", "N_3V3"),
            ("C_3V3_2.2", "N_GND"),
        ],
    )


def _strip_caps_on_net(ir: CircuitIR, net_id: str) -> CircuitIR:
    """Helper: return a copy of ``ir`` with every capacitor on ``net_id`` removed.

    Used by the cap-count regression tests below to assert the LDO actually
    depends on the cap rule rather than just on the pin names.
    """
    cap_ids_on_net: set[str] = set()
    for c in ir.connections:
        if c.net_id != net_id:
            continue
        comp_id, _, _ = c.pin_id.rpartition(".")
        for comp in ir.components:
            if comp.id == comp_id and comp.device_kind == DeviceKind.CAPACITOR:
                cap_ids_on_net.add(comp_id)
                break
    return CircuitIR(
        meta=ir.meta,
        components=[c for c in ir.components if c.id not in cap_ids_on_net],
        nets=ir.nets,
        connections=[
            c for c in ir.connections if c.pin_id.rpartition(".")[0] not in cap_ids_on_net
        ],
    )


def test_build_power_tree_t268_path(component_db: ComponentDatabase) -> None:
    """Tree must contain the canonical VBUS → JW5065 → 5V → HT7533 → 3.3V path."""
    ir = _synth_full_path_ir()
    graph = _jit_load(ir).graph
    tree = build_power_tree(ir, graph, component_db)

    # Sources are roots.
    assert tree.roots, "expected at least one source root"
    root_id = tree.roots[0]
    assert root_id.startswith("source::"), f"root should be a source, got {root_id}"

    # Locate U_JW and U_HT regulator nodes.
    jw_id = "regulator::U_JW"
    ht_id = "regulator::U_HT"
    assert jw_id in tree.nodes and ht_id in tree.nodes

    # The tree text must contain the canonical path labels.
    text = tree.to_text()
    assert "USB_5V" in text and "JW5065" in text
    assert "5V" in text and "HT7533" in text
    assert "3.3V" in text
    # The MCU load on 3.3V should appear as a leaf.
    assert "U_MCU" in text


def test_to_text_marker_prefixes(component_db: ComponentDatabase) -> None:
    ir = _synth_full_path_ir()
    graph = _jit_load(ir).graph
    tree = build_power_tree(ir, graph, component_db)
    text = tree.to_text()
    assert "[SRC]" in text
    assert "[REG]" in text
    assert "[NET]" in text
    assert "[LOAD]" in text


def test_to_text_emits_confidence_with_two_decimals(component_db: ComponentDatabase) -> None:
    """Spec §Scores are reported with fixed precision: regulator lines carry ``conf=0.NN``."""
    ir = _synth_full_path_ir()
    graph = _jit_load(ir).graph
    tree = build_power_tree(ir, graph, component_db)
    text = tree.to_text()
    # Each [REG] line must end with conf= and a two-decimal score.
    reg_lines = [ln for ln in text.splitlines() if "[REG]" in ln]
    assert reg_lines, "expected at least one regulator line"
    for ln in reg_lines:
        assert "conf=" in ln, f"missing confidence in: {ln!r}"
        score = ln.rsplit("conf=", 1)[1].strip()
        # Two decimals: "0.95", "0.75", "0.50" — never "0.9533..."
        assert "." in score and len(score.split(".")[1]) == 2, (
            f"confidence not two decimals: {score!r}"
        )


def test_identify_ldo_requires_input_and_output_caps(component_db: ComponentDatabase) -> None:
    """Spec §LDO input and output capacitors require at least one each.

    We strip *every* cap on the LDO input net and the LDO output net in turn;
    the LDO must drop out of the regulator list each time.
    """
    base = _synth_full_path_ir()
    base_graph = _jit_load(base).graph

    # Strip all caps on the shared 5V net (LDO input + DCDC output).
    ir_no_in_cap = _strip_caps_on_net(base, "N_5V")
    regs_no_in = identify_regulators(ir_no_in_cap, _jit_load(ir_no_in_cap).graph, component_db)
    assert "U_HT" not in [r.component_id for r in regs_no_in if r.kind == "ldo"], (
        "LDO must not be classified when its input net has no caps"
    )

    # Strip all caps on the 3V3 net (LDO output).
    ir_no_out_cap = _strip_caps_on_net(base, "N_3V3")
    regs_no_out = identify_regulators(ir_no_out_cap, _jit_load(ir_no_out_cap).graph, component_db)
    assert "U_HT" not in [r.component_id for r in regs_no_out if r.kind == "ldo"], (
        "LDO must not be classified when its output net has no caps"
    )

    # Sanity: with caps present the LDO IS classified.
    regs_ok = identify_regulators(base, base_graph, component_db)
    assert "U_HT" in [r.component_id for r in regs_ok if r.kind == "ldo"]


def test_identify_dcdc_does_not_require_caps(component_db: ComponentDatabase) -> None:
    """Spec §Buck DCDC only requires SW+VIN+VOUT pins; cap counting is LDO-specific.

    This guards against an over-eager refactor: even if the synthetic IR had
    no caps on the buck's input/output, JW5065 should still be classified.
    """
    ir = _synth_jw5065_ir()  # Has caps (C1, C2) but rule is independent
    graph = _jit_load(ir).graph
    regs = identify_regulators(ir, graph, component_db)
    jw = [r for r in regs if r.component_id == "U2"]
    assert jw, "JW5065 should still be classified as DCDC"
    assert jw[0].kind == "dcdc_buck"


def test_confidence_levels_kb_matched_vs_structural(component_db: ComponentDatabase) -> None:
    """Spec §Confidence and warnings: kb_matched + structural_complete → high."""
    ir = _synth_full_path_ir()
    graph = _jit_load(ir).graph
    regs = identify_regulators(ir, graph, component_db)
    jw = next(r for r in regs if r.component_id == "U_JW")
    ht = next(r for r in regs if r.component_id == "U_HT")
    # Both have KB match (JW/HT prefix) AND GND pin (structural complete).
    assert jw.confidence == "high"
    assert ht.confidence == "high"
    # The score is fixed-precision.
    assert jw.confidence_score == 0.95
    assert ht.confidence_score == 0.95


def test_power_tree_to_text_works_on_empty() -> None:
    tree = PowerTree()
    out = tree.to_text()
    assert "empty power tree" in out


def test_power_tree_returns_empty_when_no_sources(component_db: ComponentDatabase) -> None:
    """No connectors means no source nets — tree is empty but the call still succeeds."""
    ir = _synth_ht7533_ir()  # No connectors, all nets internal
    graph = _jit_load(ir).graph
    tree = build_power_tree(ir, graph, component_db)
    assert tree.roots == []


def test_build_power_tree_warns_unknown_regulator_ic(component_db: ComponentDatabase) -> None:
    """Spec §Confidence and warnings: an IC whose part_number is not in kb gets a warning."""
    # Add an IC with a part_number that the seed KB does not know about.
    base = _synth_full_path_ir()
    extra_ir = build_ir(
        components=[
            make_component(
                "U_MYSTERY",
                DeviceKind.IC,
                [("1", "VIN"), ("2", "GND"), ("3", "VOUT")],
                part_number="MYSTERY1234",
            ),
        ],
        nets=[
            make_net("N_USB2", "USB_5V_2", NetType.POWER),
            make_net("N_GND2", "GND", NetType.GND),
            make_net("N_OUT_M", "M_RAIL", NetType.POWER),
        ],
        wiring=[
            ("U_MYSTERY.1", "N_USB2"),
            ("U_MYSTERY.2", "N_GND2"),
            ("U_MYSTERY.3", "N_OUT_M"),
        ],
    )
    merged = CircuitIR(
        meta=base.meta,
        components=base.components + extra_ir.components,
        nets=base.nets + extra_ir.nets,
        connections=base.connections + extra_ir.connections,
    )
    graph = _jit_load(merged).graph
    tree = build_power_tree(merged, graph, component_db)
    assert any("U_MYSTERY" in w and "unclassified power IC" in w for w in tree.warnings), (
        f"expected unknown-regulator warning, got: {tree.warnings}"
    )
    # Also rendered into to_text().
    assert "# warning:" in tree.to_text()


def test_build_power_tree_warns_misclassified_ground_net(component_db: ComponentDatabase) -> None:
    """Spec §mis-tagged ground name-pattern gate emits a warning."""
    # CGND with NetType=POWER: filter out, but warn so the classifier can be audited.
    ir = build_ir(
        components=[
            make_component(
                "USB1",
                DeviceKind.CONNECTOR,
                [("1", "VBUS"), ("2", "GND")],
                part_number=None,
            ),
        ],
        nets=[
            make_net("N_USB", "USB_5V", NetType.POWER),
            make_net("N_CGND", "CGND", NetType.POWER),  # mis-tagged
        ],
        wiring=[
            ("USB1.1", "N_USB"),
            ("USB1.2", "N_CGND"),
        ],
    )
    graph = _jit_load(ir).graph
    tree = build_power_tree(ir, graph, component_db)
    assert any("mis-classified ground-like net" in w and "CGND" in w for w in tree.warnings), (
        f"expected ground-misclassification warning, got: {tree.warnings}"
    )
    # CGND must still NOT appear as a source (gating still works).
    source_names = [tree.nodes[r].label for r in tree.roots]
    assert not any("CGND" in s for s in source_names)
