"""Check primitive graph size."""
from circuit_graph.primitives import load_primitives, primitive_to_bipartite_graph

templates = load_primitives()
t = next(t for t in templates if t.id == 'opamp-unit-differential-amplifier')
g = primitive_to_bipartite_graph(t)
print(f'Primitive graph: {len(g.nodes)} nodes, {len(g.edges)} edges')
print('Nodes:', list(g.nodes()))
