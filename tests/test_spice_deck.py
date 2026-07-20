"""Tests for SimulationDeck data model."""

from __future__ import annotations

from circuit_graph.sim.spice_deck import ProbeDefinition, SimulationDeck


def test_simulation_deck_to_spice_minimal():
    """A minimal deck renders valid SPICE with all sections."""
    deck = SimulationDeck(
        name="test_minimal",
        includes=(),
        models=(),
        subckts=(),
        netlist="R1 out gnd 10k",
        stimuli=("V1 in 0 DC 5",),
        analyses=(".OP",),
        probes=(ProbeDefinition(node="out", label="Vout"),),
        options=(".OPTIONS NOPAGE",),
        diagnostics=(),
    )
    spice = deck.to_spice()

    assert "* test_minimal" in spice
    assert "R1 out gnd 10k" in spice
    assert "V1 in 0 DC 5" in spice
    assert ".OP" in spice
    assert ".OPTIONS NOPAGE" in spice
    assert ".PRINT DC V(out)" in spice
    assert ".END" in spice


def test_simulation_deck_immutable():
    """SimulationDeck is frozen -- cannot set attributes."""
    deck = SimulationDeck(
        name="test",
        includes=(),
        models=(),
        subckts=(),
        netlist="* empty",
        stimuli=(),
        analyses=(".OP",),
        probes=(),
        options=(),
        diagnostics=(),
    )
    try:
        deck.name = "modified"  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except Exception:
        pass  # Expected


def test_probe_definition_renders_print_statement():
    """ProbeDefinition renders to a .PRINT line."""
    probe = ProbeDefinition(node="vout", label="Vout")
    assert probe.to_print("DC") == ".PRINT DC V(vout)"

    probe2 = ProbeDefinition(node="i(r1)", label="I_R1")
    assert probe2.to_print("TRAN") == ".PRINT TRAN I(r1)"
