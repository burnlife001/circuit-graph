"""Analyze a single component's neighborhood from a Protel netlist.

Usage:
    uv run python scripts/analyze_component.py <designator> <netlist> [--hops N]

Example:
    uv run python scripts/analyze_component.py R96 "fixtures/protel/FX-T268-V6-10米-5065.NET" --hops 3
"""

import argparse
import json
import subprocess
import sys


def analyze(designator: str, netlist: str, hops: int = 2) -> dict:
    """Run ``cg view`` and return the parsed neighborhood graph."""
    result = subprocess.run(
        ["uv", "run", "cg", "view", designator, netlist, "--hops", str(hops), "--pretty"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def print_component(comp: dict) -> None:
    """Pretty-print the focal component's attributes."""
    summary = {
        "id": comp.get("id"),
        "type": comp.get("type"),
        "value": comp.get("value") or comp.get("part_number"),
        "package": comp.get("package"),
        "pins": [{"pin": p["pin"], "net": p["net"]} for p in comp.get("pins", [])],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def print_neighbors(graph: dict, nets: set[str]) -> None:
    """List every component attached to each of the focal nets."""
    components = graph.get("components", {})
    for net in sorted(nets):
        print(f"\nNET {net}:")
        for cid, c in sorted(components.items()):
            for pin in c.get("pins", []):
                if pin.get("net") == net:
                    value = c.get("value") or c.get("part_number", "")
                    print(f"  {cid} pin{pin['pin']} ({value})")


def print_neighbors_by_type(graph: dict, nets: set[str], kind: str) -> None:
    """List neighbors of a specific component type touching the focal nets."""
    components = graph.get("components", {})
    print(f"\n== {kind.capitalize()}s on focal nets ==")
    found = False
    for cid, c in sorted(components.items()):
        if c.get("type") != kind:
            continue
        pin_nets = [p["net"] for p in c.get("pins", [])]
        if set(pin_nets) & nets:
            found = True
            value = c.get("value") or c.get("part_number", "")
            print(f"  {cid} {value} -> {pin_nets}")
    if not found:
        print(f"  (no {kind}s found)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a component's local neighborhood in a netlist."
    )
    parser.add_argument("designator", help="Component designator, e.g. R96")
    parser.add_argument("netlist", help="Path to the Protel .NET file")
    parser.add_argument("--hops", type=int, default=2, help="Neighborhood radius (default: 2)")
    args = parser.parse_args()

    graph = analyze(args.designator, args.netlist, args.hops)
    components = graph.get("components", {})
    if args.designator not in components:
        print(f"Component {args.designator} not found in {args.netlist}", file=sys.stderr)
        return 1

    focal = components[args.designator]
    focal_nets = {p["net"] for p in focal.get("pins", [])}

    print(f"== {args.designator} ==")
    print_component(focal)
    print_neighbors(graph, focal_nets)
    print_neighbors_by_type(graph, focal_nets, "resistor")
    print_neighbors_by_type(graph, focal_nets, "capacitor")
    print_neighbors_by_type(graph, focal_nets, "inductor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
