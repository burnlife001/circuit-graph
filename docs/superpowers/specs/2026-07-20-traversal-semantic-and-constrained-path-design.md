# Traversal Edge-Semantic Filtering & Constrained Shortest Path

- Date: 2026-07-20
- Status: Approved design — pending implementation
- Source: `todolist_fix.md` tasks 5 & 6
- Owner: circuit-graph query engine

## Goal

Unlock two query capabilities that the current traversal engine cannot express:

1. **Edge-semantic-aware traversal** (task 5): filter BFS/DFS walks by the `direction` / `role` annotation that `EdgeSemanticEnricher` writes onto each `GraphEdge.metadata["edge_semantic"]`.
2. **Constrained shortest path** (task 6): find one shortest path between two pins that satisfies user-supplied predicates — enabling "USB.5V → U2.VDD via power only" and "DC-only path (avoid series capacitors)" without writing one-off scripts.

Together these turn the recognizer-style "preset predicate combo" pattern from task 3 into a generic, user-driven facility.

## Non-Goals

- No Cypher / DSL parser. Predicates are Python dataclass fields.
- No multi-criterion optimization (Pareto, weighted cost). One shortest path, period.
- No new graph storage. Reuse `CircuitGraph._get_edges` adjacency lookup introduced in commit `f8b212a`.
- No L0 physical layer (pad/via/trace). Out of scope per todolist.

## Design

### Task 5 — Extend `TraversalOptions`

File: `circuit_graph/ir.py` (around line 251, the existing dataclass).

Add two optional list fields with `None` default — backward-compatible:

```python
pin_roles: list[str] | None = None        # whitelist on edge.metadata.edge_semantic.role
edge_directions: list[str] | None = None  # whitelist on edge.metadata.edge_semantic.direction
```

Filter semantics — applied **at edge expansion time**, not at node-filter time:

- Existing `net_types` / `device_kinds` (task 2) filter which **target nodes** are kept.
- New `pin_roles` / `edge_directions` filter which **edges** are crossed.
- Both default to `None` (no filter) → bit-for-bit identical behavior to current code.

Integration point: `circuit_graph/graph.py` — `CircuitGraph._traverse`, the single shared BFS/DFS engine. Inside the `for edge in self._get_edges(...)` loop, before extending the queue, read `edge.metadata.get("edge_semantic", {})` and skip when `role`/`direction` is set on the option but the edge's value is not in the whitelist.

Extracted predicate helper to avoid duplication with task 6:

```python
# circuit_graph/query.py
def edge_matches_semantic(edge: GraphEdge, options) -> bool:
    sem = edge.metadata.get("edge_semantic") or {}
    if options.pin_roles is not None and sem.get("role") not in options.pin_roles:
        return False
    if options.edge_directions is not None and sem.get("direction") not in options.edge_directions:
        return False
    return True
```

`_traverse` calls it. `find_constrained_path` (task 6) calls it. One implementation, two consumers.

### Task 6 — Constrained Shortest Path

#### Public API

`circuit_graph/query.py`:

```python
@dataclass
class PathOptions:
    pin_roles: list[str] | None = None
    edge_directions: list[str] | None = None
    forbid_component_kinds: list[DeviceKind] | None = None
    forbid_net_types: list[NetType] | None = None
    max_path_length: int | None = None


@dataclass
class PathResult:
    nodes: list[str]            # ordered, src ... dst
    edges: list[str]            # same length as nodes minus 1
    length: int                 # number of edges
    constraints: PathOptions    # echo back so CLI can print what was applied


def find_constrained_path(
    graph: CircuitGraph,
    src: str,
    dst: str,
    options: PathOptions | None = None,
) -> PathResult | None:
    """Return one shortest path src→dst satisfying options, or None if none exists."""
```

#### Algorithm

Plain BFS with edge predicates:

1. Validate `src` and `dst` exist in `graph._node_map`. Missing → return `None` (not an error — querying an unknown pin in the CLI is a soft failure, exit 0).
2. Initialize `parents: dict[str, str | None] = {src: None}`, queue `[src]`.
3. Pop node; if equal to `dst`, walk `parents` backwards to reconstruct path; return.
4. For each neighbor via `graph._get_edges(node, "both")`:
   - Reject if `edge_matches_semantic(edge, options) is False`.
   - Reject if either endpoint node (component / net) is forbidden by `forbid_component_kinds` / `forbid_net_types`.
   - Reject if `max_path_length` is set and `len(path_so_far) + 1 > max_path_length`.
   - Reject if neighbor already in `parents` (visited).
5. Queue survivor with `parents[neighbor] = node`. Continue.
6. Queue exhausted → `None`.

Complexity: O(V + E) per query, identical to existing `traverse_bfs`. No Dijkstra needed because edge weight is uniformly 1.

#### CLI surface

`circuit_graph/cli.py` — new top-level subcommand `cg path`:

```
cg path <src> <dst> [--pin-role ROLE]...
                [--direction DIR]...
                [--forbid-component-kind KIND]...
                [--forbid-net-type TYPE]...
                [--max-length N]
                [--pretty]
                <NETLIST>
```

- Repeated flags accumulate into the corresponding `PathOptions` list (Python's argparse `action="append"`).
- `--pretty` switches JSON output from compact to indented.
- No path found → exit 0, JSON `{"path": null, "constraints": {...}}`, stderr `"no path matching constraints"`.
- `--with-power` / `--with-loads` style aliases: **not added in this change**. The flag vocabulary stays minimal; users compose predicates explicitly.

Output schema:

```json
{
  "src": "U1.5",
  "dst": "U2.14",
  "path": {
    "nodes": ["U1.5", "VCC_5V", "U2.14"],
    "edges": ["e1", "e7"],
    "length": 2
  },
  "constraints": {
    "pin_roles": ["power"],
    "forbid_component_kinds": null
  }
}
```

When no path: `"path": null`, same shape otherwise.

#### Power-tree recognizer integration

**Do not** wire `find_constrained_path` into `power_tree_recognizer._loads_on_net`. The recognizer was rewritten in commit `05b6c54` using a different internal BFS shape and works; replacing it now risks silent regressions and forces an awkward `PathOptions` conversion. Add a `# TODO(consolidation): power_tree_recognizer may adopt find_constrained_path once predicate call site stabilizes` comment instead.

## Data Model

No schema changes. Both tasks operate on existing structures:

- `TraversalOptions` (ir.py) — add 2 fields.
- `PathOptions` (query.py) — new dataclass, mirrors `TraversalOptions` semantic fields plus two new `forbid_*` lists.
- `PathResult` (query.py) — new return type.
- `GraphEdge.metadata` — already a free-form dict; `EdgeSemanticEnricher` writes `{"direction", "role", "level"}` under key `"edge_semantic"`. Read-only consumer here.

## Error Handling

| Condition | Behavior |
|---|---|
| `src` / `dst` not in graph | Return `None`. CLI: exit 0, JSON `path: null`, stderr note. |
| `src == dst` | Return `PathResult(nodes=[src], edges=[], length=0)`. |
| Constraints impossible | BFS exhausts → `None`. Same as above. |
| `EdgeSemanticEnricher` not run on the IR | All edges have `metadata["edge_semantic"] = None` → semantic filters match nothing → result is `None`. CLI emits a one-time warning `"hint: run EdgeSemanticEnricher first to populate edge semantics"`. |
| Unknown `--pin-role` value (typo) | No match → `None`. No schema-enforced enum; the field is `list[str]` because the enricher's `role` vocabulary is open (power/gnd/analog/digital/clock today, expandable tomorrow). |

## Testing

### Unit — `tests/test_query.py` (new file or top of existing)

| Test | Asserts |
|---|---|
| `test_traverse_with_pin_role` | Construct graph with two pin-net edges of different roles. `_traverse` with `pin_roles=["power"]` skips the non-power edge. |
| `test_traverse_with_edge_direction` | Same shape, `edge_directions=["load"]` only walks edges tagged `direction=load`. |
| `test_traverse_semantic_none_is_noop` | With `pin_roles=None, edge_directions=None`, output equals current traversal — guards the `None`-default contract. |
| `test_find_constrained_path_simple` | A–B single hop returns `length=1`. |
| `test_find_constrained_path_no_path` | Disconnected graph → `None`. |
| `test_find_constrained_path_src_eq_dst` | Returns `length=0`. |
| `test_find_constrained_path_forbid_component` | A–[R]–B, `forbid_component_kinds=[RESISTOR]` → `None`. |
| `test_find_constrained_path_forbid_net_type` | Path traverses a GND net, `forbid_net_types=[NetType.GROUND]` → `None`. |
| `test_find_constrained_path_max_length` | `max_path_length=1` blocks a 2-hop path → `None`. |
| `test_find_constrained_path_returns_shortest` | Two paths of length 2 and 3; result is length 2. |
| `test_find_constrained_path_unknown_node` | `src="ghost"` → `None`. |

### Integration — `tests/test_cli.py` (extend existing)

| Test | Asserts |
|---|---|
| `test_cg_path_basic` | `cg path U1.5 U2.14 fixtures/protel/FX-T268-V6-10米-5065.NET` exits 0 and JSON contains `nodes` array. |
| `test_cg_path_pin_role_power` | With `--pin-role power` and a real power-rail example, output path contains only `power` net. |
| `test_cg_path_no_path` | With `--pin-role does_not_exist`, output `path: null`, exit 0. |
| `test_cg_path_pretty` | `--pretty` flag produces indented JSON. |

### Manual smoke

```
cg path U1.5V U2.VDD fixtures/protel/FX-T268-V6-10米-5065.NET --pin-role power --pretty
cg path USB.5V U2.VDD fixtures/protel/FX-T268-V6-10米-5065.NET \
    --pin-role power --forbid-component-kind capacitor --pretty
```

## Rollout

Two commits, each independently releasable:

1. **Commit 1 — task 5**: `feat(ir/query): TraversalOptions 支持 pin_roles/edge_directions 边语义过滤`
   - Files: `circuit_graph/ir.py`, `circuit_graph/query.py` (helper), `circuit_graph/graph.py` (`_traverse` predicate), `tests/test_query.py`, `tests/test_graph.py`.
   - Readme update: document the two new fields in the `TraversalOptions` section.
   - Push.

2. **Commit 2 — task 6**: `feat(query/cli): find_constrained_path + cg path 子命令`
   - Files: `circuit_graph/query.py` (`PathOptions`, `PathResult`, `find_constrained_path`), `circuit_graph/cli.py` (`cg path`), `tests/test_query.py`, `tests/test_cli.py`.
   - Readme update: document `cg path` usage with the two smoke examples above.
   - Push.

## Open Questions

None — both clarifying questions resolved by user during brainstorming:

- CLI surface → new subcommand `cg path` (not `--path` flag on `cg view`).
- DC-only expression → generic `--forbid-component-kind` (not hardcoded `--dc-only`).

## Acceptance Criteria

- All existing tests green after each commit.
- New tests cover the matrix above.
- `cg path` works on the standard fixture `fixtures/protel/FX-T268-V6-10米-5065.NET`.
- `drivers(net)`-style query reproducible via `find_constrained_path` semantics (added as a docstring example, not a new public function — avoids API bloat).