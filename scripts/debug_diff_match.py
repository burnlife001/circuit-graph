"""Debug why U2A doesn't match differential-amplifier."""
from pathlib import Path
from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.primitives import load_primitives
from circuit_graph.vf2_matcher import Vf2TopologyMatcher
from scripts.debug_split import parse_net

net_file = Path("fixtures/protel/add-primitives.NET")
ir = parse_net(net_file)
ir2 = run_enrichers(ir, [MultiUnitSplitter()]).ir

templates = load_primitives()
diff_template = next(t for t in templates if t.id == "opamp-unit-differential-amplifier")

print(f"Template: {diff_template.id}")
for comp in diff_template.components:
    print(f"  comp {comp.id} (required={comp.required}): pins={[(p.number, p.net) for p in comp.pins]}")

matcher = Vf2TopologyMatcher(ir2)
result = matcher.find_matches(diff_template, timeout_seconds=5.0)
print(f"\nMatches: {len(result.matches)}")
for m in result.matches:
    print(f"  mapping: {m.component_mapping}")
    print(f"  signals: {m.signal_mapping}")
    print(f"  evidence: {m.evidence}")
