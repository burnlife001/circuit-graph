"""Debug sallen-key graph structure."""
from circuit_graph.primitives import load_primitives, circuit_to_bipartite_graph, primitive_to_bipartite_graph
from tests.test_vf2_matcher import _sallen_key_ir

ir = _sallen_key_ir()
templates = load_primitives()
template = next(t for t in templates if t.id == "sallen-key-low-pass")

circ_g = circuit_to_bipartite_graph(ir)
prim_g = primitive_to_bipartite_graph(template)

print("=== Circuit graph ===")
print(f"Nodes: {len(circ_g.nodes)}")
print(f"Edges: {len(circ_g.edges)}")
print("\nEdges:")
for u, v in circ_g.edges():
    print(f"  {u} <-> {v}")

print("\n=== Primitive graph ===")
print(f"Nodes: {len(prim_g.nodes)}")
print(f"Edges: {len(prim_g.edges)}")
print("\nEdges:")
for u, v in prim_g.edges():
    print(f"  {u} <-> {v}")
