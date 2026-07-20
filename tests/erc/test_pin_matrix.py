"""Tests for circuit_graph.erc.pin_matrix."""
from __future__ import annotations

from circuit_graph.erc.models import ElectricalPinType
from circuit_graph.erc.pin_matrix import PinConflictMatrix, PinSeverity


def test_default_matrix_covers_all_pin_type_pairs():
    """Every (a, b) of the 12 types must produce a result without KeyError."""
    matrix = PinConflictMatrix.default()
    for t1 in ElectricalPinType:
        for t2 in ElectricalPinType:
            sev = matrix.check(t1, t2)
            assert sev in (PinSeverity.OK, PinSeverity.WARNING, PinSeverity.ERROR)


def test_output_to_output_is_error():
    """Two outputs on the same net is a driver conflict (KiCad default)."""
    matrix = PinConflictMatrix.default()
    assert matrix.check(ElectricalPinType.OUTPUT, ElectricalPinType.OUTPUT) == PinSeverity.ERROR


def test_power_output_to_power_output_is_error():
    """Two power rails shorted is a hard error."""
    matrix = PinConflictMatrix.default()
    assert (
        matrix.check(ElectricalPinType.POWER_OUT, ElectricalPinType.POWER_OUT)
        == PinSeverity.ERROR
    )


def test_output_to_power_output_is_error():
    """Logic output shorted to power rail is an error."""
    matrix = PinConflictMatrix.default()
    assert (
        matrix.check(ElectricalPinType.OUTPUT, ElectricalPinType.POWER_OUT) == PinSeverity.ERROR
    )


def test_no_connect_to_anything_is_error():
    """NC marker should never share a net with another pin."""
    matrix = PinConflictMatrix.default()
    assert matrix.check(ElectricalPinType.NO_CONNECT, ElectricalPinType.INPUT) == PinSeverity.ERROR
    assert matrix.check(ElectricalPinType.NO_CONNECT, ElectricalPinType.OUTPUT) == PinSeverity.ERROR
    nc_passive = matrix.check(ElectricalPinType.NO_CONNECT, ElectricalPinType.PASSIVE)
    assert nc_passive == PinSeverity.ERROR


def test_no_connect_to_unspecified_is_error():
    """Regression: rule-ordering bug overwrote NC <-> UNSPECIFIED to WARNING.

    The UNSPECIFIED warning loop used to run AFTER the NO_CONNECT error loop,
    silently downgrading the pair. NO_CONNECT must error with every type
    except FREE.
    """
    matrix = PinConflictMatrix.default()
    assert (
        matrix.check(ElectricalPinType.NO_CONNECT, ElectricalPinType.UNSPECIFIED)
        == PinSeverity.ERROR
    )
    assert (
        matrix.check(ElectricalPinType.UNSPECIFIED, ElectricalPinType.NO_CONNECT)
        == PinSeverity.ERROR
    )


def test_passive_to_anything_is_ok():
    """Passive pins (resistors, capacitors) are universally compatible
    except when paired with NO_CONNECT (which is always an error)."""
    matrix = PinConflictMatrix.default()
    for t in ElectricalPinType:
        if t == ElectricalPinType.NO_CONNECT:
            continue
        assert matrix.check(ElectricalPinType.PASSIVE, t) == PinSeverity.OK
        assert matrix.check(t, ElectricalPinType.PASSIVE) == PinSeverity.OK


def test_unspecified_to_signal_is_warning():
    """Unspecified pins next to defined signal pins generate a warning."""
    matrix = PinConflictMatrix.default()
    assert (
        matrix.check(ElectricalPinType.UNSPECIFIED, ElectricalPinType.INPUT)
        == PinSeverity.WARNING
    )
    assert (
        matrix.check(ElectricalPinType.UNSPECIFIED, ElectricalPinType.OUTPUT)
        == PinSeverity.WARNING
    )


def test_matrix_is_symmetric():
    """For every (a, b), check(a, b) == check(b, a)."""
    matrix = PinConflictMatrix.default()
    for t1 in ElectricalPinType:
        for t2 in ElectricalPinType:
            assert matrix.check(t1, t2) == matrix.check(t2, t1)


def test_set_rule_overrides_default():
    """Custom rules take precedence over defaults."""
    matrix = PinConflictMatrix.default()
    matrix.set_rule(ElectricalPinType.OUTPUT, ElectricalPinType.OUTPUT, PinSeverity.WARNING)
    assert matrix.check(ElectricalPinType.OUTPUT, ElectricalPinType.OUTPUT) == PinSeverity.WARNING


def test_set_rule_preserves_symmetry():
    """set_rule(a, b) must also affect check(b, a)."""
    matrix = PinConflictMatrix.default()
    matrix.set_rule(ElectricalPinType.INPUT, ElectricalPinType.OUTPUT, PinSeverity.WARNING)
    assert matrix.check(ElectricalPinType.OUTPUT, ElectricalPinType.INPUT) == PinSeverity.WARNING
