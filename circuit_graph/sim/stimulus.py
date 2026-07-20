"""Stimulus selector — topology category -> default stimulus + analyses.

Phase 1: pure fallback table, no dependency on v2 primitives.
Phase 3+: v2 primitive ``simulation`` fields override these defaults.
"""

from __future__ import annotations

#: Mapping from topology category to (stimuli, analyses).
#: Each value is (list_of_stimulus_lines, list_of_analysis_lines).
_CATEGORY_TABLE: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "opamp_amplifier": (
        # stimuli
        ("V1 {node} 0 DC 0 AC 1.0",),
        # analyses
        (".OP", ".AC DEC 10 1 1e6"),
    ),
    "opamp_comparator": (
        ("V1 {node} 0 PULSE(0 5 0 1n 1n 10u 20u)",),
        (".OP", ".TRAN 1u 40u"),
    ),
    "opamp_buffer": (
        ("V1 {node} 0 DC 0 AC 1.0",),
        (".OP", ".AC DEC 10 1 1e6"),
    ),
    "opamp_integrator": (
        ("V1 {node} 0 PULSE(0 1 0 1n 1n 50u 100u)",),
        (".OP", ".TRAN 1u 200u"),
    ),
    "dcdc_converter": (
        ("V_SUPPLY {node} 0 DC 12",),
        (".OP", ".TRAN 1u 500u"),
    ),
    "ldo": (
        ("VIN {node} 0 DC 5",),
        (".OP",),
    ),
    "filter_active": (
        ("V1 {node} 0 DC 0 AC 1.0",),
        (".OP", ".AC DEC 20 1 10e6"),
    ),
    "filter_passive": (
        ("V1 {node} 0 DC 0 AC 1.0",),
        (".OP", ".AC DEC 10 1 1e6"),
    ),
    "oscillator": (
        (),
        (".TRAN 1n 10u",),
    ),
}


class StimulusSelector:
    """Select default stimulus and analyses for a topology category.

    This is the fallback used when the primitive has no ``simulation`` field
    (v1 primitives or Phase 1 hardcoded path).  Phase 3+ will prefer the
    v2 primitive's ``simulation.stimulus`` and ``simulation.analyses`` fields.
    """

    def select(self, category: str, *, node: str = "1") -> tuple[str, ...]:
        """Return stimulus lines for the given topology category.

        Args:
            category: Topology category key, e.g. ``"opamp_amplifier"``.
            node: SPICE node name to inject the stimulus at.

        Returns:
            Tuple of SPICE stimulus lines, or empty tuple if unknown/not needed.
        """
        entry = _CATEGORY_TABLE.get(category)
        if entry is None:
            return ()
        stimuli, _ = entry
        return tuple(line.format(node=node) for line in stimuli)

    def default_analyses(self, category: str) -> tuple[str, ...]:
        """Return default analysis lines for the given topology category.

        Args:
            category: Topology category key.

        Returns:
            Tuple of SPICE analysis lines (``.OP``, ``.AC ...``, etc.).
        """
        entry = _CATEGORY_TABLE.get(category)
        if entry is None:
            return (".OP",)
        _, analyses = entry
        return analyses
