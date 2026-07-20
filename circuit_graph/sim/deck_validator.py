"""DeckValidator — pre-flight checks on a SimulationDeck.

Catches common SPICE pitfalls before Ngspice runs them:
floating nodes, zero-impedance loops, missing analyses, empty netlists.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from circuit_graph.sim.spice_deck import SimulationDeck

# Regex to extract node names from a netlist line.
# Matches alphanumeric + underscore node names.
_NODE_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")

# Known ground node names that should not be flagged as floating.
_GROUND_NODES = frozenset({"0", "gnd", "GND", "GNDD", "GNDA", "AGND", "DGND", "PGND"})

# SPICE keywords that appear in netlist lines but are not node names.
_SPICE_KEYWORDS = frozenset({
    "DC", "AC", "SIN", "PULSE", "EXP", "PWL", "SFFM", "AM",
})


class DeckValidator:
    """Pre-flight validation for a :class:`SimulationDeck`.

    Validations are advisory — they produce diagnostics tuples but do not
    block execution.  Callers decide whether to abort based on severity.
    """

    def validate(self, deck: "SimulationDeck") -> tuple[str, ...]:
        """Run all checks and return diagnostic messages.

        Returns an empty tuple when the deck passes all checks.
        """
        diags: list[str] = []

        self._check_empty_netlist(deck, diags)
        self._check_analyses(deck, diags)
        self._check_floating_nodes(deck, diags)
        self._check_zero_impedance(deck, diags)

        return tuple(diags)

    # ── individual checks ──────────────────────────────────────────

    def _check_empty_netlist(self, deck: "SimulationDeck", diags: list[str]) -> None:
        netlist = deck.netlist.strip()
        subckts = "\n".join(deck.subckts)
        if not netlist and not subckts:
            diags.append("WARNING: netlist is empty (no components or subcircuits)")

    def _check_analyses(self, deck: "SimulationDeck", diags: list[str]) -> None:
        if not deck.analyses:
            diags.append("WARNING: no analysis commands (.OP, .AC, .TRAN, etc.)")

    def _check_floating_nodes(self, deck: "SimulationDeck", diags: list[str]) -> None:
        """Flag nodes that appear exactly once (possible floating node).

        A node with only one connection has no DC path to ground.
        """
        node_counts: dict[str, int] = {}
        for line in deck.netlist.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            # Skip the first token (component name)
            tokens = line.split()
            nodes = [t for t in tokens[1:] if _NODE_RE.match(t) and t not in _SPICE_KEYWORDS]
            for node in nodes:
                if node in _GROUND_NODES:
                    continue
                node_counts[node] = node_counts.get(node, 0) + 1

        for node, count in node_counts.items():
            if count == 1:
                diags.append(
                    f"WARNING: node '{node}' appears only once — possible floating node"
                )

    def _check_zero_impedance(self, deck: "SimulationDeck", diags: list[str]) -> None:
        """Flag 0-ohm resistors that may create short circuits."""
        for line in deck.netlist.strip().splitlines():
            line_stripped = line.strip()
            if not line_stripped or line_stripped.startswith("*"):
                continue
            if not line_stripped.upper().startswith("R"):
                continue
            tokens = line_stripped.split()
            if len(tokens) < 4:
                continue
            value = tokens[3]
            try:
                v = float(value)
                if v == 0.0:
                    diags.append(
                        f"WARNING: zero-ohm resistor '{tokens[0]}' detected — "
                        f"may short nodes {tokens[1]} and {tokens[2]}"
                    )
            except ValueError:
                # Non-numeric value (e.g. "10k") — parse with suffix
                v = _parse_spice_value(value)
                if v is not None and v == 0.0:
                    diags.append(
                        f"WARNING: zero-ohm resistor '{tokens[0]}' detected — "
                        f"may short nodes {tokens[1]} and {tokens[2]}"
                    )


def _parse_spice_value(raw: str) -> float | None:
    """Parse a SPICE-format value string (e.g. '10k', '1e3', '0.1u') to float.

    Returns None if unparseable.
    """
    raw = raw.strip().lower()
    multipliers: dict[str, float] = {
        "t": 1e12, "g": 1e9, "meg": 1e6, "x": 1e6,
        "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9,
        "p": 1e-12, "f": 1e-15, "mil": 25.4e-6,
    }
    try:
        return float(raw)
    except ValueError:
        pass
    # Try multiplier suffix
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if raw.endswith(suffix):
            try:
                return float(raw[: -len(suffix)]) * mult
            except ValueError:
                return None
    return None
