"""检查 differential-amplifier 在 add-primitives.NET 上是否匹配到 U2。"""
from pathlib import Path
from circuit_graph.parsers.protel import parse_file
from circuit_graph.primitives import load_primitives, load_primitive
from circuit_graph.vf2_matcher import Vf2TopologyMatcher
from circuit_graph.scoring import score_matches

net = Path("fixtures/protel/add-primitives.NET")
ir = parse_file(net)

tpl_dir = Path("primitives")
# 尝试只加载 differential-amplifier 一个模板
try:
    template = load_primitive(tpl_dir / "amplifier" / "differential-amplifier.json")
except Exception:
    # fallback: 全量加载然后按 id 过滤
    all_tpl = load_primitives(tpl_dir)
    template = next(t for t in all_tpl if t.id == "differential-amplifier")

print(f"template: {template.id}, components={len(template.components)}")
for c in template.components:
    print(f"  comp {c.id} type={c.type} pins={[ (p.number, p.net) for p in c.pins ]}")

matcher = Vf2TopologyMatcher(ir)
result = matcher.find_matches(template, timeout_seconds=5.0)
print(f"aborted={result.aborted} msg={result.message!r} matches={len(result.matches)}")
for m in result.matches[:5]:
    print("  mapping:", m.component_mapping)
    print("  signals:", m.signal_mapping)
    print("  evidence:", m.evidence)

component_map = {c.id: c for c in ir.components}
net_map = {n.id: n for n in ir.nets}
scored = score_matches(result.matches, template, component_map, net_map,
                       weights=template.scoring.weights,
                       thresholds=template.scoring.thresholds)
for m, b in scored[:5]:
    print(f"  score={b.fused:.3f} conf={b.confidence} comps={m.component_mapping}")
