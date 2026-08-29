"""Debug sallen-key matching."""
from circuit_graph.primitives import load_primitives, circuit_to_bipartite_graph, primitive_to_bipartite_graph
from circuit_graph.vf2_matcher import Vf2TopologyMatcher, _node_match_circuit_to_primitive
from tests.test_vf2_matcher import _sallen_key_ir

ir = _sallen_key_ir()
templates = load_primitives()
template = next(t for t in templates if t.id == "sallen-key-low-pass")

circ_g = circuit_to_bipartite_graph(ir)
prim_g = primitive_to_bipartite_graph(template)

print("=== Circuit nodes ===")
for node, attrs in circ_g.nodes(data=True):
    if 'U10' in node:
        pin = attrs.get('pin')
        print(f"  {node}: pin={pin.number if pin else None}")

print("\n=== Primitive nodes ===")
for node, attrs in prim_g.nodes(data=True):
    if 'U_op' in node:
        pin = attrs.get('pin')
        print(f"  {node}: pin={pin.number if pin else None}")

print("\n=== Node compatibility ===")
for circ_node, circ_attrs in circ_g.nodes(data=True):
    if 'U10' in circ_node:
        for prim_node, prim_attrs in prim_g.nodes(data=True):
            if 'U_op' in prim_node:
                match = _node_match_circuit_to_primitive(circ_attrs, prim_attrs, template, ir)
                circ_pin = circ_attrs.get('pin')
                prim_pin = prim_attrs.get('pin')
                print(f"  {circ_node}({circ_pin.number if circ_pin else '?'}) vs {prim_node}({prim_pin.number if prim_pin else '?'}): {match}")
