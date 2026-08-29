"""Check if R4 exists in split IR."""
from pathlib import Path
from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from scripts.debug_split import parse_net

ir = parse_net(Path('fixtures/protel/add-primitives.NET'))
ir2 = run_enrichers(ir, [MultiUnitSplitter()]).ir

r4 = next((c for c in ir2.components if c.id == 'R4'), None)
if r4:
    print(f'R4: pins={[(p.number, p.id) for p in r4.pins]}')
    for conn in ir2.connections:
        if conn.pin_id.startswith('R4.'):
            net = next(n for n in ir2.nets if n.id == conn.net_id)
            print(f'  {conn.pin_id} -> {net.name}')
else:
    print('R4 not found')

# Check all components
print('\nAll components:')
for c in ir2.components:
    if c.id.startswith('R'):
        print(f'  {c.id}')
