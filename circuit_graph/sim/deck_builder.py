"""DeckBuilder — SPICE deck assembly.

Phase 1: hardcoded LM321 non-inverting amplifier proof-of-concept.
Phase 3+: reads v2 primitive ``simulation`` fields for template-driven assembly.
"""

from __future__ import annotations

from circuit_graph.sim.spice_deck import ProbeDefinition, SimulationDeck
from circuit_graph.sim.stimulus import StimulusSelector


class DeckBuilder:
    """Build :class:`SimulationDeck` instances.

    Phase 1 provides a single hardcoded method for the LM321 non-inverting
    amplifier.  Phase 3 will add template-driven methods that consume v2
    primitive data.
    """

    def __init__(self) -> None:
        self._stimulus = StimulusSelector()

    def build_lm321_non_inverting(
        self,
        *,
        rf: str = "10k",
        rg: str = "1k",
        vcc: str = "5V",
    ) -> SimulationDeck:
        """Build a deck for an LM321 non-inverting amplifier.

        Topology:
            VCC -> LM321 V+
            GND -> LM321 V-
            Rf  between OUT and IN-
            Rg  between IN- and GND
            AC 1.0V stimulus at IN+

        Gain formula: Av = 1 + Rf/Rg

        With defaults Rf=10k, Rg=1k -> Av ~= 11.
        """
        # Ideal opamp model: voltage-controlled voltage source with high gain
        # .SUBCKT IDEAL_OPAMP_1CH  in_p  in_n  vcc  vee  out
        opamp_model = (
            "* Ideal single-supply opamp model (LM321-like)\n"
            ".SUBCKT IDEAL_OPAMP_1CH in_p in_n vcc vee out\n"
            "E1 out vee in_p in_n 1e5\n"
            ".ENDS IDEAL_OPAMP_1CH\n"
        )
        opamp_instance = "XOPAMP in_p in_n vcc 0 out IDEAL_OPAMP_1CH"

        # Full netlist
        netlist = "\n".join([
            "* LM321 Non-Inverting Amplifier (hardcoded PoC)",
            f"Rf out in_n {rf}",
            f"Rg in_n 0 {rg}",
            f"Rload out 0 10k",
            f"VDC vcc 0 DC {vcc}",
        ])

        # Phase 1: DC bias on input so opamp has a non-zero operating point
        stimuli = ("VIN in_p 0 DC 0.5",)
        analyses = (".OP",)

        probes = (
            ProbeDefinition(
                node="out",
                label="Vout",
                expected=(
                    ("gain_min", 9.0),
                    ("gain_max", 13.0),
                ),
            ),
            ProbeDefinition(node="in_p", label="Vin"),
        )

        return SimulationDeck(
            name="LM321 Non-Inverting Amplifier (Phase 1 PoC)",
            models=(opamp_model,),
            subckts=(opamp_instance,),
            netlist=netlist,
            stimuli=stimuli,
            analyses=analyses,
            probes=probes,
            options=(".OPTIONS NOPAGE NOECHO",),
        )
