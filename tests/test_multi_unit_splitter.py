"""Tests for multi-unit IC splitting (e.g. LM358 dual op-amp)."""

from __future__ import annotations

import pytest

from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
from circuit_graph.ir import DeviceKind, NetType
from tests.fir_helpers import build_ir, make_component, make_net


def _lm358_ir() -> object:
    """Build a minimal IR with an LM358 dual op-amp.

    U2 unit B (pins 1/2/3) forms a differential amplifier with R4.
    U2 unit C (pins 5/6/7) forms a voltage follower (OUT直连IN-).
    """
    u2 = make_component(
        "U2",
        DeviceKind.IC,
        [
            ("1", "OUT1"), ("2", "IN1-"), ("3", "IN1+"),
            ("4", "V-"), ("8", "V+"),
            ("5", "IN2+"), ("6", "IN2-"), ("7", "OUT2"),
        ],
        part_number="LM358",
        footprint="SOP-8",
    )
    r4 = make_component(
        "R4",
        DeviceKind.RESISTOR,
        [("1", ""), ("2", "")],
        raw_value="100K",
    )

    nets = [
        make_net("N_VCC", "+5V", NetType.POWER),
        make_net("N_GND", "GND", NetType.GND),
        make_net("N_OUT1", "NetR4_1", NetType.SIGNAL),  # U2.1 and U2.5
        make_net("N_IN1-", "NetR4_2", NetType.SIGNAL),  # U2.2
        make_net("N_IN1+", "NetR6_2", NetType.SIGNAL),  # U2.3
        make_net("N_OUT2", "RFDIN1", NetType.SIGNAL),   # U2.6 and U2.7
    ]

    wiring = [
        ("U2.8", "N_VCC"),
        ("U2.4", "N_GND"),
        # Unit B (pins 1/2/3): differential amplifier
        ("U2.1", "N_OUT1"),
        ("U2.2", "N_IN1-"),
        ("U2.3", "N_IN1+"),
        ("R4.1", "N_OUT1"),
        ("R4.2", "N_IN1-"),
        # Unit C (pins 5/6/7): voltage follower
        ("U2.5", "N_OUT1"),  # IN2+ connected to OUT1
        ("U2.6", "N_OUT2"),  # IN2- connected to OUT2
        ("U2.7", "N_OUT2"),  # OUT2
    ]

    return build_ir([u2, r4], nets, wiring)


def test_multi_unit_splitter_splits_lm358() -> None:
    """MultiUnitSplitter splits LM358 into U2A (power), U2B (opamp1), U2C (opamp2)."""
    ir = _lm358_ir()
    ir2 = run_enrichers(ir, [MultiUnitSplitter()]).ir

    # Should have U2A, U2B, U2C, R4 (original U2 is replaced)
    comp_ids = {c.id for c in ir2.components}
    assert "U2A" in comp_ids
    assert "U2B" in comp_ids
    assert "U2C" in comp_ids
    assert "U2" not in comp_ids
    assert "R4" in comp_ids

    # U2A is power unit (V+, V-)
    u2a = next(c for c in ir2.components if c.id == "U2A")
    assert {p.number for p in u2a.pins} == {"V+", "V-"}

    # U2B is first opamp (OUT, IN-, IN+)
    u2b = next(c for c in ir2.components if c.id == "U2B")
    assert {p.number for p in u2b.pins} == {"OUT", "IN-", "IN+"}

    # U2C is second opamp (OUT, IN-, IN+)
    u2c = next(c for c in ir2.components if c.id == "U2C")
    assert {p.number for p in u2c.pins} == {"OUT", "IN-", "IN+"}


def test_multi_unit_splitter_rewrites_connections() -> None:
    """Connections are rewritten to use logical pin names (e.g. U2B.OUT)."""
    ir = _lm358_ir()
    ir2 = run_enrichers(ir, [MultiUnitSplitter()]).ir

    conn_map = {c.pin_id: c.net_id for c in ir2.connections}

    # U2B connections
    assert conn_map["U2B.OUT"] == "N_OUT1"
    assert conn_map["U2B.IN-"] == "N_IN1-"
    assert conn_map["U2B.IN+"] == "N_IN1+"

    # U2C connections
    assert conn_map["U2C.OUT"] == "N_OUT2"
    assert conn_map["U2C.IN-"] == "N_OUT2"
    assert conn_map["U2C.IN+"] == "N_OUT1"


def test_topology_recognizer_identifies_u2b_as_differential_amplifier() -> None:
    """U2B (pins 1/2/3) with Rf is recognized as differential-amplifier."""
    ir = _lm358_ir()
    ir2 = run_enrichers(ir, [MultiUnitSplitter(), TopologyRecognizer()]).ir

    blocks = {b.id: b for b in ir2.blocks}
    assert "opamp-unit-differential-amplifier-u2b" in blocks
    block = blocks["opamp-unit-differential-amplifier-u2b"]
    assert set(block.component_ids) == {"U2B", "R4"}


def test_topology_recognizer_identifies_u2c_as_voltage_follower() -> None:
    """U2C (pins 5/6/7) with OUT直连IN- is recognized as voltage-follower."""
    ir = _lm358_ir()
    ir2 = run_enrichers(ir, [MultiUnitSplitter(), TopologyRecognizer()]).ir

    blocks = {b.id: b for b in ir2.blocks}
    assert "opamp-unit-voltage-follower-u2c" in blocks
    block = blocks["opamp-unit-voltage-follower-u2c"]
    assert set(block.component_ids) == {"U2C"}


def test_multi_unit_splitter_ignores_non_multi_unit_devices() -> None:
    """Components not in multi_unit_devices.json are not split."""
    r1 = make_component("R1", DeviceKind.RESISTOR, [("1", ""), ("2", "")])
    gnd = make_net("N_GND", "GND", NetType.GND)
    sig = make_net("N_SIG", "SIG", NetType.SIGNAL)
    ir = build_ir([r1], [gnd, sig], [("R1.1", "N_GND"), ("R1.2", "N_SIG")])

    ir2 = run_enrichers(ir, [MultiUnitSplitter()]).ir

    # R1 should not be split
    assert len(ir2.components) == 1
    assert ir2.components[0].id == "R1"
