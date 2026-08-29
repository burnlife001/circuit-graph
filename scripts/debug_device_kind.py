"""Debug component_map device_kind."""
from pathlib import Path
from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from scripts.debug_split import parse_net

ir = parse_net(Path('fixtures/protel/add-primitives.NET'))
ir2 = run_enrichers(ir, [MultiUnitSplitter()]).ir

component_map = {c.id: c for c in ir2.components}
for cid in ['R4', 'U2B']:
    if cid in component_map:
        c = component_map[cid]
        print(f'{cid}: device_kind={c.device_kind}, value={c.device_kind.value}')
