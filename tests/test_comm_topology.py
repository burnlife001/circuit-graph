"""Tests for the communication topology recognizer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from circuit_graph.comm_topology import (
    CommBus,
    detect_comm_topology,
)
from circuit_graph.ir import DeviceKind, NetType
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from tests.fir_helpers import build_ir, make_component, make_net

T268_FIXTURE = Path("fixtures/protel/FX-T268-V6-10米-5065.NET")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_bus(buses: list[CommBus], protocol: str, bus_id: str) -> CommBus | None:
    for bus in buses:
        if bus.protocol == protocol and bus.id == bus_id:
            return bus
    return None


# ---------------------------------------------------------------------------
# Synthetic fixture tests
# ---------------------------------------------------------------------------


def test_detect_i2c_bus_requires_sda_and_scl() -> None:
    """A complete SDA+SCL pair with two ICs is detected as I2C."""
    mcu = make_component(
        "U1",
        DeviceKind.IC,
        [("1", "PB6"), ("2", "PB7"), ("3", "VCC")],
        part_number="STM32F103",
    )
    sensor = make_component("U2", DeviceKind.IC, [("1", "SDA"), ("2", "SCL"), ("3", "VCC")])
    sda = make_net("N_SDA", "SDA", NetType.SIGNAL)
    scl = make_net("N_SCL", "SCL", NetType.SIGNAL)
    vcc = make_net("N_VCC", "3.3V", NetType.POWER)
    ir = build_ir(
        components=[mcu, sensor],
        nets=[sda, scl, vcc],
        wiring=[
            ("U1.1", "N_SDA"),
            ("U1.2", "N_SCL"),
            ("U1.3", "N_VCC"),
            ("U2.1", "N_SDA"),
            ("U2.2", "N_SCL"),
            ("U2.3", "N_VCC"),
        ],
    )
    result = detect_comm_topology(ir)
    assert len(result.buses) == 1
    bus = result.buses[0]
    assert bus.protocol == "i2c"
    assert sorted(bus.nets) == ["SCL", "SDA"]
    assert bus.master_id == "U1"
    assert bus.slave_ids == ["U2"]
    assert bus.confidence == "high"


def test_detect_uart_bus_with_txd_rxd_aliases() -> None:
    """UART aliases TXD/RXD are accepted in addition to TX/RX."""
    mcu = make_component("U1", DeviceKind.IC, [("1", "TXD"), ("2", "RXD"), ("3", "VCC")])
    bridge = make_component("U2", DeviceKind.IC, [("1", "TXD"), ("2", "RXD"), ("3", "VCC")])
    txd = make_net("N_TXD", "TXD", NetType.SIGNAL)
    rxd = make_net("N_RXD", "RXD", NetType.SIGNAL)
    vcc = make_net("N_VCC", "3.3V", NetType.POWER)
    ir = build_ir(
        components=[mcu, bridge],
        nets=[txd, rxd, vcc],
        wiring=[
            ("U1.1", "N_TXD"),
            ("U1.2", "N_RXD"),
            ("U1.3", "N_VCC"),
            ("U2.1", "N_TXD"),
            ("U2.2", "N_RXD"),
            ("U2.3", "N_VCC"),
        ],
    )
    result = detect_comm_topology(ir)
    assert len(result.buses) == 1
    bus = result.buses[0]
    assert bus.protocol == "uart"
    assert sorted(bus.nets) == ["RXD", "TXD"]
    assert bus.master_id == "U1"


def test_detect_spi_bus_with_optional_chip_select() -> None:
    """A full SPI bus with MOSI/MISO/SCK/CS is detected."""
    mcu = make_component(
        "U1",
        DeviceKind.IC,
        [("1", "MOSI"), ("2", "MISO"), ("3", "SCK"), ("4", "CS"), ("5", "VCC")],
    )
    flash = make_component(
        "U3",
        DeviceKind.IC,
        [("1", "MOSI"), ("2", "MISO"), ("3", "SCK"), ("4", "CS"), ("5", "VCC")],
    )
    mosi = make_net("N_MOSI", "MOSI", NetType.SIGNAL)
    miso = make_net("N_MISO", "MISO", NetType.SIGNAL)
    sck = make_net("N_SCK", "SCK", NetType.SIGNAL)
    cs = make_net("N_CS", "CS", NetType.SIGNAL)
    vcc = make_net("N_VCC", "3.3V", NetType.POWER)
    ir = build_ir(
        components=[mcu, flash],
        nets=[mosi, miso, sck, cs, vcc],
        wiring=[
            ("U1.1", "N_MOSI"),
            ("U1.2", "N_MISO"),
            ("U1.3", "N_SCK"),
            ("U1.4", "N_CS"),
            ("U1.5", "N_VCC"),
            ("U3.1", "N_MOSI"),
            ("U3.2", "N_MISO"),
            ("U3.3", "N_SCK"),
            ("U3.4", "N_CS"),
            ("U3.5", "N_VCC"),
        ],
    )
    result = detect_comm_topology(ir)
    assert len(result.buses) == 1
    bus = result.buses[0]
    assert bus.protocol == "spi"
    assert sorted(bus.nets) == ["CS", "MISO", "MOSI", "SCK"]
    assert bus.master_id == "U1"
    assert bus.slave_ids == ["U3"]


def test_detect_pwm_complementary_pair() -> None:
    """PWM + PWMN nets form a complementary PWM channel."""
    mcu = make_component("U1", DeviceKind.IC, [("1", "PWM1"), ("2", "PWM1N"), ("3", "VCC")])
    driver = make_component("Q1", DeviceKind.MOSFET, [("1", "G")])
    pwm_p = make_net("N_PWM1", "T1PWM", NetType.SIGNAL)
    pwm_n = make_net("N_PWM1N", "T1PWMN", NetType.SIGNAL)
    vcc = make_net("N_VCC", "3.3V", NetType.POWER)
    ir = build_ir(
        components=[mcu, driver],
        nets=[pwm_p, pwm_n, vcc],
        wiring=[
            ("U1.1", "N_PWM1"),
            ("U1.2", "N_PWM1N"),
            ("U1.3", "N_VCC"),
            ("Q1.1", "N_PWM1"),
        ],
    )
    result = detect_comm_topology(ir)
    bus = _find_bus(result.buses, "pwm", "pwm-t1pwm")
    assert bus is not None
    assert sorted(bus.nets) == ["T1PWM", "T1PWMN"]
    assert bus.master_id == "U1"


def test_i2c_single_component_is_rejected() -> None:
    """A lone SDA/SCL net without multiple components must not become a bus."""
    mcu = make_component("U1", DeviceKind.IC, [("1", "SDA"), ("2", "SCL")])
    sda = make_net("N_SDA", "SDA", NetType.SIGNAL)
    scl = make_net("N_SCL", "SCL", NetType.SIGNAL)
    ir = build_ir(
        components=[mcu],
        nets=[sda, scl],
        wiring=[("U1.1", "N_SDA"), ("U1.2", "N_SCL")],
    )
    result = detect_comm_topology(ir)
    assert not result.buses
    assert any("LOW_COMPONENT_COUNT" in w for w in result.warnings)


def test_detect_comm_topology_does_not_mutate_ir() -> None:
    """The recognizer must not modify the input IR."""
    mcu = make_component("U1", DeviceKind.IC, [("1", "SDA"), ("2", "SCL")])
    sensor = make_component("U2", DeviceKind.IC, [("1", "SDA"), ("2", "SCL")])
    sda = make_net("N_SDA", "SDA", NetType.SIGNAL)
    scl = make_net("N_SCL", "SCL", NetType.SIGNAL)
    ir = build_ir(
        components=[mcu, sensor],
        nets=[sda, scl],
        wiring=[
            ("U1.1", "N_SDA"),
            ("U1.2", "N_SCL"),
            ("U2.1", "N_SDA"),
            ("U2.2", "N_SCL"),
        ],
    )
    original_edges = list(ir.semantic_edges)
    detect_comm_topology(ir)
    assert ir.semantic_edges == original_edges


# ---------------------------------------------------------------------------
# T268 integration tests
# ---------------------------------------------------------------------------


def test_t268_detects_i2c_uart_pwm() -> None:
    """The T268 fixture has SDA/SCL, TXD/RXD and several PWM pairs."""
    if not T268_FIXTURE.exists():
        pytest.skip(f"fixture not found: {T268_FIXTURE}")

    ir = ProtelNetParser().parse(
        ParserInput(source_id=str(T268_FIXTURE), file_path=str(T268_FIXTURE))
    )
    result = detect_comm_topology(ir)
    protocols = [b.protocol for b in result.buses]
    assert "i2c" in protocols
    assert "uart" in protocols
    assert "pwm" in protocols

    i2c = _find_bus(result.buses, "i2c", "i2c-bus")
    assert i2c is not None
    assert sorted(i2c.nets) == ["SCL", "SDA"]
    assert i2c.master_id is not None

    uart = _find_bus(result.buses, "uart", "uart-bus")
    assert uart is not None
    assert sorted(uart.nets) == ["RXD", "TXD"]

    pwm_buses = [b for b in result.buses if b.protocol == "pwm"]
    assert len(pwm_buses) >= 4


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_comm_prints_buses() -> None:
    """``cg view --with-comm <file>`` returns comm_context JSON."""
    if not T268_FIXTURE.exists():
        pytest.skip(f"fixture not found: {T268_FIXTURE}")

    from click.testing import CliRunner

    from circuit_graph.cli import main as cli_main

    runner = CliRunner()
    # U2 is the main MCU on the T268 fixture — it sits on the comm buses.
    result = runner.invoke(cli_main, ["view", "U2", str(T268_FIXTURE), "--with-comm"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "comm_context" in data
    protocols = {b["protocol"].upper() for b in data["comm_context"]["buses"]}
    # The T268 fixture has at least I2C / UART wiring; PWM presence depends
    # on the schematic revision, so we assert the strong signals only.
    assert "I2C" in protocols
    assert "UART" in protocols


def test_detect_comm_topology_no_buses() -> None:
    """A netlist with only generic signal nets yields no buses."""
    mcu = make_component("U1", DeviceKind.IC, [("1", "GP0")])
    net = make_net("N_GP0", "GP0", NetType.SIGNAL)
    ir = build_ir(
        components=[mcu],
        nets=[net],
        wiring=[("U1.1", "N_GP0")],
    )
    result = detect_comm_topology(ir)
    assert not result.buses
    assert not result.warnings
