"""End-to-end coverage: every primitive template must fire on at least one
real Protel .NET fixture.

Each case runs the full enricher chain (MultiUnitSplitter -> NetClassifier
-> EdgeSemanticEnricher -> TopologyRecognizer) on a fixture and asserts that
the expected block exists with the expected anchor component.

Fixtures are recognised once per module and cached — recognition on the
large boards (FX-T268, 500W series) is the slow part.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.enrichers.net_classifier import NetClassifier
from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from circuit_graph.primitives import load_primitives

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "protel"

# (primitive_id, fixture_file, expected_block_id_prefix, expected_component_subset)
CASES = [
    # --- amplifier ---
    ("differential-comparator", "500W-TX-Main-Zero.NET", "differential-comparator-u5", {"U5"}),
    ("inverting-amplifier", "FX-T268-V6-10米-5065.NET", "inverting-amplifier-u7", {"U7"}),
    ("opamp-unit-differential-amplifier", "add-primitives.NET",
     "opamp-unit-differential-amplifier-u2b", {"U2B", "R4"}),
    ("opamp-unit-voltage-follower", "add-primitives.NET",
     "opamp-unit-voltage-follower-u2c", {"U2C"}),
    ("sallen-key-low-pass", "extra-primitives.NET", "sallen-key-low-pass-u1",
     {"U1", "R1", "R2", "C1", "C2"}),
    # --- filter ---
    ("lc-pi-filter", "500W-RX.NET", "lc-pi-filter-c14", {"C14"}),
    ("rc-high-pass", "500W-RX.NET", "rc-high-pass-c3", {"C3"}),
    ("rc-low-pass", "500W-RX.NET", "rc-low-pass-c1", {"C1"}),
    # --- interface ---
    ("fet-level-shifter", "extra-primitives.NET", "fet-level-shifter-q1", {"Q1", "R3", "R4"}),
    # --- power ---
    ("bridge-rectifier-package", "500W-TX-Main-Zero.NET", "bridge-rectifier-package-bd1", {"BD1"}),
    ("bridge-rectifier", "add-primitives.NET", "bridge-rectifier-d1", {"D1"}),
    ("current-shunt-monitor", "433_RX.NET", "current-shunt-monitor-r1", {"R1"}),
    ("integrated-bridge-rectifier", "extra-primitives.NET",
     "integrated-bridge-rectifier-bd1", {"BD1"}),
    ("peak-envelope-detector", "FX-T268-V6-10米-5065.NET",
     "peak-envelope-detector-c56", {"D2", "R54", "R49", "R58", "C56"}),
    ("voltage-divider", "500W-RX.NET", "voltage-divider-r1", {"R1"}),
    # --- protection ---
    ("flyback-diode", "extra-primitives.NET", "flyback-diode-d1", {"D1", "L1"}),
    ("tvs-protection", "500W-TX-Main.NET", "tvs-protection-d5", {"D5"}),
    # --- switch ---
    ("nmos-low-side-switch", "500W-TX-Main-Zero.NET", "nmos-low-side-switch-q1", {"Q1"}),
    ("npn-low-side-switch", "500W-TX-Main-Zero.NET", "npn-low-side-switch-esd2", {"ESD2"}),
    ("pmos-high-side-switch", "FX-T268-V6-10米-5065.NET", "pmos-high-side-switch-q2", {"Q2"}),
    # --- termination ---
    ("parallel-termination", "extra-primitives.NET", "parallel-termination-r5", {"R5", "R6"}),
    ("pull-down-resistor", "500W-RX.NET", "pull-down-resistor-r2", {"R2"}),
    ("pull-up-resistor", "433_RX.NET", "pull-up-resistor-r3", {"R3"}),
    ("type-c-cc-pull-down", "FX-T268-V6-10米-5065.NET", "type-c-cc-pull-down-r1", {"R1"}),
    # --- timing ---
    ("crystal-with-load-caps", "add-primitives.NET", "crystal-with-load-caps-c1", {"C1"}),
]


def _recognize(fixture_name: str):
    netlist = FIXTURE_DIR / fixture_name
    if not netlist.exists():
        pytest.skip(f"Fixture not found: {netlist}")
    ir = ProtelNetParser().parse(ParserInput(file_path=str(netlist)))
    return run_enrichers(
        ir,
        [MultiUnitSplitter(), NetClassifier(), EdgeSemanticEnricher(), TopologyRecognizer()],
    ).ir


@pytest.fixture(scope="module")
def recognized_cache():
    return {}


@pytest.mark.parametrize(
    "primitive_id,fixture,block_prefix,expected_comps",
    CASES,
    ids=[f"{c[0]}-on-{c[1]}" for c in CASES],
)
def test_primitive_fires_on_fixture(
    primitive_id, fixture, block_prefix, expected_comps, recognized_cache
):
    if fixture not in recognized_cache:
        recognized_cache[fixture] = _recognize(fixture)
    ir2 = recognized_cache[fixture]

    blocks = [b for b in ir2.blocks if b.id.startswith(block_prefix)]
    assert blocks, (
        f"No block starting with {block_prefix!r} in {fixture}; "
        f"got: {sorted(b.id for b in ir2.blocks)}"
    )
    assert any(expected_comps <= set(b.component_ids) for b in blocks), (
        f"Expected {block_prefix} block covering {sorted(expected_comps)}; "
        f"got: {[(b.id, sorted(b.component_ids)) for b in blocks]}"
    )


def test_every_primitive_has_a_fixture_case():
    """Guard: adding a new primitive JSON without a fixture case fails here."""
    template_ids = {t.id for t in load_primitives()}
    covered = {c[0] for c in CASES}
    missing = template_ids - covered
    assert not missing, f"Primitives without a fixture case: {sorted(missing)}"
