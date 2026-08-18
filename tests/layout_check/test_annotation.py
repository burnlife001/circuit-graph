"""Annotation assertion tests (design doc §6 layout rules).

12 tests total: 4 per rule (pass / fail / boundary-or-empty / applies-filter).
"""
from __future__ import annotations

from circuit_graph.layout_check.assertions.annotation import (
    DesignatorPresent,
    SignalNameOnNet,
    ValuePresent,
)
from circuit_graph.layout_check.model import (
    AppliesTo,
    LayoutComponent,
    LayoutNet,
    LayoutSchematic,
)

# -- shared fixtures ----------------------------------------------------------

def _comp(id_, value="10k", type_="resistor", pos=None, rot=None):
    """Build a LayoutComponent with a sensible default refdes_prefix."""
    return LayoutComponent(
        id=id_,
        type=type_,
        value=value,
        position_mm=pos,
        rotation_deg=rot,
        refdes_prefix="".join(c for c in id_ if c.isalpha()) or id_,
        sheet=None,
        page=None,
        pins=[],
    )


def _net(name, kind="signal"):
    return LayoutNet(name=name, kind=kind, members=[])


def _sch(components, nets=(), wires=()):
    return LayoutSchematic(
        components=list(components),
        nets=list(nets),
        wires=list(wires),
    )


# -- DesignatorPresent --------------------------------------------------------

def test_designator_present_passes_when_id_nonempty():
    s = _sch([_comp("R96")])
    assert DesignatorPresent().evaluate(s, None, {}) == []


def test_designator_present_fails_on_empty_id():
    s = _sch([_comp("")])
    vs = DesignatorPresent().evaluate(s, None, {})
    assert len(vs) == 1
    assert vs[0].assert_kind == "designator-present"
    assert vs[0].location.component is None  # no id to record
    assert vs[0].context == {"component_id": ""}


def test_designator_present_fails_on_whitespace_only_id():
    s = _sch([_comp("   ")])
    vs = DesignatorPresent().evaluate(s, None, {})
    assert len(vs) == 1
    assert vs[0].context["component_id"] == "   "


def test_designator_present_applies_filter_narrows_match():
    """Filter narrows to R*; both components have non-empty ids → 0 violations."""
    s = _sch([_comp("R96"), _comp("C5")])
    applies = AppliesTo(refdes_prefix=["R*"], type=[], net=[], kind=[])
    assert DesignatorPresent().evaluate(s, applies, {}) == []


# -- ValuePresent -------------------------------------------------------------

def test_value_present_passes_with_real_value():
    s = _sch([_comp("R96", value="10k")])
    assert ValuePresent().evaluate(s, None, {}) == []


def test_value_present_fails_when_value_is_none():
    s = _sch([_comp("R96", value=None)])
    vs = ValuePresent().evaluate(s, None, {})
    assert len(vs) == 1
    assert vs[0].assert_kind == "value-present"
    assert vs[0].context["value"] is None
    assert vs[0].context["component_id"] == "R96"


def test_value_present_fails_on_default_forbidden_nc():
    s = _sch([_comp("R96", value="NC")])
    vs = ValuePresent().evaluate(s, None, {})
    assert len(vs) == 1
    assert "NC" in vs[0].context["forbidden_default"]
    assert vs[0].context["value"] == "NC"


def test_value_present_custom_forbidden_list():
    """Custom param overrides the default forbidden list."""
    s = _sch([_comp("R96", value="DNP")])
    vs = ValuePresent().evaluate(s, None, {"forbidden_default": ["DNP", "?"]})
    assert len(vs) == 1
    assert vs[0].context["forbidden_default"] == ["DNP", "?"]
    assert vs[0].context["value"] == "DNP"


# -- SignalNameOnNet ----------------------------------------------------------

def test_signal_name_on_net_passes_for_real_names():
    s = _sch(
        [],
        nets=[
            _net("V_OUT"),
            _net("GND", kind="ground"),
            _net("+5V", kind="power"),
        ],
    )
    assert SignalNameOnNet().evaluate(s, None, {}) == []


def test_signal_name_on_net_fails_on_kicad_autoname():
    s = _sch([], nets=[_net("NetU1_2"), _net("NetC5_1")])
    vs = SignalNameOnNet().evaluate(s, None, {})
    assert len(vs) == 2
    assert all(v.assert_kind == "signal-name-on-net" for v in vs)
    # The default forbidden list is ("Net*", "NetU?_?", "NetC?_?"); the
    # broader ``Net*`` matches both nets first (iteration order), so
    # ``matched_pattern`` is "Net*" for both. The more-specific patterns
    # are still recorded in ``forbidden_patterns``.
    assert all(v.context["matched_pattern"] == "Net*" for v in vs)
    names = sorted(v.context["net_name"] for v in vs)
    assert names == ["NetC5_1", "NetU1_2"]
    # Each violation has the full forbidden list in context.
    for v in vs:
        assert "Net*" in v.context["forbidden_patterns"]
        assert "NetU?_?" in v.context["forbidden_patterns"]
        assert "NetC?_?" in v.context["forbidden_patterns"]


def test_signal_name_on_net_custom_forbidden_patterns():
    s = _sch([], nets=[_net("AUTO_1"), _net("V_OUT")])
    vs = SignalNameOnNet().evaluate(
        s, None, {"forbidden_default_names": ["AUTO_*"]}
    )
    assert len(vs) == 1
    assert vs[0].context["net_name"] == "AUTO_1"
    assert vs[0].context["matched_pattern"] == "AUTO_*"


def test_signal_name_on_net_filter_by_kind_only_power():
    """Two nets with the same name but different kinds; filter by kind
    narrows to the power one."""
    s = _sch(
        [],
        nets=[
            _net("NetTmp", kind="signal"),
            _net("NetTmp", kind="power"),
        ],
    )
    applies = AppliesTo(refdes_prefix=[], type=[], net=[], kind=["power"])
    vs = SignalNameOnNet().evaluate(
        s, applies, {"forbidden_default_names": ["NetTmp"]}
    )
    assert len(vs) == 1
    assert vs[0].location.net == "NetTmp"
    # The signal-named one is filtered out; only the power one matches.
