"""Example: load a GraphML file exported by ``cg graph graphml`` with NetworkX."""

import sys
from pathlib import Path

import networkx as nx

DEFAULT_GRAPHML = Path(__file__).resolve().parent.parent / "output" / "circuit.graphml"


def main() -> None:
    graphml_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_GRAPHML
    if not graphml_path.exists():
        print(f"GraphML file not found: {graphml_path}")
        print(
            "Generate one with: "
            "uv run cg graph graphml <file.net> -o output/circuit.graphml"
        )
        sys.exit(1)

    g = nx.read_graphml(graphml_path)

    print("== GraphML Stats ==")
    print(f"Nodes: {g.number_of_nodes()}")
    print(f"Edges: {g.number_of_edges()}")

    kinds: dict[str, int] = {}
    for _node, data in g.nodes(data=True):
        kinds[data.get("kind", "unknown")] = kinds.get(data.get("kind", "unknown"), 0) + 1
    print("\n== Node kinds ==")
    for kind, count in sorted(kinds.items()):
        print(f"  {kind}: {count}")

    edge_kinds: dict[str, int] = {}
    for _u, _v, data in g.edges(data=True):
        kind = data.get("kind", "unknown")
        edge_kinds[kind] = edge_kinds.get(kind, 0) + 1
    print("\n== Edge kinds ==")
    for kind, count in sorted(edge_kinds.items()):
        print(f"  {kind}: {count}")

    print("\n== R70 node attributes ==")
    if "R70" in g.nodes:
        for key, value in sorted(g.nodes["R70"].items()):
            print(f"  {key}: {value}")

    print("\n== R70 neighbors via connects_to/has_pin ==")
    if "R70" in g.nodes:
        for neighbor in nx.all_neighbors(g, "R70"):
            edge_data = g.edges.get(("R70", neighbor)) or g.edges.get((neighbor, "R70"))
            kind = edge_data.get("kind", "unknown") if edge_data else "unknown"
            print(f"  {neighbor} ({kind})")


if __name__ == "__main__":
    main()
