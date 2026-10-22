"""Tests for the VF2 topology subgraph matcher."""

from __future__ import annotations

import pytest

from circuit_graph.ir import DeviceKind, NetType
from circuit_graph.primitives import load_primitives
from circuit_graph.vf2_matcher import Vf2TopologyMatcher
from tests.fir_helpers import build_ir, make_component, make_net


def _cc_pull_down_ir(
    *,
    value: str = "5.1K",
    cc_name: str = "CC1",
) -> object:
    """Build a minimal IR containing a single candidate CC pull-down resistor."""
    r = make_component(
        "R1",
        DeviceKind.RESISTOR,
        [("1", cc_name), ("2", "GND")],
        raw_value=value,
    )
    cc = make_net("N_CC", cc_name, NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [r],
        [cc, gnd],
        [("R1.1", "N_CC"), ("R1.2", "N_GND")],
    )


@pytest.fixture
def cc_template():
    return next(t for t in load_primitives() if t.id == "type-c-cc-pull-down")


def test_find_matches_detects_cc_pull_down(cc_template) -> None:
    """The matcher finds the single Type-C CC pull-down resistor."""
    matcher = Vf2TopologyMatcher(_cc_pull_down_ir())
    result = matcher.find_matches(cc_template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.component_mapping == {"R_pull": "R1"}
    assert match.signal_mapping["cc"] == "N_CC"
    assert match.signal_mapping["gnd"] == "N_GND"
    assert any("R1" in e for e in match.evidence)


def test_find_matches_no_match_for_wrong_net_name(cc_template) -> None:
    """A resistor on nets that do not match the primitive patterns is skipped."""
    matcher = Vf2TopologyMatcher(_cc_pull_down_ir(cc_name="FOO"))
    result = matcher.find_matches(cc_template, timeout_seconds=2.0)

    assert not result.aborted
    assert not result.matches


def test_find_matches_still_structural_match_for_wrong_value(cc_template) -> None:
    """VF2 matches structure only; value filtering happens in scoring."""
    matcher = Vf2TopologyMatcher(_cc_pull_down_ir(value="10K"))
    result = matcher.find_matches(cc_template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1


def test_find_matches_honours_timeout_on_large_graph(cc_template) -> None:
    """A near-zero timeout aborts before the full exponential search completes."""
    # One real candidate so the matcher enters the isomorphism iterator; the
    # rest of the graph provides enough candidate roots to make early abort
    # meaningful.
    cc_resistor = make_component(
        "RCC",
        DeviceKind.RESISTOR,
        [("1", "CC1"), ("2", "GND")],
        raw_value="5.1K",
    )
    components = [cc_resistor] + [
        make_component(
            f"R{i}",
            DeviceKind.RESISTOR,
            [("1", f"NET{i}"), ("2", "GND")],
            raw_value="5.1K",
        )
        for i in range(2499)
    ]
    nets = [
        make_net("N_CC", "CC1", NetType.SIGNAL),
        make_net("N_GND", "GND", NetType.GND),
    ] + [make_net(f"N{i}", f"NET{i}", NetType.SIGNAL) for i in range(2499)]
    wiring = [("RCC.1", "N_CC"), ("RCC.2", "N_GND")]
    wiring += [(f"R{i}.1", f"N{i}") for i in range(2499)]
    wiring += [(f"R{i}.2", "N_GND") for i in range(2499)]
    ir = build_ir(components, nets, wiring)

    matcher = Vf2TopologyMatcher(ir)
    result = matcher.find_matches(cc_template, timeout_seconds=0.0)

    assert result.aborted
    assert result.duration_seconds < 2.0


def test_find_matches_large_graph_within_default_timeout(cc_template) -> None:
    """A 5000-node graph finishes within the default timeout without aborting."""
    components = [
        make_component(
            f"R{i}",
            DeviceKind.RESISTOR,
            [("1", f"NET{i}"), ("2", "GND")],
            raw_value="5.1K",
        )
        for i in range(2500)
    ]
    nets = [make_net(f"N{i}", f"NET{i}", NetType.SIGNAL) for i in range(2500)] + [
        make_net("N_GND", "GND", NetType.GND)
    ]
    wiring = [(f"R{i}.1", f"N{i}") for i in range(2500)] + [
        (f"R{i}.2", "N_GND") for i in range(2500)
    ]
    ir = build_ir(components, nets, wiring)

    matcher = Vf2TopologyMatcher(ir)
    result = matcher.find_matches(cc_template, timeout_seconds=2.0)

    assert not result.aborted
    assert result.duration_seconds < 2.0
    # No false positives: the synthetic nets do not match CC/GND patterns.
    assert not result.matches


# ---------------------------------------------------------------------------
# Strict-mode tests (expected_role / edge_semantic)
# ---------------------------------------------------------------------------


def _make_pull_up_template():
    """Inline primitive used to drive strict-mode tests (not a real library entry)."""
    from circuit_graph.primitives import (
        PrimitiveComponent,
        PrimitiveConstraints,
        PrimitivePin,
        PrimitiveScoring,
        PrimitiveSignal,
        PrimitiveTemplate,
    )

    return PrimitiveTemplate(
        id="test-pull-up",
        name="Test Pull-Up",
        description="",
        role="unknown",
        version="0.1.0",
        signals=[
            PrimitiveSignal(
                id="vcc", type="net", name_pattern="^VCC$",
                expected_role="power", expected_direction="bidirectional",
            ),
            PrimitiveSignal(
                id="gpio", type="net", name_pattern="^GPIO",
                expected_role="digital",
            ),
        ],
        components=[
            PrimitiveComponent(
                id="R_pull", type="Resistor",
                pins=[
                    PrimitivePin(number="1", net="vcc"),
                    PrimitivePin(number="2", net="gpio"),
                ],
            ),
        ],
        constraints=PrimitiveConstraints(),
        scoring=PrimitiveScoring(),
        raw={},
    )


def _pull_up_ir(*, with_semantics: bool = True, vcc_role: str = "power"):
    """Build a 1-resistor IR; resistor between VCC and GPIO nets."""
    r = make_component(
        "R1", DeviceKind.RESISTOR,
        [("1", "VCC"), ("2", "GPIO1")],
        raw_value="10K",
    )
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    gpio = make_net("N_GPIO", "GPIO1", NetType.SIGNAL)
    edge_semantics = {}
    if with_semantics:
        edge_semantics = {
            ("R1.1", "N_VCC"):  {"role": vcc_role, "direction": "bidirectional", "level": "5V"},
            ("R1.2", "N_GPIO"): {"role": "digital", "direction": "bidirectional", "level": None},
        }
    return build_ir([r], [vcc, gpio],
                    [("R1.1", "N_VCC"), ("R1.2", "N_GPIO")],
                    edge_semantics=edge_semantics)


def test_strict_mode_role_match_passes():
    """When edge_semantic matches expected_role, the primitive matches."""
    matcher = Vf2TopologyMatcher(_pull_up_ir(with_semantics=True, vcc_role="power"))
    result = matcher.find_matches(_make_pull_up_template(), timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].signal_mapping["vcc"] == "N_VCC"
    assert result.matches[0].signal_mapping["gpio"] == "N_GPIO"


def test_strict_mode_role_mismatch_rejected():
    """When edge_semantic.role disagrees with expected_role, no match."""
    matcher = Vf2TopologyMatcher(_pull_up_ir(with_semantics=True, vcc_role="digital"))
    result = matcher.find_matches(_make_pull_up_template(), timeout_seconds=2.0)

    assert not result.aborted
    assert result.matches == []


def test_strict_mode_missing_edge_semantic_rejected():
    """Strict mode: edges without edge_semantic metadata are rejected."""
    matcher = Vf2TopologyMatcher(_pull_up_ir(with_semantics=False))
    result = matcher.find_matches(_make_pull_up_template(), timeout_seconds=2.0)

    assert not result.aborted
    assert result.matches == []


# ---------------------------------------------------------------------------
# Group 1 primitives: rc-low-pass / rc-high-pass / crystal / bridge-rectifier
# ---------------------------------------------------------------------------


def _rc_low_pass_ir():
    r = make_component("R10", DeviceKind.RESISTOR, [("1", "VIN"), ("2", "VOUT")], raw_value="1K")
    # 1 µF shunt: rc-low-pass now hard-excludes 1nF/100nF decoupling caps
    # (strict_value), so this fixture must use a non-decoupling value.
    c = make_component("C10", DeviceKind.CAPACITOR, [("1", "VOUT"), ("2", "GND")],
                       raw_value="1uF")
    vin = make_net("N_VIN", "VIN", NetType.SIGNAL)
    vout = make_net("N_VOUT", "VOUT", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [r, c], [vin, vout, gnd],
        [("R10.1", "N_VIN"), ("R10.2", "N_VOUT"),
         ("C10.1", "N_VOUT"), ("C10.2", "N_GND")],
    )


def test_find_matches_rc_low_pass():
    template = next(t for t in load_primitives() if t.id == "rc-low-pass")
    matcher = Vf2TopologyMatcher(_rc_low_pass_ir())
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    sig = result.matches[0].signal_mapping
    comp = result.matches[0].component_mapping
    assert comp["R_series"] == "R10"
    assert comp["C_shunt"] == "C10"
    assert sig["vout"] == "N_VOUT"
    assert sig["gnd"] == "N_GND"


def _rc_high_pass_ir():
    c = make_component("C20", DeviceKind.CAPACITOR, [("1", "VIN"), ("2", "VOUT")],
                       raw_value="100nF")
    r = make_component("R20", DeviceKind.RESISTOR, [("1", "VOUT"), ("2", "GND")], raw_value="10K")
    vin = make_net("N_VIN", "VIN", NetType.SIGNAL)
    vout = make_net("N_VOUT", "VOUT", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [c, r], [vin, vout, gnd],
        [("C20.1", "N_VIN"), ("C20.2", "N_VOUT"),
         ("R20.1", "N_VOUT"), ("R20.2", "N_GND")],
    )


def test_find_matches_rc_high_pass():
    template = next(t for t in load_primitives() if t.id == "rc-high-pass")
    matcher = Vf2TopologyMatcher(_rc_high_pass_ir())
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    assert comp["C_series"] == "C20"
    assert comp["R_shunt"] == "R20"


def _crystal_ir():
    x = make_component("X1", DeviceKind.UNKNOWN, [("1", "XTAL1"), ("2", "XTAL2")], raw_value="8MHz")
    cp = make_component("C30", DeviceKind.CAPACITOR, [("1", "XTAL1"), ("2", "GND")],
                        raw_value="22pF")
    cn = make_component("C31", DeviceKind.CAPACITOR, [("1", "XTAL2"), ("2", "GND")],
                        raw_value="22pF")
    xp = make_net("N_XP", "XTAL1", NetType.SIGNAL)
    xn = make_net("N_XN", "XTAL2", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [x, cp, cn], [xp, xn, gnd],
        [("X1.1", "N_XP"), ("X1.2", "N_XN"),
         ("C30.1", "N_XP"), ("C30.2", "N_GND"),
         ("C31.1", "N_XN"), ("C31.2", "N_GND")],
    )


def test_find_matches_crystal_with_load_caps():
    template = next(t for t in load_primitives() if t.id == "crystal-with-load-caps")
    matcher = Vf2TopologyMatcher(_crystal_ir())
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    assert comp["X1"] == "X1"
    assert {comp["C_p"], comp["C_n"]} == {"C30", "C31"}


def _crystal_with_shield_ir():
    """4-pin crystal whose shield pin (4) is tied to GND — the common SAW /
    canned-crystal form factor, which the template's optional gnd pin covers."""
    x = make_component(
        "X1", DeviceKind.UNKNOWN,
        [("1", "XTAL1"), ("2", "XTAL2"), ("4", "GND")],
        raw_value="12MHz",
    )
    cp = make_component("C2", DeviceKind.CAPACITOR, [("1", "XTAL1"), ("2", "GND")],
                        raw_value="20pF")
    cn = make_component("C3", DeviceKind.CAPACITOR, [("1", "XTAL2"), ("2", "GND")],
                        raw_value="20pF")
    xp = make_net("N_XP", "XTAL1", NetType.SIGNAL)
    xn = make_net("N_XN", "XTAL2", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [x, cp, cn], [xp, xn, gnd],
        [("X1.1", "N_XP"), ("X1.2", "N_XN"), ("X1.4", "N_GND"),
         ("C2.1", "N_XP"), ("C2.2", "N_GND"),
         ("C3.1", "N_XN"), ("C3.2", "N_GND")],
    )


def test_find_matches_crystal_with_load_caps_optional_shield_pin():
    """The optional ground pin lets a 4-pin grounded crystal match while the
    plain 2-pin crystal (test above) still matches via the pin-less variant."""
    template = next(t for t in load_primitives() if t.id == "crystal-with-load-caps")
    matcher = Vf2TopologyMatcher(_crystal_with_shield_ir())
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    assert comp["X1"] == "X1"
    assert {comp["C_p"], comp["C_n"]} == {"C2", "C3"}


def _bridge_rectifier_ir():
    d1 = make_component("D1", DeviceKind.DIODE, [("A", "AC1"), ("K", "VDC")], raw_value="1N4007")
    d2 = make_component("D2", DeviceKind.DIODE, [("A", "AC2"), ("K", "VDC")], raw_value="1N4007")
    d3 = make_component("D3", DeviceKind.DIODE, [("A", "GND"), ("K", "AC1")], raw_value="1N4007")
    d4 = make_component("D4", DeviceKind.DIODE, [("A", "GND"), ("K", "AC2")], raw_value="1N4007")
    ac1 = make_net("N_AC1", "AC1", NetType.SIGNAL)
    ac2 = make_net("N_AC2", "AC2", NetType.SIGNAL)
    vdc = make_net("N_VDC", "VDC_OUT", NetType.POWER)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [d1, d2, d3, d4], [ac1, ac2, vdc, gnd],
        [("D1.A", "N_AC1"), ("D1.K", "N_VDC"),
         ("D2.A", "N_AC2"), ("D2.K", "N_VDC"),
         ("D3.A", "N_GND"), ("D3.K", "N_AC1"),
         ("D4.A", "N_GND"), ("D4.K", "N_AC2")],
    )


def test_find_matches_bridge_rectifier():
    template = next(t for t in load_primitives() if t.id == "bridge-rectifier")
    matcher = Vf2TopologyMatcher(_bridge_rectifier_ir())
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    # All four diodes must be in the mapping
    assert {comp["D1"], comp["D2"], comp["D3"], comp["D4"]} == {"D1", "D2", "D3", "D4"}


# ---------------------------------------------------------------------------
# Group 2 primitives: pull-up / pull-down / tvs / switches
# All rely on strict-mode expected_role matching.
# ---------------------------------------------------------------------------


def _pull_up_ir_with_semantics():
    r = make_component("R100", DeviceKind.RESISTOR, [("1", "VCC"), ("2", "GPIO1")], raw_value="10K")
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    sig = make_net("N_SIG", "GPIO1", NetType.SIGNAL)
    return build_ir(
        [r], [vcc, sig],
        [("R100.1", "N_VCC"), ("R100.2", "N_SIG")],
        edge_semantics={
            ("R100.1", "N_VCC"): {"role": "power",    "direction": "bidirectional", "level": "5V"},
            ("R100.2", "N_SIG"): {"role": "digital",  "direction": "bidirectional", "level": None},
        },
    )


def test_find_matches_pull_up():
    template = next(t for t in load_primitives() if t.id == "pull-up-resistor")
    matcher = Vf2TopologyMatcher(_pull_up_ir_with_semantics())
    result = matcher.find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["R_pull"] == "R100"
    assert result.matches[0].signal_mapping == {"vcc": "N_VCC", "signal": "N_SIG"}


def test_find_matches_pull_up_rejected_when_vcc_mistagged_digital():
    """Same topology, but VCC net is mis-tagged as digital → strict-mode rejects."""
    r = make_component("R100", DeviceKind.RESISTOR, [("1", "VCC"), ("2", "GPIO1")], raw_value="10K")
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    sig = make_net("N_SIG", "GPIO1", NetType.SIGNAL)
    ir = build_ir(
        [r], [vcc, sig],
        [("R100.1", "N_VCC"), ("R100.2", "N_SIG")],
        edge_semantics={
            ("R100.1", "N_VCC"): {"role": "digital", "direction": "bidirectional", "level": None},
            ("R100.2", "N_SIG"): {"role": "digital", "direction": "bidirectional", "level": None},
        },
    )
    template = next(t for t in load_primitives() if t.id == "pull-up-resistor")
    result = Vf2TopologyMatcher(ir).find_matches(template, timeout_seconds=2.0)
    assert result.matches == []


def _pull_down_ir():
    r = make_component("R101", DeviceKind.RESISTOR, [("1", "GPIO2"), ("2", "GND")],
                       raw_value="4.7K")
    sig = make_net("N_SIG", "GPIO2", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [r], [sig, gnd],
        [("R101.1", "N_SIG"), ("R101.2", "N_GND")],
        edge_semantics={
            ("R101.1", "N_SIG"): {"role": "digital", "direction": "bidirectional", "level": None},
            ("R101.2", "N_GND"): {"role": "gnd",     "direction": "bidirectional", "level": None},
        },
    )


def test_find_matches_pull_down():
    template = next(t for t in load_primitives() if t.id == "pull-down-resistor")
    result = Vf2TopologyMatcher(_pull_down_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["R_pull"] == "R101"


def _tvs_ir():
    d = make_component("D10", DeviceKind.DIODE, [("A", "GND"), ("K", "VBUS")], raw_value="SMBJ5.0A")
    vbus = make_net("N_VBUS", "VBUS", NetType.POWER)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [d], [vbus, gnd],
        [("D10.A", "N_GND"), ("D10.K", "N_VBUS")],
        edge_semantics={
            ("D10.A", "N_GND"):  {"role": "gnd",   "direction": "bidirectional", "level": None},
            ("D10.K", "N_VBUS"): {"role": "power", "direction": "bidirectional", "level": "5V"},
        },
    )


def test_find_matches_tvs_protection():
    template = next(t for t in load_primitives() if t.id == "tvs-protection")
    result = Vf2TopologyMatcher(_tvs_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["D_tvs"] == "D10"


def _npn_low_side_ir():
    q = make_component("Q1", DeviceKind.UNKNOWN, [("C", "MOTOR"), ("E", "GND"), ("B", "CTRL")],
                       raw_value="2N2222", part_number="2N2222")
    load = make_net("N_LOAD", "MOTOR", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    ctrl = make_net("N_CTRL", "CTRL_PWM", NetType.SIGNAL)
    return build_ir(
        [q], [load, gnd, ctrl],
        [("Q1.C", "N_LOAD"), ("Q1.E", "N_GND"), ("Q1.B", "N_CTRL")],
        edge_semantics={
            ("Q1.E", "N_GND"):  {"role": "gnd",     "direction": "bidirectional", "level": None},
            ("Q1.B", "N_CTRL"): {"role": "digital", "direction": "load",          "level": None},
        },
    )


def test_find_matches_npn_low_side():
    template = next(t for t in load_primitives() if t.id == "npn-low-side-switch")
    result = Vf2TopologyMatcher(_npn_low_side_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["Q1"] == "Q1"


def _nmos_low_side_ir():
    q = make_component("Q2", DeviceKind.MOSFET, [("D", "LED"), ("S", "GND"), ("G", "PWM")],
                       raw_value="2N7002", part_number="2N7002")
    load = make_net("N_LOAD", "LED", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    ctrl = make_net("N_CTRL", "PWM", NetType.SIGNAL)
    return build_ir(
        [q], [load, gnd, ctrl],
        [("Q2.D", "N_LOAD"), ("Q2.S", "N_GND"), ("Q2.G", "N_CTRL")],
        edge_semantics={
            ("Q2.S", "N_GND"):  {"role": "gnd",     "direction": "bidirectional", "level": None},
            ("Q2.G", "N_CTRL"): {"role": "digital", "direction": "load",          "level": None},
        },
    )


def test_find_matches_nmos_low_side():
    template = next(t for t in load_primitives() if t.id == "nmos-low-side-switch")
    result = Vf2TopologyMatcher(_nmos_low_side_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["Q1"] == "Q2"


def _pmos_high_side_ir():
    q = make_component("Q3", DeviceKind.MOSFET, [("S", "VCC"), ("D", "LOAD"), ("G", "EN")],
                       raw_value="AO3401", part_number="AO3401")
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    load = make_net("N_LOAD", "LOAD", NetType.SIGNAL)
    ctrl = make_net("N_CTRL", "EN", NetType.SIGNAL)
    return build_ir(
        [q], [vcc, load, ctrl],
        [("Q3.S", "N_VCC"), ("Q3.D", "N_LOAD"), ("Q3.G", "N_CTRL")],
        edge_semantics={
            ("Q3.S", "N_VCC"):  {"role": "power",   "direction": "bidirectional", "level": "5V"},
            ("Q3.G", "N_CTRL"): {"role": "digital", "direction": "load",          "level": None},
        },
    )


def test_find_matches_pmos_high_side():
    template = next(t for t in load_primitives() if t.id == "pmos-high-side-switch")
    result = Vf2TopologyMatcher(_pmos_high_side_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].component_mapping["Q1"] == "Q3"


# ---------------------------------------------------------------------------
# Group 3 primitives: flyback / current-shunt / fet-level-shifter / lc-pi / sallen-key
# ---------------------------------------------------------------------------


def _flyback_ir():
    ind = make_component("L1", DeviceKind.INDUCTOR, [("1", "VCC"), ("2", "SW")],
                         raw_value="10uH")
    dio = make_component("D5", DeviceKind.DIODE, [("A", "SW"), ("K", "VCC")], raw_value="1N4148")
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    sw = make_net("N_SW", "SW", NetType.SIGNAL)
    return build_ir(
        [ind, dio], [vcc, sw],
        [("L1.1", "N_VCC"), ("L1.2", "N_SW"),
         ("D5.A", "N_SW"), ("D5.K", "N_VCC")],
        edge_semantics={
            ("L1.1", "N_VCC"): {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("D5.K", "N_VCC"): {"role": "power", "direction": "bidirectional", "level": "5V"},
        },
    )


def test_find_matches_flyback_diode():
    template = next(t for t in load_primitives() if t.id == "flyback-diode")
    result = Vf2TopologyMatcher(_flyback_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    assert comp["L1"] == "L1"
    assert comp["D_fly"] == "D5"


def _current_shunt_ir():
    r = make_component("R200", DeviceKind.RESISTOR, [("1", "VBUS"), ("2", "VDD")], raw_value="0.1")
    vin = make_net("N_VIN", "VBUS", NetType.POWER)
    vout = make_net("N_VOUT", "VDD", NetType.POWER)
    return build_ir(
        [r], [vin, vout],
        [("R200.1", "N_VIN"), ("R200.2", "N_VOUT")],
        edge_semantics={
            ("R200.1", "N_VIN"):  {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("R200.2", "N_VOUT"): {"role": "power", "direction": "bidirectional", "level": "5V"},
        },
    )


def test_find_matches_current_shunt():
    template = next(t for t in load_primitives() if t.id == "current-shunt-monitor")
    result = Vf2TopologyMatcher(_current_shunt_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    # R200 is symmetric → VF2 may find 2 matches (swapped pwr_in/pwr_out)
    assert len(result.matches) >= 1
    assert result.matches[0].component_mapping["R_shunt"] == "R200"


def _fet_level_shifter_ir():
    q = make_component("Q4", DeviceKind.MOSFET, [("S", "SDA_3V3"), ("D", "SDA_5V"), ("G", "3V3")],
                       raw_value="BSS138", part_number="BSS138")
    r_lo = make_component("R300", DeviceKind.RESISTOR, [("1", "SDA_3V3"), ("2", "3V3")],
                          raw_value="10K")
    r_hi = make_component("R301", DeviceKind.RESISTOR, [("1", "SDA_5V"), ("2", "5V")],
                          raw_value="10K")
    v3 = make_net("N_3V3", "3V3", NetType.POWER)
    v5 = make_net("N_5V", "5V", NetType.POWER)
    sl = make_net("N_SL", "SDA_3V3", NetType.SIGNAL)
    sh = make_net("N_SH", "SDA_5V", NetType.SIGNAL)
    return build_ir(
        [q, r_lo, r_hi], [v3, v5, sl, sh],
        [("Q4.S", "N_SL"), ("Q4.D", "N_SH"), ("Q4.G", "N_3V3"),
         ("R300.1", "N_SL"), ("R300.2", "N_3V3"),
         ("R301.1", "N_SH"), ("R301.2", "N_5V")],
        edge_semantics={
            ("Q4.G", "N_3V3"):   {"role": "power",   "direction": "bidirectional", "level": "3V3"},
            ("R300.2", "N_3V3"): {"role": "power",   "direction": "bidirectional", "level": "3V3"},
            ("R301.2", "N_5V"):  {"role": "power",   "direction": "bidirectional", "level": "5V"},
            ("Q4.S", "N_SL"):    {"role": "digital", "direction": "bidirectional", "level": None},
            ("R300.1", "N_SL"):  {"role": "digital", "direction": "bidirectional", "level": None},
            ("Q4.D", "N_SH"):    {"role": "digital", "direction": "bidirectional", "level": None},
            ("R301.1", "N_SH"):  {"role": "digital", "direction": "bidirectional", "level": None},
        },
    )


def test_find_matches_fet_level_shifter():
    template = next(t for t in load_primitives() if t.id == "fet-level-shifter")
    result = Vf2TopologyMatcher(_fet_level_shifter_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    assert comp["Q1"] == "Q4"
    assert {comp["R_low"], comp["R_high"]} == {"R300", "R301"}


def _lc_pi_ir():
    c_in = make_component("C40", DeviceKind.CAPACITOR, [("1", "VIN"), ("2", "GND")],
                          raw_value="10uF")
    ind = make_component("L2", DeviceKind.INDUCTOR, [("1", "VIN"), ("2", "VOUT")],
                         raw_value="10uH")
    c_out = make_component("C41", DeviceKind.CAPACITOR, [("1", "VOUT"), ("2", "GND")],
                           raw_value="10uF")
    vin = make_net("N_VIN", "VIN", NetType.POWER)
    vout = make_net("N_VOUT", "VOUT", NetType.POWER)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [c_in, ind, c_out], [vin, vout, gnd],
        [("C40.1", "N_VIN"), ("C40.2", "N_GND"),
         ("L2.1", "N_VIN"), ("L2.2", "N_VOUT"),
         ("C41.1", "N_VOUT"), ("C41.2", "N_GND")],
        edge_semantics={
            ("C40.1", "N_VIN"):  {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("L2.1", "N_VIN"):   {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("L2.2", "N_VOUT"):  {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("C41.1", "N_VOUT"): {"role": "power", "direction": "bidirectional", "level": "5V"},
            ("C40.2", "N_GND"):  {"role": "gnd",   "direction": "bidirectional", "level": None},
            ("C41.2", "N_GND"):  {"role": "gnd",   "direction": "bidirectional", "level": None},
        },
    )


def test_find_matches_lc_pi_filter():
    template = next(t for t in load_primitives() if t.id == "lc-pi-filter")
    result = Vf2TopologyMatcher(_lc_pi_ir()).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    comp = result.matches[0].component_mapping
    assert comp["L1"] == "L2"
    assert {comp["C_in"], comp["C_out"]} == {"C40", "C41"}


def _sallen_key_ir():
    """Sallen-Key IR with intermediate nets (n1, n2) as required by the primitive."""
    u = make_component("U10", DeviceKind.IC,
                       [("+", "VIN"), ("-", "VOUT"), ("OUT", "VOUT"),
                        ("V+", "VCC"), ("V-", "GND")],
                       raw_value="LM358", part_number="LM358")
    r1 = make_component("R400", DeviceKind.RESISTOR, [("1", ""), ("2", "")], raw_value="10K")
    r2 = make_component("R401", DeviceKind.RESISTOR, [("1", ""), ("2", "")], raw_value="10K")
    c1 = make_component("C50", DeviceKind.CAPACITOR, [("1", ""), ("2", "")], raw_value="10nF")
    c2 = make_component("C51", DeviceKind.CAPACITOR, [("1", ""), ("2", "")], raw_value="10nF")
    vin = make_net("N_VIN", "VIN", NetType.SIGNAL)
    vout = make_net("N_VOUT", "VOUT", NetType.SIGNAL)
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    gnd = make_net("N_GND", "GND", NetType.GND)
    n1 = make_net("N_N1", "NetR400_2", NetType.SIGNAL)  # intermediate net between R1/R2/C1
    n2 = make_net("N_N2", "NetR401_2", NetType.SIGNAL)  # intermediate net between R2/C2/U_op.+
    return build_ir(
        [u, r1, r2, c1, c2], [vin, vout, vcc, gnd, n1, n2],
        [("U10.+", "N_N2"), ("U10.-", "N_VOUT"), ("U10.OUT", "N_VOUT"),
         ("U10.V+", "N_VCC"), ("U10.V-", "N_GND"),
         ("R400.1", "N_VIN"), ("R400.2", "N_N1"),
         ("R401.1", "N_N1"), ("R401.2", "N_N2"),
         ("C50.1", "N_N1"), ("C50.2", "N_VOUT"),
         ("C51.1", "N_N2"), ("C51.2", "N_VOUT")],
        edge_semantics={
            ("U10.V+", "N_VCC"): {"role": "power", "direction": "load",         "level": "5V"},
            ("U10.V-", "N_GND"): {"role": "gnd",   "direction": "bidirectional", "level": None},
        },
    )


def test_find_matches_sallen_key_low_pass():
    template = next(t for t in load_primitives() if t.id == "sallen-key-low-pass")
    result = Vf2TopologyMatcher(_sallen_key_ir(), ignore_pin_numbers=True).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    # Sallen-Key is approximate (VF2 ignores pin numbers); allow any number >= 1
    assert len(result.matches) >= 1


def test_peak_envelope_detector_matches_protel_netlist():
    """End-to-end: FX-T268 has two symmetric peak-detector front-ends for the
    SW5001 boost rails (VBAK_A via D2, VBAK_B via D3). Both should be
    recognised as peak-envelope-detector blocks.
    """
    from pathlib import Path

    from circuit_graph.enrichers import run_enrichers
    from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
    from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
    from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

    netlist = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "protel"
        / "FX-T268-V6-10米-5065.NET"
    )
    ir = ProtelNetParser().parse(ParserInput(file_path=str(netlist)))
    ir2 = run_enrichers(ir, [MultiUnitSplitter(), TopologyRecognizer()]).ir

    blocks = [b for b in ir2.blocks if b.id.startswith("peak-envelope-detector-")]
    assert any({"D2", "R54", "R49", "R58", "C56"} <= set(b.component_ids) for b in blocks), (
        f"Expected a peak-envelope-detector block covering D2 branch; got: "
        f"{[(b.id, b.component_ids) for b in blocks]}"
    )
    assert any({"D3", "R56", "R50", "R59", "C58"} <= set(b.component_ids) for b in blocks), (
        f"Expected a peak-envelope-detector block covering D3 branch; got: "
        f"{[(b.id, b.component_ids) for b in blocks]}"
    )


# ---------------------------------------------------------------------------
# integrated-bridge-rectifier / bridge-rectifier-package
# ---------------------------------------------------------------------------


def test_find_matches_integrated_bridge_rectifier():
    """Diode-typed single block bridging L/N/310V/GND matches the feature-based
    integrated-bridge primitive (no footprint confirmation needed)."""
    bd = make_component(
        "D1", DeviceKind.DIODE,
        [("1", "L"), ("2", "N"), ("3", "310V"), ("4", "GND")],
        raw_value="US1M",
    )
    l_net = make_net("N_L", "L", NetType.SIGNAL)
    n_net = make_net("N_N", "N", NetType.SIGNAL)
    dc = make_net("N_310V", "310V", NetType.POWER)
    gnd = make_net("N_GND", "GND", NetType.GND)
    ir = build_ir(
        [bd], [l_net, n_net, dc, gnd],
        [("D1.1", "N_L"), ("D1.2", "N_N"), ("D1.3", "N_310V"), ("D1.4", "N_GND")],
    )
    template = next(t for t in load_primitives() if t.id == "integrated-bridge-rectifier")
    result = Vf2TopologyMatcher(ir).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    sig = result.matches[0].signal_mapping
    assert {sig["ac_l"], sig["ac_n"]} == {"N_L", "N_N"}
    assert sig["dc_plus"] == "N_310V"
    assert sig["dc_minus"] == "N_GND"


def test_find_matches_bridge_rectifier_package_auto_named_nets():
    """Footprint-confirmed bridge (BridgeRectifier) matches the package
    primitive even when every net is Protel auto-named (NetBRG1_*)."""
    brg = make_component(
        "BRG1", DeviceKind.BRIDGE_RECTIFIER,
        [("1", "NetBRG1_1"), ("2", "NetBRG1_2"), ("3", "NetBRG1_3"), ("4", "NetBRG1_4")],
        raw_value="GBJ1510",
    )
    nets = [make_net(f"N{i}", f"NetBRG1_{i}", NetType.SIGNAL) for i in range(1, 5)]
    ir = build_ir(
        [brg], nets,
        [(f"BRG1.{i}", f"N{i}") for i in range(1, 5)],
    )
    template = next(t for t in load_primitives() if t.id == "bridge-rectifier-package")
    result = Vf2TopologyMatcher(ir).find_matches(template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) >= 1
    assert result.matches[0].component_mapping["BD"] == "BRG1"


def test_integrated_bridge_rectifier_matches_protel_ac_frontend():
    """End-to-end: add-primitives.NET merges four US1M diodes into one D1
    component bridging L / N / 310V / GND — the integrated-bridge primitive
    must fire even though the designator is a plain 'D1'.
    """
    from pathlib import Path

    from circuit_graph.enrichers import run_enrichers
    from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
    from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
    from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

    netlist = Path(__file__).resolve().parent.parent / "fixtures" / "protel" / "add-primitives.NET"
    ir = ProtelNetParser().parse(ParserInput(file_path=str(netlist)))

    # Premise: the fixture encodes the bridge as a single component (e.g. D1
    # appearing four times in the source, merged by the parser). If the fixture
    # has been edited to use separate D1/D2/D3/D4 designators the test premise
    # no longer holds.
    us1m_ids = [c.id for c in ir.components if (c.raw_value or "").startswith("US1M")]
    if len(set(us1m_ids)) != 1:
        import pytest
        pytest.skip("add-primitives.NET no longer encodes the merged-D1 bridge pattern")

    ir2 = run_enrichers(ir, [MultiUnitSplitter(), TopologyRecognizer()]).ir

    blocks = [b for b in ir2.blocks if b.id.startswith("integrated-bridge-rectifier-")]
    assert any("D1" in b.component_ids for b in blocks), (
        f"Expected an integrated-bridge-rectifier block containing D1; got: "
        f"{[(b.id, b.component_ids) for b in blocks]}"
    )

