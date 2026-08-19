"""Decoupling capacitor proximity assertion tests (§5 layout rules)."""
from __future__ import annotations

from circuit_graph.layout_check.assertions.decoupling import (
    DecoupNearIcPowerPin,
    _parse_cap_value_farads,
)
from circuit_graph.layout_check.model import (
    AppliesTo,
    LayoutComponent,
    LayoutNet,
    LayoutPin,
    LayoutSchematic,
)

# ---- fixture helpers --------------------------------------------------------


def _comp(
    id_,
    value=None,
    type_="ic",
    pos=None,
    pins=(),
):
    return LayoutComponent(
        id=id_,
        type=type_,
        value=value,
        position_mm=pos,
        rotation_deg=None,
        refdes_prefix="".join(c for c in id_ if c.isalpha()) or id_,
        sheet=None,
        page=None,
        pins=list(pins),
    )


def _pin(num, net, x=None, y=None):
    return LayoutPin(number=num, net=net, x_mm=x, y_mm=y)


def _net(name, kind="power"):
    return LayoutNet(name=name, kind=kind, members=[])


def _sch(components, nets=(), wires=()):
    return LayoutSchematic(
        components=list(components), nets=list(nets), wires=list(wires)
    )


# ---- _parse_cap_value_farads unit tests -------------------------------------


def test_parse_cap_value_valid_units():
    assert _parse_cap_value_farads("100n") == 100e-9
    assert _parse_cap_value_farads("100nF") == 100e-9
    assert _parse_cap_value_farads("0.1uF") == 100e-9
    assert _parse_cap_value_farads(".1u") == 100e-9
    assert _parse_cap_value_farads("10pF") == 10e-12
    assert _parse_cap_value_farads("1u") == 1e-6
    assert _parse_cap_value_farads("  100nF  ") == 100e-9  # whitespace tolerated


def test_parse_cap_value_rejects_bare_number_and_garbage():
    # Bare numbers have no unit → reject (would otherwise need to guess
    # "100" = 100 F which is nonsensical).
    assert _parse_cap_value_farads("100") is None
    assert _parse_cap_value_farads(None) is None
    assert _parse_cap_value_farads("NC") is None
    assert _parse_cap_value_farads("") is None
    assert _parse_cap_value_farads("abc") is None
    assert _parse_cap_value_farads("100nX") is None  # trailing junk


# ---- DecoupNearIcPowerPin: pass / fail / boundary ---------------------------


def test_decoup_passes_when_cap_within_tolerance():
    # Cap at (10, 0), IC power pin at (0, 0) → dist 10 mm; max 15 mm → PASS.
    cap = _comp(
        "C1",
        value="100nF",
        type_="capacitor",
        pos=(10.0, 0.0),
        pins=[
            _pin("1", "5V", x=11.0, y=0.0),
            _pin("2", "GND", x=9.0, y=0.0),
        ],
    )
    ic = _comp(
        "U1",
        pos=(0.0, 0.0),
        value="LM321",
        pins=[_pin("3", "5V", x=0.0, y=0.0)],
    )
    s = _sch(
        [cap, ic],
        nets=[_net("5V", "power"), _net("GND", "ground")],
    )
    vs = DecoupNearIcPowerPin().evaluate(s, None, {"max_distance_mm": 15.0})
    assert vs == []


def test_decoup_fails_when_cap_far():
    cap = _comp(
        "C1",
        value="100nF",
        type_="capacitor",
        pos=(100.0, 100.0),
        pins=[
            _pin("1", "5V", x=101.0, y=100.0),
            _pin("2", "GND", x=99.0, y=100.0),
        ],
    )
    ic = _comp(
        "U1",
        pos=(0.0, 0.0),
        pins=[_pin("3", "5V", x=0.0, y=0.0)],
    )
    s = _sch(
        [cap, ic],
        nets=[_net("5V", "power"), _net("GND", "ground")],
    )
    vs = DecoupNearIcPowerPin().evaluate(s, None, {"max_distance_mm": 5.0})
    assert len(vs) == 1
    assert vs[0].assert_kind == "decoup-near-ic-power-pin"
    assert vs[0].severity == "error"
    assert vs[0].context["nearest_distance_mm"] > 5.0
    assert vs[0].context["nearest_ic"] == "U1"
    assert vs[0].context["max_distance_mm"] == 5.0
    assert vs[0].location.component == "C1"


def test_decoup_boundary_passes_at_exactly_max():
    # dist=10, tolerance=10 → PASS (uniform boundary semantics).
    cap = _comp(
        "C1",
        value="100nF",
        type_="capacitor",
        pos=(10.0, 0.0),
        pins=[
            _pin("1", "5V", x=11.0, y=0.0),
            _pin("2", "GND", x=9.0, y=0.0),
        ],
    )
    ic = _comp(
        "U1",
        pos=(0.0, 0.0),
        pins=[_pin("3", "5V", x=0.0, y=0.0)],
    )
    s = _sch(
        [cap, ic],
        nets=[_net("5V", "power"), _net("GND", "ground")],
    )
    vs = DecoupNearIcPowerPin().evaluate(s, None, {"max_distance_mm": 10.0})
    assert vs == []


# ---- DecoupNearIcPowerPin: cap identification --------------------------------


def test_decoup_skips_cap_when_value_too_large():
    # 1 µF cap is 10× the 100 nF threshold → not a decoup cap → vacuous-pass.
    cap = _comp(
        "C1",
        value="1uF",
        type_="capacitor",
        pos=(100.0, 100.0),
        pins=[
            _pin("1", "5V", x=101.0, y=100.0),
            _pin("2", "GND", x=99.0, y=100.0),
        ],
    )
    ic = _comp(
        "U1",
        pos=(0.0, 0.0),
        pins=[_pin("3", "5V", x=0.0, y=0.0)],
    )
    s = _sch(
        [cap, ic],
        nets=[_net("5V", "power"), _net("GND", "ground")],
    )
    vs = DecoupNearIcPowerPin().evaluate(s, None, {"max_distance_mm": 5.0})
    assert vs == []


def test_decoup_skips_cap_when_not_powergnd_pair():
    # Cap has 2 pins but both are power → no power-gnd pair → not a decoup.
    cap = _comp(
        "C1",
        value="100nF",
        type_="capacitor",
        pos=(100.0, 100.0),
        pins=[
            _pin("1", "5V", x=101.0, y=100.0),
            _pin("2", "5V", x=99.0, y=100.0),
        ],
    )
    ic = _comp(
        "U1",
        pos=(0.0, 0.0),
        pins=[_pin("3", "5V", x=0.0, y=0.0)],
    )
    s = _sch(
        [cap, ic],
        nets=[_net("5V", "power"), _net("GND", "ground")],
    )
    vs = DecoupNearIcPowerPin().evaluate(s, None, {"max_distance_mm": 5.0})
    assert vs == []


def test_decoup_skips_cap_when_pin_coords_missing():
    # Cap's pins lack x_mm/y_mm — rule uses cap.position_mm, not pin
    # coords, so the cap still counts as a candidate.
    cap = _comp(
        "C1",
        value="100nF",
        type_="capacitor",
        pos=(10.0, 0.0),
        pins=[_pin("1", "5V"), _pin("2", "GND")],
    )
    ic = _comp(
        "U1",
        pos=(0.0, 0.0),
        pins=[_pin("3", "5V", x=0.0, y=0.0)],
    )
    s = _sch(
        [cap, ic],
        nets=[_net("5V", "power"), _net("GND", "ground")],
    )
    vs = DecoupNearIcPowerPin().evaluate(s, None, {"max_distance_mm": 15.0})
    assert vs == []  # dist=10mm < 15mm → PASS


def test_decoup_handles_protel_no_violations():
    # Protel has no pin coords → cap skipped (no position_mm); IC has
    # no power pins with coords → no ic_pins → vacuous-pass end-to-end.
    cap = _comp(
        "C1",
        value="100nF",
        type_="capacitor",
        pos=None,
        pins=[_pin("1", "5V"), _pin("2", "GND")],
    )
    ic = _comp(
        "U1",
        pos=None,
        pins=[_pin("3", "5V")],
    )
    s = _sch(
        [cap, ic],
        nets=[_net("5V", "power"), _net("GND", "ground")],
    )
    vs = DecoupNearIcPowerPin().evaluate(s, None, {"max_distance_mm": 5.0})
    assert vs == []


# ---- DecoupNearIcPowerPin: nearest-IC + IC-prefix config --------------------


def test_decoup_finds_nearest_among_multiple_ics():
    # Cap at (50,50); ic1 power pin at (0,0) → ~70.7 mm; ic2 power pin at
    # (50,60) → 10 mm. Nearest = U2.
    cap = _comp(
        "C1",
        value="100nF",
        type_="capacitor",
        pos=(50.0, 50.0),
        pins=[
            _pin("1", "5V", x=51.0, y=50.0),
            _pin("2", "GND", x=49.0, y=50.0),
        ],
    )
    ic1 = _comp(
        "U1",
        pos=(0.0, 0.0),
        pins=[_pin("3", "5V", x=0.0, y=0.0)],
    )
    ic2 = _comp(
        "U2",
        pos=(50.0, 60.0),
        pins=[_pin("7", "5V", x=50.0, y=60.0)],
    )
    s = _sch(
        [cap, ic1, ic2],
        nets=[_net("5V", "power"), _net("GND", "ground")],
    )
    # Loose: nearest (U2) is 10 mm < 15 mm → PASS.
    assert (
        DecoupNearIcPowerPin().evaluate(s, None, {"max_distance_mm": 15.0}) == []
    )
    # Tight: nearest (U2) is 10 mm > 5 mm → FAIL on C1, nearest_ic=U2.
    vs = DecoupNearIcPowerPin().evaluate(s, None, {"max_distance_mm": 5.0})
    assert len(vs) == 1
    assert vs[0].location.component == "C1"
    assert vs[0].context["nearest_ic"] == "U2"
    assert vs[0].context["nearest_distance_mm"] == 10.0


def test_decoup_custom_ic_prefix_config():
    # IC is a J* (J1) not U* → default ic_refdes_prefix=["U*"] would miss
    # it → no IC power pins → vacuous-pass. Override ic_refdes_prefix
    # to ["J*"] → IC is now recognised → violation emitted.
    cap = _comp(
        "C1",
        value="100nF",
        type_="capacitor",
        pos=(100.0, 0.0),
        pins=[
            _pin("1", "5V", x=101.0, y=0.0),
            _pin("2", "GND", x=99.0, y=0.0),
        ],
    )
    ic = _comp(
        "J1",
        pos=(0.0, 0.0),
        pins=[_pin("3", "5V", x=0.0, y=0.0)],
    )
    s = _sch(
        [cap, ic],
        nets=[_net("5V", "power"), _net("GND", "ground")],
    )
    # Default prefix U*: J1 is not an IC → vacuous-pass.
    assert (
        DecoupNearIcPowerPin().evaluate(s, None, {"max_distance_mm": 5.0}) == []
    )
    # Override to J*: J1 IS an IC → violation.
    vs = DecoupNearIcPowerPin().evaluate(
        s, None, {"max_distance_mm": 5.0, "ic_refdes_prefix": ["J*"]}
    )
    assert len(vs) == 1
    assert vs[0].context["nearest_ic"] == "J1"


def test_decoup_applies_to_filter_excludes_components():
    # applies_to filters to R* → neither C1 nor U1 match → no candidates
    # on either side → no violations.
    cap = _comp(
        "C1",
        value="100nF",
        type_="capacitor",
        pos=(100.0, 100.0),
        pins=[
            _pin("1", "5V", x=101.0, y=100.0),
            _pin("2", "GND", x=99.0, y=100.0),
        ],
    )
    ic = _comp(
        "U1",
        pos=(0.0, 0.0),
        pins=[_pin("3", "5V", x=0.0, y=0.0)],
    )
    s = _sch(
        [cap, ic],
        nets=[_net("5V", "power"), _net("GND", "ground")],
    )
    applies = AppliesTo(refdes_prefix=["R*"], type=[], net=[], kind=[])
    vs = DecoupNearIcPowerPin().evaluate(s, applies, {"max_distance_mm": 5.0})
    assert vs == []


# ---- DecoupNearIcPowerPin: contextual counts --------------------------------


def test_decoup_violation_context_counts_caps_and_ic_pins():
    # Two caps, both far. Both ICs contribute power pins. Verify the
    # context counts reflect the schematic, not the per-violation slice.
    cap_a = _comp(
        "C1",
        value="100nF",
        type_="capacitor",
        pos=(50.0, 0.0),
        pins=[_pin("1", "5V", x=51.0, y=0.0), _pin("2", "GND", x=49.0, y=0.0)],
    )
    cap_b = _comp(
        "C2",
        value="100nF",
        type_="capacitor",
        pos=(0.0, 50.0),
        pins=[_pin("1", "5V", x=1.0, y=50.0), _pin("2", "GND", x=-1.0, y=50.0)],
    )
    ic1 = _comp(
        "U1",
        pos=(0.0, 0.0),
        pins=[
            _pin("3", "5V", x=0.0, y=0.0),
            _pin("4", "5V", x=0.0, y=0.0),
        ],
    )
    ic2 = _comp(
        "U2",
        pos=(60.0, 60.0),
        pins=[_pin("7", "5V", x=60.0, y=60.0)],
    )
    s = _sch(
        [cap_a, cap_b, ic1, ic2],
        nets=[_net("5V", "power"), _net("GND", "ground")],
    )
    vs = DecoupNearIcPowerPin().evaluate(s, None, {"max_distance_mm": 5.0})
    # Both caps are 50+ mm from every IC power pin → 2 violations.
    assert len(vs) == 2
    for v in vs:
        assert v.context["cap_count"] == 2
        assert v.context["ic_power_pin_count"] == 3  # U1 has 2 power pins, U2 has 1
        assert v.context["max_distance_mm"] == 5.0
