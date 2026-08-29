"""Debug why U2A matches voltage-follower."""
from pathlib import Path
from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from circuit_graph.primitives import load_primitives
from circuit_graph.vf2_matcher import Vf2TopologyMatcher
from scripts.debug_split import parse_net

net_file = Path("fixtures/protel/add-primitives.NET")
ir = parse_net(net_file)
ir2 = run_enrichers(ir, [MultiUnitSplitter()]).ir

templates = load_primitives()
vf_template = next(t for t in templates if t.id == "opamp-unit-voltage-follower")

print(f"Template: {vf_template.id}")
for comp in vf_template.components:
    print(f"  comp {comp.id}: pins={[(p.number, p.net) for p in comp.pins]}")

matcher = Vf2TopologyMatcher(ir2)
result = matcher.find_matches(vf_template, timeout_seconds=5.0)
print(f"\nMatches: {len(result.matches)}")
for m in result.matches:
    print(f"  mapping: {m.component_mapping}")
    print(f"  signals: {m.signal_mapping}")
    print(f"  evidence: {m.evidence}")
