"""Debug VF2 matching for differential-amplifier on U2A."""
from pathlib import Path
from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.primitives import load_primitives, circuit_to_bipartite_graph, primitive_to_bipartite_graph
from scripts.debug_split import parse_net

ir = parse_net(Path('fixtures/protel/add-primitives.NET'))
ir2 = run_enrichers(ir, [MultiUnitSplitter()]).ir

templates = load_primitives()
diff_template = next(t for t in templates if t.id == "opamp-unit-differential-amplifier")

# Build graphs
circ_g = circuit_to_bipartite_graph(ir2)
prim_g = primitive_to_bipartite_graph(diff_template)

print("=== Circuit graph nodes ===")
for node, attrs in circ_g.nodes(data=True):
    if 'U2A' in node or 'R4' in node:
        print(f"  {node}: kind={attrs.get('kind')}, pin={attrs.get('pin')}")

print("\n=== Circuit graph edges ===")
for u, v in circ_g.edges():
    if ('U2A' in u or 'R4' in u) and ('U2A' in v or 'R4' in v or 'Net' in v):
        print(f"  {u} <-> {v}")

print("\n=== Primitive graph nodes ===")
for node, attrs in prim_g.nodes(data=True):
    print(f"  {node}: kind={attrs.get('kind')}, pin={attrs.get('pin')}")

print("\n=== Primitive graph edges ===")
for u, v in prim_g.edges():
    print(f"  {u} <-> {v}")
