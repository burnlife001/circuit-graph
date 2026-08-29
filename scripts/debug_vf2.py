"""Manual VF2 test for differential-amplifier on U2A."""
from pathlib import Path
from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.primitives import load_primitives, circuit_to_bipartite_graph, primitive_to_bipartite_graph
from circuit_graph.vf2_matcher import _SemanticGraphMatcher, _node_match_circuit_to_primitive
from scripts.debug_split import parse_net

ir = parse_net(Path('fixtures/protel/add-primitives.NET'))
ir2 = run_enrichers(ir, [MultiUnitSplitter()]).ir

templates = load_primitives()
diff_template = next(t for t in templates if t.id == "opamp-unit-differential-amplifier")

circ_g = circuit_to_bipartite_graph(ir2)
prim_g = primitive_to_bipartite_graph(diff_template)

def node_match(circ_attrs, prim_attrs):
    return _node_match_circuit_to_primitive(circ_attrs, prim_attrs, diff_template, ir2)

gm = _SemanticGraphMatcher(
    circ_g,
    prim_g,
    template=diff_template,
    edge_semantics={},
    node_match=node_match,
)

print("Searching for matches...")
count = 0
for mapping in gm.subgraph_isomorphisms_iter():
    count += 1
    print(f"\nMatch {count}:")
    for circ_key, prim_key in mapping.items():
        print(f"  {prim_key} -> {circ_key}")
    if count >= 3:
        break

if count == 0:
    print("No matches found")

    # Debug: check node compatibility
    print("\n=== Node compatibility check ===")
    for circ_node, circ_attrs in circ_g.nodes(data=True):
        if 'U2A' in circ_node or 'R4' in circ_node:
            for prim_node, prim_attrs in prim_g.nodes(data=True):
                if 'opamp' in prim_node or 'Rf' in prim_node:
                    match = node_match(circ_attrs, prim_attrs)
                    print(f"  {circ_node} vs {prim_node}: {match}")

    # Debug: check net compatibility
    print("\n=== Net compatibility check ===")
    for circ_node, circ_attrs in circ_g.nodes(data=True):
        if circ_node.startswith('n:'):
            circ_net = circ_attrs.get('data')
            if circ_net and ('NetR4' in circ_net.name or 'NetR6' in circ_net.name):
                for prim_node, prim_attrs in prim_g.nodes(data=True):
                    if prim_node.startswith('n:'):
                        prim_sig = prim_attrs.get('data')
                        if prim_sig:
                            match = node_match(circ_attrs, prim_attrs)
                            print(f"  {circ_node}({circ_net.name}) vs {prim_node}({prim_sig.id}): {match}")
